"""Second V2 batch: reversible discovery preferences and real leased subscription jobs."""

import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from careerpilot import accounts, growth, job_policy, subscriptions
from careerpilot.domain import Principal
from careerpilot.persistence import User
from careerpilot.platform_models import BackgroundTask
from careerpilot.runtime import claim, run_once
from tests.test_v1_release import PROFILE, exam, ok, version

JD = (
    "岗位：后端实习生\n公司：测试企业\n地点：上海\n学历：本科及以上\n2027届\n"
    "实习时长：3个月\n工作类型：实习\n工作许可：不限\n资格：不限\n要求：Python"
)
CONFIG = {
    "name": "每周后端岗位",
    "frequency": "weekly",
    "filters": {"location": "上海"},
    "timezone": "Asia/Shanghai",
    "quiet_start": 0,
    "quiet_end": 0,
    "confirmed": True,
}


def job(client, headers, text=JD, url=None):
    return ok(
        client.post(
            "/api/v1/jobs/import", headers=headers, json={"content": text, "source_url": url}
        ),
        201,
    )


def pref(client, headers, body, revision=0):
    return ok(
        client.put(
            "/api/v1/job-preferences",
            headers=version(headers, revision),
            json={**body, "confirmed": True},
        )
    )


def subscribe(client, headers, **kwargs):
    return ok(
        client.post(
            "/api/v1/job-subscriptions",
            headers={**headers, "Idempotency-Key": kwargs.pop("key", "sub-1")},
            json={**CONFIG, **kwargs},
        ),
        201,
    )


def queue(client, headers, sub, key="run-1"):
    return ok(
        client.post(
            f"/api/v1/job-subscriptions/{sub['id']}/check",
            headers={**headers, "Idempotency-Key": key},
            json={"confirmed": True},
        ),
        202,
    )


def notices(client, headers):
    return ok(client.get("/api/v1/job-notifications", headers=headers))


def principal_for(engine, user_id):
    with Session(engine) as db:
        return Principal(user_id, db.get(User, user_id).tenant_id)


def test_favorites_blocks_search_matching_and_reversible_filtering(system):
    client, _, alice, bob, *_ = system
    a = job(client, alice)
    b = job(client, alice, JD.replace("测试企业", "另一企业"))
    p = pref(client, alice, {"favorite_ids": [a["id"]], "blocked_companies": ["测试企业"]})
    assert [j["id"] for j in ok(client.get("/api/v1/jobs", headers=alice))] == [b["id"]]
    result = ok(client.get("/api/v1/job-matches", headers=alice))
    assert result["hidden"][0]["job_id"] == a["id"]
    assert ok(client.get("/api/v1/saved-jobs", headers=alice))[0]["id"] == a["id"]
    assert ok(client.get(f"/api/v1/jobs/{a['id']}", headers=alice))["id"] == a["id"]
    p = pref(
        client, alice, {"favorite_ids": [a["id"]], "excluded_keywords": ["后端"]}, p["version"]
    )
    assert not ok(client.get("/api/v1/jobs", headers=alice))
    pref(client, alice, {}, p["version"])
    assert len(ok(client.get("/api/v1/jobs", headers=alice))) == 2
    assert (
        client.put(
            "/api/v1/job-preferences",
            headers=version(bob, 0),
            json={"favorite_ids": [a["id"]], "confirmed": True},
        ).status_code
        == 404
    )
    assert (
        client.put(
            "/api/v1/job-preferences", headers=version(alice, 1), json={"confirmed": True}
        ).status_code
        == 412
    )


def test_compare_unknown_fields_source_versions_and_owner_boundaries(system):
    client, _, alice, bob, *_ = system
    a, b = job(client, alice), job(client, alice, "岗位：信息缺失\n要求：SQL")
    compared = ok(
        client.post("/api/v1/jobs/compare", headers=alice, json={"job_ids": [a["id"], b["id"]]})
    )
    assert compared["jobs"][1]["fields"]["education"]["value"] is None
    assert compared["jobs"][0]["version"] == a["version"]
    assert compared["jobs"][0]["fields"]["location"]["evidence"]["quote"] == "上海"
    assert (
        client.post(
            "/api/v1/jobs/compare", headers=bob, json={"job_ids": [a["id"], b["id"]]}
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/api/v1/jobs/compare", headers=alice, json={"job_ids": [a["id"], a["id"]]}
        ).status_code
        == 422
    )


