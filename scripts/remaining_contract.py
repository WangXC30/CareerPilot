"""New local V4 records remain private and revoked after database restore."""

from datetime import date


def checked(response, status=200):
    assert response.status_code == status, response.status_code
    return response.json()


def create_remaining(client, alice):
    attempt = checked(
        client.post(
            "/api/v1/practice-attempts",
            headers=alice,
            json={
                "title": "restore synthetic",
                "kind": "algorithm",
                "difficulty": "easy",
                "tags": ["boundary"],
                "outcome": "unjudged",
                "minutes": 10,
                "practiced_on": date.today().isoformat(),
                "confirmed": True,
            },
        ),
        201,
    )
    sub = checked(
        client.post(
            "/api/v1/content-subscriptions",
            headers=alice,
            json={
                "keyword": "restore",
                "interval_hours": 24,
                "confirmed": True,
            },
        ),
        201,
    )
    checked(
        client.delete(
            "/api/v1/content-subscriptions/" + sub["id"], headers={**alice, "If-Match": '"1"'}
        )
    )
    experiment = checked(
        client.post("/api/v1/agent-experiments", headers=alice, json={"confirmed": True}), 201
    )
    checked(
        client.post(
            "/api/v1/agent-experiments/" + experiment["id"] + "/review",
            headers={**alice, "If-Match": '"1"'},
            json={"note": "local only", "confirmed": True},
        )
    )
    fact = checked(
        client.post(
            "/api/v1/resume-facts",
            headers=alice,
            json={
                "kind": "experience",
                "source_type": "manual",
                "text": "完成 2 项练习",
                "confirmed": True,
            },
        ),
        201,
    )
    resume = checked(
        client.post(
            "/api/v1/resumes",
            headers={**alice, "Idempotency-Key": "remaining-resume"},
            json={
                "title": "restore bilingual",
                "fact_ids": [fact["id"]],
                "confirmed": True,
            },
        ),
        201,
    )
    translation = checked(
        client.post(
            "/api/v1/resume-translations",
            headers=alice,
            json={
                "resume_version_id": resume["id"],
                "locale": "en-US",
                "confirmed": True,
                "mappings": [{"fact_id": fact["id"], "text": "Completed 2 exercises"}],
            },
        ),
        201,
    )
    brand = checked(
        client.post(
            "/api/v1/brand-profiles",
            headers=alice,
            json={
                "translation_id": translation["id"],
                "fact_ids": [fact["id"]],
                "headline": "restore profile",
                "privacy_reviewed": True,
                "confirmed": True,
            },
        ),
        201,
    )
    share = checked(
        client.post(
            "/api/v1/object-shares",
            headers=alice,
            json={
                "kind": "brand_profile",
                "source_id": brand["id"],
                "source_version": 1,
                "fields": ["items"],
                "public": True,
                "confirmed": True,
            },
        ),
        201,
    )
    checked(
        client.post(
            "/api/v1/resume-facts/" + fact["id"] + "/revoke",
            headers=alice,
            json={"reason": "restore revoked", "confirmed": True},
        )
    )
    return {"attempt": attempt, "subscription": sub, "experiment": experiment, "share": share}


def verify_remaining(client, alice, bob, state):
    attempts = checked(client.get("/api/v1/practice-attempts", headers=alice))["attempts"]
    assert state["attempt"] in attempts
    assert not checked(client.get("/api/v1/practice-attempts", headers=bob))["attempts"]
    subs = checked(client.get("/api/v1/content-subscriptions", headers=alice))
    assert next(s for s in subs if s["id"] == state["subscription"]["id"])["status"] == "cancelled"
    experiment = state["experiment"]
    replay = checked(
        client.get("/api/v1/agent-experiments/" + experiment["id"] + "/replay", headers=alice)
    )
    assert not replay["enabled"] and all(r["identical"] for r in replay["results"])
    share = state["share"]
    assert (
        client.get(
            "/api/v1/public-shares/" + share["id"], headers={"X-Share-Ticket": share["ticket"]}
        ).status_code
        == 409
    )
    print(
        "V4 restore: private practice, cancelled content, frozen replay, "
        "withdrawn bilingual share passed"
    )
