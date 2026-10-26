"""V4 local scenarios, versioned views and adaptive practice contracts."""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from careerpilot import growth, insights, matching, notifications
from careerpilot import subscriptions as source_subscriptions
from careerpilot.domain import Principal
from careerpilot.persistence import User
from tests.test_v1_release import exam
from tests.test_v2_hiring import fact, resume
from tests.test_v2_operations import publish, review, roles
from tests.test_v2_opportunities import job
from tests.test_v4_collaboration import ok


def test_direction_versions_restore_without_fact_mutation(system):
    client, _, alice, bob, _, _ = system
    source = fact(client, alice, "2024 年完成 2 个项目")
    body = {"title": "后端方向", "fact_ids": [source["id"]], "project_ids": [], "confirmed": True}
    view = ok(client.post("/api/v1/direction-views", headers=alice, json=body), 201)
    endpoint = "/api/v1/direction-views/" + view["id"]
    assert client.get(endpoint, headers=bob).status_code == 404
    updated = ok(
        client.put(
            endpoint, headers={**alice, "If-Match": '"1"'}, json={**body, "title": "数据方向"}
        )
    )
    assert updated["version"] == 2
    restored = ok(
        client.post(
            endpoint + "/restore",
            headers={**alice, "If-Match": '"2"'},
            json={"target_version": 1, "confirmed": True},
        )
    )
    assert restored["title"] == body["title"] and restored["version"] == 3
    assert ok(client.get(endpoint, headers=alice))["facts"][0]["text"] == source["text"]
    assert client.put(endpoint, headers={**alice, "If-Match": '"1"'}, json=body).status_code == 412


def test_matching_policy_review_canary_and_rollback(system):
    client, engine, alice, bob, _, ids = system
    roles(system)
    weights = {**matching.WEIGHTS, "required_skills": 40, "project_evidence": 20}
    assert sum(weights.values()) == 100

    def propose(base, rollout):
        return ok(
            client.post(
                "/api/v1/admin/catalogue-proposals",
                headers=alice,
                json={
                    "entry_id": "matching-policy",
                    "entry_kind": "matching_policy",
                    "base_version": base,
                    "payload": {
                        "weights": weights,
                        "rollout_percent": rollout,
                        "basis": "固定合成排序回归",
                    },
                    "note": "本地安全灰度",
                    "confirmed": True,
                    "public_only_confirmed": True,
                },
            ),
            201,
        )

    with Session(engine) as db:
        user = db.get(User, ids[0])
        principal = Principal(user.id, user.tenant_id)
        assert matching.active_policy(db, principal)["version"] == matching.WEIGHT_VERSION
    first = ok(publish(client, bob, ok(review(client, bob, propose(0, 0)))))
    assert first["status"] == "published"
    with Session(engine) as db:
        assert matching.active_policy(db, principal)["version"] == matching.WEIGHT_VERSION
    ok(publish(client, bob, ok(review(client, bob, propose(1, 100)))))
    with Session(engine) as db:
        assert matching.active_policy(db, principal) == {
            "weights": weights,
            "version": "matching-policy:2",
        }
    rollback = ok(
        client.post(
            "/api/v1/admin/catalogue/matching-policy/rollback-proposal",
            headers={**alice, "If-Match": '"2"'},
            json={"target_version": 1, "note": "撤回本地灰度", "confirmed": True},
        ),
        201,
    )
    ok(publish(client, bob, ok(review(client, bob, rollback))))
    with Session(engine) as db:
        assert matching.active_policy(db, principal)["version"] == matching.WEIGHT_VERSION


def test_translation_mapping_rejects_changed_numbers(system):
    client, _, alice, bob, _, _ = system
    source = fact(client, alice, "2024 年完成 2 个项目")
    version = resume(client, alice, [source])
    body = {
        "resume_version_id": version["id"],
        "locale": "en-US",
        "mappings": [{"fact_id": source["id"], "text": "Completed 3 projects in 2024"}],
        "confirmed": True,
    }
    assert client.post("/api/v1/resume-translations", headers=alice, json=body).status_code == 422
    body["mappings"][0]["text"] = "Completed 2 projects in 2024"
    ok(client.post("/api/v1/resume-translations", headers=alice, json=body), 201)
    assert client.post("/api/v1/resume-translations", headers=bob, json=body).status_code == 404
    assert ok(client.get("/api/v1/resume-translations", headers=alice))[0]["sources_current"]


def test_scenarios_preserve_dependencies_and_report_assumptions(system):
    client, _, alice, _, _, _ = system
    results = ok(
        client.post(
            "/api/v1/career-scenarios",
            headers=alice,
            json={"career_ids": ["career-backend", "career-ai"], "weekly_minutes": 60},
        )
    )
    for route in results:
        seen = set()
        for step in route["steps"]:
            assert set(step["requires"]) <= seen
            seen.add(step["skill_id"])
        assert route["assumptions"] and route["risks"]
        assert route["estimated_weeks"] > 0
        assert route["estimated_weeks_range"]["upper"] >= route["estimated_weeks"]
        assert "敏感性示例" in route["estimated_weeks_range"]["basis"]
    assert ok(client.get("/api/v1/plans", headers=alice)) == []


