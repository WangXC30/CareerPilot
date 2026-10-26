"""Authoritative telemetry, offline drafts and owned practice/index subscriptions."""

import base64
import json
import time
from datetime import date

from sqlalchemy.orm import Session

from careerpilot import analytics, content_subscriptions, growth, voice_asr
from careerpilot.domain import Principal
from careerpilot.growth_models import GrowthRecord, GrowthRevision
from careerpilot.persistence import CareerGoal
from tests.test_v4_collaboration import ok
from tests.test_v4_local_tools import _owner, owned


def test_source_channel_failure_isolated_and_private(system, monkeypatch):
    from careerpilot import source_channels
    from tests.test_growth import import_job

    client, _, alice, bob, _, _ = system
    import_job(client, alice)
    owned(
        system,
        "media_index",
        {
            "title": "Python",
            "expires_at": int(time.time()) + 1000,
            "segments": [{"anchor": "page:1", "text": "Python"}],
        },
    )

    def broken(*_):
        raise RuntimeError("secret exception must not escape")

    monkeypatch.setattr(
        source_channels,
        "CHANNELS",
        (
            ("jobs", "jobs", source_channels.jobs),
            ("broken", "broken", broken),
            ("media", "media", source_channels.indexed),
        ),
    )
    result = ok(client.get("/api/v1/source-channels?q=Python", headers=alice))
    assert [c["status"] for c in result["channels"]] == ["available", "unavailable", "available"]
    assert len(result["channels"][0]["items"]) == len(result["channels"][2]["items"]) == 1
    assert "secret exception" not in str(result)
    assert all(
        not c["items"] for c in ok(client.get("/api/v1/source-channels", headers=bob))["channels"]
    )


def test_public_bilingual_selection_revoke_and_source_withdrawal(system):
    from tests.test_v2_hiring import fact, resume

    client, _, alice, bob, _, _ = system
    first = fact(client, alice, "2024 年完成 2 个项目")
    second = fact(client, alice, "未选择的私人事实")
    version = resume(client, alice, [first, second])
    translation = ok(
        client.post(
            "/api/v1/resume-translations",
            headers=alice,
            json={
                "resume_version_id": version["id"],
                "locale": "en-US",
                "confirmed": True,
                "mappings": [
                    {"fact_id": first["id"], "text": "Completed 2 projects in 2024"},
                    {"fact_id": second["id"], "text": "Unselected private fact"},
                ],
            },
        ),
        201,
    )
    body = {
        "translation_id": translation["id"],
        "fact_ids": [first["id"]],
        "headline": "项目记录",
        "privacy_reviewed": True,
        "confirmed": True,
    }
    assert client.post("/api/v1/brand-profiles", headers=bob, json=body).status_code == 404
    assert (
        client.post(
            "/api/v1/brand-profiles", headers=alice, json={**body, "privacy_reviewed": False}
        ).status_code
        == 422
    )
    brand = ok(client.post("/api/v1/brand-profiles", headers=alice, json=body), 201)
    share_body = {
        "kind": "brand_profile",
        "source_id": brand["id"],
        "source_version": 1,
        "fields": ["items"],
        "public": True,
        "lifetime_hours": 1,
        "confirmed": True,
    }
    share = ok(client.post("/api/v1/object-shares", headers=alice, json=share_body), 201)
    public = "/api/v1/public-shares/" + share["id"]
    ticket = {"X-Share-Ticket": share["ticket"]}
    content = ok(client.get(public, headers=ticket))
    assert "Completed 2 projects" in str(content) and "Unselected" not in str(content)
    assert first["id"] not in str(content) and "translation_id" not in str(content)
    ok(client.delete("/api/v1/object-shares/" + share["id"], headers=alice))
    assert client.get(public, headers=ticket).status_code == 404
    share = ok(client.post("/api/v1/object-shares", headers=alice, json=share_body), 201)
    ok(
        client.post(
            "/api/v1/resume-facts/" + first["id"] + "/revoke",
            headers=alice,
            json={"reason": "不再公开", "confirmed": True},
        )
    )
    assert client.get(
        "/api/v1/public-shares/" + share["id"], headers={"X-Share-Ticket": share["ticket"]}
    ).status_code in {404, 409}


