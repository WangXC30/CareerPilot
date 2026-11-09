"""New-stack contracts: graph recovery, event replay, drafts and source reuse."""

import hashlib
import json

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from careerpilot import workflows
from careerpilot.growth_models import GrowthRecord
from careerpilot.runtime import run_once

PROFILE = {
    "education": "bachelor",
    "major": "计算机",
    "academic_year": "大三",
    "graduation_date": "2027-06-30",
    "preferred_cities": ["上海"],
    "weekly_minutes": 600,
    "timezone": "Asia/Shanghai",
    "confirmed": True,
}


def test_workspace_guidance_is_resumable_and_versioned(system):
    client, _, alice, bob, *_ = system
    url = "/api/v1/workflows/guidance"
    initial = client.get(url, headers=alice).json()
    assert initial["status"] == "not_started" and initial["version"] == 0
    saved = client.put(url, headers={**alice, "If-Match": '"0"'}, json={"action": "learn"})
    assert saved.status_code == 200
    assert saved.json()["purpose"] == "learn" and saved.json()["status"] == "in_progress"
    assert "weekly_budget" in saved.json()["missing"]
    assert client.get(url, headers=alice).json() == saved.json()
    stale = client.put(url, headers={**alice, "If-Match": '"0"'}, json={"action": "career"})
    assert stale.status_code == 412
    assert client.get(url, headers=bob).json()["status"] == "not_started"
    version = saved.json()["version"]
    deferred = client.put(
        url, headers={**alice, "If-Match": f'"{version}"'}, json={"action": "defer"}
    ).json()
    assert deferred["status"] == "deferred"
    resumed = client.put(
        url, headers={**alice, "If-Match": f'"{deferred["version"]}"'}, json={"action": "resume"}
    ).json()
    assert resumed["status"] == "in_progress" and resumed["purpose"] == "learn"
    profile = client.put("/api/v1/profile", headers={**alice, "If-Match": '"0"'}, json=PROFILE)
    assert profile.status_code == 200
    assert client.get(url, headers=alice).json()["status"] == "satisfied"
    assert "weekly_budget" not in client.get(url, headers=alice).json()["missing"]


def test_guidance_detects_private_profile_draft(system):
    client, _, alice, *_ = system
    draft = client.put(
        "/api/v1/drafts/profile/personal",
        headers={**alice, "If-Match": '"0"'},
        json={"values": {"major": "计算机"}, "base_record_version": 0},
    )
    assert draft.status_code == 200
    state = client.get("/api/v1/workflows/guidance", headers=alice).json()
    assert state["profile_draft"] is True and state["status"] == "draft_pending"
    assert (
        client.put(
            "/api/v1/profile", headers={**alice, "If-Match": '"0"'}, json=PROFILE
        ).status_code
        == 200
    )
    assert (
        client.get("/api/v1/workflows/guidance", headers=alice).json()["status"] == "draft_pending"
    )


def test_deferred_guidance_recomputes_after_profile_is_saved(system):
    client, _, alice, *_ = system
    url = "/api/v1/workflows/guidance"
    assert (
        client.put(url, headers={**alice, "If-Match": '"0"'}, json={"action": "defer"}).json()[
            "status"
        ]
        == "deferred"
    )
    assert (
        client.put(
            "/api/v1/profile", headers={**alice, "If-Match": '"0"'}, json=PROFILE
        ).status_code
        == 200
    )
    assert client.get(url, headers=alice).json()["status"] == "satisfied"


def test_guidance_survives_app_restart_without_a_model_request(system):
    from fastapi.testclient import TestClient

    from careerpilot.api import create_app

    client, engine, alice, *_ = system
    url = "/api/v1/workflows/guidance"
    first = client.put(url, headers={**alice, "If-Match": '"0"'}, json={"action": "career"})
    assert first.status_code == 200
    with TestClient(create_app(str(engine.url), local_only=False)) as restarted:
        recovered = restarted.get(url, headers=alice)
        assert recovered.status_code == 200
        assert recovered.json()["purpose"] == "career"
        assert recovered.json()["version"] == first.json()["version"]
        assert restarted.get("/api/v1/workflows/conversations", headers=alice).json() == []


