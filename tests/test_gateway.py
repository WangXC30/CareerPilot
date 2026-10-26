import asyncio

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from careerpilot.domain import DomainError, Principal
from careerpilot.gateway import Gateway, ProviderReply, strict_json
from careerpilot.persistence import User
from careerpilot.platform_models import Consent, ModelUsage

SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
    "additionalProperties": False,
}


class FakeProvider:
    model_id, external = "offline-fixture-v1", False

    def __init__(self, outputs):
        self.outputs, self.calls, self.envelope = iter(outputs), 0, ""

    async def generate(self, envelope, max_tokens):
        self.calls += 1
        self.envelope = envelope
        result = next(self.outputs)
        if result == "timeout":
            await asyncio.sleep(1)
        return ProviderReply(result, 8, 4)


def principal_of(system):
    _, engine, _, _, _, (alice, _) = system
    with Session(engine) as db:
        return Principal(alice, db.get(User, alice).tenant_id)


def generate(gateway, principal, **kwargs):
    return asyncio.run(
        gateway.generate(
            principal,
            "task1",
            "private-resume-canary",
            SCHEMA,
            prompt_version="prompt.v1",
            schema_version="schema.v1",
            trace_id="gateway-trace",
            **kwargs,
        )
    )


def test_schema_retry_and_versions_without_content_persistence(system):
    _, engine, *_ = system
    provider = FakeProvider(['{"answer":4}', '{"answer":"ok"}'])
    result = generate(Gateway(engine, provider), principal_of(system))
    assert result == {"answer": "ok"} and provider.calls == 2
    assert "untrusted_data" in provider.envelope
    with Session(engine) as db:
        usages = db.scalars(select(ModelUsage)).all()
        assert [u.error_class for u in usages] == ["MODEL_SCHEMA_ERROR", "success"]
        assert all(
            u.trace_id == "gateway-trace" and u.prompt_version == "prompt.v1" for u in usages
        )
        assert "private-resume-canary" not in repr([u.__dict__ for u in usages])


@pytest.mark.parametrize(
    "output", ["not-json", '{"answer":"x","extra":1}', '{"answer":"x","answer":"y"}']
)
def test_invalid_json_is_rejected(system, output):
    _, engine, *_ = system
    with pytest.raises(DomainError, match="模型处理失败"):
        generate(Gateway(engine, FakeProvider([output])), principal_of(system), retries=0)


def test_timeout_and_quota_are_bounded(system):
    _, engine, *_ = system
    principal = principal_of(system)
    provider = FakeProvider(["timeout"])
    with pytest.raises(DomainError) as error:
        generate(Gateway(engine, provider, timeout=0.01), principal, retries=0)
    assert error.value.code == "MODEL_TIMEOUT"
    with pytest.raises(DomainError) as error:
        generate(Gateway(engine, provider, daily_tokens=1), principal)
    assert error.value.code == "QUOTA_EXCEEDED" and provider.calls == 1


def test_external_model_requires_current_consent(system):
    _, engine, *_ = system
    principal = principal_of(system)
    provider = FakeProvider(['{"answer":"ok"}'])
    provider.external = True
    gateway = Gateway(engine, provider)
    with pytest.raises(DomainError) as error:
        generate(gateway, principal)
    assert error.value.code == "CONSENT_REQUIRED" and provider.calls == 0
    with Session(engine) as db, db.begin():
        db.add(
            Consent(
                user_id=principal.user_id,
                tenant_id=principal.tenant_id,
                purpose="external_model",
                granted=1,
            )
        )
    assert generate(gateway, principal) == {"answer": "ok"}
    with Session(engine) as db:
        usage = db.scalar(select(ModelUsage))
        assert usage.error_class == "success"
        assert (usage.input_tokens, usage.output_tokens, usage.estimated_cost_micros) == (8, 4, 12)


def test_nonfinite_and_remote_schema_rejected():
    with pytest.raises(ValueError):
        strict_json('{"x":NaN}')


def test_remote_schema_never_reaches_provider(system):
    _, engine, *_ = system
    provider = FakeProvider([])
    with pytest.raises(DomainError) as error:
        asyncio.run(
            Gateway(engine, provider).generate(
                principal_of(system),
                "task1",
                "private",
                {"$ref": "https://untrusted.example/schema"},
                prompt_version="v1",
                schema_version="v1",
                trace_id="trace",
            )
        )
    assert error.value.code == "INVALID_SCHEMA" and provider.calls == 0


def test_revocation_during_external_call_suppresses_result(system):
    _, engine, *_ = system
    principal = principal_of(system)
    with Session(engine) as db, db.begin():
        db.add(
            Consent(
                user_id=principal.user_id,
                tenant_id=principal.tenant_id,
                purpose="external_model",
                granted=1,
            )
        )

    class RevokingProvider(FakeProvider):
        external = True

        async def generate(self, envelope, max_tokens):
            with Session(engine) as db, db.begin():
                consent = db.get(
                    Consent, (principal.user_id, principal.tenant_id, "external_model")
                )
                consent.granted = 0
            return ProviderReply('{"answer":"private"}', 8, 4)

    with pytest.raises(DomainError) as error:
        generate(Gateway(engine, RevokingProvider([])), principal)
    assert error.value.code == "CONSENT_REVOKED"


def test_task_quota_rolls_back_daily_reservation(system):
    _, engine, *_ = system
    principal = principal_of(system)
    provider = FakeProvider(['{"answer":"ok"}'])
    with pytest.raises(DomainError) as error:
        generate(Gateway(engine, provider, task_tokens=1), principal)
    assert error.value.code == "QUOTA_EXCEEDED" and provider.calls == 0
    assert generate(Gateway(engine, provider), principal) == {"answer": "ok"}


def test_provider_chat_overhead_is_reserved_before_any_request(system):
    _, engine, *_ = system
    provider = FakeProvider(['{"answer":"ok"}'])
    provider.input_token_overhead = 1024
    gateway = Gateway(engine, provider, task_tokens=1024)
    with pytest.raises(DomainError) as error:
        generate(gateway, principal_of(system))
    assert error.value.code == "QUOTA_EXCEEDED" and provider.calls == 0


@pytest.mark.parametrize("overhead", [True, -1, 8193, "1024"])
def test_invalid_provider_overhead_rejects_without_usage_or_call(system, overhead):
    _, engine, *_ = system
    provider = FakeProvider([])
    provider.input_token_overhead = overhead
    with pytest.raises(DomainError) as error:
        generate(Gateway(engine, provider), principal_of(system))
    assert error.value.code == "MODEL_LIMIT" and provider.calls == 0
    with Session(engine) as db:
        assert list(db.scalars(select(ModelUsage))) == []