def test_deadline_notice_requires_current_source_evidence(system):
    from careerpilot import notifications
    from tests.test_growth import import_job

    client, engine, alice, _, _, _ = system
    owner = Principal(*_owner(system))
    today = date.today().isoformat()
    valid = ok(import_job(client, alice, "岗位：有效截止\n截止日期：" + today), 201)
    missing = ok(import_job(client, alice, "岗位：未知截止"), 201)
    with Session(engine) as db:
        row = db.get(GrowthRecord, missing["id"])
        value = json.loads(row.payload_json)
        value["fields"]["deadline"]["value"] = today
        row.payload_json = growth.dump(value)
        db.commit()
        result = notifications.content(
            db, owner, "deadline_check", int(time.time()), {"timezone": "UTC"}
        )
        assert [j["job_id"] for j in result["jobs"]] == [valid["id"]]
        row = db.get(GrowthRecord, valid["id"])
        row.payload_json = growth.dump({**json.loads(row.payload_json), "status": "closed"})
        db.commit()
        assert not notifications.content(
            db, owner, "deadline_check", int(time.time()), {"timezone": "UTC"}
        )["jobs"]


def voice(system):
    interview = owned(system, "interview_session", {"status": "pending"})
    return ok(
        system[0].post(
            "/api/v1/voice-records",
            headers=system[2],
            json={
                "confirmed": True,
                "recording_consent": True,
                "interview_id": interview["id"],
                "audio_base64": base64.b64encode(b"RIFF0000WAVEtest").decode(),
            },
        ),
        201,
    )


def test_offline_draft_consent_cas_isolation_and_no_overwrite(system, monkeypatch):
    client, _, alice, bob, _, _ = system
    v = voice(system)
    path = "/api/v1/voice-records/" + v["id"]
    reviewed = ok(
        client.put(
            path + "/transcript", headers=alice, json={"text": "reviewed", "confirmed": True}
        )
    )
    monkeypatch.setattr(voice_asr.shutil, "which", lambda _: "/usr/bin/tool")
    calls = []
    monkeypatch.setattr(voice_asr, "decode", lambda _: calls.append(True) or "draft")
    body = {"confirmed": True, "locale": "en-US", "transcription_consent": True}
    headers = {**alice, "If-Match": f'"{reviewed["version"]}"'}
    assert (
        client.post(
            path + "/draft", headers=headers, json={**body, "transcription_consent": False}
        ).status_code
        == 422
    )
    assert (
        client.post(path + "/draft", headers={**bob, "If-Match": '"2"'}, json=body).status_code
        == 404
    )
    assert (
        client.post(path + "/draft", headers={**alice, "If-Match": '"1"'}, json=body).status_code
        == 412
    )
    assert not calls
    draft = ok(client.post(path + "/draft", headers=headers, json=body))
    assert draft["transcript"] == "reviewed" and draft["transcript_draft"] == "draft"
    assert "audio_base64" not in draft
    removed = ok(client.delete(path + "/audio", headers=alice))
    assert removed["transcript_draft"] is None and removed["transcript"] == "reviewed"
    assert (
        client.post(
            path + "/draft", headers={**alice, "If-Match": f'"{removed["version"]}"'}, json=body
        ).status_code
        == 404
    )


def test_deletion_during_decode_cannot_resurrect_audio(system, monkeypatch):
    client, engine, alice, _, _, _ = system
    v = voice(system)
    owner = Principal(*_owner(system))
    monkeypatch.setattr(voice_asr.shutil, "which", lambda _: "/usr/bin/tool")

    def decode(_):
        from careerpilot.personal_tools import remove_recording

        with Session(engine) as db:
            remove_recording(db, owner, v["id"])
            db.commit()
        return "late draft"

    monkeypatch.setattr(voice_asr, "decode", decode)
    response = client.post(
        "/api/v1/voice-records/" + v["id"] + "/draft",
        headers={**alice, "If-Match": '"1"'},
        json={"confirmed": True, "locale": "en-US", "transcription_consent": True},
    )
    assert response.status_code == 404
    with Session(engine) as db:
        assert db.get(GrowthRecord, v["id"]) is None


