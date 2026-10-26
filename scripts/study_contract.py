"""Shared real API persistence contract for study modes, feedback and review history."""

from datetime import datetime
from zoneinfo import ZoneInfo

from careerpilot.cli import provision


def checked(response, code=200):
    assert response.status_code == code, response.text
    return response.json()


def create_study(client, engine):
    _, token = provision(engine, "Synthetic study contract")
    h = {"Authorization": "Bearer " + token}
    checked(
        client.put(
            "/api/v1/profile",
            headers={**h, "If-Match": '"0"'},
            json={
                "education": "bachelor",
                "major": "计算机",
                "academic_year": "大三",
                "graduation_date": "2027-06-30",
                "preferred_cities": ["上海"],
                "weekly_minutes": 300,
                "timezone": "Asia/Shanghai",
                "confirmed": True,
            },
        )
    )
    j = checked(
        client.post(
            "/api/v1/jobs/import", headers=h, json={"content": "岗位：学习演练\n要求：Python SQL"}
        ),
        201,
    )
    p = checked(
        client.post(
            "/api/v1/plans",
            headers=h,
            json={
                "job_ids": [j["id"]],
                "start_date": datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat(),
            },
        ),
        201,
    )
    p = checked(
        client.post(
            f"/api/v1/plans/{p['id']}/publish",
            headers={**h, "If-Match": '"1"'},
            json={"confirmed": True},
        )
    )
    a = checked(client.post("/api/v1/assessments", headers=h, json={"skill_id": "python"}), 201)
    checked(
        client.post(
            f"/api/v1/assessments/{a['id']}/submit",
            headers={**h, "If-Match": '"1"'},
            json={"answers": [0, 0, 0]},
        )
    )
    preview = checked(
        client.post(
            f"/api/v1/plans/{p['id']}/mode-preview",
            headers={
                **h,
                "If-Match": f'"{p["version"]}"',
                "Idempotency-Key": "study-contract-mode",
            },
            json={"mode": "light", "confirmed": True},
        ),
        201,
    )
    checked(
        client.put(
            "/api/v1/learning-resources/resource-python/feedback",
            headers={**h, "If-Match": '"0"'},
            json={
                "favorite": True,
                "quality": "poor",
                "broken": True,
                "note": "演练坏链",
                "confirmed": True,
            },
        )
    )
    checked(
        client.put(
            "/api/v1/notification-settings",
            headers={**h, "If-Match": '"0"'},
            json={"status": "active", "spaced_review": True, "confirmed": True},
        )
    )
    return {"headers": h, "plan_id": p["id"], "preview_id": preview["id"]}


def verify_study(client, state):
    h = state["headers"]
    previews = checked(client.get("/api/v1/plan-previews", headers=h))
    p = next(p for p in previews if p["id"] == state["preview_id"])
    assert p["status"] == "pending" and not p["stale"]
    checked(
        client.post(
            f"/api/v1/plan-previews/{p['id']}/accept",
            headers={**h, "If-Match": '"1"'},
            json={"confirmed": True},
        )
    )
    assert (
        client.post(
            f"/api/v1/plan-previews/{p['id']}/accept",
            headers={**h, "If-Match": '"1"'},
            json={"confirmed": True},
        ).status_code
        == 409
    )
    plan = checked(client.get("/api/v1/plans", headers=h))[0]
    assert plan["study_mode"] == "light" and plan["validation"]["budget_minutes"] == 75
    assert all(m <= 75 for m in plan["validation"]["weekly_minutes"].values())
    queue = checked(client.get("/api/v1/study-reviews", headers=h))
    assert len(queue["items"][0]["wrong_questions"]) == 2
    resources = checked(client.get("/api/v1/learning-resources", headers=h))
    assert any(r.get("local_fallback") and r["skill_id"] == "python" for r in resources)
    settings = checked(client.get("/api/v1/notification-settings", headers=h))
    assert settings["spaced_review"] and "spaced_review" in settings["next_runs"]
    checked(
        client.put(
            "/api/v1/notification-settings",
            headers={**h, "If-Match": f'"{settings["version"]}"'},
            json={"status": "active", "spaced_review": False, "confirmed": True},
        )
    )