def test_feedback_reranks_and_undo_restores_without_changing_application_state(system):
    client, engine, alice, bob, *_ = system
    for i in range(3):
        job(client, alice, JD.replace("后端实习生", f"后端实习生{i}"))
    before = ok(client.get("/api/v1/job-recommendations", headers=alice))["needs_review"]
    target = before[-1]["job_id"]
    feedback = ok(
        client.put(
            f"/api/v1/jobs/{target}/feedback",
            headers=version(alice, 0),
            json={"label": "like", "reason": "明确兴趣", "confirmed": True},
        )
    )
    after = ok(client.get("/api/v1/job-recommendations", headers=alice))["needs_review"]
    assert after[0]["job_id"] == target and after[0]["score"] == before[-1]["score"]
    assert after[0]["feedback_adjustment"] == 5
    ok(
        client.post(
            f"/api/v1/jobs/{target}/feedback/undo",
            headers=version(alice, feedback["version"]),
            json={"confirmed": True},
        )
    )
    reverted = ok(client.get("/api/v1/job-recommendations", headers=alice))["needs_review"]
    assert [r["job_id"] for r in reverted] == [r["job_id"] for r in before]
    assert len(ok(client.get(f"/api/v1/jobs/{target}/feedback/history", headers=alice))) == 2
    ok(
        client.put(
            f"/api/v1/jobs/{target}/feedback",
            headers=version(alice, 2),
            json={"label": "applied", "confirmed": True},
        )
    )
    assert target not in [
        r["job_id"]
        for r in ok(client.get("/api/v1/job-recommendations", headers=alice))["needs_review"]
    ]
    assert (
        client.put(
            f"/api/v1/jobs/{target}/feedback",
            headers=version(bob, 0),
            json={"label": "like", "confirmed": True},
        ).status_code
        == 404
    )
    with Session(engine) as db:
        assert not list(
            db.scalars(select(growth.GrowthRecord).where(growth.GrowthRecord.kind == "application"))
        )


def test_modes_can_be_disabled_and_hard_fail_never_gets_promoted(system):
    client, _, alice, *_ = system
    ok(client.put("/api/v1/profile", headers=version(alice, 0), json=PROFILE))
    bad = job(client, alice, JD.replace("本科及以上", "博士及以上"))
    ok(
        client.put(
            f"/api/v1/jobs/{bad['id']}/feedback",
            headers=version(alice, 0),
            json={"label": "like", "confirmed": True},
        )
    )
    for mode in job_policy.MODES:
        result = ok(client.get(f"/api/v1/job-recommendations?mode={mode}", headers=alice))
        assert not result["eligible"] and not result["needs_review"]
    good = job(client, alice)
    assert ok(client.get("/api/v1/job-recommendations?mode=stretch", headers=alice))["needs_review"]
    exam(client, alice)
    assert not ok(client.get("/api/v1/job-recommendations?mode=stretch", headers=alice))[
        "needs_review"
    ]
    pref(client, alice, {"enabled_modes": ["best"]})
    assert ok(client.get("/api/v1/job-recommendations?mode=entry", headers=alice))["disabled"]
    assert client.get("/api/v1/job-recommendations?mode=unknown", headers=alice).status_code == 422
    assert good["id"]


def test_subscription_incremental_semantic_dedup_and_read_receipts(system):
    client, engine, alice, bob, *_ = system
    a = job(client, alice, url="https://example.com/jobs/backend")
    job(client, alice, JD.replace("上海", "北京"))
    sub = subscribe(client, alice)
    run = queue(client, alice, sub)
    assert queue(client, alice, sub)["id"] == run["id"]
    assert run_once(engine)
    initial = notices(client, alice)
    assert len(initial) == 1 and len(initial[0]["entries"]) == 1
    assert initial[0]["entries"][0]["change"] == "initial"
    assert "本地岗位库" in initial[0]["scope"]
    # A new source URL increments the JD version without a material change.
    same = job(client, alice, url="https://example.com/another-source")
    assert same["id"] == a["id"] and same["version"] > a["version"]
    queue(client, alice, sub, "again")
    run_once(engine)
    assert len(notices(client, alice)) == 1
    updated = job(client, alice, JD + "\n薪资：10000元/月", "https://example.com/jobs/backend")
    queue(client, alice, sub, "changed")
    run_once(engine)
    changed = notices(client, alice)
    assert len(changed) == 2
    entry = next(n for n in changed if n["id"] != initial[0]["id"])["entries"][0]
    assert entry["change"] == "updated" and entry["job_version"] == updated["version"]
    ok(
        client.patch(
            f"/api/v1/job-notifications/{initial[0]['id']}",
            headers=version(alice, 1),
            json={"read": True, "confirmed": True},
        )
    )
    assert (
        client.patch(
            f"/api/v1/job-notifications/{initial[0]['id']}",
            headers=version(bob, 1),
            json={"read": True, "confirmed": True},
        ).status_code
        == 404
    )
    assert not notices(client, bob)


