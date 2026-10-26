import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from careerpilot.domain import Principal
from careerpilot.persistence import AuditEvent
from careerpilot.platform_models import BackgroundTask, InboxReceipt, OutboxEvent, StateChange
from careerpilot.runtime import (
    cancel,
    checkpoint,
    claim,
    consume_event,
    deliver_events,
    enqueue,
    run_once,
)


def create_job(system, key="runtime-1"):
    _, engine, _, _, _, (alice, _) = system
    from careerpilot.persistence import User

    with Session(engine) as db, db.begin():
        principal = Principal(alice, db.get(User, alice).tenant_id)
        result = enqueue(db, principal, "baseline.validate", key, "test-trace")
    return result["id"], principal


def test_atomic_enqueue_and_rollback(system):
    _, engine, _, _, _, _ = system
    job_id, principal = create_job(system)
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(BackgroundTask)) == 1
        assert db.scalar(select(func.count()).select_from(OutboxEvent)) == 1
        assert enqueue(db, principal, "baseline.validate", "runtime-1", "other")["id"] == job_id
    with pytest.raises(RuntimeError), Session(engine) as db, db.begin():
        enqueue(db, principal, "baseline.validate", "rolledback", "trace")
        raise RuntimeError("crash")
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(BackgroundTask)) == 1


def test_outbox_replay_and_crash_are_idempotent(system):
    _, engine, *_ = system
    create_job(system)
    with pytest.raises(RuntimeError), Session(engine) as db, db.begin():
        consume_event(db, db.scalar(select(OutboxEvent)), crash=True)
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(InboxReceipt)) == 0
        assert db.scalar(select(func.count()).select_from(AuditEvent)) == 0
    deliver_events(engine)
    deliver_events(engine)
    with Session(engine) as db, db.begin():
        event = db.scalar(select(OutboxEvent))
        assert consume_event(db, event) is False
        assert db.scalar(select(func.count()).select_from(AuditEvent)) == 1


def test_worker_success_preserves_trace(system):
    _, engine, *_ = system
    job_id, _ = create_job(system)
    assert run_once(engine)
    assert not run_once(engine)
    with Session(engine) as db:
        task = db.get(BackgroundTask, job_id)
        assert (task.status, task.checkpoint, task.attempts) == ("success", "completed", 1)
        assert {row.trace_id for row in db.scalars(select(StateChange))} == {"test-trace"}


def test_retry_backoff_dead_letter_and_error_redaction(system):
    _, engine, *_ = system
    job_id, _ = create_job(system)

    def fail(*_):
        raise RuntimeError("Bearer do-not-store-this-private-secret")

    now = int(time.time())
    run_once(engine, now=now, handler=fail)
    assert not run_once(engine, now=now + 1, handler=fail)
    run_once(engine, now=now + 3, handler=fail)
    run_once(engine, now=now + 8, handler=fail)
    with Session(engine) as db:
        task = db.get(BackgroundTask, job_id)
        assert task.status == "failed" and task.attempts == 3
        assert task.last_error_class == "WORKER_HANDLER_ERROR"


def test_expired_lease_recovered_and_single_claim(system):
    _, engine, *_ = system
    job_id, _ = create_job(system)
    now = int(time.time())
    first = claim(engine, now, lease_seconds=1)
    assert first and first[0] == job_id
    assert claim(engine, now) is None
    second = claim(engine, now + 2)
    assert second and second[1] != first[1]


def test_parallel_claim_has_one_winner(system):
    _, engine, *_ = system
    create_job(system)
    now = int(time.time())
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: claim(engine, now), range(2)))
    assert sum(r is not None for r in results) == 1


def test_crashed_worker_recovers_and_old_lease_cannot_write(system):
    _, engine, *_ = system
    job_id, _ = create_job(system)
    now = int(time.time())
    first = claim(engine, now, lease_seconds=1)
    assert first
    assert run_once(engine, now=now + 2)
    assert checkpoint(engine, job_id, first[1], "stale-overwrite", now + 2) is False
    with Session(engine) as db:
        task = db.get(BackgroundTask, job_id)
        assert (task.status, task.checkpoint, task.attempts) == ("success", "completed", 2)


def test_cancel_at_checkpoint_prevents_success(system):
    _, engine, *_ = system
    job_id, principal = create_job(system)

    def cancel_while_running(*_):
        with Session(engine) as db, db.begin():
            cancel(db, principal, job_id, db.get(BackgroundTask, job_id).version)

    run_once(engine, handler=cancel_while_running)
    with Session(engine) as db:
        assert db.get(BackgroundTask, job_id).status == "cancelled"


def test_task_api_isolation_and_version(system):
    client, engine, alice, bob, *_ = system
    response = client.post(
        "/api/v1/agent-tasks", headers={**alice, "Idempotency-Key": "api-task"}, json={}
    )
    assert response.status_code == 202, response.text
    job = response.json()
    assert client.get(f"/api/v1/agent-tasks/{job['id']}", headers=bob).status_code == 404
    assert (
        client.post(
            f"/api/v1/agent-tasks/{job['id']}/cancel", headers={**bob, "If-Match": '"1"'}
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/v1/agent-tasks/{job['id']}/cancel", headers={**alice, "If-Match": '"2"'}
        ).status_code
        == 412
    )
    assert (
        client.post(
            f"/api/v1/agent-tasks/{job['id']}/cancel", headers={**alice, "If-Match": '"1"'}
        ).status_code
        == 200
    )
