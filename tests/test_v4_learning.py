"""Real wrong-answer tutoring, explicit peer review and non-punitive boundaries."""

import json
from unittest.mock import patch

from sqlalchemy import select
from sqlalchemy.orm import Session

from careerpilot import adaptive, assessment, growth
from careerpilot.cli import provision
from careerpilot.growth_models import GrowthRecord, GrowthRevision
from careerpilot.persistence import AuditEvent
from careerpilot.platform_models import QuotaBucket
from tests.test_v1_release import version
from tests.test_v4_collaboration import join, ok


def completed(client, headers):
    with patch.object(adaptive.time, "time", return_value=1000000000):
        exam = ok(
            client.post(
                "/api/v1/adaptive-exams",
                headers=headers,
                json={"skill_ids": ["python"], "confirmed": True},
            ),
            201,
        )
        while exam["current"]:
            q = exam["current"]
            bank = assessment.BANK[q["skill_id"]] + adaptive.EXTRA.get(q["skill_id"], [])
            exam = ok(
                client.post(
                    f"/api/v1/adaptive-exams/{exam['id']}/answer",
                    headers=version(headers, exam["version"]),
                    json={"choice": (bank[q["index"]][2] + 1) % 3, "confirmed": True},
                )
            )
    return exam


def review_fixture(system):
    client, _, alice, bob, _, ids = system
    exam = completed(client, alice)
    appeal = ok(
        client.post(
            f"/api/v1/adaptive-exams/{exam['id']}/appeal",
            headers=version(alice, exam["version"]),
            json={"text": "我熟悉这些题目，请核对计时背景", "confirmed": True},
        )
    )
    space = ok(
        client.post(
            "/api/v1/workspaces", headers=alice, json={"name": "本次练习复核", "confirmed": True}
        ),
        201,
    )
    join(client, alice, bob, ids, space)
    body = {"workspace_id": space["id"], "reviewer_id": ids[1], "confirmed": True}
    return client, alice, bob, appeal, space, body


def test_adaptive_rule_and_single_answer_do_not_make_high_risk_decisions(system):
    client, _, alice, *_ = system
    evidence = ok(client.get("/api/v1/skill-evidence", headers=alice))
    with patch.object(adaptive.time, "time", return_value=1000000000):
        exam = ok(
            client.post(
                "/api/v1/adaptive-exams",
                headers=alice,
                json={"skill_ids": ["python", "sql"], "confirmed": True},
            ),
            201,
        )
        assert exam["coverage"] == {"questions_per_skill": 3, "skill_count": 2}
        q = exam["current"]
        first = ok(
            client.post(
                f"/api/v1/adaptive-exams/{exam['id']}/answer",
                headers=version(alice, 1),
                json={"choice": assessment.BANK["python"][q["index"]][2], "confirmed": True},
            )
        )
        assert first["current"]["difficulty"] == "application" and first["review_flags"] == []
        assert (
            client.post(
                "/api/v1/teaching-sessions",
                headers=alice,
                json={
                    "exam_id": exam["id"],
                    "answer_index": 0,
                    "confirmed": True,
                },
            ).status_code
            == 422
        )
        second = ok(
            client.post(
                f"/api/v1/adaptive-exams/{exam['id']}/answer",
                headers=version(alice, first["version"]),
                json={"choice": 0, "confirmed": True},
            )
        )
    flag = second["review_flags"][0]
    assert flag["observations"] == 2 and flag["answer_indices"] == [0, 1]
    assert "不影响成绩" in flag["notice"]
    assert ok(client.get("/api/v1/skill-evidence", headers=alice)) == evidence


def test_tutor_uses_owned_wrong_item_gradual_hints_cas_and_deletion(system):
    client, engine, alice, bob, *_ = system
    exam = completed(client, alice)
    body = {"exam_id": exam["id"], "answer_index": 0, "confirmed": True}
    assert client.post("/api/v1/teaching-sessions", headers=bob, json=body).status_code == 404
    session = ok(client.post("/api/v1/teaching-sessions", headers=alice, json=body), 201)
    endpoint = "/api/v1/teaching-sessions/" + session["id"]
    assert session["item"] == exam["answers"][0] and session["level"] == 1
    followup = {
        "attempt": "<img src=x onerror=alert(1)>",
        "question": "条件改变会怎样？",
        "action": "hint",
        "focus": "condition",
        "confirmed": True,
    }
    assert (
        client.post(endpoint + "/turns", headers=version(bob, 1), json=followup).status_code == 404
    )
    for expected_level in (2, 3):
        session = ok(
            client.post(
                endpoint + "/turns", headers=version(alice, session["version"]), json=followup
            )
        )
        assert session["level"] == expected_level
    assert exam["answers"][0]["explanation"] in session["turns"][-1]["hint"]
    assert (
        client.post(endpoint + "/turns", headers=version(alice, 1), json=followup).status_code
        == 412
    )
    assert ok(client.get(endpoint, headers=alice)) == session
    assert client.get(endpoint, headers=bob).status_code == 404
    assert ok(client.get("/api/v1/teaching-sessions", headers=bob)) == []
    assert ok(client.get("/api/v1/skill-evidence", headers=alice)) == []
    assert client.delete(endpoint, headers=bob).status_code == 404
    ok(client.delete(endpoint, headers=alice))
    assert client.get(endpoint, headers=alice).status_code == 404
    with Session(engine) as db:
        assert db.get(GrowthRecord, session["id"]) is None
        assert not list(
            db.scalars(select(GrowthRevision).where(GrowthRevision.record_id == session["id"]))
        )