def test_subscription_config_pause_cancel_undo_and_snapshot_isolation(system):
    client, engine, alice, bob, *_ = system
    job(client, alice)
    sub = subscribe(client, alice)
    run = queue(client, alice, sub)
    current = ok(client.get("/api/v1/job-subscriptions", headers=alice))[0]
    paused = ok(
        client.patch(
            f"/api/v1/job-subscriptions/{sub['id']}",
            headers=version(alice, current["version"]),
            json={"status": "paused", "confirmed": True},
        )
    )
    assert not run_once(engine) and not notices(client, alice)
    assert (
        client.post(
            f"/api/v1/job-subscriptions/{sub['id']}/check",
            headers={**alice, "Idempotency-Key": "no"},
            json={"confirmed": True},
        ).status_code
        == 409
    )
    cancelled = ok(
        client.patch(
            f"/api/v1/job-subscriptions/{sub['id']}",
            headers=version(alice, paused["version"]),
            json={"status": "cancelled", "confirmed": True},
        )
    )
    resumed = ok(
        client.patch(
            f"/api/v1/job-subscriptions/{sub['id']}",
            headers=version(alice, cancelled["version"]),
            json={"status": "active", "confirmed": True},
        )
    )
    assert resumed["status"] == "active"
    queue(client, alice, sub, "resumed")
    assert run_once(engine) and len(notices(client, alice)) == 1
    runs = ok(client.get(f"/api/v1/job-subscriptions/{sub['id']}/runs", headers=alice))
    assert next(r for r in runs if r["id"] == run["id"])["task"]["status"] == "cancelled"
    assert client.get(f"/api/v1/job-subscriptions/{sub['id']}/runs", headers=bob).status_code == 404


def test_creation_idempotency_and_validation(system):
    client, _, alice, *_ = system
    a = subscribe(client, alice)
    assert subscribe(client, alice)["id"] == a["id"]
    assert (
        client.post(
            "/api/v1/job-subscriptions",
            headers={**alice, "Idempotency-Key": "sub-1"},
            json={**CONFIG, "name": "different"},
        ).status_code
        == 409
    )
    for field, value in [
        ("timezone", "Not/A_Zone"),
        ("source", "unconfigured_external"),
        ("hour", 24),
    ]:
        assert (
            client.post(
                "/api/v1/job-subscriptions",
                headers={**alice, "Idempotency-Key": "bad"},
                json={**CONFIG, field: value},
            ).status_code
            == 422
        )


def test_retry_after_commit_and_receipt_are_idempotent(system):
    client, engine, alice, *_ = system
    job(client, alice)
    sub = subscribe(client, alice)
    run = queue(client, alice, sub)
    now = int(time.time())

    def crash_after_commit(engine, task_id, token, at):
        subscriptions.handler(engine, task_id, token, at)
        raise RuntimeError("injected_after_commit")

    run_once(engine, now=now, handler=crash_after_commit)
    assert len(notices(client, alice)) == 1
    assert run_once(engine, now=now + 3)
    assert len(notices(client, alice)) == 1
    with Session(engine) as db:
        task = db.get(BackgroundTask, run["id"])
        assert task.status == "success" and task.attempts == 2


def test_handler_rollback_preserves_unseen_changes_for_retry(system, monkeypatch):
    client, engine, alice, *_ = system
    job(client, alice)
    sub = subscribe(client, alice)
    queue(client, alice, sub)
    original = subscriptions.collect

    def fail(*_):
        raise RuntimeError("injected_before_commit")

    monkeypatch.setattr(subscriptions, "collect", fail)
    now = int(time.time())
    run_once(engine, now=now)
    assert not notices(client, alice)
    assert ok(client.get("/api/v1/job-subscriptions", headers=alice))[0]["seen_count"] == 0
    monkeypatch.setattr(subscriptions, "collect", original)
    assert run_once(engine, now=now + 3)
    assert len(notices(client, alice)) == 1


