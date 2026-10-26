"""Reusable local V4 persistence/revocation contract, with synthetic accounts only."""

import base64
import json
from unittest.mock import patch

from learning_contract import create_learning, verify_learning
from remaining_contract import create_remaining, verify_remaining
from sqlalchemy.orm import Session

from careerpilot import application_lab, growth, insights
from careerpilot.cli import provision
from careerpilot.domain import Principal
from careerpilot.persistence import User
from careerpilot.v1_models import CatalogueEntry


def checked(response, status=200):
    assert response.status_code == status, response.status_code
    return response.json()


def create_v4(client, engine):
    alice_id, alice_token = provision(engine, "V4 contract Alice")
    bob_id, bob_token = provision(engine, "V4 contract Bob")
    alice = {"Authorization": "Bearer " + alice_token}
    bob = {"Authorization": "Bearer " + bob_token}
    with Session(engine) as db:
        sources = []
        for user_id in (alice_id, bob_id):
            user = db.get(User, user_id)
            sources.append(
                growth.save(
                    db,
                    Principal(user.id, user.tenant_id),
                    "project_asset",
                    {"title": "Selected title", "status": "active", "content": "PRIVATE-NOTE"},
                    "contract",
                )
            )
        user = db.get(User, alice_id)
        interview = growth.save(
            db,
            Principal(user.id, user.tenant_id),
            "interview_session",
            {"status": "pending", "facts": [], "questions": [], "answers": []},
            "contract",
        )
        db.commit()
    space = checked(
        client.post("/api/v1/workspaces", headers=alice, json={"name": "V4", "confirmed": True}),
        201,
    )

    def invite():
        return checked(
            client.post(
                f"/api/v1/workspaces/{space['id']}/invitations",
                headers=alice,
                json={"recipient_id": bob_id, "confirmed": True},
            ),
            201,
        )

    invitation = invite()
    checked(
        client.post(
            f"/api/v1/workspace-invitations/{invitation['id']}/accept",
            headers=bob,
            json={"ticket": invitation["ticket"], "confirmed": True},
        )
    )

    def share(headers, source, public=False):
        return checked(
            client.post(
                "/api/v1/object-shares",
                headers=headers,
                json={
                    "kind": "project_asset",
                    "source_id": source["id"],
                    "source_version": source["version"],
                    "fields": ["title"],
                    "workspace_id": space["id"],
                    "public": public,
                    "confirmed": True,
                },
            ),
            201,
        )

    owner_share = share(alice, sources[0])
    departed_share = share(bob, sources[1], public=True)
    checked(client.delete(f"/api/v1/workspaces/{space['id']}/members/{bob_id}", headers=bob))
    pending = invite()
    fact = checked(
        client.post(
            "/api/v1/resume-facts",
            headers=alice,
            json={
                "kind": "experience",
                "source_type": "manual",
                "text": "2 项练习",
                "confirmed": True,
            },
        ),
        201,
    )
    direction_body = {
        "title": "初始方向",
        "fact_ids": [fact["id"]],
        "project_ids": [],
        "confirmed": True,
    }
    direction = checked(
        client.post("/api/v1/direction-views", headers=alice, json=direction_body), 201
    )
    direction = checked(
        client.put(
            "/api/v1/direction-views/" + direction["id"],
            headers={**alice, "If-Match": '"1"'},
            json={**direction_body, "title": "更新方向"},
        )
    )
    trends = [
        checked(
            client.post(
                "/api/v1/trend-subscriptions",
                headers=alice,
                json={"keyword": "Python", "interval_hours": 6, "confirmed": True},
            ),
            201,
        )
        for _ in range(2)
    ]
    checked(client.delete("/api/v1/trend-subscriptions/" + trends[1]["id"], headers=alice))
    recording = checked(
        client.post(
            "/api/v1/voice-records",
            headers=alice,
            json={
                "interview_id": interview["id"],
                "audio_base64": base64.b64encode(b"RIFF0000WAVEsynthetic").decode(),
                "recording_consent": True,
                "confirmed": True,
            },
        ),
        201,
    )
    checked(client.delete("/api/v1/voice-records/" + recording["id"] + "/audio", headers=alice))
    resume = checked(
        client.post(
            "/api/v1/resumes",
            headers={**alice, "Idempotency-Key": "v4-contract-resume"},
            json={"title": "合成演练简历", "fact_ids": [fact["id"]], "confirmed": True},
        ),
        201,
    )
    job = checked(
        client.post(
            "/api/v1/jobs/import",
            headers=alice,
            json={"content": "岗位：本地合同演练\n要求：Python"},
        ),
        201,
    )
    application = checked(
        client.post(
            "/api/v1/applications",
            headers={**alice, "Idempotency-Key": "v4-contract-application"},
            json={"job_id": job["id"], "resume_version_id": resume["id"], "confirmed": True},
        ),
        201,
    )
    preview = checked(
        client.post(
            "/api/v1/application-lab/preview",
            headers=alice,
            json={"application_id": application["id"], "confirmed": True},
        )
    )
    approval = checked(
        client.post(
            "/api/v1/application-lab/approve",
            headers=alice,
            json={
                "application_id": application["id"],
                "preview_hash": preview["preview_hash"],
                "confirmed": True,
            },
        )
    )
    checked(
        client.delete("/api/v1/application-lab/approvals/" + approval["approval_id"], headers=alice)
    )
    assert (
        client.post(
            "/api/v1/application-lab/submit",
            headers=alice,
            json={
                "application_id": application["id"],
                "approval_id": approval["approval_id"],
                "confirmed": True,
            },
        ).status_code
        == 403
    )
    approval = checked(
        client.post(
            "/api/v1/application-lab/approve",
            headers=alice,
            json={
                "application_id": application["id"],
                "preview_hash": preview["preview_hash"],
                "confirmed": True,
            },
        )
    )
    with patch.object(application_lab, "simulator", side_effect=TimeoutError("synthetic")):
        simulation = checked(
            client.post(
                "/api/v1/application-lab/submit",
                headers=alice,
                json={
                    "application_id": application["id"],
                    "approval_id": approval["approval_id"],
                    "confirmed": True,
                },
            )
        )
    comparison = checked(
        client.post(
            "/api/v1/offer-comparisons",
            headers=alice,
            json={
                "offers": [{"name": name, "ratings": {}, "confirmed": True} for name in ("A", "B")],
                "weights": {
                    "compensation": 30,
                    "growth": 25,
                    "location": 15,
                    "stability": 15,
                    "work_life": 15,
                },
                "confirmed": True,
            },
        ),
        201,
    )
    checked(
        client.post(
            "/api/v1/offer-comparisons/" + comparison["id"] + "/decision",
            headers={**alice, "If-Match": '"1"'},
            json={"offer_index": None, "note": "本人保留未知，暂不选择", "confirmed": True},
        )
    )
    event = checked(
        client.post(
            "/api/v1/calendar-events",
            headers=alice,
            json={
                "title": "恢复合同面试",
                "kind": "interview",
                "date": "2026-12-01",
                "time": "14:30",
                "timezone": "Asia/Shanghai",
                "confirmed": True,
            },
        ),
        201,
    )
    calendars = [
        checked(
            client.post(
                "/api/v1/calendar-syncs",
                headers=alice,
                json={
                    "event_ids": [event["id"]],
                    "confirmed": True,
                },
            ),
            201,
        )
        for _ in range(2)
    ]
    checked(client.delete("/api/v1/calendar-syncs/" + calendars[1]["id"], headers=alice))
    review = checked(
        client.post(
            "/api/v1/quarter-reviews",
            headers=alice,
            json={
                "start": "2026-10-01",
                "end": "2026-12-31",
                "reflection": "本人保留原目标",
                "confirmed": True,
            },
        ),
        201,
    )
    replay_job = checked(
        client.post(
            "/api/v1/jobs/import",
            headers=alice,
            json={
                "content": "岗位：恢复冻结词库\n要求：PyRecovery",
            },
        ),
        201,
    )
    matching_snapshot = checked(
        client.post(
            "/api/v1/matching-snapshots",
            headers=alice,
            json={
                "job_id": replay_job["id"],
                "confirmed": True,
            },
        ),
        201,
    )
    assert matching_snapshot["result"]["skills"] == []
    learning = create_learning(client, alice, bob, bob_id)
    remaining = create_remaining(client, alice)
    return {
        "alice": alice,
        "bob": bob,
        "owner_share": owner_share,
        "departed_share": departed_share,
        "pending": pending,
        "direction": direction,
        "trends": trends,
        "recording": recording,
        "simulation": simulation,
        "comparison": comparison,
        "calendar_event": event,
        "calendars": calendars,
        "quarter_review": review,
        "matching_snapshot": matching_snapshot,
        "replay_job": replay_job,
        "learning": learning,
        "remaining": remaining,
    }