def test_action_requirements_are_read_only_and_do_not_need_model(system):
    client, _, alice, bob, *_ = system
    response = client.get("/api/v1/workflows/requirements?action=plan", headers=alice)
    assert response.status_code == 200
    fields = {item["field"]: item for item in response.json()["items"]}
    assert fields["weekly_budget"]["state"] == "unknown"
    assert (
        client.put(
            "/api/v1/profile", headers={**alice, "If-Match": '"0"'}, json=PROFILE
        ).status_code
        == 200
    )
    alice_state = client.get("/api/v1/workflows/requirements?action=plan", headers=alice).json()
    bob_state = client.get("/api/v1/workflows/requirements?action=plan", headers=bob).json()
    assert (
        next(i for i in alice_state["items"] if i["field"] == "weekly_budget")["state"]
        == "available"
    )
    assert (
        next(i for i in bob_state["items"] if i["field"] == "weekly_budget")["state"] == "unknown"
    )


def test_direct_plan_uses_explicit_budget_without_fabricated_profile(system):
    client, _, alice, *_ = system
    job = client.post(
        "/api/v1/jobs/import", headers=alice, json={"content": "岗位：后端\n要求：Python"}
    ).json()
    body = {"job_ids": [job["id"]], "start_date": "2026-10-28"}
    assert client.post("/api/v1/plans", headers=alice, json=body).status_code == 422
    response = client.post("/api/v1/plans", headers=alice, json={**body, "weekly_minutes": 180})
    assert response.status_code == 201, response.text
    assert response.json()["budget_cap_minutes"] == 180
    assert client.get("/api/v1/profile", headers=alice).json()["profile"] is None
    assert (
        client.put(
            "/api/v1/profile", headers={**alice, "If-Match": '"0"'}, json=PROFILE
        ).status_code
        == 200
    )
    plan = response.json()
    publish = client.post(
        f"/api/v1/plans/{plan['id']}/publish",
        headers={**alice, "If-Match": f'"{plan["version"]}"'},
        json={"confirmed": True},
    )
    assert publish.status_code == 412


def test_explicit_plan_budget_does_not_override_confirmed_profile_cap(system):
    client, _, alice, *_ = system
    job = setup(client, alice)
    body = {"job_ids": [job], "start_date": "2026-10-28"}
    too_large = client.post("/api/v1/plans", headers=alice, json={**body, "weekly_minutes": 900})
    assert too_large.status_code == 422
    limited = client.post("/api/v1/plans", headers=alice, json={**body, "weekly_minutes": 180})
    assert limited.status_code == 201, limited.text
    assert limited.json()["mode_budget_minutes"] == 180
    assert all(
        minutes <= 180 for minutes in limited.json()["validation"]["weekly_minutes"].values()
    )


def setup(client, headers):
    response = client.put("/api/v1/profile", headers={**headers, "If-Match": '"0"'}, json=PROFILE)
    assert response.status_code == 200, response.text
    job = client.post(
        "/api/v1/jobs/import",
        headers=headers,
        json={"content": "岗位：后端实习生\n公司：合成公司\n地点：上海\n要求：Python 和 SQL"},
    )
    assert job.status_code == 201, job.text
    return job.json()["id"]


def enqueue(client, headers, **body):
    payload = {"message": "查看学习进展", "intent": "progress", **body}
    response = client.post(
        "/api/v1/workflows/runs",
        headers={
            **headers,
            "Idempotency-Key": "graph-" + hashlib.sha256(payload["message"].encode()).hexdigest(),
        },
        json=payload,
    )
    assert response.status_code == 202, response.text
    return response.json()


