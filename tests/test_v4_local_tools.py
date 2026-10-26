"""Local-only application, privacy, recording and calendar contracts."""

import base64
import time
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from careerpilot import analytics, application_lab, growth, media
from careerpilot.cli import provision
from careerpilot.domain import Principal
from careerpilot.growth_models import GrowthRecord, GrowthRevision
from careerpilot.persistence import User
from careerpilot.platform_models import Consent
from tests.test_v2_hiring import application
from tests.test_v4_collaboration import ok


def owned(system, kind, payload):
    client, engine, alice, _, _, ids = system
    with Session(engine) as db:
        user = db.get(User, ids[0])
        row = growth.save(db, Principal(user.id, user.tenant_id), kind, payload, "test")
        db.commit()
    return row


def test_application_simulation_consumes_specific_approval(system):
    client, _, alice, bob, _, _ = system
    app = application(client, alice)
    endpoint = "/api/v1/application-lab/"
    preview = ok(
        client.post(
            endpoint + "preview",
            headers=alice,
            json={"application_id": app["id"], "confirmed": True},
        )
    )
    body = {"application_id": app["id"], "preview_hash": preview["preview_hash"], "confirmed": True}
    assert client.post(endpoint + "approve", headers=bob, json=body).status_code == 404
    approval = ok(client.post(endpoint + "approve", headers=alice, json=body))
    result = ok(
        client.post(
            endpoint + "submit",
            headers=alice,
            json={
                "application_id": app["id"],
                "approval_id": approval["approval_id"],
                "confirmed": True,
            },
        )
    )
    assert result["status"] == "simulated"
    assert result["receipt"]["kind"] == "simulated_receipt"
    assert (
        client.post(
            endpoint + "submit",
            headers=alice,
            json={
                "application_id": app["id"],
                "approval_id": approval["approval_id"],
                "confirmed": True,
            },
        ).status_code
        == 403
    )
    assert ok(client.get("/api/v1/applications", headers=alice))[0]["status"] == "draft"


def test_interrupted_submission_remains_unknown_and_is_never_replayed(system):
    _, engine, _, _, _, ids = system
    with Session(engine) as db:
        user = db.get(User, ids[0])
        run = growth.save(
            db,
            Principal(user.id, user.tenant_id),
            "application_simulation",
            {"preview": {"application_id": "synthetic"}, "status": "in_progress", "receipt": None},
            "test",
        )
        row = db.scalar(select(GrowthRecord).where(GrowthRecord.id == run["id"]))
        row.created_at = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
        db.commit()
    assert application_lab.recover_uncertain(engine) == 1
    assert application_lab.recover_uncertain(engine) == 0
    with Session(engine) as db:
        row = db.scalar(select(GrowthRecord).where(GrowthRecord.id == run["id"]))
        assert growth.data(row)["status"] == "unknown_needs_manual_review"
        assert growth.data(row)["retry_allowed"] is False


def test_analytics_aggregation_cannot_include_other_tenants(system):
    _, engine, _, _, _, ids = system
    foreign = [provision(engine, f"foreign-{i}")[0] for i in range(10)]
    prior_month = (datetime.now(timezone.utc).date().replace(day=1) - timedelta(days=1)).replace(
        day=1
    )
    with Session(engine) as db:
        alice = db.get(User, ids[0])
        for key in foreign:
            user = db.get(User, key)
            db.add(
                Consent(
                    user_id=user.id,
                    tenant_id=user.tenant_id,
                    purpose="product_analytics",
                    granted=1,
                )
            )
            growth.save(
                db,
                Principal(user.id, user.tenant_id),
                "analytics_event",
                {"day": prior_month.isoformat(), "event": "learning_completed", "arm": "A"},
                "test",
            )
        db.commit()
        report = analytics.aggregate(db, Principal(alice.id, alice.tenant_id, "operator"))
    assert all(cell["suppressed"] for cell in report["cells"])


def test_analytics_events_and_revisions_expire(system):
    engine = system[1]
    old = owned(
        system,
        "analytics_event",
        {"day": (date.today() - timedelta(days=91)).isoformat(), "event": "learning_completed"},
    )
    recent = owned(
        system,
        "analytics_event",
        {"day": date.today().isoformat(), "event": "learning_completed"},
    )
    assert analytics.purge_expired(engine) == 1
    with Session(engine) as db:
        assert db.get(GrowthRecord, old["id"]) is None
        assert not list(
            db.scalars(select(GrowthRevision).where(GrowthRevision.record_id == old["id"]))
        )
        assert db.get(GrowthRecord, recent["id"]) is not None


def test_offer_unknown_is_range_and_privately_owned(system):
    client, _, alice, bob, _, _ = system
    weights = {"compensation": 40, "growth": 30, "location": 10, "stability": 10, "work_life": 10}
    result = ok(
        client.post(
            "/api/v1/offer-comparisons",
            headers=alice,
            json={
                "offers": [
                    {
                        "name": "A",
                        "ratings": {"compensation": 4, "growth": None},
                        "confirmed": True,
                    },
                    {"name": "B", "ratings": {"compensation": 3, "growth": 4}, "confirmed": True},
                ],
                "weights": weights,
                "confirmed": True,
            },
        ),
        201,
    )
    assert result["offers"][0]["score_lower"] < result["offers"][0]["score_upper"]
    assert result["decision"] is None
    assert (
        client.post(
            "/api/v1/offer-comparisons",
            headers=bob,
            json={
                "offers": [{"name": "A", "ratings": {}, "confirmed": True}],
                "weights": weights,
                "confirmed": True,
            },
        ).status_code
        == 422
    )