def test_route_comparison_explains_skipped_verified_prerequisites(system):
    client, _, alice, _, _, _ = system
    _, result = exam(client, alice)
    assert ok(result)["status"] == "passed"
    route = ok(
        client.post(
            "/api/v1/career-scenarios",
            headers=alice,
            json={"career_ids": ["career-backend"], "weekly_minutes": 60},
        )
    )[0]
    assert "python" not in {step["skill_id"] for step in route["steps"]}
    fastapi = next(step for step in route["steps"] if step["skill_id"] == "fastapi")
    assert fastapi["satisfied_prerequisites"] == ["python"]


def test_no_new_evidence_does_not_invent_trend(system):
    client, _, alice, _, _, _ = system
    job(client, alice)
    subscription = ok(
        client.post(
            "/api/v1/trend-subscriptions",
            headers=alice,
            json={"keyword": "Python", "confirmed": True},
        ),
        201,
    )
    endpoint = "/api/v1/trend-subscriptions/" + subscription["id"]
    result = ok(client.post(endpoint + "/refresh", headers=alice, json={"confirmed": True}))
    assert result["changed"] is False and result["skill_delta"] == {}
    ok(client.delete(endpoint, headers=alice))
    assert (
        client.post(endpoint + "/refresh", headers=alice, json={"confirmed": True}).status_code
        == 409
    )


def test_legacy_sample_order_does_not_invent_a_trend_change(system):
    client, engine, alice, _, _, ids = system
    job(client, alice)
    job(client, alice, text="岗位：Python 数据工程师\n要求：Python 和 SQL")
    subscription = ok(
        client.post(
            "/api/v1/trend-subscriptions",
            headers=alice,
            json={"keyword": "Python", "confirmed": True},
        ),
        201,
    )
    with Session(engine) as db:
        user = db.get(User, ids[0])
        row = growth.owned(
            db, Principal(user.id, user.tenant_id), subscription["id"], "trend_subscription"
        )
        value = growth.data(row)
        value["sample"]["sources"].reverse()
        row.payload_json = growth.dump(
            {key: item for key, item in value.items() if key not in {"id", "version", "created_at"}}
        )
        db.commit()
    result = ok(
        client.post(
            "/api/v1/trend-subscriptions/" + subscription["id"] + "/refresh",
            headers=alice,
            json={"confirmed": True},
        )
    )
    assert result["changed"] is False and result["pending_notice"] is None


def test_scheduled_trends_report_real_local_changes_and_cancel(system):
    client, engine, alice, bob, _, _ = system
    subscription = ok(
        client.post(
            "/api/v1/trend-subscriptions",
            headers=alice,
            json={"keyword": "Python", "interval_hours": 6, "confirmed": True},
        ),
        201,
    )
    job(client, bob)
    due = subscription["next_check_at"]
    assert insights.schedule_trends(engine, due - 1) == 0
    assert insights.schedule_trends(engine, due) == 1
    sample = ok(client.get("/api/v1/trend-subscriptions", headers=alice))[0]
    assert sample["sample"]["count"] == 0 and sample["changed"] is False
    local = job(client, alice)
    assert insights.schedule_trends(engine, due) == 0
    assert insights.schedule_trends(engine, due + 6 * 3600) == 1
    changed = ok(client.get("/api/v1/trend-subscriptions", headers=alice))[0]
    assert changed["changed"] is True and changed["skill_delta"]["python"] == 1
    assert changed["sample"]["sources"][0]["id"] == local["id"]
    endpoint = "/api/v1/trend-subscriptions/" + subscription["id"]
    assert client.delete(endpoint, headers=bob).status_code == 404
    paused = ok(client.delete(endpoint, headers=alice))
    assert paused["next_check_at"] is None
    assert insights.schedule_trends(engine, due + 7 * 3600 * 24) == 0


