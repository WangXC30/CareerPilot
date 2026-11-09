"""Transactional Outbox and leased, idempotent local jobs; no external side effects."""

import json
import time
from typing import Any

from sqlalchemy import and_, or_, select, text, update
from sqlalchemy.orm import Session

from careerpilot.application import audit
from careerpilot.domain import DomainError, Principal
from careerpilot.persistence import SCHEMA_REVISION, IdempotencyRecord, User, affected, new_id
from careerpilot.platform_models import BackgroundTask, InboxReceipt, OutboxEvent, StateChange
from careerpilot.safety import payload_hash
from careerpilot.states import validate_transition


def task_data(task):
    keys = (
        "id",
        "kind",
        "status",
        "version",
        "attempts",
        "max_attempts",
        "checkpoint",
        "last_error_class",
        "trace_id",
        "available_at",
    )
    return {key: getattr(task, key) for key in keys}


def owned_task(db, principal, task_id):
    task = db.scalar(
        select(BackgroundTask).where(
            BackgroundTask.id == task_id,
            BackgroundTask.user_id == principal.user_id,
            BackgroundTask.tenant_id == principal.tenant_id,
        )
    )
    if task is None:
        raise DomainError("NOT_FOUND", "任务不存在", 404)
    return task


def record_change(db, task, old, reason, actor="worker"):
    db.add(
        StateChange(
            user_id=task.user_id,
            tenant_id=task.tenant_id,
            aggregate_id=task.id,
            actor=actor,
            previous=old,
            current=task.status,
            revision=task.version,
            reason=reason,
            trace_id=task.trace_id,
        )
    )


def emit(db, task, event_type):
    db.add(
        OutboxEvent(
            user_id=task.user_id,
            tenant_id=task.tenant_id,
            aggregate_id=task.id,
            aggregate_version=task.version,
            event_type=event_type,
            trace_id=task.trace_id,
        )
    )


def enqueue(db, principal, kind, key, trace_id, request_payload=None):
    if kind not in {
        "baseline.validate",
        "growth.assistant",
        "market.subscription",
        "growth.review_pipeline",
    }:
        raise DomainError("UNSUPPORTED_TASK", "任务类型尚未启用", 422)
    request = {"action": "task.enqueue", "kind": kind}
    if request_payload is not None:
        request["payload"] = request_payload
    digest = payload_hash(request)
    previous = db.get(IdempotencyRecord, (principal.user_id, principal.tenant_id, key))
    if previous:
        if previous.request_hash != digest:
            raise DomainError("IDEMPOTENCY_CONFLICT", "幂等键已用于不同请求")
        return json.loads(previous.response_json)
    task = BackgroundTask(
        user_id=principal.user_id, tenant_id=principal.tenant_id, kind=kind, trace_id=trace_id
    )
    db.add(task)
    db.flush()
    emit(db, task, "AgentTaskQueued.v1")
    record_change(db, task, "", "user_requested", principal.user_id)
    result = task_data(task)
    db.add(
        IdempotencyRecord(
            user_id=principal.user_id,
            tenant_id=principal.tenant_id,
            key=key,
            request_hash=digest,
            response_json=json.dumps(result),
        )
    )
    return result


def cancel(db, principal, task_id, version):
    task = owned_task(db, principal, task_id)
    validate_transition("agent_task", task.status, "cancelled")
    old = task.status
    result = db.execute(
        update(BackgroundTask)
        .where(
            BackgroundTask.id == task_id,
            BackgroundTask.version == version,
            BackgroundTask.user_id == principal.user_id,
            BackgroundTask.tenant_id == principal.tenant_id,
        )
        .values(status="cancelled", version=version + 1, lease_token="", lease_until=0)
    )
    if affected(result) != 1:
        raise DomainError("VERSION_CONFLICT", "任务已更新", 412)
    db.expire_all()
    task = owned_task(db, principal, task_id)
    record_change(db, task, old, "user_cancelled", principal.user_id)
    emit(db, task, "AgentTaskCancelled.v1")
    return task_data(task)


