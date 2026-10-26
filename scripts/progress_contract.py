"""Owned report/settings/pipeline contract for PostgreSQL and restored databases."""

from datetime import datetime
from zoneinfo import ZoneInfo

from careerpilot import notifications
from careerpilot.runtime import run_once


def create_progress(client, headers, plan_id, prefix):
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    response = client.post(
        "/api/v1/study-logs",
        headers={**headers, "Idempotency-Key": prefix + "-time"},
        json={"day": today, "minutes": 17, "note": "合成本人明确计时记录", "confirmed": True},
    )
    assert response.status_code == 201, response.text
    log = response.json()
    response = client.put(
        "/api/v1/notification-settings",
        headers={**headers, "If-Match": '"0"'},
        json={
            **notifications.DEFAULTS,
            "status": "active",
            "weekly_report": True,
            "quiet_start": 0,
            "quiet_end": 0,
            "confirmed": True,
        },
    )
    assert response.status_code == 200, response.text
    settings = response.json()
    response = client.post(
        "/api/v1/review-pipelines",
        headers={**headers, "Idempotency-Key": prefix + "-review"},
        json={
            "start": today,
            "end": today,
            "plan_id": plan_id,
            "query": "python",
            "confirmed": True,
        },
    )
    assert response.status_code == 202, response.text
    return {"log": log, "settings": settings, "run": response.json(), "day": today}


def verify_progress(client, headers, engine, saved):
    assert any(
        r["id"] == saved["log"]["id"]
        for r in client.get("/api/v1/study-logs", headers=headers).json()
    )
    path = f"/api/v1/review-pipelines/{saved['run']['id']}"
    for _ in range(20):
        if client.get(path, headers=headers).json()["task"]["status"] == "success":
            break
        run_once(engine)
    result = client.get(path, headers=headers).json()
    assert result["completed_steps"] == 3 and result["task"]["status"] == "success", result
    report = client.get(
        "/api/v1/progress-report",
        headers=headers,
        params={"start": saved["day"], "end": saved["day"]},
    ).json()
    assert report["learning"]["self_reported_minutes"] == 17
    due = min(saved["settings"]["next_runs"].values())
    assert notifications.schedule_due(engine, due) == 1
    assert notifications.schedule_due(engine, due + 1) == 0
    items = client.get("/api/v1/notification-center", headers=headers).json()["items"]
    assert sum(n["record_kind"] == "personal_notice" for n in items) == 1