def test_practice_unknown_denominator_revision_and_private_delete(system):
    client, engine, alice, bob, _, _ = system
    path = "/api/v1/practice-attempts"
    body = {
        "title": "边界练习",
        "kind": "algorithm",
        "difficulty": "medium",
        "tags": ["数组"],
        "outcome": "unjudged",
        "minutes": 15,
        "practiced_on": date.today().isoformat(),
        "confirmed": True,
    }
    row = ok(client.post(path, headers=alice, json=body), 201)
    assert ok(client.get(path, headers=alice))["summary"]["cells"][0]["success_rate"] is None
    assert ok(client.get(path, headers=bob))["attempts"] == []
    assert (
        client.put(
            path + "/" + row["id"], headers={**bob, "If-Match": '"1"'}, json=body
        ).status_code
        == 404
    )
    ok(
        client.put(
            path + "/" + row["id"],
            headers={**alice, "If-Match": '"1"'},
            json={**body, "outcome": "failed"},
        )
    )
    assert (
        client.put(
            path + "/" + row["id"], headers={**alice, "If-Match": '"1"'}, json=body
        ).status_code
        == 412
    )
    ok(client.post(path, headers=alice, json={**body, "outcome": "passed"}), 201)
    cell = ok(client.get(path, headers=alice))["summary"]["cells"][0]
    assert cell["success_rate"] == 0.5 and cell["judged"] == 2
    ok(client.delete(path + "/" + row["id"], headers=alice))
    with Session(engine) as db:
        assert db.get(GrowthRevision, (row["id"], 1)) is None


def test_growth_week_requires_active_related_goal_and_opt_in(system):
    client, engine, alice, _, _, _ = system
    owner = Principal(*_owner(system))
    ok(client.put("/api/v1/consents/product_analytics", headers=alice, json={"granted": True}))
    with Session(engine) as db:
        goal = CareerGoal(
            user_id=owner.user_id, tenant_id=owner.tenant_id, title="goal", status="active"
        )
        db.add(goal)
        db.flush()
        plan = growth.save(
            db,
            owner,
            "plan",
            {"status": "active", "goal_id": goal.id, "tasks": [{"skill_id": "python"}]},
            "test",
        )
        analytics.verified_action(db, owner, {"sql"}, "test")
        assert not analytics.owner_summary(db, owner)["effective_growth_week"]
        analytics.verified_action(db, owner, {"python"}, "test")
        analytics.verified_action(db, owner, {"python"}, "test")
        db.commit()
        rows = list(db.scalars(growth.owner_query(owner, "analytics_event")))
        assert (
            len(rows) == 1
            and "goal" not in rows[0].payload_json
            and plan["id"] not in rows[0].payload_json
        )
    assert ok(client.get("/api/v1/analytics-summary", headers=alice))["effective_growth_week"]
    ok(client.put("/api/v1/consents/product_analytics", headers=alice, json={"granted": False}))
    assert not ok(client.get("/api/v1/analytics-summary", headers=alice))["effective_growth_week"]


