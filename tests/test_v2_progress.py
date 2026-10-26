"""Period semantics, atomic notifications and durable multi-step review contracts."""

import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from careerpilot import accounts, growth, notifications, reporting, review_pipeline
from careerpilot.platform_models import BackgroundTask
from careerpilot.runtime import claim, run_once
from tests.test_v1_release import PROFILE, action, exam, ok, setup_plan, version
from tests.test_v2_hiring import awaiting, material, receipt_body, transition
from tests.test_v2_opportunities import principal_for
from tests.test_v2_sources import imported

TODAY = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
START = "2026-01-01"
END = "2026-12-31"


def report(client, headers, start=START, end=END):
    return ok(
        client.get("/api/v1/progress-report", headers=headers, params={"start": start, "end": end})
    )


def log(client, headers, key="study-1", **kwargs):
    return client.post(
        "/api/v1/study-logs",
        headers={**headers, "Idempotency-Key": key},
        json={
            "day": TODAY,
            "minutes": 25,
            "note": "本人记录的练习投入",
            "confirmed": True,
            **kwargs,
        },
    )


def configure(client, headers, revision=0, **kwargs):
    return client.put(
        "/api/v1/notification-settings",
        headers=version(headers, revision),
        json={
            **notifications.DEFAULTS,
            "status": "active",
            "weekly_report": True,
            "quiet_start": 0,
            "quiet_end": 0,
            "confirmed": True,
            **kwargs,
        },
    )


def pipeline(client, headers, plan, key="pipeline-1", **kwargs):
    return client.post(
        "/api/v1/review-pipelines",
        headers={**headers, "Idempotency-Key": key},
        json={
            "start": START,
            "end": END,
            "plan_id": plan["id"],
            "query": "python",
            "confirmed": True,
            **kwargs,
        },
    )


def get_pipeline(client, headers, run):
    return ok(client.get(f"/api/v1/review-pipelines/{run['id']}", headers=headers))


def test_empty_report_and_zero_denominators_are_explicit(system):
    client, _, alice, *_ = system
    r = report(client, alice)
    assert r["learning"]["self_reported_minutes"] == 0
    assert r["funnel"]["counts"] == {"created": 0, "submitted": 0, "interviewing": 0, "offer": 0}
    assert all(s["ratio"] is None for s in r["funnel"]["ratios"])
    assert (
        client.get(
            "/api/v1/progress-report?start=2026-12-31&end=2026-01-01", headers=alice
        ).status_code
        == 422
    )


def test_planned_and_completed_minutes_do_not_become_actual_time(system):
    client, _, alice, *_ = system
    _, p = setup_plan(system)
    task = p["tasks"][0]
    p = ok(action(client, alice, p, task["id"], "start"))
    p = ok(action(client, alice, p, task["id"], "complete", outcome="已记录练习结果"))
    r = report(client, alice)
    assert r["learning"]["current_planned_minutes"] > 0
    assert r["learning"]["self_reported_minutes"] == 0
    assert len(r["learning"]["completed_tasks"]) == 1
    a = ok(log(client, alice, plan_id=p["id"], task_id=task["id"]), 201)
    assert ok(log(client, alice, plan_id=p["id"], task_id=task["id"]), 201)["id"] == a["id"]
    assert report(client, alice)["learning"]["self_reported_minutes"] == 25
    assert log(client, alice, minutes=26).status_code == 409
    ok(
        client.post(
            f"/api/v1/study-logs/{a['id']}/withdraw",
            headers=version(alice, 1),
            json={"confirmed": True},
        )
    )
    assert report(client, alice)["learning"]["self_reported_minutes"] == 0


def test_log_constraints_foreign_plan_and_daily_total(system):
    client, _, alice, bob, *_ = system
    _, p = setup_plan(system)
    assert log(client, bob, plan_id=p["id"], task_id=p["tasks"][0]["id"]).status_code == 404
    assert log(client, alice, task_id="unknown").status_code == 422
    assert log(client, alice, day="2999-01-01").status_code == 422
    assert log(client, alice, minutes=True).status_code == 422
    assert log(client, alice, note="sk_" + "x" * 24).status_code == 422
    a = ok(log(client, alice, minutes=1440), 201)
    assert log(client, alice, key="too-much", minutes=1).status_code == 422
    assert (
        client.post(
            f"/api/v1/study-logs/{a['id']}/withdraw",
            headers=version(bob, 1),
            json={"confirmed": True},
        ).status_code
        == 404
    )


def test_funnel_uses_submission_receipts_and_same_cohort_history(system):
    client, _, alice, *_ = system
    a = awaiting(client, alice)
    r = report(client, alice)
    assert r["funnel"]["counts"]["submitted"] == 0 and r["funnel"][
        "unsubmitted_or_unconfirmed_ids"
    ] == [a["id"]]
    m = material(client, alice, f"{a['job']['title']} 投递成功，申请编号 TEST-123，已提交。")
    a = ok(
        client.post(
            f"/api/v1/applications/{a['id']}/receipt",
            headers=version(alice, a["version"]),
            json=receipt_body(m),
        )
    )
    a = ok(transition(client, alice, a, "interviewing", note="本人补录真实面试通知"))
    a = ok(transition(client, alice, a, "offer", note="本人补录录用通知"))
    r = report(client, alice)
    assert r["funnel"]["counts"] == {"created": 1, "submitted": 1, "interviewing": 1, "offer": 1}
    assert all(s["numerator"] == s["denominator"] == 1 for s in r["funnel"]["ratios"])
    assert report(client, alice, "2020-01-01", "2020-12-31")["funnel"]["counts"]["created"] == 0


