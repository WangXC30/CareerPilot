"""Deleting chat removes all content copies, preserves business results and reclaims space."""

import json
import sqlite3
from uuid import NAMESPACE_URL, uuid5

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from careerpilot import accounts, conversation_deletion, growth, workflows
from careerpilot.chat_limits import MAX_TOOL_CALLS
from careerpilot.domain import Principal
from careerpilot.growth_models import GrowthRecord, GrowthRevision
from careerpilot.persistence import AuditEvent, IdempotencyRecord, User
from careerpilot.platform_models import BackgroundTask, ModelUsage, QuotaBucket
from careerpilot.runtime import run_once
from tests.test_conversations import conversation, decision, profile, result, resume, send
from tests.test_conversations import install as install

CANARY = "chat-delete-private-canary"


def current(client, headers, key):
    return next(
        row
        for row in client.get("/api/v1/workflows/conversations", headers=headers).json()
        if row["id"] == key
    )


def remove(client, headers, key, version=None):
    version = current(client, headers, key)["version"] if version is None else version
    return client.request(
        "DELETE",
        f"/api/v1/workflows/conversations/{key}",
        headers={**headers, "If-Match": f'"{version}"'},
        json={"confirmed": True},
    )


def test_deletion_erases_history_receipts_events_and_checkpoints_but_keeps_results(system, install):
    client, engine, alice, bob, *_ = system
    job_id = profile(client, alice)
    plan = client.post(
        "/api/v1/plans", headers=alice, json={"job_ids": [job_id], "start_date": "2026-10-27"}
    ).json()
    install(
        [
            decision(answer=CANARY),
            decision("remember", text="保留独立偏好"),
            decision(answer="偏好已保存"),
        ]
    )
    chat = conversation(client, alice)
    other = conversation(client, alice)
    foreign = conversation(client, bob)
    first = send(client, alice, chat, CANARY).json()
    run_once(engine)
    second = send(client, alice, chat, "保存偏好").json()
    run_once(engine)
    resume(client, alice, result(client, alice, second))
    run_once(engine)
    with Session(engine) as db, db.begin():
        row = db.get(GrowthRecord, first["id"])
        for kind, index in [("model", MAX_TOOL_CALLS), ("tool", MAX_TOOL_CALLS - 1)]:
            growth.save(
                db,
                Principal(row.user_id, row.tenant_id, "student"),
                "chat_" + kind + "_receipt",
                {"text": CANARY},
                "test-extended-receipt",
                record_id=str(
                    uuid5(NAMESPACE_URL, f"careerpilot:{first['id']}:chat.{kind}:{index}")
                ),
            )
    with Session(engine) as db:
        budgets = {
            (r.user_id, r.scope): r.used
            for r in db.scalars(select(QuotaBucket))
            if r.scope.startswith("day:") or r.scope == "growth.chat.cost.v1"
        }
        usage_count = len(list(db.scalars(select(ModelUsage))))
    deleted = remove(client, alice, chat)
    assert deleted.status_code == 204 and deleted.content == b""
    assert (
        client.get(f"/api/v1/workflows/conversations/{chat}/runs", headers=alice).status_code == 404
    )
    assert client.get(f"/api/v1/workflows/runs/{first['id']}", headers=alice).status_code == 404
    assert CANARY not in json.dumps(client.get("/api/v1/account/export", headers=alice).json())
    workflows.process_cleanup(engine)
    for run in (first, second):
        with workflows.saver(engine) as saver:
            assert saver.get_tuple({"configurable": {"thread_id": run["id"]}}) is None
    with Session(engine) as db:
        keys = [
            chat,
            first["id"],
            second["id"],
            *conversation_deletion.receipt_ids(first["id"]),
            *conversation_deletion.receipt_ids(second["id"]),
        ]
        assert not db.scalar(select(GrowthRecord).where(GrowthRecord.id.in_(keys)))
        assert not db.scalar(select(GrowthRevision).where(GrowthRevision.record_id.in_(keys)))
        assert not db.scalar(select(AuditEvent).where(AuditEvent.aggregate_id.in_(keys)))
        assert not db.get(BackgroundTask, first["id"])
        assert not db.get(GrowthRecord, conversation_deletion.cleanup_id(chat))
        assert not any(
            json.loads(row.response_json).get("id") in {first["id"], second["id"]}
            for row in db.scalars(select(IdempotencyRecord))
        )
        assert len(list(db.scalars(select(ModelUsage)))) == usage_count
        assert budgets == {
            (r.user_id, r.scope): r.used
            for r in db.scalars(select(QuotaBucket))
            if r.scope.startswith("day:") or r.scope == "growth.chat.cost.v1"
        }
    assert client.get("/api/v1/plans", headers=alice).json()[0]["id"] == plan["id"]
    assert len(client.get("/api/v1/workflows/preferences", headers=alice).json()) == 1
    assert {
        row["id"] for row in client.get("/api/v1/workflows/conversations", headers=alice).json()
    } == {other}
    assert (
        client.get(f"/api/v1/workflows/conversations/{foreign}/runs", headers=bob).status_code
        == 200
    )