def test_trend_summary_is_opt_in_bounded_deduplicated_and_cancelable(system):
    client, engine, alice, bob, _, _ = system
    ok(
        client.put(
            "/api/v1/notification-settings",
            headers={**alice, "If-Match": '"0"'},
            json={
                **notifications.DEFAULTS,
                "quiet_start": 0,
                "quiet_end": 0,
                "daily_limit": 1,
                "confirmed": True,
            },
        )
    )
    subscriptions = [
        ok(
            client.post(
                "/api/v1/trend-subscriptions",
                headers=alice,
                json={"keyword": "Python", "interval_hours": 6, "confirmed": True},
            ),
            201,
        )
        for _ in range(2)
    ]
    job(client, alice)
    due = max(row["next_check_at"] for row in subscriptions)
    assert insights.schedule_trends(engine, due) == 2
    assert insights.schedule_trends(engine, due) == 0
    notices = ok(client.get("/api/v1/notification-center", headers=alice))["items"]
    assert len(notices) == 1 and notices[0]["kind"] == "trend_change"
    assert notices[0]["content"]["trend"]["skill_delta"]["python"] == 1
    assert ok(client.get("/api/v1/notification-center", headers=bob))["items"] == []
    pending = next(
        row
        for row in ok(client.get("/api/v1/trend-subscriptions", headers=alice))
        if row["pending_notice"]
    )
    ok(client.delete("/api/v1/trend-subscriptions/" + pending["id"], headers=alice))
    insights.schedule_trends(engine, due + 24 * 3600)
    assert len(ok(client.get("/api/v1/notification-center", headers=alice))["items"]) == 1


def test_changed_trend_waits_for_quiet_window_to_end(system):
    client, engine, alice, _, _, _ = system
    subscription = ok(
        client.post(
            "/api/v1/trend-subscriptions",
            headers=alice,
            json={"keyword": "Python", "interval_hours": 6, "confirmed": True},
        ),
        201,
    )
    due = subscription["next_check_at"]
    hour = datetime.fromtimestamp(due, timezone.utc).hour
    config = {
        **notifications.DEFAULTS,
        "timezone": "UTC",
        "quiet_start": hour,
        "quiet_end": (hour + 1) % 24,
    }
    ok(
        client.put(
            "/api/v1/notification-settings",
            headers={**alice, "If-Match": '"0"'},
            json={**config, "confirmed": True},
        )
    )
    job(client, alice)
    assert insights.schedule_trends(engine, due) == 1
    assert ok(client.get("/api/v1/notification-center", headers=alice))["items"] == []
    after_quiet = source_subscriptions.next_allowed(due, config)
    assert insights.schedule_trends(engine, after_quiet) == 0
    notices = ok(client.get("/api/v1/notification-center", headers=alice))["items"]
    assert len(notices) == 1 and notices[0]["delivered_at"] == after_quiet


def test_cold_start_retains_unknown_jobs(system):
    client, _, alice, _, _, _ = system
    job(client, alice)
    result = ok(
        client.post(
            "/api/v1/multi-target-jobs",
            headers=alice,
            json={"career_ids": ["career-backend"], "weekly_minutes": 60},
        )
    )
    assert result[0]["jobs"]
    assert all(j["cold_start"] and j["eligibility"] == "UNKNOWN" for j in result[0]["jobs"])


def test_adaptive_practice_versioning_no_capability_upgrade(system):
    client, _, alice, bob, _, _ = system
    before = ok(client.get("/api/v1/skill-evidence", headers=alice))
    exam = ok(
        client.post(
            "/api/v1/adaptive-exams",
            headers=alice,
            json={"skill_ids": ["python", "sql"], "confirmed": True},
        ),
        201,
    )
    assert "expected_choice" not in str(exam)
    endpoint = "/api/v1/adaptive-exams/" + exam["id"] + "/answer"
    assert (
        client.post(
            endpoint, headers={**bob, "If-Match": '"1"'}, json={"choice": 0, "confirmed": True}
        ).status_code
        == 404
    )
    for _ in range(6):
        exam = ok(
            client.post(
                endpoint,
                headers={**alice, "If-Match": f'"{exam["version"]}"'},
                json={"choice": 0, "confirmed": True},
            )
        )
    assert exam["status"] == "completed" and len(exam["answers"]) == 6
    assert len({(a["skill_id"], a["index"]) for a in exam["answers"]}) == 6
    assert ok(client.get("/api/v1/skill-evidence", headers=alice)) == before
    assert (
        client.post(
            endpoint,
            headers={**alice, "If-Match": f'"{exam["version"]}"'},
            json={"choice": 0, "confirmed": True},
        ).status_code
        == 409
    )
    appeal = ok(
        client.post(
            "/api/v1/adaptive-exams/" + exam["id"] + "/appeal",
            headers={**alice, "If-Match": f'"{exam["version"]}"'},
            json={"text": "熟悉题目，请核对计时提示", "confirmed": True},
        )
    )
    assert appeal["appeal"]["status"] == "pending_human_review"


def test_teaching_uses_sources_without_granting_evidence(system):
    client, _, alice, _, _, _ = system
    hint = ok(
        client.post(
            "/api/v1/teaching-turns",
            headers=alice,
            json={
                "skill_id": "python",
                "question_index": 0,
                "level": 3,
                "attempt": "我不确定",
                "confirmed": True,
            },
        ),
        201,
    )
    assert hint["source_url"].startswith("https://docs.python.org")
    assert ok(client.get("/api/v1/skill-evidence", headers=alice)) == []