def test_guidance_answer_links_owned_run_and_deduplicates(system):
    client, _, alice, bob, *_ = system
    url = "/api/v1/workflows/guidance"
    started = client.put(url, headers={**alice, "If-Match": '"0"'}, json={"action": "learn"}).json()
    run = enqueue(client, alice, message="我每周有三小时")
    payload = {"action": "answered", "run_id": run["id"]}
    saved = client.put(url, headers={**alice, "If-Match": f'"{started["version"]}"'}, json=payload)
    assert saved.status_code == 200
    assert saved.json()["response_received"] is True
    assert saved.json()["asked"] == ["purpose", "learn"]
    assert saved.json()["answered"] == ["purpose", "learn"]
    assert run["id"] not in json.dumps(saved.json())
    again = client.put(url, headers={**alice, "If-Match": f'"{started["version"]}"'}, json=payload)
    assert again.status_code == 200 and again.json()["version"] == saved.json()["version"]
    other = client.put(url, headers={**bob, "If-Match": '"0"'}, json={"action": "learn"}).json()
    denied = client.put(url, headers={**bob, "If-Match": f'"{other["version"]}"'}, json=payload)
    assert denied.status_code == 404


def test_graph_persists_replays_events_and_enforces_ownership(system):
    client, engine, alice, bob, *_ = system
    run = enqueue(client, alice)
    run_once(engine)
    result = client.get(f"/api/v1/workflows/runs/{run['id']}", headers=alice).json()
    assert result["status"] == "completed", result
    assert result["graph_version"] == workflows.GRAPH_VERSION
    with workflows.saver(engine) as store:
        saved = store.get_tuple({"configurable": {"thread_id": run["id"]}})
        assert saved.checkpoint["channel_values"]["status"] == "completed"
    assert client.get(f"/api/v1/workflows/runs/{run['id']}", headers=bob).status_code == 404
    stream = client.get(f"/api/v1/workflows/runs/{run['id']}/events?follow=false", headers=alice)
    assert stream.status_code == 200
    assert '"status": "completed"' in stream.text and "event: snapshot" in stream.text
    replay = client.get(
        f"/api/v1/workflows/runs/{run['id']}/events?follow=false&after={result['sequence']}",
        headers=alice,
    )
    assert "event: update" not in replay.text and "event: snapshot" in replay.text
    again = enqueue(client, alice)
    assert again["id"] == run["id"]
    changed = client.post(
        "/api/v1/workflows/runs",
        headers={
            **alice,
            "Idempotency-Key": "graph-" + hashlib.sha256("查看学习进展".encode()).hexdigest(),
        },
        json={"message": "different"},
    )
    assert changed.status_code == 409


def test_auto_assistant_answers_greetings_and_explains_unsupported_questions(system):
    client, engine, alice, *_ = system
    for message in ("你好", "你能做什么", "解释一下量子力学"):
        run = enqueue(client, alice, message=message, intent="auto")
        assert run_once(engine)
        response = client.get(f"/api/v1/workflows/runs/{run['id']}", headers=alice).json()
        assert response["status"] == "completed"
        assert response["result"]["kind"] == "assistant_help"
        assert "任务型助手" in response["summary"]
        assert "Python 学习资源" in response["summary"]
        assert response["steps"][0]["tool"] == "growth.help"
        english = client.get(
            f"/api/v1/workflows/runs/{run['id']}",
            headers={
                **alice,
                "Accept-Language": "en-US",
            },
        ).json()
        assert "Open-ended model chat is not connected" in english["summary"]


