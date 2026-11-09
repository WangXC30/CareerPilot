"""Business editing preserves state machines, evidence and related records."""

import copy
from datetime import date, timedelta
from uuid import uuid4

import pytest

from tests.test_information import deletion
from tests.test_v1_release import action, ok, setup_plan, version
from tests.test_v2_hiring import fact, resume
from tests.test_v2_interviews import session


def plan_body(plan):
    fields = {
        "id",
        "title",
        "skill_id",
        "minutes",
        "date",
        "requires",
        "resources",
        "definition_of_done",
    }
    return {
        "title": "统一维护的学习计划",
        "start_date": plan["start_date"],
        "deadline": plan.get("deadline"),
        "tasks": [
            {key: value for key, value in task.items() if key in fields} for task in plan["tasks"]
        ],
    }


def preview(client, headers, plan, body):
    return client.post(
        f"/api/v1/plans/{plan['id']}/edit-preview",
        headers=version(headers, plan["version"]),
        json=body,
    )


def test_plan_edit_add_remove_tasks_preview_then_confirm_and_delete(system):
    client, _, alice, bob, *_ = system
    _, plan = setup_plan(system)
    body = plan_body(plan)
    removed = body["tasks"].pop()
    body["tasks"][0]["title"] = "调整后的第一项任务"
    added = {
        "id": str(uuid4()),
        "title": "新增 Python 练习",
        "skill_id": "python",
        "minutes": 30,
        "date": (date.fromisoformat(plan["tasks"][-1]["date"]) + timedelta(days=7)).isoformat(),
        "requires": [],
        "resources": ["resource-python"],
        "definition_of_done": "记录练习过程",
    }
    body["tasks"].append(added)
    assert preview(client, bob, plan, body).status_code == 404
    staged = ok(preview(client, alice, plan, body), 201)
    assert (
        client.get(f"/api/v1/plans/{plan['id']}", headers=alice).json()["version"]
        == plan["version"]
    )
    ok(
        client.post(
            f"/api/v1/plan-previews/{staged['id']}/accept",
            headers=version(alice, staged["version"]),
            json={"confirmed": True},
        )
    )
    saved = client.get(f"/api/v1/plans/{plan['id']}", headers=alice).json()
    assert saved["title"] == body["title"] and saved["status"] == "active"
    assert added["id"] in {task["id"] for task in saved["tasks"]}
    assert removed["id"] not in {task["id"] for task in saved["tasks"]}
    assert next(task for task in saved["tasks"] if task["id"] == added["id"])["status"] == "pending"
    assert preview(client, alice, plan, body).status_code == 412
    assert deletion(client, alice, "plan", saved).status_code == 204


@pytest.mark.parametrize("mutation", ["budget", "cycle", "skill", "state", "missing_dependency"])
def test_plan_editor_cannot_bypass_domain_rules(system, mutation):
    client, _, alice, *_ = system
    _, plan = setup_plan(system)
    body = plan_body(plan)
    if mutation == "budget":
        body["tasks"][0]["minutes"] = 10080
    elif mutation == "cycle":
        body["tasks"][0]["requires"] = [body["tasks"][0]["id"]]
    elif mutation == "skill":
        body["tasks"][0]["skill_id"] = "unregistered-skill"
    elif mutation == "state":
        body["tasks"][0]["status"] = "completed"
    else:
        body["tasks"][0]["requires"] = [str(uuid4())]
    assert preview(client, alice, plan, body).status_code == 422
    assert (
        client.get(f"/api/v1/plans/{plan['id']}", headers=alice).json()["version"]
        == plan["version"]
    )


@pytest.mark.parametrize("state", ["running", "locked", "completed"])
def test_plan_editor_preserves_protected_tasks(system, state):
    client, _, alice, *_ = system
    _, plan = setup_plan(system)
    task_id = plan["tasks"][0]["id"]
    plan = ok(action(client, alice, plan, task_id, "lock" if state == "locked" else "start"))
    if state == "completed":
        plan = ok(action(client, alice, plan, task_id, "complete", outcome="本人记录的实际成果"))
    body = plan_body(plan)
    body["tasks"][0]["title"] = "试图改写受保护任务"
    assert preview(client, alice, plan, body).status_code == 422
    body["tasks"] = body["tasks"][1:]
    assert preview(client, alice, plan, body).status_code == 422


def test_plan_preview_reject_and_stale_accept_do_not_overwrite(system):
    client, _, alice, *_ = system
    _, plan = setup_plan(system)
    staged = ok(preview(client, alice, plan, plan_body(plan)), 201)
    ok(
        client.post(
            f"/api/v1/plan-previews/{staged['id']}/reject",
            headers=version(alice, 1),
            json={"confirmed": True},
        )
    )
    assert (
        client.get(f"/api/v1/plans/{plan['id']}", headers=alice).json()["version"]
        == plan["version"]
    )
    staged = ok(preview(client, alice, plan, plan_body(plan)), 201)
    ok(action(client, alice, plan, plan["tasks"][0]["id"], "lock"))
    assert (
        client.post(
            f"/api/v1/plan-previews/{staged['id']}/accept",
            headers=version(alice, 1),
            json={"confirmed": True},
        ).status_code
        == 412
    )