def test_recording_consent_and_audio_purge(system):
    client, _, alice, bob, _, _ = system
    interview = owned(system, "interview_session", {"status": "pending"})
    audio = base64.b64encode(b"RIFF" + b"0" * 4 + b"WAVE" + b"1234").decode()
    body = {
        "interview_id": interview["id"],
        "audio_base64": audio,
        "retention_days": 1,
        "recording_consent": True,
        "confirmed": True,
    }
    assert (
        client.post(
            "/api/v1/voice-records", headers=alice, json={**body, "recording_consent": False}
        ).status_code
        == 422
    )
    created = ok(client.post("/api/v1/voice-records", headers=alice, json=body), 201)
    assert "audio_base64" not in str(created)
    assert (
        client.get("/api/v1/voice-records/" + created["id"] + "/audio", headers=bob).status_code
        == 404
    )
    assert client.get(
        "/api/v1/voice-records/" + created["id"] + "/audio", headers=alice
    ).content.startswith(b"RIFF")
    result = ok(
        client.put(
            "/api/v1/voice-records/" + created["id"] + "/transcript",
            headers=alice,
            json={"text": "本人核对的转写", "confirmed": True},
        )
    )
    assert result["transcription_method"] == "human_reviewed"
    ok(client.delete("/api/v1/voice-records/" + created["id"] + "/audio", headers=alice))
    assert (
        client.get("/api/v1/voice-records/" + created["id"] + "/audio", headers=alice).status_code
        == 404
    )
    with Session(system[1]) as db:
        row = growth.owned(db, Principal(*_owner(system)), created["id"], "voice_record")
        assert audio not in row.payload_json
        assert all(
            audio not in h["payload"].get("audio_base64", "")
            for h in growth.history(db, Principal(*_owner(system)), created["id"], "voice_record")
        )


def _owner(system):
    with Session(system[1]) as db:
        user = db.get(User, system[5][0])
        return user.id, user.tenant_id


def test_calendar_export_and_revoke(system):
    client, _, alice, bob, _, _ = system
    milestone = owned(
        system, "milestone", {"title": "完成课程", "target_date": "2026-12-01", "completed": False}
    )
    sync = ok(
        client.post(
            "/api/v1/calendar-syncs",
            headers=alice,
            json={"event_ids": [milestone["id"]], "confirmed": True},
        ),
        201,
    )
    endpoint = "/api/v1/calendar-syncs/" + sync["id"] + "/export"
    assert client.get(endpoint, headers=bob).status_code == 404
    assert "DTSTART;VALUE=DATE:20261201" in client.get(endpoint, headers=alice).text
    ok(client.delete("/api/v1/calendar-syncs/" + sync["id"], headers=alice))
    assert client.get(endpoint, headers=alice).status_code == 404


def test_analytics_opt_in_revoke_removes_events(system):
    client, _, alice, _, _, _ = system
    endpoint = "/api/v1/analytics-events"
    assert (
        ok(
            client.post(
                endpoint, headers=alice, json={"event": "learning_completed", "confirmed": True}
            )
        )["collected"]
        is False
    )
    ok(client.put("/api/v1/consents/product_analytics", headers=alice, json={"granted": True}))
    assert (
        ok(
            client.post(
                endpoint, headers=alice, json={"event": "learning_completed", "confirmed": True}
            )
        )["collected"]
        is False
    )
    application(client, alice)
    with Session(system[1]) as db:
        assert (
            len(list(db.scalars(growth.owner_query(Principal(*_owner(system)), "analytics_event"))))
            == 1
        )
    ok(client.put("/api/v1/consents/product_analytics", headers=alice, json={"granted": False}))
    with Session(system[1]) as db:
        assert (
            list(db.scalars(growth.owner_query(Principal(*_owner(system)), "analytics_event")))
            == []
        )


def test_multimedia_rejects_unlicensed_and_invalid_files(system):
    client, _, alice, _, _, _ = system
    body = {
        "kind": "pdf",
        "title": "local",
        "source_url": "",
        "license_basis": "self-authored",
        "content_base64": base64.b64encode(b"not pdf").decode(),
        "rights_confirmed": True,
        "confirmed": True,
    }
    assert (
        client.post(
            "/api/v1/media-index", headers=alice, json={**body, "rights_confirmed": False}
        ).status_code
        == 422
    )
    assert client.post("/api/v1/media-index", headers=alice, json=body).status_code == 422


def test_expired_media_and_audio_disappear_before_worker_and_are_physically_purged(system):
    client, engine, alice, _, _, _ = system
    indexed = owned(
        system,
        "media_index",
        {"expires_at": int(time.time()) - 1, "segments": [{"anchor": "page:1", "text": "old"}]},
    )
    recorded = owned(
        system,
        "voice_record",
        {"expires_at": int(time.time()) - 1, "audio_deleted": False, "audio_base64": "old"},
    )
    assert ok(client.get("/api/v1/media-index", headers=alice)) == []
    assert ok(client.get("/api/v1/voice-records", headers=alice)) == []
    assert (
        client.get("/api/v1/voice-records/" + recorded["id"] + "/audio", headers=alice).status_code
        == 404
    )
    media.purge_expired(engine)
    with Session(engine) as db:
        for key in (indexed["id"], recorded["id"]):
            assert db.get(GrowthRecord, key) is None
            assert not list(
                db.scalars(select(GrowthRevision).where(GrowthRevision.record_id == key))
            )


def test_local_agent_fixture_cannot_enable_multi_model(system):
    client, _, alice, _, _, _ = system
    result = ok(
        client.post("/api/v1/agent-experiments", headers=alice, json={"confirmed": True}), 201
    )
    assert result["enabled"] is False and result["results"][0]["provider_calls"] == 0
    assert result["quality_gain"] >= 0
