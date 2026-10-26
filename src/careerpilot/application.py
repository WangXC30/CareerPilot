"""Transactional use cases; API and future Agent tools share these rules."""

import hashlib
import json

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from careerpilot.domain import DomainError, Principal
from careerpilot.persistence import AuditEvent, CareerGoal, IdempotencyRecord, affected


def owned_goal(session: Session, principal: Principal, goal_id: str) -> CareerGoal:
    goal = session.scalar(
        select(CareerGoal).where(
            CareerGoal.id == goal_id,
            CareerGoal.user_id == principal.user_id,
            CareerGoal.tenant_id == principal.tenant_id,
        )
    )
    if goal is None:
        raise DomainError("NOT_FOUND", "资源不存在", 404)
    return goal


def goal_data(goal: CareerGoal) -> dict:
    return {key: getattr(goal, key) for key in ("id", "title", "status", "version", "created_at")}


def create_goal(session: Session, principal: Principal, title: str, key: str, trace: str):
    digest = hashlib.sha256(
        json.dumps({"action": "goal.create", "title": title}, sort_keys=True).encode()
    ).hexdigest()
    existing = session.get(IdempotencyRecord, (principal.user_id, principal.tenant_id, key))
    if existing:
        if existing.request_hash != digest:
            raise DomainError("IDEMPOTENCY_CONFLICT", "幂等键已用于不同请求")
        return json.loads(existing.response_json)
    goal = CareerGoal(user_id=principal.user_id, tenant_id=principal.tenant_id, title=title)
    session.add(goal)
    session.flush()
    result = goal_data(goal)
    session.add(
        IdempotencyRecord(
            user_id=principal.user_id,
            tenant_id=principal.tenant_id,
            key=key,
            request_hash=digest,
            response_json=json.dumps(result),
        )
    )
    audit(session, principal, goal.id, "goal.created", 1, trace)
    return result


def audit(session, principal, goal_id, action, revision, trace):
    if action.startswith("goal."):
        from careerpilot.growth_models import GrowthRecord, GrowthRevision

        goal = owned_goal(session, principal, goal_id)
        row = session.scalar(
            select(GrowthRecord).where(
                GrowthRecord.id == goal_id,
                GrowthRecord.user_id == principal.user_id,
                GrowthRecord.tenant_id == principal.tenant_id,
                GrowthRecord.kind == "goal_history",
            )
        )
        payload = json.dumps({**goal_data(goal), "reason": action}, ensure_ascii=False)
        # The goal's existing CAS/transaction is authoritative. Snapshot it without
        # recursively emitting a second audit event for the same operation.
        if row is None:
            row = GrowthRecord(
                id=goal_id,
                user_id=principal.user_id,
                tenant_id=principal.tenant_id,
                kind="goal_history",
                payload_json=payload,
                version=revision,
            )
            session.add(row)
        else:
            row.payload_json, row.version = payload, revision
        session.flush()
        session.add(
            GrowthRevision(
                record_id=goal_id,
                version=revision,
                user_id=principal.user_id,
                tenant_id=principal.tenant_id,
                payload_json=payload,
            )
        )
    session.add(
        AuditEvent(
            user_id=principal.user_id,
            tenant_id=principal.tenant_id,
            aggregate_id=goal_id,
            action=action,
            revision=revision,
            trace_id=trace,
        )
    )


def rename_goal(
    session: Session, principal: Principal, goal_id: str, title: str, version: int, trace: str
):
    owned_goal(session, principal, goal_id)
    result = session.execute(
        update(CareerGoal)
        .where(
            CareerGoal.id == goal_id,
            CareerGoal.user_id == principal.user_id,
            CareerGoal.tenant_id == principal.tenant_id,
            CareerGoal.version == version,
        )
        .values(title=title, version=version + 1)
    )
    if affected(result) != 1:
        raise DomainError("VERSION_CONFLICT", "资源已更新，请刷新后重试", 412)
    audit(session, principal, goal_id, "goal.renamed", version + 1, trace)
    session.expire_all()
    return goal_data(owned_goal(session, principal, goal_id))