def test_tutor_session_limit_and_shared_daily_quota(system):
    client, engine, alice, *_ = system
    exam = completed(client, alice)
    session = ok(
        client.post(
            "/api/v1/teaching-sessions",
            headers=alice,
            json={"exam_id": exam["id"], "answer_index": 0, "confirmed": True},
        ),
        201,
    )
    endpoint = f"/api/v1/teaching-sessions/{session['id']}/turns"
    body = {
        "attempt": "我先列条件",
        "focus": "counterexample",
        "action": "reflect",
        "confirmed": True,
    }
    for _ in range(11):
        session = ok(client.post(endpoint, headers=version(alice, session["version"]), json=body))
    assert session["level"] == 1
    assert (
        client.post(endpoint, headers=version(alice, session["version"]), json=body).status_code
        == 429
    )
    with Session(engine) as db, db.begin():
        bucket = next(b for b in db.scalars(select(QuotaBucket)) if b.scope.startswith("teaching:"))
        bucket.used = 59
    ok(
        client.post(
            "/api/v1/teaching-turns",
            headers=alice,
            json={
                "skill_id": "python",
                "question_index": 0,
                "level": 1,
                "confirmed": True,
            },
        ),
        201,
    )
    assert (
        client.post(
            "/api/v1/teaching-sessions",
            headers=alice,
            json={
                "exam_id": exam["id"],
                "answer_index": 0,
                "confirmed": True,
            },
        ).status_code
        == 429
    )


def test_review_requires_explicit_consent_private_source_and_accepted_member(system):
    client, alice, bob, exam, space, body = review_fixture(system)
    endpoint = f"/api/v1/adaptive-exams/{exam['id']}/review-requests"
    assert ok(client.get("/api/v1/practice-reviews", headers=bob)) == []
    pending = ok(
        client.post(
            "/api/v1/adaptive-exams",
            headers=alice,
            json={"skill_ids": ["python"], "confirmed": True},
        ),
        201,
    )
    assert (
        client.post(
            f"/api/v1/adaptive-exams/{pending['id']}/review-requests", headers=alice, json=body
        ).status_code
        == 409
    )
    assert (
        client.post(endpoint, headers=alice, json={**body, "confirmed": False}).status_code == 422
    )
    assert client.post(endpoint, headers=bob, json=body).status_code == 404
    assert (
        client.post(endpoint, headers=alice, json={**body, "reviewer_id": system[5][0]}).status_code
        == 422
    )
    shared = ok(client.post(endpoint, headers=alice, json=body), 201)
    assert client.post(endpoint, headers=alice, json=body).status_code == 409
    snapshot = ok(client.get("/api/v1/practice-reviews/" + shared["id"], headers=bob))
    assert set(snapshot["snapshot"]) == {
        "bank_version",
        "skills",
        "answers",
        "review_flags",
        "appeal",
        "summary",
    }
    assert snapshot["can_review"] and not snapshot["is_owner"]
    assert ok(client.get("/api/v1/adaptive-exams", headers=bob)) == []
    _, outsider_token = provision(system[1], "Unrelated reviewer")
    outsider = {"Authorization": "Bearer " + outsider_token}
    assert (
        client.get("/api/v1/practice-reviews/" + shared["id"], headers=outsider).status_code == 404
    )
    assert ok(client.get("/api/v1/practice-reviews", headers=outsider)) == []


