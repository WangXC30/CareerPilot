"""Provider-neutral, bounded structured generation. Never log model inputs/outputs."""

import asyncio
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from jsonschema import Draft202012Validator
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from careerpilot.domain import DomainError, Principal
from careerpilot.persistence import affected, new_id
from careerpilot.platform_models import ModelUsage, QuotaBucket
from careerpilot.safety import require_consent


@dataclass(frozen=True)
class ProviderReply:
    content: str
    input_tokens: int
    output_tokens: int


class Provider(Protocol):
    model_id: str
    external: bool

    async def generate(self, envelope: str, max_tokens: int) -> ProviderReply: ...


def strict_json(raw: str):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def invalid(_):
        raise ValueError("non-finite JSON number")

    return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)


def structured_envelope(data: str, schema: dict) -> str:
    return json.dumps(
        {
            "instruction": "Extract data only. No tools or side effects.",
            "untrusted_data": data,
            "output_schema": schema,
        }
    )


def reserve(db: Session, principal: Principal, scope: str, amount: int, limit: int):
    key = (principal.user_id, principal.tenant_id, scope)
    if db.get(QuotaBucket, key) is None:
        try:
            with db.begin_nested():
                db.add(QuotaBucket(user_id=key[0], tenant_id=key[1], scope=scope, used=0))
                db.flush()
        except IntegrityError:
            pass
    result = db.execute(
        update(QuotaBucket)
        .where(
            QuotaBucket.user_id == key[0],
            QuotaBucket.tenant_id == key[1],
            QuotaBucket.scope == scope,
            QuotaBucket.used + amount <= limit,
        )
        .values(used=QuotaBucket.used + amount)
    )
    if affected(result) != 1:
        raise DomainError("QUOTA_EXCEEDED", "已达到调用配额", 429)


class Gateway:
    def __init__(
        self,
        engine,
        provider: Provider,
        *,
        timeout: float = 10,
        daily_tokens: int = 100_000,
        task_tokens: int = 20_000,
        micros_per_token: int = 1,
    ):
        self.engine, self.provider, self.timeout = engine, provider, timeout
        self.daily_tokens, self.task_tokens = daily_tokens, task_tokens
        self.micros_per_token = micros_per_token

    def reservation(self, envelope: str, max_tokens: int) -> int:
        """Share one conservative bound with callers that reserve monetary budgets."""
        overhead = getattr(self.provider, "input_token_overhead", 0)
        if type(overhead) is not int or not 0 <= overhead <= 8192:
            raise DomainError("MODEL_LIMIT", "模型请求超出限制", 422)
        return len(envelope.encode()) + max_tokens + overhead

    async def generate(
        self,
        principal: Principal,
        task_id: str,
        data: str,
        schema: dict,
        *,
        prompt_version: str,
        schema_version: str,
        trace_id: str,
        max_tokens: int = 1024,
        retries: int = 1,
    ):
        if not (1 <= max_tokens <= 4096 and 0 <= retries <= 2 and len(data.encode()) <= 32768):
            raise DomainError("MODEL_LIMIT", "模型请求超出限制", 422)

        # No remote refs can cause a network fetch during schema validation.
        def refs(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    if key == "$ref" and not str(item).startswith("#/"):
                        raise DomainError("INVALID_SCHEMA", "不允许外部 Schema 引用", 422)
                    refs(item)
            elif isinstance(value, list):
                for item in value:
                    refs(item)

        refs(schema)
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema)
        envelope = structured_envelope(data, schema)
        reserved = self.reservation(envelope, max_tokens)
        period = datetime.now(timezone.utc).date().isoformat()
        for attempt in range(retries + 1):
            usage_id = new_id()
            with Session(self.engine) as db, db.begin():
                if self.provider.external:
                    require_consent(db, principal, "external_model")
                reserve(db, principal, "day:" + period, reserved, self.daily_tokens)
                reserve(db, principal, "task:" + task_id, reserved, self.task_tokens)
                db.add(
                    ModelUsage(
                        id=usage_id,
                        user_id=principal.user_id,
                        tenant_id=principal.tenant_id,
                        task_id=task_id,
                        period=period,
                        model_id=self.provider.model_id,
                        prompt_version=prompt_version,
                        schema_version=schema_version,
                        reserved_tokens=reserved,
                        trace_id=trace_id,
                    )
                )
            start = time.monotonic()
            error = ""
            parsed = None
            reply = None
            try:
                reply = await asyncio.wait_for(
                    self.provider.generate(envelope, max_tokens), timeout=self.timeout
                )
                if (
                    type(reply.input_tokens) is not int
                    or type(reply.output_tokens) is not int
                    or reply.input_tokens < 0
                    or reply.output_tokens < 0
                    or reply.input_tokens + reply.output_tokens > reserved
                    or reply.output_tokens > max_tokens
                    or len(reply.content.encode()) > 65536
                ):
                    raise ValueError("provider usage/output exceeds limit")
                parsed = strict_json(reply.content)
                if not isinstance(parsed, dict) or not validator.is_valid(parsed):
                    raise ValueError("invalid structured output")
            except TimeoutError:
                error = "MODEL_TIMEOUT"
            except (ValueError, TypeError):
                error = "MODEL_SCHEMA_ERROR"
            except Exception:
                error = "MODEL_PROVIDER_ERROR"
            with Session(self.engine) as db, db.begin():
                usage = db.get(ModelUsage, usage_id)
                assert usage is not None
                usage.latency_ms = int((time.monotonic() - start) * 1000)
                usage.error_class = error or "success"
                if reply and type(reply.input_tokens) is int and type(reply.output_tokens) is int:
                    usage.input_tokens = max(0, min(reserved, reply.input_tokens))
                    usage.output_tokens = max(0, min(max_tokens, reply.output_tokens))
                    usage.estimated_cost_micros = (
                        usage.input_tokens + usage.output_tokens
                    ) * self.micros_per_token
                # Recheck live consent before publishing a private result.
                if self.provider.external:
                    try:
                        require_consent(db, principal, "external_model")
                    except DomainError:
                        error = "CONSENT_REVOKED"
                        usage.error_class = error
            if not error:
                return parsed
            if error == "CONSENT_REVOKED":
                raise DomainError(error, "授权已撤销", 403)
            if attempt == retries:
                raise DomainError(error, "模型处理失败，可手动继续或稍后重试", 503)
        raise AssertionError("unreachable")