def retry(db, principal, task_id, version):
    task = owned_task(db, principal, task_id)
    if task.kind == "growth.assistant":
        from careerpilot import growth

        growth.lock_owner(db, principal)
        record = db.get(growth.GrowthRecord, task_id)
        body = json.loads(record.payload_json).get("input", {}) if record else {}
        if conversation_id := body.get("conversation_id"):
            conversation = growth.data(growth.owned(db, principal, conversation_id, "conversation"))
            if conversation["run_ids"][-1] != task_id:
                raise DomainError("CHAT_RETRY_STALE", "已有后续消息，请重新发送请求。", 409)
    validate_transition("agent_task", task.status, "queued")
    if task.status != "failed":
        raise DomainError("INVALID_TRANSITION", "仅失败任务可手动重试")
    result = db.execute(
        update(BackgroundTask)
        .where(
            BackgroundTask.id == task_id,
            BackgroundTask.version == version,
        )
        .values(
            status="queued",
            version=version + 1,
            attempts=0,
            available_at=0,
            last_error_class="",
            lease_token="",
            lease_until=0,
        )
    )
    if affected(result) != 1:
        raise DomainError("VERSION_CONFLICT", "任务已更新", 412)
    db.expire_all()
    task = owned_task(db, principal, task_id)
    record_change(db, task, "failed", "manual_retry", principal.user_id)
    emit(db, task, "AgentTaskQueued.v1")
    return task_data(task)


def consume_event(db, event, crash=False):
    """Effect and inbox receipt commit together; crash before ack is safe to replay."""
    key = (event.id, "task-audit.v1")
    if db.get(InboxReceipt, key):
        return False
    principal = Principal(event.user_id, event.tenant_id)
    audit(
        db, principal, event.aggregate_id, event.event_type, event.aggregate_version, event.trace_id
    )
    db.add(InboxReceipt(event_id=event.id, consumer="task-audit.v1"))
    db.flush()
    if crash:
        raise RuntimeError("injected_crash_before_commit")
    return True


def deliver_events(engine, limit=50):
    # The CAS update serializes delivery across worker processes; no external I/O
    # occurs inside this transaction. Uncommitted claims roll back on process death.
    with Session(engine) as db, db.begin():
        ids = list(
            db.scalars(select(OutboxEvent.id).where(OutboxEvent.delivered == 0).limit(limit))
        )
        for event_id in ids:
            claimed = db.execute(
                update(OutboxEvent)
                .where(OutboxEvent.id == event_id, OutboxEvent.delivered == 0)
                .values(delivered=1)
            )
            if affected(claimed) == 1:
                consume_event(db, db.get(OutboxEvent, event_id))


def claim(engine, now, lease_seconds=30):
    with Session(engine) as db, db.begin():
        task = db.scalar(
            select(BackgroundTask)
            .where(
                or_(
                    and_(BackgroundTask.status == "queued", BackgroundTask.available_at <= now),
                    and_(BackgroundTask.status == "running", BackgroundTask.lease_until <= now),
                )
            )
            .order_by(BackgroundTask.created_at)
            .limit(1)
        )
        if task is None:
            return None
        version, old = task.version, task.status
        token = new_id()
        values: dict[str, Any] = dict(version=version + 1)
        if task.attempts >= task.max_attempts:
            values.update(status="failed", last_error_class="LEASE_EXHAUSTED", lease_token="")
        else:
            values.update(
                status="running",
                attempts=task.attempts + 1,
                lease_until=now + lease_seconds,
                lease_token=token,
            )
        result = db.execute(
            update(BackgroundTask)
            .where(BackgroundTask.id == task.id, BackgroundTask.version == version)
            .values(**values)
        )
        if affected(result) != 1:
            return None
        db.expire_all()
        task = db.get(BackgroundTask, task.id)
        assert task is not None
        record_change(db, task, old, "lease_claimed" if task.status == "running" else "dead_letter")
        if task.status == "failed":
            emit(db, task, "AgentTaskFailed.v1")
            return None
        return task.id, token


