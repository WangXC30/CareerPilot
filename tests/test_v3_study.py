"""Study pacing respects plan invariants; reviews and feedback remain owned."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from careerpilot import notifications
from careerpilot.v1_models import SkillEvidence
from tests.test_v1_release import action, exam, ok, setup_plan, version
from tests.test_v3_workflow import decide


def preview(client, headers, plan, mode="light", key="mode-key"):
    return client.post(
        f"/api/v1/plans/{plan['id']}/mode-preview",
        headers={**version(headers, plan["version"]), "Idempotency-Key": key},
        json={"mode": mode, "confirmed": True},
    )


@pytest.mark.parametrize(
    "mode,budget", [("sprint", 600), ("balanced", 450), ("research_busy", 300), ("light", 150)]
)
def test_modes_are_reviewable_budgeted_and_preserve_work(system, mode, budget):
    client, _, alice, *_ = system
    _, plan = setup_plan(system)
    plan = ok(action(client, alice, plan, plan["tasks"][0]["id"], "lock"))
    p = ok(preview(client, alice, plan, mode), 201)
    assert p["validation_error"] is None
    assert p["mode_change"]["after_budget"] == budget
    assert ok(preview(client, alice, plan, mode), 201)["id"] == p["id"]
    assert ok(client.get("/api/v1/plans", headers=alice))[0]["version"] == plan["version"]
    assert sum(t["minutes"] for t in p["candidate"]["tasks"]) == sum(
        t["minutes"] for t in plan["tasks"]
    )
    assert p["candidate"]["tasks"][0] == plan["tasks"][0]
    assert all(m <= budget for m in p["candidate"]["validation"]["weekly_minutes"].values())
    ok(decide(client, alice, p))
    assert decide(client, alice, p).status_code == 409
    current = ok(client.get("/api/v1/plans", headers=alice))[0]
    assert current["study_mode"] == mode and current["validation"]["budget_minutes"] == budget
    assert ok(client.get("/api/v1/profile", headers=alice))["weekly_minutes"] == 600


def test_small_budget_splits_dependencies_and_replan_keeps_mode(system):
    client, _, alice, *_ = system
    _, plan = setup_plan(system, budget=60, content="岗位：开发\n要求：FastAPI")
    p = ok(preview(client, alice, plan), 201)
    assert p["validation_error"] is None
    assert max(t["minutes"] for t in p["candidate"]["tasks"]) == 15
    ok(decide(client, alice, p))
    p2 = ok(client.post(f"/api/v1/plans/{plan['id']}/replan", headers=alice))
    assert p2["validation_error"] is None and p2["candidate"]["validation"]["budget_minutes"] == 15
    ok(decide(client, alice, p2))


def test_modes_reject_stale_foreign_and_locked_budget_conflicts(system):
    client, _, alice, bob, *_ = system
    _, plan = setup_plan(system, budget=180)
    assert preview(client, bob, plan).status_code == 404
    p = ok(preview(client, alice, plan), 201)
    plan = ok(action(client, alice, plan, plan["tasks"][0]["id"], "lock"))
    assert decide(client, alice, p).status_code == 412
    p = ok(preview(client, alice, plan, key="new-mode"), 201)
    assert p["validation_error"]["code"] == "BUDGET_EXCEEDED"
    assert decide(client, alice, p).status_code == 422
    ok(decide(client, alice, p, "reject"))
    assert ok(client.get("/api/v1/plans", headers=alice))[0]["version"] == plan["version"]


def feedback(client, headers, resource="resource-python", expected=0, **changes):
    return client.put(
        f"/api/v1/learning-resources/{resource}/feedback",
        headers=version(headers, expected),
        json={
            "favorite": True,
            "quality": "helpful",
            "broken": True,
            "note": "无法打开",
            "confirmed": True,
            **changes,
        },
    )


def test_feedback_is_private_cas_and_broken_resource_has_replacement(system):
    client, _, alice, bob, *_ = system
    result = ok(feedback(client, alice))
    assert result["version"] == 1
    public = ok(client.get("/api/v1/catalogue/resource", headers=alice))
    assert all("feedback" not in r for r in public)
    assert next(r for r in public if r["id"] == "resource-python")["status"] == "available"
    assert feedback(client, alice).status_code == 412
    resources = ok(client.get("/api/v1/learning-resources", headers=alice))
    original = next(r for r in resources if r["id"] == "resource-python")
    assert original["status"] == "reported_unavailable"
    fallback = next(r for r in resources if r.get("local_fallback"))
    assert fallback["skill_id"] == "python" and fallback["url"] == "/app/#skills"
    found = ok(client.get("/api/v1/web/search?q=python", headers=alice))["results"]
    assert all(r["id"] != "resource-python" for r in found)
    assert any(r.get("local_fallback") for r in found)
    discovery = ok(client.get("/api/v1/source-discovery?skill_id=python", headers=alice))
    assert any(r["id"] == "local-practice-python" for r in discovery["results"])
    assert all(r["id"] != "resource-python" for r in discovery["results"])
    other = ok(client.get("/api/v1/learning-resources", headers=bob))
    assert next(r for r in other if r["id"] == "resource-python")["status"] == "available"
    exported = ok(client.get("/api/v1/account/export", headers=alice))
    assert any(r["kind"] == "resource_feedback" for r in exported["data"]["growth_records"])
    ok(feedback(client, alice, expected=1, broken=False, favorite=False))
    assert all(
        not r.get("local_fallback")
        for r in ok(client.get("/api/v1/learning-resources", headers=alice))
    )


def test_bad_link_blocks_duplicate_url_not_alternative_and_scans_note(system):
    client, _, alice, *_ = system
    ok(feedback(client, alice, "resource-git"))
    resources = ok(client.get("/api/v1/learning-resources", headers=alice))
    assert (
        next(r for r in resources if r["id"] == "resource-git-book")["status"]
        == "reported_unavailable"
    )
    assert next(r for r in resources if r["id"] == "resource-git-video")["status"] == "available"
    assert feedback(client, alice, "unknown").status_code == 404
    response = client.put(
        "/api/v1/learning-resources/resource-python/feedback",
        headers={**version(alice, 0), "Content-Type": "application/json"},
        content='{"note":"\\ud800","confirmed":true}',
    )
    assert response.status_code == 422


def test_review_queue_wrong_questions_retake_revoke_and_owner_isolation(system):
    client, _, alice, bob, *_ = system
    created, response = exam(client, alice, answers=[0, 0, 0])
    ok(response)
    queue = ok(client.get("/api/v1/study-reviews", headers=alice))
    assert queue["items"][0]["assessment_id"] == created["id"]
    assert len(queue["items"][0]["wrong_questions"]) == 2
    assert queue["items"][0]["wrong_questions"][0]["question"]["prompt"]
    assert ok(client.get("/api/v1/study-reviews", headers=bob))["items"] == []
    _, response = exam(client, alice)
    passed = ok(response)
    item = ok(client.get("/api/v1/study-reviews", headers=alice))["items"][0]
    assert (
        item["conclusion"] == "passed"
        and item["wrong_questions"] == []
        and item["interval_days"] == 1
    )
    _, response = exam(client, alice)
    newest = ok(response)
    assert ok(client.get("/api/v1/study-reviews", headers=alice))["items"][0]["interval_days"] == 1
    for evidence in [newest["evidence"], passed["evidence"]]:
        ok(
            client.post(
                f"/api/v1/skill-evidence/{evidence['id']}/revoke",
                headers=alice,
                json={"confirmed": True, "reason": "重新核对"},
            )
        )
    assert (
        ok(client.get("/api/v1/study-reviews", headers=alice))["items"][0]["conclusion"] == "failed"
    )


def test_review_intervals_days_notifications_dedup_and_opt_out(system):
    client, engine, alice, *_ = system
    now = datetime.now(timezone.utc)
    for offset in [10, 8, 7]:
        _, response = exam(client, alice)
        evidence = ok(response)["evidence"]
        with Session(engine) as db:
            row = db.get(SkillEvidence, evidence["id"])
            row.created_at = (now - timedelta(days=offset)).isoformat()
            db.commit()
    item = ok(client.get("/api/v1/study-reviews", headers=alice))["items"][0]
    assert item["interval_days"] == 7 and item["due"]
    body = {
        "status": "active",
        "spaced_review": True,
        "timezone": "UTC",
        "hour": 12,
        "quiet_start": 0,
        "quiet_end": 0,
        "confirmed": True,
    }
    saved = ok(client.put("/api/v1/notification-settings", headers=version(alice, 0), json=body))
    due = saved["next_runs"]["spaced_review"]
    assert notifications.schedule_due(engine, due) == 1
    assert notifications.schedule_due(engine, due) == 0
    center = ok(client.get("/api/v1/notification-center", headers=alice))
    assert len(center["items"][0]["content"]["items"]) == 1
    saved = ok(client.get("/api/v1/notification-settings", headers=alice))
    ok(
        client.put(
            "/api/v1/notification-settings",
            headers=version(alice, saved["version"]),
            json={**body, "spaced_review": False},
        )
    )
    assert notifications.schedule_due(engine, due + 86400 * 2) == 0
    with Session(engine) as db:
        assert len(list(db.scalars(select(SkillEvidence)))) == 3