def test_stale_worker_cannot_write_digest(system):
    client, engine, alice, *_ = system
    job(client, alice)
    sub = subscribe(client, alice)
    run = queue(client, alice, sub)
    now = int(time.time())
    old = claim(engine, now, lease_seconds=1)
    current = claim(engine, now + 2)
    assert old and current and old[1] != current[1]
    subscriptions.handler(engine, run["id"], old[1], now + 2)
    assert not notices(client, alice)
    subscriptions.handler(engine, run["id"], current[1], now + 2)
    subscriptions.handler(engine, run["id"], current[1], now + 2)
    assert len(notices(client, alice)) == 1


def test_concurrent_schedulers_only_enqueue_one_run(system):
    client, engine, alice, _, _, (alice_id, _) = system
    sub = subscribe(client, alice)
    now = int(time.time())
    with Session(engine) as db, db.begin():
        row = growth.owned(db, principal_for(engine, alice_id), sub["id"], "job_subscription")
        row.payload_json = growth.dump({**json.loads(row.payload_json), "next_run_at": now})
    with ThreadPoolExecutor(max_workers=2) as pool:
        counts = list(pool.map(lambda _: subscriptions.schedule_due(engine, now=now), range(2)))
    assert sum(counts) == 1
    with Session(engine) as db:
        assert (
            len(
                list(
                    db.scalars(
                        select(BackgroundTask).where(BackgroundTask.kind == "market.subscription")
                    )
                )
            )
            == 1
        )


def test_timezone_weekly_quiet_and_dst_rules():
    config = {**CONFIG, "weekday": 0, "hour": 9, "quiet_start": 22, "quiet_end": 8}
    now = int(datetime.fromisoformat("2026-10-21T00:30:00+00:00").timestamp())
    weekly = subscriptions.next_occurrence(now, config)
    assert (
        datetime.fromtimestamp(weekly, ZoneInfo("Asia/Shanghai"))
        .isoformat()
        .startswith("2026-10-26T09:00")
    )
    late = int(datetime.fromisoformat("2026-10-21T15:00:00+00:00").timestamp())
    allowed = subscriptions.next_allowed(late, config)
    assert (
        datetime.fromtimestamp(allowed, ZoneInfo("Asia/Shanghai"))
        .isoformat()
        .startswith("2026-10-22T08:00")
    )
    dst = {**config, "frequency": "daily", "timezone": "America/New_York", "hour": 2}
    missing = int(datetime.fromisoformat("2026-03-08T00:00:00-05:00").timestamp())
    assert (
        datetime.fromtimestamp(
            subscriptions.next_occurrence(missing, dst), ZoneInfo(dst["timezone"])
        ).day
        == 9
    )


def test_delayed_worker_obeys_quiet_hours_instead_of_sending_at_night(system):
    client, engine, alice, _, _, (alice_id, _) = system
    job(client, alice)
    sub = subscribe(client, alice, timezone="UTC", quiet_start=22, quiet_end=8)
    day = int(datetime.fromisoformat("2026-10-21T12:00:00+00:00").timestamp())
    night = int(datetime.fromisoformat("2026-10-21T23:00:00+00:00").timestamp())
    with Session(engine) as db, db.begin():
        principal = principal_for(engine, alice_id)
        row = growth.owned(db, principal, sub["id"], "job_subscription")
        subscriptions.enqueue_run(db, principal, row, "quiet-check", "test-trace", day)
    run_once(engine, now=night)
    assert not notices(client, alice)
    morning = subscriptions.next_allowed(
        night, {**CONFIG, "timezone": "UTC", "quiet_start": 22, "quiet_end": 8}
    )
    assert run_once(engine, now=morning)
    assert len(notices(client, alice)) == 1


def test_deadline_change_is_notified_once_without_rewriting_source_job(system):
    client, engine, alice, *_ = system
    a = job(client, alice, JD + "\n截止日期：2030-01-02")
    sub = subscribe(client, alice, timezone="UTC")
    before = int(datetime.fromisoformat("2030-01-01T12:00:00+00:00").timestamp())
    after = int(datetime.fromisoformat("2030-01-03T12:00:00+00:00").timestamp())
    queue(client, alice, sub)
    run_once(engine, now=before)
    queue(client, alice, sub, "deadline")
    run_once(engine, now=after)
    entries = [e for n in notices(client, alice) for e in n["entries"]]
    assert [e["change"] for e in entries].count("deadline_passed") == 1
    assert ok(client.get(f"/api/v1/jobs/{a['id']}", headers=alice))["status"] == a["status"]
    queue(client, alice, sub, "still-expired")
    run_once(engine, now=after + 3600)
    assert len(notices(client, alice)) == 2