def test_auto_replan_uses_the_owned_published_plan(system):
    client, engine, alice, *_ = system
    job_id = setup(client, alice)
    draft = client.post(
        "/api/v1/plans",
        headers=alice,
        json={
            "job_ids": [job_id],
            "start_date": "2026-10-27",
        },
    ).json()
    response = client.post(
        f"/api/v1/plans/{draft['id']}/publish",
        headers={
            **alice,
            "If-Match": '"1"',
        },
        json={"confirmed": True},
    )
    assert response.status_code == 200
    run = enqueue(client, alice, message="调整计划", intent="auto", require_confirmation=False)
    assert run_once(engine)
    result = client.get(f"/api/v1/workflows/runs/{run['id']}", headers=alice).json()
    assert result["status"] == "completed", result
    assert result["steps"][0]["arguments"]["plan_id"] == draft["id"]


def test_confirmation_survives_reopen_and_creates_one_plan(system):
    client, engine, alice, *_ = system
    job_id = setup(client, alice)
    run = enqueue(client, alice, message="生成计划", intent="plan", job_ids=[job_id])
    run_once(engine)
    waiting = client.get(f"/api/v1/workflows/runs/{run['id']}", headers=alice).json()
    assert waiting["status"] == "waiting_confirmation", waiting
    with Session(engine) as db:
        assert (
            db.scalar(
                select(func.count()).select_from(GrowthRecord).where(GrowthRecord.kind == "plan")
            )
            == 0
        )
    command = {**alice, "If-Match": f'"{waiting["task_version"]}"'}
    resumed = client.post(
        f"/api/v1/workflows/runs/{run['id']}/resume", headers=command, json={"approved": True}
    )
    assert resumed.status_code == 202, resumed.text
    assert (
        client.post(
            f"/api/v1/workflows/runs/{run['id']}/resume", headers=command, json={"approved": True}
        ).status_code
        == 409
    )
    run_once(engine)
    completed = client.get(f"/api/v1/workflows/runs/{run['id']}", headers=alice).json()
    assert completed["status"] == "completed", completed
    assert len(client.get("/api/v1/plans", headers=alice).json()) == 1


def test_receipt_reconciles_business_commit_before_checkpoint(system):
    client, engine, alice, *_ = system
    from careerpilot.runtime import claim

    job_id = setup(client, alice)
    enqueue(
        client,
        alice,
        message="恢复计划",
        intent="plan",
        job_ids=[job_id],
        require_confirmation=False,
    )
    task_id, token = claim(engine, 100)
    with workflows.saver(engine) as store:
        graph = workflows.build_graph(engine, task_id, token, store)
        config = {"configurable": {"thread_id": task_id}}
        # A domain receipt is durable even if the checkpoint of that node is lost.
        result = graph.invoke({"run_id": task_id}, config)
        assert result["status"] == "completed"
        store.delete_thread(task_id)
        graph.invoke({"run_id": task_id}, config)
    with Session(engine) as db:
        assert (
            db.scalar(
                select(func.count()).select_from(GrowthRecord).where(GrowthRecord.kind == "plan")
            )
            == 1
        )