def test_content_subscription_new_only_quiet_cancel_and_expiry(system):
    client, engine, alice, bob, _, _ = system
    now = int(time.time())
    owner = Principal(*_owner(system))
    owned(
        system,
        "notification_settings",
        {"timezone": "UTC", "quiet_start": 0, "quiet_end": 0, "daily_limit": 4},
    )
    row = ok(
        client.post(
            "/api/v1/content-subscriptions",
            headers=alice,
            json={"keyword": "python", "interval_hours": 6, "confirmed": True},
        ),
        201,
    )

    def due():
        with Session(engine) as db:
            r = db.get(GrowthRecord, row["id"])
            r.payload_json = growth.dump({**json.loads(r.payload_json), "next_check_at": now - 1})
            db.commit()

    indexed = owned(
        system,
        "media_index",
        {
            "title": "Python",
            "expires_at": now + 1000,
            "segments": [{"anchor": "page:1", "text": "private excerpt"}],
        },
    )
    assert len(ok(client.get("/api/v1/media-index?q=python", headers=alice))) == 1
    assert ok(client.get("/api/v1/media-index?q=private", headers=bob)) == []
    due()
    assert content_subscriptions.schedule(engine, now) == 1
    assert content_subscriptions.schedule(engine, now) == 0
    assert (
        client.delete(
            "/api/v1/content-subscriptions/" + row["id"], headers={**bob, "If-Match": '"1"'}
        ).status_code
        == 404
    )
    with Session(engine) as db:
        version = db.get(GrowthRecord, row["id"]).version
        notices = [growth.data(r) for r in db.scalars(growth.owner_query(owner, "personal_notice"))]
        assert len(notices) == 1 and "private excerpt" not in str(notices)
    ok(
        client.delete(
            "/api/v1/content-subscriptions/" + row["id"],
            headers={**alice, "If-Match": f'"{version}"'},
        )
    )
    ok(client.delete("/api/v1/media-index/" + indexed["id"], headers=alice))
    assert content_subscriptions.schedule(engine, now + 999999) == 0


def test_experiment_replay_and_review_never_enable_models(system):
    client, _, alice, bob, _, _ = system
    row = ok(client.post("/api/v1/agent-experiments", headers=alice, json={"confirmed": True}), 201)
    path = "/api/v1/agent-experiments/" + row["id"]
    assert client.get(path + "/replay", headers=bob).status_code == 404
    replay = ok(client.get(path + "/replay", headers=alice))
    assert all(r["identical"] for r in replay["results"])
    assert replay["enabled"] is False
    body = {"note": "合成样例不足以证明真实质量收益", "confirmed": True}
    reviewed = ok(client.post(path + "/review", headers={**alice, "If-Match": '"1"'}, json=body))
    assert reviewed["enabled"] is False and reviewed["human_review"]["note"] == body["note"]
    assert (
        client.post(path + "/review", headers={**alice, "If-Match": '"1"'}, json=body).status_code
        == 412
    )


def test_offline_decoder_failure_cleans_temporary_files(monkeypatch):
    import subprocess
    from pathlib import Path

    import pytest

    from careerpilot.domain import DomainError

    paths = []
    monkeypatch.setattr(voice_asr.shutil, "which", lambda _: "/usr/bin/tool")

    def fail(command, **kwargs):
        paths.append(Path(command[-1]))
        assert paths[-1].exists()
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(voice_asr.subprocess, "run", fail)
    with pytest.raises(DomainError):
        voice_asr.decode(base64.b64encode(b"RIFF0000WAVE").decode())
    assert paths and all(not p.parent.exists() for p in paths)


def test_aggregate_threshold_rounding_and_legacy_exclusion(system):
    from datetime import timedelta
    from uuid import uuid4

    from careerpilot.persistence import User
    from careerpilot.platform_models import Consent

    owner = Principal(*_owner(system))
    prior = (analytics.today().replace(day=1) - timedelta(days=1)).isoformat()
    with Session(system[1]) as db:
        for index in range(12):
            uid = str(uuid4())
            db.add(User(id=uid, tenant_id=owner.tenant_id, display_name="synthetic"))
            db.flush()
            db.add(
                Consent(
                    user_id=uid, tenant_id=owner.tenant_id, purpose="product_analytics", granted=1
                )
            )
            growth.save(
                db,
                Principal(uid, owner.tenant_id),
                "analytics_event",
                {
                    "day": prior,
                    "event": "assessment_completed",
                    "arm": "A",
                    "collection_version": analytics.VERSION if index < 9 else "legacy",
                },
                "test",
            )
        db.commit()
        operator = Principal(owner.user_id, owner.tenant_id, "operator")
        assert all(c["suppressed"] for c in analytics.aggregate(db, operator)["cells"])
        rows = list(
            db.scalars(growth.owner_query(Principal(uid, owner.tenant_id), "analytics_event"))
        )
        rows[0].payload_json = growth.dump(
            {**json.loads(rows[0].payload_json), "collection_version": analytics.VERSION}
        )
        db.commit()
        cells = analytics.aggregate(db, operator)["cells"]
        cell = next(c for c in cells if c["event"] == "assessment_completed" and c["arm"] == "A")
        assert cell["participants_rounded"] == 10 and not cell["suppressed"]
        assert uid not in str(cells)


