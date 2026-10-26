"""Deterministic approval boundary. No live sending tools are shipped in V0."""

import time
from dataclasses import dataclass

from jsonschema import Draft202012Validator
from sqlalchemy import update

from careerpilot.domain import DomainError
from careerpilot.persistence import affected
from careerpilot.platform_models import Approval
from careerpilot.safety import payload_hash


@dataclass(frozen=True)
class ToolSpec:
    name: str
    risk: int
    schema: dict
    allowed_roles: frozenset[str]
    timeout_seconds: int = 10
    max_calls: int = 10


def issue_approval(db, principal, action, target, payload, ttl=300):
    if not (1 <= ttl <= 300):
        raise DomainError("INVALID_APPROVAL", "审批有效期不能超过五分钟", 422)
    approval = Approval(
        user_id=principal.user_id,
        tenant_id=principal.tenant_id,
        action=action,
        target=target,
        payload_hash=payload_hash(payload),
        expires_at=int(time.time()) + ttl,
    )
    db.add(approval)
    db.flush()
    return approval.id


def authorize_tool(
    db,
    principal,
    spec,
    payload,
    *,
    target="",
    approval_id=None,
    calls=0,
    elapsed_seconds=0,
    now=None,
):
    if (
        principal.role not in spec.allowed_roles
        or not Draft202012Validator(spec.schema).is_valid(payload)
        or calls >= spec.max_calls
        or elapsed_seconds >= spec.timeout_seconds
    ):
        raise DomainError("TOOL_DENIED", "工具调用超出已授权范围", 403)
    if spec.risk < 2:
        return
    if not approval_id:
        raise DomainError("APPROVAL_REQUIRED", "该操作需要具体确认", 403)
    result = db.execute(
        update(Approval)
        .where(
            Approval.id == approval_id,
            Approval.user_id == principal.user_id,
            Approval.tenant_id == principal.tenant_id,
            Approval.action == spec.name,
            Approval.target == target,
            Approval.payload_hash == payload_hash(payload),
            Approval.expires_at > (int(time.time()) if now is None else now),
            Approval.consumed == 0,
        )
        .values(consumed=1)
    )
    if affected(result) != 1:
        raise DomainError("APPROVAL_INVALID", "审批无效、已过期或已使用", 403)