def verify_v4(client, engine, state):
    alice, bob = state["alice"], state["bob"]
    pending = state["pending"]
    endpoint = f"/api/v1/workspace-invitations/{pending['id']}/accept"
    body = {"ticket": pending["ticket"], "confirmed": True}
    checked(client.post(endpoint, headers=bob, json=body))
    assert client.post(endpoint, headers=bob, json=body).status_code == 404
    owner_share = state["owner_share"]
    shared = checked(client.get("/api/v1/object-shares/" + owner_share["id"], headers=bob))
    assert shared["content"] == {"title": "Selected title"}
    departed = state["departed_share"]
    assert client.get("/api/v1/object-shares/" + departed["id"], headers=alice).status_code == 404
    assert (
        client.get(
            "/api/v1/public-shares/" + departed["id"],
            headers={"X-Share-Ticket": departed["ticket"]},
        ).status_code
        == 404
    )
    direction = checked(
        client.get("/api/v1/direction-views/" + state["direction"]["id"], headers=alice)
    )
    assert direction["version"] == 2 and direction["title"] == "更新方向"
    assert direction["facts"][0]["text"] == "2 项练习"
    assert len(direction["history"]) == 2
    assert (
        client.get(
            "/api/v1/voice-records/" + state["recording"]["id"] + "/audio", headers=alice
        ).status_code
        == 404
    )
    due = state["trends"][0]["next_check_at"]
    assert insights.schedule_trends(engine, due) == 1
    assert insights.schedule_trends(engine, due) == 0
    subscriptions = checked(client.get("/api/v1/trend-subscriptions", headers=alice))
    assert next(row for row in subscriptions if row["status"] == "active")["changed"] is True
    assert next(row for row in subscriptions if row["status"] == "paused")["next_check_at"] is None
    print(
        "V4 persistence: scoped share, permanent revoke, pending invite, views/audio/trends passed"
    )
    simulation = state["simulation"]
    runs = checked(client.get("/api/v1/application-lab/runs", headers=alice))
    assert len(runs) == 1 and runs[0]["status"] == "unknown_needs_manual_review"
    assert checked(client.get("/api/v1/application-lab/runs", headers=bob)) == []
    reviewed = checked(
        client.post(
            "/api/v1/application-lab/runs/" + simulation["id"] + "/review",
            headers={**alice, "If-Match": f'"{simulation["version"]}"'},
            json={"note": "本人核对未取得模拟回执", "confirmed": True},
        )
    )
    assert reviewed["status"] == "reviewed_unknown" and reviewed["receipt"] is None
    comparison = checked(client.get("/api/v1/offer-comparisons", headers=alice))[0]
    assert comparison["decision"]["offer_index"] is None
    assert comparison["decision"]["external_acceptance"] is False
    print("V4 persistence: unknown simulation review and private Offer decision passed")
    active, revoked = state["calendars"]
    endpoint = "/api/v1/calendar-syncs/" + active["id"]
    exported = client.get(endpoint + "/export", headers=alice)
    assert exported.status_code == 200 and "DTSTART:20261201T063000Z" in exported.text
    assert client.get(endpoint + "/export", headers=bob).status_code == 404
    assert (
        client.get("/api/v1/calendar-syncs/" + revoked["id"] + "/export", headers=alice).status_code
        == 404
    )
    checked(
        client.delete(
            endpoint + "/events/" + state["calendar_event"]["id"],
            headers={**alice, "If-Match": '"1"'},
        )
    )
    assert client.get(endpoint + "/export", headers=alice).status_code == 404
    review = state["quarter_review"]
    assert checked(client.get("/api/v1/quarter-reviews/" + review["id"], headers=alice)) == review
    assert checked(client.get("/api/v1/quarter-reviews", headers=bob)) == []
    print("V4 persistence: calendar event, revocation and immutable private review passed")
    # Simulated public-catalogue fixture mutation only, in this disposable restored DB.
    with Session(engine) as db, db.begin():
        python = db.get(CatalogueEntry, "python")
        original_payload, original_version = python.payload_json, python.version
        value = json.loads(python.payload_json)
        value["aliases"].append("PyRecovery")
        python.payload_json = growth.dump(value)
        python.version += 1
    try:
        snapshot = state["matching_snapshot"]
        historical = checked(
            client.get("/api/v1/matching-snapshots/" + snapshot["id"] + "/replay", headers=alice)
        )
        assert historical["identical"] and historical["result"] == snapshot["result"]
        updated = checked(
            client.post(
                "/api/v1/matching-snapshots",
                headers=alice,
                json={"job_id": state["replay_job"]["id"], "confirmed": True},
            ),
            201,
        )
        assert updated["result"]["skills"][0]["evidence"]["quote"] == "PyRecovery"
        assert checked(client.get("/api/v1/matching-snapshots", headers=bob)) == []
    finally:
        # Leave other restored contracts' pending previews on their original catalogue.
        with Session(engine) as db, db.begin():
            python = db.get(CatalogueEntry, "python")
            python.payload_json, python.version = original_payload, original_version
    print("V4 persistence: frozen catalogue replay survives current alias changes")
    verify_learning(client, alice, bob, state["learning"])
    verify_remaining(client, alice, bob, state["remaining"])