def test_delete_requires_ownership_confirmation_and_latest_version(system):
    client, _, alice, bob, *_ = system
    chat = conversation(client, alice)
    assert remove(client, bob, chat, 1).status_code == 404
    assert remove(client, alice, chat, 99).status_code == 412
    response = client.request(
        "DELETE",
        f"/api/v1/workflows/conversations/{chat}",
        headers={**alice, "If-Match": '"1"'},
        json={"confirmed": False},
    )
    assert response.status_code == 422
    assert (
        client.request(
            "DELETE",
            f"/api/v1/workflows/conversations/{chat}",
            headers=alice,
            json={"confirmed": True},
        ).status_code
        == 422
    )
    assert current(client, alice, chat)["version"] == 1


@pytest.mark.parametrize("started", [False, True])
def test_delete_cancels_queued_or_waiting_runs_without_saving_pending_business(
    system, install, started
):
    client, engine, alice, *_ = system
    model = install([decision("remember", text=CANARY)])
    chat = conversation(client, alice)
    run = send(client, alice, chat).json()
    if started:
        run_once(engine)
        assert result(client, alice, run)["status"] == "waiting_confirmation"
    assert remove(client, alice, chat).status_code == 204
    assert not run_once(engine)
    workflows.process_cleanup(engine)
    assert client.get("/api/v1/workflows/preferences", headers=alice).json() == []
    assert len(model.requests) == int(started)
    with Session(engine) as db:
        assert not db.get(BackgroundTask, run["id"])
    assert (
        client.post(
            f"/api/v1/workflows/runs/{run['id']}/resume",
            headers={**alice, "If-Match": '"1"'},
            json={"approved": True},
        ).status_code
        == 404
    )


def test_delete_during_model_request_prevents_late_effect_and_worker_survives(system, install):
    client, engine, alice, *_ = system
    chat = conversation(client, alice)
    run = send(client, alice, chat, CANARY).json()

    def delete_while_running(data):
        assert remove(client, alice, chat).status_code == 204
        return decision("remember", text=CANARY)

    model = install([delete_while_running])
    assert not run_once(engine)
    workflows.process_cleanup(engine)
    assert len(model.requests) == 1
    assert client.get("/api/v1/workflows/preferences", headers=alice).json() == []
    assert CANARY not in json.dumps(client.get("/api/v1/account/export", headers=alice).json())
    with workflows.saver(engine) as saver:
        assert saver.get_tuple({"configurable": {"thread_id": run["id"]}}) is None
    install([decision()])
    next_run = send(client, alice, conversation(client, alice)).json()
    assert run_once(engine)
    assert result(client, alice, next_run)["status"] == "completed"


def test_delete_after_tool_commit_keeps_saved_preference(system, install):
    client, engine, alice, *_ = system
    chat = conversation(client, alice)
    run = send(client, alice, chat).json()

    def delete_after_tool(data):
        assert remove(client, alice, chat).status_code == 204
        return decision(answer="late reply must disappear")

    install([decision("remember", text="独立偏好需要保留"), delete_after_tool])
    run_once(engine)
    resume(client, alice, result(client, alice, run))
    assert not run_once(engine)
    workflows.process_cleanup(engine)
    assert len(client.get("/api/v1/workflows/preferences", headers=alice).json()) == 1
    assert client.get(f"/api/v1/workflows/runs/{run['id']}", headers=alice).status_code == 404