def test_evidence_and_user_records_are_separate_and_owned(system):
    client, _, alice, bob, *_ = system
    exam(client, alice)
    ok(log(client, alice), 201)
    r = report(client, alice)
    assert (
        r["evidence"]["new_records"] and "python" in r["evidence"]["currently_verified_foundation"]
    )
    assert not report(client, bob)["evidence"]["new_records"]
    assert ok(client.get("/api/v1/study-logs", headers=bob)) == []
    assert (
        reporting.local_day("2026-10-20T20:00:00+00:00", "Asia/Shanghai").isoformat()
        == "2026-10-21"
    )


def test_notification_defaults_opt_in_quiet_limit_and_atomic_dedup(system):
    client, engine, alice, _, _, ids = system
    assert ok(client.get("/api/v1/notification-settings", headers=alice))["status"] == "paused"
    assert notifications.schedule_due(engine) == 0
    config = ok(configure(client, alice, daily_learning=True, daily_limit=1))
    due = min(config["next_runs"].values())
    with ThreadPoolExecutor(max_workers=2) as pool:
        counts = list(pool.map(lambda _: notifications.schedule_due(engine, due), range(2)))
    assert sum(counts) == 1
    center = ok(client.get("/api/v1/notification-center", headers=alice))
    assert len(center["items"]) == 1 and center["channels"]["email"] is False
    notice = center["items"][0]
    ok(
        client.post(
            f"/api/v1/personal-notices/{notice['id']}/read",
            headers=version(alice, 1),
            json={"confirmed": True},
        )
    )
    assert (
        client.post(
            f"/api/v1/personal-notices/{notice['id']}/read",
            headers=version(alice, 1),
            json={"confirmed": True},
        ).status_code
        == 412
    )
    assert (
        client.put(
            "/api/v1/notification-settings",
            headers=version(alice, config["version"]),
            json={**notifications.DEFAULTS, "channel": "email", "confirmed": True},
        ).status_code
        == 422
    )
    with Session(engine) as db:
        assert notifications.settings(db, principal_for(engine, ids[0]))["next_runs"]