def test_favorites_and_block_choices_do_not_create_repeat_job_change_notifications(system):
    client, engine, alice, *_ = system
    a = job(client, alice)
    sub = subscribe(client, alice, favorites_only=True)
    queue(client, alice, sub)
    run_once(engine)
    assert not notices(client, alice)
    p = pref(client, alice, {"favorite_ids": [a["id"]]})
    queue(client, alice, sub, "favorited")
    run_once(engine)
    assert len(notices(client, alice)) == 1
    p = pref(client, alice, {}, p["version"])
    queue(client, alice, sub, "unfavorited")
    run_once(engine)
    p = pref(
        client, alice, {"favorite_ids": [a["id"]], "blocked_companies": ["测试企业"]}, p["version"]
    )
    queue(client, alice, sub, "blocked")
    run_once(engine)
    p = pref(client, alice, {"favorite_ids": [a["id"]]}, p["version"])
    queue(client, alice, sub, "unblocked")
    run_once(engine)
    assert len(notices(client, alice)) == 1


def test_configuration_edit_cancels_old_run_and_resets_scope_explicitly(system):
    client, engine, alice, bob, *_ = system
    job(client, alice)
    sub = subscribe(client, alice)
    queue(client, alice, sub)
    current = ok(client.get("/api/v1/job-subscriptions", headers=alice))[0]
    changed = ok(
        client.put(
            f"/api/v1/job-subscriptions/{sub['id']}",
            headers=version(alice, current["version"]),
            json={**CONFIG, "filters": {"location": "北京"}},
        )
    )
    assert changed["configuration_version"] == 2 and changed["last_run_at"] is None
    assert not run_once(engine) and not notices(client, alice)
    queue(client, alice, sub, "new-scope")
    assert run_once(engine) and not notices(client, alice)
    assert (
        client.put(
            f"/api/v1/job-subscriptions/{sub['id']}", headers=version(bob, 1), json=CONFIG
        ).status_code
        == 404
    )
    assert (
        client.patch(
            f"/api/v1/job-subscriptions/{sub['id']}",
            headers=version(alice, 1),
            json={"status": "paused", "confirmed": True},
        ).status_code
        == 412
    )


def test_new_discovery_records_export_and_delete_with_account(system):
    client, engine, alice, bob, *_ = system
    a = job(client, alice)
    pref(client, alice, {"favorite_ids": [a["id"]]})
    ok(
        client.put(
            f"/api/v1/jobs/{a['id']}/feedback",
            headers=version(alice, 0),
            json={"label": "misclassified", "reason": "资格需核对", "confirmed": True},
        )
    )
    sub = subscribe(client, alice)
    queue(client, alice, sub)
    run_once(engine)
    exported = ok(client.get("/api/v1/account/export", headers=alice))
    expected = {
        "job_preferences",
        "job_feedback",
        "job_subscription",
        "subscription_run",
        "job_digest",
    }
    assert expected <= {r["kind"] for r in exported["data"]["growth_records"]}
    ok(
        client.post(
            "/api/v1/account/deletion", headers=alice, json={"confirmation": "DELETE MY ACCOUNT"}
        ),
        202,
    )
    assert accounts.process_deletion(engine)
    with Session(engine) as db:
        assert not list(
            db.scalars(select(growth.GrowthRecord).where(growth.GrowthRecord.kind.in_(expected)))
        )
        assert not list(db.scalars(select(BackgroundTask)))
    assert not ok(client.get("/api/v1/job-subscriptions", headers=bob))


def test_dst_repeated_hour_and_fractional_timezone_do_not_duplicate_daily_slots():
    config = {**CONFIG, "frequency": "daily", "timezone": "America/New_York", "hour": 1}
    first = int(datetime.fromisoformat("2026-11-01T01:30:00-04:00").timestamp())
    next_at = subscriptions.next_occurrence(first, config)
    assert (
        datetime.fromtimestamp(next_at, ZoneInfo(config["timezone"])).date().isoformat()
        == "2026-11-02"
    )
    fractional = {**config, "timezone": "Asia/Kathmandu", "hour": 9}
    slot = datetime.fromtimestamp(
        subscriptions.next_occurrence(first, fractional), ZoneInfo(fractional["timezone"])
    )
    assert slot.hour == 9 and slot.minute == 0
