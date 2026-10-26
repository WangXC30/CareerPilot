"""Shared simulated-provider contract for database persistence and browser tests."""

import os
import secrets
from unittest.mock import patch

from cryptography.fernet import Fernet

from careerpilot import github_binding
from careerpilot.cli import provision
from careerpilot.domain import DomainError


class Provider:
    def __init__(self):
        self.token = secrets.token_urlsafe(32)
        self.scope = ""
        self.calls = []
        self.fail_revoke = False

    def __call__(self, host, method, path, **kwargs):
        self.calls.append((host, method, path))
        if path == "/login/oauth/access_token":
            assert kwargs["body"]["code_verifier"]
            return {"access_token": self.token, "scope": self.scope, "token_type": "bearer"}
        if method == "DELETE":
            if self.fail_revoke:
                raise DomainError("GITHUB_UNAVAILABLE", "模拟撤销故障", 502)
            return {}
        assert kwargs["token"] == self.token
        if path == "/user":
            return {"id": 123, "login": "public-student", "email": "not-retained@example.test"}
        return [
            {"name": "demo", "private": False, "owner": {"id": 123}, "description": "not retained"},
            {"name": "private-secret", "private": True, "owner": {"id": 123}},
            {"name": "foreign", "private": False, "owner": {"id": 456}},
        ]


def configured(state):
    return patch.dict(os.environ, state["env"])


def checked(response, status=200):
    assert response.status_code == status, response.text
    return response.json()


def create_history(client, engine):
    _, token = provision(engine, "Synthetic profile history")
    headers = {"Authorization": "Bearer " + token}
    state = {
        "headers": headers,
        "env": dict(
            zip(
                ("CAREERPILOT_" + key for key in github_binding.CONFIG),
                (
                    "contract-client",
                    secrets.token_urlsafe(24),
                    "http://127.0.0.1:8000/app/github-callback.html",
                    Fernet.generate_key().decode(),
                ),
                strict=True,
            )
        ),
    }
    goal = checked(
        client.post(
            "/api/v1/career-goals",
            headers={**headers, "Idempotency-Key": "history-contract"},
            json={"title": "Before"},
        ),
        201,
    )
    checked(
        client.patch(
            f"/api/v1/career-goals/{goal['id']}",
            headers={**headers, "If-Match": '"1"'},
            json={"title": "After"},
        )
    )
    with configured(state):
        state["pending"] = checked(
            client.post("/api/v1/github-binding/start", headers=headers, json={"confirmed": True})
        )
    return state


def verify_history(client, state):
    headers = state["headers"]
    history = checked(client.get("/api/v1/profile-timeline", headers=headers))
    change = next(e for e in history["events"] if e["kind"] == "goal_history" and e["version"] == 2)
    assert change["changes"]["title"] == {"before": "Before", "after": "After"}
    fake = Provider()
    with configured(state), patch.object(github_binding, "remote", fake):
        body = {
            "confirmed": True,
            "state": state["pending"]["state"],
            "code": "restored-synthetic-code",
        }
        checked(client.post("/api/v1/github-binding/finish", headers=headers, json=body))
        assert (
            client.post("/api/v1/github-binding/finish", headers=headers, json=body).status_code
            == 409
        )
        result = checked(
            client.post("/api/v1/github-binding/sync", headers=headers, json={"confirmed": True})
        )
        assert [r["name"] for r in result["binding"]["repositories"]] == ["demo"]
        exported = client.get("/api/v1/account/export", headers=headers).text
        assert "github_secret" not in exported and fake.token not in exported
        checked(
            client.post("/api/v1/github-binding/unbind", headers=headers, json={"confirmed": True})
        )
        assert checked(client.get("/api/v1/github-binding", headers=headers))["binding"] is None
    print(
        "Profile history and encrypted pending OAuth persistence/consume-once/"
        "sync/purge passed (simulated provider)"
    )