def checkpoint(engine, task_id, token, stage, now):
    with Session(engine) as db, db.begin():
        result = db.execute(
            update(BackgroundTask)
            .where(
                BackgroundTask.id == task_id,
                BackgroundTask.status == "running",
                BackgroundTask.lease_token == token,
                BackgroundTask.lease_until > now,
            )
            .values(checkpoint=stage, lease_until=now + 30, version=BackgroundTask.version + 1)
        )
        return affected(result) == 1


def baseline_handler(engine, task_id, token, now):
    with Session(engine) as db:
        task = db.get(BackgroundTask, task_id)
        if task is None or task.kind != "baseline.validate":
            raise ValueError("unsupported baseline task")
        owner = db.scalar(
            select(User).where(User.id == task.user_id, User.tenant_id == task.tenant_id)
        )
        if owner is None:
            raise ValueError("invalid owner")
    if not checkpoint(engine, task_id, token, "ownership_checked", now):
        return
    with engine.connect() as connection:
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
        if revision != SCHEMA_REVISION:
            raise ValueError("schema revision mismatch")
        # The required platform tables must be queryable, not merely listed by a
        # version row. These queries contain no private data in their output.
        connection.execute(select(OutboxEvent.id).limit(1)).first()
        connection.execute(select(InboxReceipt.event_id).limit(1)).first()
    if not checkpoint(engine, task_id, token, "contracts_checked", now):
        return


def dispatch_handler(engine, task_id, token, now):
    with Session(engine) as db:
        task = db.get(BackgroundTask, task_id)
        kind = task.kind if task else ""
    if kind == "growth.assistant":
        from careerpilot.assistant import handler

        return handler(engine, task_id, token, now)
    if kind == "growth.review_pipeline":
        from careerpilot.review_pipeline import handler

        return handler(engine, task_id, token, now)
    if kind == "market.subscription":
        from careerpilot.subscriptions import handler

        return handler(engine, task_id, token, now)
    return baseline_handler(engine, task_id, token, now)


def run_once(engine, *, now=None, handler=None):
    handler = handler or dispatch_handler
    fixed_clock = now is not None
    now = int(time.time()) if now is None else now
    deliver_events(engine)
    claimed = claim(engine, now)
    if claimed is None:
        return False
    task_id, token = claimed
    error = ""
    try:
        handler(engine, task_id, token, now)
    except Exception:
        # Persist an enum, never an exception message which may contain credentials.
        error = "WORKER_HANDLER_ERROR"
    if not fixed_clock:
        now = int(time.time())
    with Session(engine) as db, db.begin():
        task = db.get(BackgroundTask, task_id)
        if task is None:
            return False
        if task.status != "running" or task.lease_token != token or task.lease_until <= now:
            return False
        old = task.status
        target = (
            "success"
            if not error
            else ("failed" if task.attempts >= task.max_attempts else "queued")
        )
        validate_transition("agent_task", old, target)
        result = db.execute(
            update(BackgroundTask)
            .where(
                BackgroundTask.id == task_id,
                BackgroundTask.version == task.version,
                BackgroundTask.lease_token == token,
                BackgroundTask.status == "running",
            )
            .values(
                status=target,
                version=task.version + 1,
                last_error_class=error,
                checkpoint="completed" if not error else task.checkpoint,
                available_at=now + 2**task.attempts,
                lease_token="",
                lease_until=0,
            )
        )
        if affected(result) != 1:
            return False
        db.expire_all()
        task = db.get(BackgroundTask, task_id)
        assert task is not None
        record_change(db, task, old, error or "completed")
        emit(db, task, "AgentTaskStateChanged.v1")
    deliver_events(engine)
    return True
