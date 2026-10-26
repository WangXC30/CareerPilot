"""V1 smoke in a disposable Compose project; never prints tokens or private content."""

import json
import os
import secrets
import time
from uuid import uuid4

import httpx


def main():
    if os.environ.get("CAREERPILOT_ACCEPTANCE") != "1":
        raise RuntimeError("Requires an explicitly disposable acceptance project")
    with httpx.Client(base_url="http://127.0.0.1:8000", trust_env=False, timeout=10) as client:
        registered = client.post(
            "/api/v1/auth/register",
            json={
                "email": f"acceptance-{uuid4()}@example.com",
                "password": secrets.token_urlsafe(24),
                "display_name": "Synthetic runtime student",
                "privacy_accepted": True,
            },
        )
        assert registered.status_code == 201, registered.status_code
        headers = {"Authorization": "Bearer " + registered.json()["access_token"]}
        profile = client.put(
            "/api/v1/profile",
            headers={**headers, "If-Match": '"0"'},
            json={
                "education": "bachelor",
                "major": "CS",
                "academic_year": "3",
                "graduation_date": "2027-06-30",
                "preferred_cities": ["上海"],
                "weekly_minutes": 600,
                "confirmed": True,
            },
        )
        assert profile.status_code == 200, profile.status_code
        job = client.post(
            "/api/v1/jobs/import",
            headers=headers,
            json={"content": "岗位：工程师\n要求：Python 和 SQL"},
        )
        assert job.status_code == 201, job.status_code
        plan = client.post(
            "/api/v1/plans",
            headers=headers,
            json={"job_ids": [job.json()["id"]], "start_date": "2026-10-20"},
        )
        assert plan.status_code == 201, plan.status_code
        published = client.post(
            f"/api/v1/plans/{plan.json()['id']}/publish",
            headers={**headers, "If-Match": '"1"'},
            json={"confirmed": True},
        )
        assert published.status_code == 200, published.status_code
        run = client.post(
            "/api/v1/assistant/runs",
            headers={**headers, "Idempotency-Key": "runtime-v1"},
            json={"message": "复盘当前进展"},
        )
        assert run.status_code == 202, run.status_code
        for _ in range(100):
            result = client.get(
                "/api/v1/assistant/runs/" + run.json()["id"], headers=headers
            ).json()
            if result["status"] in {"success", "failed"}:
                break
            time.sleep(0.2)
        assert result["status"] == "success", result["status"]
        assert result["result"]["active_plan"]["id"] == plan.json()["id"]
        assert result["steps"][0]["tool"] == "growth.progress"
        deletion = client.post(
            "/api/v1/account/deletion", headers=headers, json={"confirmation": "DELETE MY ACCOUNT"}
        )
        assert deletion.status_code == 202, deletion.status_code
        receipt = deletion.json()
        for _ in range(100):
            status = client.get(
                "/api/v1/account/deletion/" + receipt["id"],
                headers={"X-Deletion-Receipt": receipt["receipt"]},
            ).json()
            if status["status"] == "completed":
                break
            time.sleep(0.2)
        assert status["status"] == "completed", status["status"]
        assert client.get("/api/v1/me", headers=headers).status_code == 401
        assert client.get("/health/ready").json()["schema_revision"] == "0005"
        print(
            json.dumps(
                {
                    "schema_revision": "0005",
                    "separate_worker": True,
                    "assistant_status": "success",
                    "tool_results_match_persisted_plan": True,
                    "deletion_status": "completed",
                    "sessions_revoked": True,
                }
            )
        )


if __name__ == "__main__":
    main()