def test_plan_deletion_keeps_dependency_guard_and_does_not_detach_logs(system):
    client, _, alice, *_ = system
    _, plan = setup_plan(system)
    log = ok(
        client.post(
            "/api/v1/study-logs",
            headers={**alice, "Idempotency-Key": "linked-log"},
            json={
                "day": "2026-10-20",
                "minutes": 30,
                "plan_id": plan["id"],
                "task_id": plan["tasks"][0]["id"],
                "confirmed": True,
            },
        ),
        201,
    )
    assert deletion(client, alice, "plan", plan).status_code == 409
    kept = client.get("/api/v1/study-logs", headers=alice).json()
    assert kept[0]["id"] == log["id"] and kept[0]["plan_id"] == plan["id"]


def test_goal_archive_and_restore_preserve_history_and_owner(system):
    client, _, alice, bob, *_ = system
    goal = ok(
        client.post(
            "/api/v1/career-goals",
            headers={**alice, "Idempotency-Key": "new-goal"},
            json={"title": "目标维护"},
        ),
        201,
    )
    path = f"/api/v1/business-records/career_goal/{goal['id']}/archive"
    assert (
        client.post(
            path, headers=version(bob, 1), json={"confirmed": True, "archived": True}
        ).status_code
        == 404
    )
    assert client.post(path, headers=version(alice, 1), json={"archived": True}).status_code == 422
    archived = ok(
        client.post(path, headers=version(alice, 1), json={"confirmed": True, "archived": True})
    )
    assert archived["status"] == "archived"
    restored = ok(
        client.post(path, headers=version(alice, 2), json={"confirmed": True, "archived": False})
    )
    assert restored["status"] == "draft"
    assert len(client.get(f"/api/v1/career-goals/{goal['id']}/history", headers=alice).json()) == 3


def test_interview_management_never_exposes_answers_and_archive_is_reversible(system):
    client, _, alice, *_ = system
    record = session(client, alice)
    path = f"/api/v1/business-records/interview_session/{record['id']}"
    edited = ok(
        client.put(
            path + "/details",
            headers=version(alice, 1),
            json={"title": "模拟面试", "note": "下次复习沟通题", "confirmed": True},
        )
    )
    assert edited["status"] == "running" and edited["answered_count"] == 0
    archived = ok(
        client.post(
            path + "/archive", headers=version(alice, 2), json={"archived": True, "confirmed": True}
        )
    )
    assert archived["user_archived"] and archived["status"] == "running"
    values = client.get("/api/v1/information?kind=interview_session", headers=alice).json()
    assert "expected_choice" not in str(values) and "explanation" not in str(values)
    history = client.get(
        f"/api/v1/information/interview_session/{record['id']}/history", headers=alice
    ).json()
    assert "expected_choice" not in str(history)
    restored = ok(
        client.post(
            path + "/archive",
            headers=version(alice, 3),
            json={"archived": False, "confirmed": True},
        )
    )
    assert not restored["user_archived"] and restored["answered_count"] == 0


def test_goal_with_plan_cannot_be_archived_implicitly(system):
    client, _, alice, *_ = system
    goal = ok(
        client.post(
            "/api/v1/career-goals",
            headers={**alice, "Idempotency-Key": "linked-goal"},
            json={"title": "有计划的目标"},
        ),
        201,
    )
    staged = ok(client.post(f"/api/v1/career-goals/{goal['id']}/preview", headers=alice))
    ok(
        client.post(
            f"/api/v1/goal-previews/{staged['id']}/accept",
            headers=version(alice, 1),
            json={"confirmed": True},
        )
    )
    _, plan = setup_plan(system)
    assert (
        client.post(
            f"/api/v1/business-records/career_goal/{goal['id']}/archive",
            headers=version(alice, 2),
            json={"archived": True, "confirmed": True},
        ).status_code
        == 409
    )
    assert client.get(f"/api/v1/plans/{plan['id']}", headers=alice).json()["goal_id"] == goal["id"]


def test_translation_edit_preserves_fact_and_number_checks(system):
    client, _, alice, bob, *_ = system
    selected = fact(client, alice, "完成 2 项练习")
    current = resume(client, alice, facts=[selected])
    body = {
        "resume_version_id": current["id"],
        "locale": "en-US",
        "confirmed": True,
        "mappings": [{"fact_id": selected["id"], "text": "Completed 2 exercises."}],
    }
    translated = ok(client.post("/api/v1/resume-translations", headers=alice, json=body), 201)
    path = f"/api/v1/resume-translations/{translated['id']}"
    assert client.put(path, headers=version(bob, 1), json=body).status_code == 404
    body["mappings"][0]["text"] = "Finished 2 exercises."
    assert ok(client.put(path, headers=version(alice, 1), json=body))["version"] == 2
    body["mappings"][0]["text"] = "Finished 3 exercises."
    assert client.put(path, headers=version(alice, 2), json=body).status_code == 422


def test_offer_edit_recalculates_and_preserves_original_history(system):
    client, _, alice, *_ = system
    offer = {"name": "方案", "ratings": {"growth": 3}, "confirmed": True}
    body = {
        "offers": [offer, {**offer, "name": "方案二"}],
        "weights": {"growth": 100},
        "confirmed": True,
    }
    record = ok(client.post("/api/v1/offer-comparisons", headers=alice, json=body), 201)
    changed = copy.deepcopy(body)
    changed["offers"][0]["ratings"]["growth"] = 5
    saved = ok(
        client.put(
            f"/api/v1/offer-comparisons/{record['id']}", headers=version(alice, 1), json=changed
        )
    )
    assert saved["offers"][0]["score_lower"] == 100 and saved["decision"] is None
    changed["offers"][0]["ratings"]["growth"] = 9
    assert (
        client.put(
            f"/api/v1/offer-comparisons/{record['id']}", headers=version(alice, 2), json=changed
        ).status_code
        == 422
    )