def test_content_quiet_pending_expiry_and_cancel(system):
    from datetime import datetime, timezone

    client, engine, alice, _, _, _ = system
    now = int(time.time())
    hour = datetime.fromtimestamp(now, timezone.utc).hour
    owned(
        system,
        "notification_settings",
        {"timezone": "UTC", "quiet_start": hour, "quiet_end": (hour + 1) % 24, "daily_limit": 4},
    )
    sub = ok(
        client.post(
            "/api/v1/content-subscriptions",
            headers=alice,
            json={"keyword": "quiet", "interval_hours": 6, "confirmed": True},
        ),
        201,
    )
    media = owned(
        system, "media_index", {"title": "quiet", "expires_at": now + 7200, "segments": []}
    )
    with Session(engine) as db:
        row = db.get(GrowthRecord, sub["id"])
        row.payload_json = growth.dump({**json.loads(row.payload_json), "next_check_at": now - 1})
        db.commit()
    assert content_subscriptions.schedule(engine, now) == 0
    with Session(engine) as db:
        assert json.loads(db.get(GrowthRecord, sub["id"]).payload_json)["pending"]
        row = db.get(GrowthRecord, media["id"])
        row.payload_json = growth.dump({**json.loads(row.payload_json), "expires_at": now - 1})
        db.commit()
    assert content_subscriptions.schedule(engine, now + 3600) == 0
    with Session(engine) as db:
        row = db.get(GrowthRecord, sub["id"])
        assert json.loads(row.payload_json)["pending"] is None
        version = row.version
    ok(
        client.delete(
            "/api/v1/content-subscriptions/" + sub["id"],
            headers={**alice, "If-Match": f'"{version}"'},
        )
    )
    assert content_subscriptions.schedule(engine, now + 100000) == 0


def test_media_and_asr_limits_prevent_processing(system, monkeypatch):
    from careerpilot import media
    from careerpilot.gateway import reserve

    client, engine, alice, _, _, _ = system
    owner = Principal(*_owner(system))
    v = voice(system)
    with Session(engine) as db:
        reserve(db, owner, "voice-asr:" + time.strftime("%Y-%m-%d", time.gmtime()), 20, 20)
        for index in range(50):
            growth.save(
                db,
                owner,
                "media_index",
                {"title": str(index), "segments": [], "expires_at": int(time.time()) + 600},
                "test",
            )
        db.commit()

    def unexpected(*_):
        raise AssertionError("processing must not run")

    monkeypatch.setattr(media, "extract", unexpected)
    monkeypatch.setattr(voice_asr, "decode", unexpected)
    monkeypatch.setattr(voice_asr.shutil, "which", lambda _: "/usr/bin/tool")
    response = client.post(
        "/api/v1/media-index",
        headers=alice,
        json={
            "kind": "pdf",
            "title": "cap",
            "license_basis": "self",
            "rights_confirmed": True,
            "content_base64": "eA==",
            "confirmed": True,
        },
    )
    assert response.status_code == 422 and response.json()["code"] == "MEDIA_LIMIT"
    response = client.post(
        "/api/v1/voice-records/" + v["id"] + "/draft",
        headers={**alice, "If-Match": '"1"'},
        json={"locale": "en-US", "transcription_consent": True, "confirmed": True},
    )
    assert response.status_code == 429
