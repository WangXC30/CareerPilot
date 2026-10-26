"""Reusable HTTP hiring contract for PostgreSQL and snapshot recovery drills."""

import time
from datetime import datetime, timezone


def create_hiring(client, headers, job_id, prefix):
    facts = []
    for kind, text in [
        ("name", "合成学生"),
        ("contact", "synthetic@example.com"),
        ("project", "使用 Python 完成课程中的数据整理练习。"),
    ]:
        response = client.post(
            "/api/v1/resume-facts",
            headers=headers,
            json={"kind": kind, "source_type": "manual", "text": text, "confirmed": True},
        )
        assert response.status_code == 201, response.text
        facts.append(response.json()["id"])
    resume = client.post(
        "/api/v1/resumes",
        headers={**headers, "Idempotency-Key": prefix + "-resume"},
        json={"title": "Synthetic resume", "fact_ids": facts, "job_id": job_id, "confirmed": True},
    )
    assert resume.status_code == 201, resume.text
    v = resume.json()
    application = client.post(
        "/api/v1/applications",
        headers={**headers, "Idempotency-Key": prefix + "-app"},
        json={"job_id": job_id, "resume_version_id": v["id"], "confirmed": True},
    )
    assert application.status_code == 201, application.text
    a = application.json()
    for state in ("prepared", "awaiting_user_submission"):
        body = {"target": state, "confirmed": True}
        if state == "prepared":
            body["checklist"] = {
                k: True for k in ("facts", "contact", "requirements", "privacy", "source_reviewed")
            }
        response = client.post(
            f"/api/v1/applications/{a['id']}/transition",
            headers={**headers, "If-Match": f'"{a["version"]}"'},
            json=body,
        )
        assert response.status_code == 200, response.text
        a = response.json()
    notice_time = int(time.time()) + 3600
    local = (
        datetime.fromtimestamp(notice_time, timezone.utc)
        .replace(tzinfo=None)
        .isoformat(timespec="seconds")
    )
    reminder = client.post(
        f"/api/v1/applications/{a['id']}/reminders",
        headers={**headers, "Idempotency-Key": prefix + "-reminder"},
        json={
            "kind": "follow_up",
            "local_time": local,
            "timezone": "UTC",
            "quiet_start": 0,
            "quiet_end": 0,
            "confirmed": True,
        },
    )
    assert reminder.status_code == 201, reminder.text
    return {"version": v, "application": a, "reminder": reminder.json()}


def verify_hiring(client, headers, saved):
    v, a = saved["version"], saved["application"]
    version = client.get(f"/api/v1/resume-versions/{v['id']}", headers=headers)
    assert version.status_code == 200
    assert {k: version.json()[k] for k in v} == v
    applications = client.get("/api/v1/applications", headers=headers).json()
    current = next(item for item in applications if item["id"] == a["id"])
    assert (
        current["resume_version_id"] == v["id"] and current["status"] == "awaiting_user_submission"
    )
    assert len(client.get(f"/api/v1/applications/{a['id']}/history", headers=headers).json()) == 3
    export = client.post(f"/api/v1/resume-versions/{v['id']}/export/docx", headers=headers)
    assert (
        export.status_code == 200 and export.headers["x-resume-content-hash"] == v["content_hash"]
    )