def test_cancel_stops_later_effects_and_stale_resume(system):
    client, engine, alice, *_ = system
    job_id = setup(client, alice)
    run = enqueue(client, alice, message="取消计划", intent="plan", job_ids=[job_id])
    run_once(engine)
    waiting = client.get(f"/api/v1/workflows/runs/{run['id']}", headers=alice).json()
    cancelled = client.post(
        f"/api/v1/workflows/runs/{run['id']}/cancel",
        headers={
            **alice,
            "If-Match": f'"{waiting["task_version"]}"',
        },
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"
    assert not run_once(engine)
    assert not client.get("/api/v1/plans", headers=alice).json()


def test_private_draft_conflict_and_export(system):
    client, _, alice, bob, *_ = system
    url = "/api/v1/drafts/profile/personal"
    values = {"values": {"major": "未完成的专业"}, "base_record_version": 0}
    saved = client.put(url, headers={**alice, "If-Match": '"0"'}, json=values)
    assert saved.status_code == 200, saved.text
    assert client.get(url, headers=bob).json()["values"] == {}
    assert client.put(url, headers={**alice, "If-Match": '"0"'}, json=values).status_code == 412
    assert client.get("/api/v1/profile", headers=alice).json()["version"] == 0
    exported = client.get("/api/v1/account/export", headers=alice).json()
    assert "未完成的专业" in json.dumps(exported, ensure_ascii=False)


def test_structured_experience_reuses_memory_and_revocation(system):
    client, _, alice, *_ = system
    body = {
        "kind": "internship",
        "title": "课程实践",
        "action": "实现数据导入",
        "confirmed": True,
        "start_date": {"value": "2026-03", "precision": "month"},
    }
    headers = {**alice, "If-Match": '"0"', "Idempotency-Key": "experience-create"}
    response = client.put("/api/v1/experiences/new", headers=headers, json=body)
    assert response.status_code == 200, response.text
    experience = response.json()
    assert (
        client.put("/api/v1/experiences/new", headers=headers, json=body).json()["id"]
        == experience["id"]
    )
    assert experience["experience"]["start_date"]["value"] == "2026-03"
    assert experience["experience"]["result"] == ""
    fact = client.post(
        f"/api/v1/experiences/{experience['id']}/fact",
        headers={
            **alice,
            "If-Match": '"1"',
            "Idempotency-Key": "use-experience",
        },
        json={"confirmed": True},
    )
    assert fact.status_code == 201, fact.text
    assert fact.json()["source"]["type"] == "memory"
    response = client.delete(
        f"/api/v1/memories/{experience['id']}", headers={**alice, "If-Match": '"1"'}
    )
    assert response.status_code == 204, response.text
    facts = client.get("/api/v1/resume-facts", headers=alice).json()
    assert facts[0]["source_status"] == "source_deleted"


def test_date_precision_rejects_invented_or_invalid_dates(system):
    client, _, alice, *_ = system
    response = client.put(
        "/api/v1/experiences/new",
        headers={**alice, "If-Match": '"0"', "Idempotency-Key": "bad-date"},
        json={
            "kind": "education",
            "title": "合成学校",
            "action": "学习课程",
            "confirmed": True,
            "start_date": {"value": "2026-03-01", "precision": "month"},
        },
    )
    assert response.status_code == 422


def test_storage_backup_restores_both_databases_and_files_without_overwrite(system, tmp_path):
    from pathlib import Path

    from careerpilot.storage_backup import backup, restore, verify

    client, engine, alice, *_ = system
    enqueue(client, alice)
    run_once(engine)
    source = Path(engine.url.database).parent
    attachment = source / "attachments" / "example.txt"
    attachment.parent.mkdir()
    attachment.write_text("synthetic attachment", encoding="utf-8")
    target = source / "backups"
    target.mkdir()
    backup(source, target / "checkpoint")
    manifest = verify(target / "checkpoint")
    assert {"test.db", "workflows.sqlite", "attachments/example.txt"} <= set(manifest["files"])
    restored = source.parent / "restored"
    restore(target / "checkpoint", restored)
    assert (restored / "attachments/example.txt").read_text() == "synthetic attachment"
    import pytest

    with pytest.raises(FileExistsError):
        restore(target / "checkpoint", restored)


def test_export_and_deletion_cover_owned_checkpoints(system):
    from careerpilot import accounts
    from careerpilot.domain import Principal
    from careerpilot.persistence import User

    client, engine, alice, *_ = system
    run = enqueue(client, alice)
    run_once(engine)
    exported = client.get("/api/v1/account/export", headers=alice).json()
    assert exported["data"]["workflow_checkpoints"]
    owner_id = run["id"]
    with Session(engine) as db, db.begin():
        record = db.get(GrowthRecord, run["id"])
        user = db.get(User, record.user_id)
        accounts.request_deletion(db, Principal(user.id, user.tenant_id), "test")
    assert accounts.process_deletion(engine)
    with workflows.saver(engine) as store:
        assert store.get_tuple({"configurable": {"thread_id": owner_id}}) is None