def test_human_decision_records_actual_reviewer_without_penalty_and_revokes(system):
    client, alice, bob, exam, _, body = review_fixture(system)
    shared = ok(
        client.post(
            f"/api/v1/adaptive-exams/{exam['id']}/review-requests", headers=alice, json=body
        ),
        201,
    )
    endpoint = "/api/v1/practice-reviews/" + shared["id"]
    decision = {
        "conclusion": "inconclusive",
        "note": "计时不足以判断真实能力或诚信",
        "confirmed": True,
    }
    assert (
        client.post(endpoint + "/decision", headers=version(alice, 1), json=decision).status_code
        == 404
    )
    assert (
        client.post(endpoint + "/decision", headers=version(bob, 9), json=decision).status_code
        == 412
    )
    assert (
        client.post(
            endpoint + "/decision",
            headers=version(bob, 1),
            json={**decision, "conclusion": "cheating"},
        ).status_code
        == 422
    )
    result = ok(client.post(endpoint + "/decision", headers=version(bob, 1), json=decision))
    assert result["decision"]["penalty"] is None and result["decision"]["grade_changed"] is False
    assert result["decision"]["reviewed_by"] == system[5][1]
    assert (
        client.post(endpoint + "/decision", headers=version(bob, 2), json=decision).status_code
        == 409
    )
    assert ok(client.get("/api/v1/adaptive-exams", headers=alice))[0] == exam
    assert ok(client.get("/api/v1/skill-evidence", headers=alice)) == []
    with Session(system[1]) as db:
        event = db.scalar(
            select(AuditEvent).where(AuditEvent.action == "practice_review.human_decision")
        )
        assert event.user_id == system[5][1]
    assert client.delete(endpoint, headers=version(bob, 2)).status_code == 404
    ok(client.delete(endpoint, headers=version(alice, 2)))
    assert client.get(endpoint, headers=bob).status_code == 404
    assert ok(client.get("/api/v1/practice-reviews", headers=bob)) == []
    retained = ok(client.get(endpoint, headers=alice))
    assert (
        retained["is_owner"]
        and not retained["access_active"]
        and retained["decision"] == result["decision"]
    )


def test_member_removal_and_rejoin_never_resurrect_old_review(system):
    client, alice, bob, exam, space, body = review_fixture(system)
    shared = ok(
        client.post(
            f"/api/v1/adaptive-exams/{exam['id']}/review-requests", headers=alice, json=body
        ),
        201,
    )
    endpoint = "/api/v1/practice-reviews/" + shared["id"]
    ok(client.delete(f"/api/v1/workspaces/{space['id']}/members/{system[5][1]}", headers=alice))
    assert client.get(endpoint, headers=bob).status_code == 404
    join(client, alice, bob, system[5], space)
    assert client.get(endpoint, headers=bob).status_code == 404
    fresh = ok(
        client.post(
            f"/api/v1/adaptive-exams/{exam['id']}/review-requests", headers=alice, json=body
        ),
        201,
    )
    assert client.get("/api/v1/practice-reviews/" + fresh["id"], headers=bob).status_code == 200


def test_expiry_or_appeal_change_invalidates_reviewer_access(system):
    client, alice, bob, exam, _, body = review_fixture(system)
    path = f"/api/v1/adaptive-exams/{exam['id']}"
    shared = ok(client.post(path + "/review-requests", headers=alice, json=body), 201)
    endpoint = "/api/v1/practice-reviews/" + shared["id"]
    with Session(system[1]) as db, db.begin():
        row = db.get(GrowthRecord, shared["id"])
        row.payload_json = growth.dump({**json.loads(row.payload_json), "expires_at": 1})
    assert client.get(endpoint, headers=bob).status_code == 404
    fresh = ok(client.post(path + "/review-requests", headers=alice, json=body), 201)
    ok(
        client.post(
            path + "/appeal",
            headers=version(alice, exam["version"]),
            json={"text": "补充不同背景，请重新授权新快照", "confirmed": True},
        )
    )
    assert client.get("/api/v1/practice-reviews/" + fresh["id"], headers=bob).status_code == 404


def test_same_career_paths_explain_additional_editorial_cost_and_sources(system):
    client, _, alice, *_ = system
    before = ok(client.get("/api/v1/career-goals", headers=alice))
    result = ok(
        client.post(
            "/api/v1/career-scenarios",
            headers=alice,
            json={"career_ids": ["career-backend"], "weekly_minutes": 300},
        )
    )[0]
    a, b = result["paths"]
    assert b["minutes"] - a["minutes"] == 45 * len(result["steps"])
    assert a["source_versions"] == b["source_versions"]
    assert all(p["monetary_cost"] is None for p in result["paths"])
    assert all("先修" in p["prerequisites"] for p in result["paths"])
    assert ok(client.get("/api/v1/career-goals", headers=alice)) == before