def test_cleanup_retry_and_reused_creation_key_cannot_revive_pending_deleted_chat(
    system, monkeypatch
):
    client, engine, alice, *_ = system
    headers = {**alice, "Idempotency-Key": "deleted-chat-creation-key"}
    chat = client.post("/api/v1/workflows/conversations", headers=headers, json={}).json()["id"]
    assert remove(client, alice, chat).status_code == 204
    original = conversation_deletion.compact_storage
    monkeypatch.setattr(conversation_deletion, "compact_storage", lambda engine: False)
    workflows.process_cleanup(engine)
    assert (
        client.post("/api/v1/workflows/conversations", headers=headers, json={}).status_code == 409
    )
    with Session(engine) as db:
        assert db.get(GrowthRecord, conversation_deletion.cleanup_id(chat))
    monkeypatch.setattr(conversation_deletion, "compact_storage", original)
    workflows.process_cleanup(engine)
    recreated = client.post("/api/v1/workflows/conversations", headers=headers, json={})
    assert recreated.status_code == 201
    assert (
        client.get(f"/api/v1/workflows/conversations/{chat}/runs", headers=alice).json()["items"]
        == []
    )


def test_sqlite_space_is_reclaimed_instead_of_only_hiding_chat(system, install):
    client, engine, alice, *_ = system
    install([decision(answer=CANARY)])
    chat = conversation(client, alice)
    run = send(client, alice, chat).json()
    run_once(engine)
    with Session(engine) as db, db.begin():
        row = db.get(GrowthRecord, chat)
        value = json.loads(row.payload_json)
        value["summary"] = CANARY + "x" * 1_000_000
        principal = workflows.owner(db.get(BackgroundTask, run["id"]))
        growth.save(db, principal, "conversation", value, "temporary-bloat", row, row.version)
    with sqlite3.connect(engine.url.database) as connection:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        before = connection.execute("PRAGMA page_count").fetchone()[0]
    assert remove(client, alice, chat).status_code == 204
    workflows.process_cleanup(engine)
    with sqlite3.connect(engine.url.database) as connection:
        after = connection.execute("PRAGMA page_count").fetchone()[0]
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert after < before / 2


def test_workspace_clear_includes_checkpoints_of_pending_deleted_conversations(system, install):
    client, engine, alice, *_ = system
    install([decision(answer=CANARY)])
    chat = conversation(client, alice)
    run = send(client, alice, chat).json()
    run_once(engine)
    assert remove(client, alice, chat).status_code == 204
    with Session(engine) as db, db.begin():
        user = db.get(User, system[-1][0])
        accounts.request_deletion(
            db, Principal(user.id, user.tenant_id), "temporary-workspace-clear"
        )
    assert accounts.process_deletion(engine)
    with workflows.saver(engine) as saver:
        assert saver.get_tuple({"configurable": {"thread_id": run["id"]}}) is None


def test_busy_checkpoint_cleanup_keeps_queue_for_a_later_worker_cycle(system, install, monkeypatch):
    client, engine, alice, *_ = system
    install([decision()])
    chat = conversation(client, alice)
    send(client, alice, chat)
    run_once(engine)
    assert remove(client, alice, chat).status_code == 204
    original = workflows.purge_checkpoints

    def busy(*args):
        raise sqlite3.OperationalError("database is busy")

    monkeypatch.setattr(workflows, "purge_checkpoints", busy)
    workflows.process_cleanup(engine)
    with Session(engine) as db:
        assert db.get(GrowthRecord, conversation_deletion.cleanup_id(chat))
    monkeypatch.setattr(workflows, "purge_checkpoints", original)
    workflows.process_cleanup(engine)
    with Session(engine) as db:
        assert not db.get(GrowthRecord, conversation_deletion.cleanup_id(chat))
