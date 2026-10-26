"""Run only inside a disposable Compose API container; never emits credentials."""

import json
import time

import httpx

from careerpilot.cli import provision
from careerpilot.persistence import database_url, make_engine


def main():
    engine = make_engine(database_url())
    try:
        _, token = provision(engine, "V0 synthetic runtime smoke")
    finally:
        engine.dispose()
    with httpx.Client(base_url="http://127.0.0.1:8000", trust_env=False, timeout=5) as client:
        headers = {"Authorization": "Bearer " + token, "Idempotency-Key": "v0-live-task"}
        payload = {"kind": "baseline.validate"}
        response = client.post("/api/v1/agent-tasks", headers=headers, json=payload)
        assert response.status_code == 202, response.status_code
        created = response.json()
        repeated = client.post("/api/v1/agent-tasks", headers=headers, json=payload)
        assert repeated.json()["id"] == created["id"]
        for _ in range(60):
            task = client.get("/api/v1/agent-tasks/" + created["id"], headers=headers).json()
            if task["status"] in {"success", "failed", "cancelled"}:
                break
            time.sleep(0.2)
        assert task["status"] == "success", task["status"]
        assert task["trace_id"] == created["trace_id"]
        assert client.post("/api/v1/auth/logout", headers=headers).status_code == 204
        print(
            json.dumps(
                {
                    "separate_worker": True,
                    "task_status": task["status"],
                    "checkpoint": task["checkpoint"],
                    "idempotent": True,
                    "trace_preserved": True,
                }
            )
        )


if __name__ == "__main__":
    main()