def test_notification_pause_cancel_stale_config_and_timezone_validation(system):
    client, engine, alice, bob, *_ = system
    s = ok(configure(client, alice, daily_learning=True))
    assert configure(client, alice).status_code == 412
    paused = ok(configure(client, alice, s["version"], status="paused"))
    assert (
        paused["next_runs"] == {}
        and notifications.schedule_due(engine, int(time.time()) + 10**6) == 0
    )
    assert configure(client, alice, paused["version"], timezone="invalid/zone").status_code == 422
    assert ok(client.get("/api/v1/notification-center", headers=bob))["items"] == []
    now = int(datetime(2026, 10, 21, 23, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp())
    with Session(engine) as db, db.begin():
        row = db.scalar(
            growth.owner_query(principal_for(engine, system[5][0]), "notification_settings")
        )
        old = growth.data(row)
        growth.save(
            db,
            principal_for(engine, system[5][0]),
            "notification_settings",
            {
                **reporting.body(old),
                "status": "active",
                "quiet_start": 22,
                "quiet_end": 8,
                "next_runs": {"daily_learning": now},
            },
            "test",
            row,
            row.version,
        )
    assert notifications.schedule_due(engine, now) == 0
    assert notifications.schedule_due(engine, now + 10 * 3600) == 1


def test_week_month_boundaries_and_dst_schedule_are_defined():
    now = int(datetime(2026, 11, 1, 10, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp())
    assert reporting.previous_period(now, "Asia/Shanghai", "monthly_report") == (
        "2026-10-01",
        "2026-10-31",
    )
    config = {**notifications.DEFAULTS, "timezone": "America/New_York", "hour": 2}
    before_gap = int(datetime(2026, 3, 7, 3, tzinfo=ZoneInfo("America/New_York")).timestamp())
    next_day = datetime.fromtimestamp(
        notifications.next_run(before_gap, config, "daily_learning"), ZoneInfo(config["timezone"])
    )
    assert next_day.day == 9 and next_day.hour == 2


def test_pipeline_success_has_three_steps_one_preview_and_no_plan_mutation(system):
    client, engine, alice, bob, *_ = system
    _, p = setup_plan(system)
    r = ok(pipeline(client, alice, p), 202)
    assert ok(pipeline(client, alice, p), 202)["id"] == r["id"]
    assert pipeline(client, bob, p).status_code == 404
    assert run_once(engine)
    result = get_pipeline(client, alice, r)
    assert result["task"]["status"] == "success" and result["completed_steps"] == 3
    assert [s["name"] for s in result["steps"]] == list(review_pipeline.STEPS)
    assert ok(client.get(f"/api/v1/plans/{p['id']}", headers=alice))["version"] == p["version"]
    assert len(ok(client.get("/api/v1/plan-previews", headers=alice))) == 1
    assert client.get(f"/api/v1/review-pipelines/{r['id']}", headers=bob).status_code == 404


def test_pipeline_failure_resumes_failed_step_retains_prior_snapshot(system, monkeypatch):
    client, engine, alice, *_ = system
    _, p = setup_plan(system)
    r = ok(pipeline(client, alice, p), 202)
    original = review_pipeline.execute_step
    calls = []

    def interrupted(db, principal, run, stage, trace):
        calls.append(stage)
        if stage == "source_references" and calls.count(stage) == 1:
            raise OSError("network failed Bearer never-retain-this-secret")
        return original(db, principal, run, stage, trace)

    monkeypatch.setattr(review_pipeline, "execute_step", interrupted)
    now = int(time.time())
    run_once(engine, now=now)
    partial = get_pipeline(client, alice, r)
    assert partial["completed_steps"] == 1 and partial["task"]["status"] == "queued"
    assert "never-retain" not in json.dumps(partial)
    run_once(engine, now=now + 3)
    resumed = get_pipeline(client, alice, r)
    assert resumed["completed_steps"] == 3 and resumed["steps"][0] == partial["steps"][0]
    assert calls.count("period_report") == 1 and calls.count("source_references") == 2


def test_pipeline_committed_before_ack_survives_expired_lease_without_duplicate_preview(system):
    client, engine, alice, *_ = system
    _, p = setup_plan(system)
    r = ok(pipeline(client, alice, p), 202)
    now = int(time.time())
    task_id, token = claim(engine, now)
    review_pipeline.handler(engine, task_id, token, now)
    assert get_pipeline(client, alice, r)["completed_steps"] == 3
    run_once(engine, now=now + 31)
    assert get_pipeline(client, alice, r)["task"]["status"] == "success"
    assert len(ok(client.get("/api/v1/plan-previews", headers=alice))) == 1


def test_pipeline_stale_context_cancel_and_old_worker_fence(system):
    client, engine, alice, *_ = system
    _, p = setup_plan(system)
    r = ok(pipeline(client, alice, p), 202)
    now = int(time.time())
    old = claim(engine, now, lease_seconds=1)
    fresh = claim(engine, now + 2)
    review_pipeline.handler(engine, *old, now + 2)
    assert get_pipeline(client, alice, r)["completed_steps"] == 0
    task = get_pipeline(client, alice, r)["task"]
    ok(
        client.post(
            f"/api/v1/agent-tasks/{task['id']}/cancel", headers=version(alice, task["version"])
        )
    )
    review_pipeline.handler(engine, *fresh, now + 2)
    assert get_pipeline(client, alice, r)["completed_steps"] == 0
    r2 = ok(pipeline(client, alice, p, key="new-run"), 202)
    ok(
        client.put(
            "/api/v1/profile", headers=version(alice, 1), json={**PROFILE, "preferences": "已变更"}
        )
    )
    before = ok(client.get("/api/v1/plan-previews", headers=alice))
    run_once(engine, now=now + 4)
    assert get_pipeline(client, alice, r2)["last_error"]["code"] == "STALE_WORKFLOW_CONTEXT"
    assert ok(client.get("/api/v1/plan-previews", headers=alice)) == before


def test_pipeline_does_not_copy_private_reference_contents(system):
    client, engine, alice, *_ = system
    _, p = setup_plan(system)
    source = imported(
        client, alice, title="private-unique-reference", text="本人私有摘录", claims=[]
    )
    r = ok(pipeline(client, alice, p), 202)
    run_once(engine)
    result = get_pipeline(client, alice, r)
    assert source["id"] in result["steps"][1]["result"]["private_reference_ids"]
    assert "本人私有摘录" not in json.dumps(result, ensure_ascii=False)
    assert "private-unique-reference" not in json.dumps(result)


def test_new_private_aggregates_export_and_delete_with_account(system):
    client, engine, alice, _, _, ids = system
    _, p = setup_plan(system)
    a = ok(log(client, alice), 201)
    r = ok(pipeline(client, alice, p), 202)
    run_once(engine)
    ok(configure(client, alice))
    with Session(engine) as db:
        exported = json.dumps(accounts.export_data(db, principal_for(engine, ids[0])))
        assert a["id"] in exported and r["id"] in exported
    ok(
        client.post(
            "/api/v1/account/deletion", headers=alice, json={"confirmation": "DELETE MY ACCOUNT"}
        ),
        202,
    )
    accounts.process_deletion(engine)
    with Session(engine) as db:
        assert not list(
            db.scalars(select(growth.GrowthRecord).where(growth.GrowthRecord.user_id == ids[0]))
        )
        assert not list(db.scalars(select(BackgroundTask).where(BackgroundTask.user_id == ids[0])))
