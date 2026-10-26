"""OAuth provider is simulated; real GitHub application acceptance is separate."""

import json
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, urlsplit

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.orm import Session

from careerpilot import accounts, github_binding
from careerpilot.domain import DomainError, Principal
from careerpilot.growth_models import GrowthRecord, GrowthRevision
from careerpilot.persistence import User
from scripts.profile_history_contract import Provider
from tests.test_v1_release import PROFILE, exam, ok, setup_plan, version


@pytest.fixture
def provider(monkeypatch):
    for name, value in zip(
        github_binding.CONFIG,
        (
            "test-client",
            secrets.token_urlsafe(24),
            "http://127.0.0.1:8000/app/github-callback.html",
            Fernet.generate_key().decode(),
        ),
        strict=True,
    ):
        monkeypatch.setenv("CAREERPILOT_" + name, value)
    fake = Provider()
    monkeypatch.setattr(github_binding, "remote", fake)
    return fake


def post(client, headers, action, **body):
    return client.post(
        "/api/v1/github-binding/" + action, headers=headers, json={"confirmed": True, **body}
    )


def bind(client, headers):
    started = ok(post(client, headers, "start"))
    return started, ok(
        post(client, headers, "finish", state=started["state"], code="synthetic-code")
    )


def test_oauth_pkce_owner_state_replay_and_encrypted_export(system, provider):
    client, engine, alice, bob, *_ = system
    started = ok(post(client, alice, "start"))
    query = parse_qs(urlsplit(started["authorization_url"]).query, keep_blank_values=True)
    assert query["scope"] == [""] and query["code_challenge_method"] == ["S256"]
    assert len(query["code_challenge"][0]) == 43
    assert post(client, bob, "finish", state=started["state"], code="fake").status_code == 409
    assert not provider.calls
    result = ok(post(client, alice, "finish", state=started["state"], code="fake"))
    assert result["binding"]["scopes"] == []
    assert post(client, alice, "finish", state=started["state"], code="fake").status_code == 409
    assert post(client, alice, "start").status_code == 409
    exported = ok(client.get("/api/v1/account/export", headers=alice))
    assert "github_secret" not in json.dumps(exported)
    assert provider.token not in json.dumps(exported)
    assert "email" not in json.dumps(result)
    with Session(engine) as db:
        row = db.scalar(select(GrowthRecord).where(GrowthRecord.kind == "github_secret"))
        assert provider.token not in row.payload_json
        assert db.scalar(select(GrowthRevision).where(GrowthRevision.record_id == row.id)) is None
    assert ok(client.get("/api/v1/github-binding", headers=bob))["binding"] is None


@pytest.mark.parametrize("scope", ["repo", "public_repo", "read:user"])
def test_oauth_rejects_inherited_broader_scopes(system, provider, scope):
    client, _, alice, *_ = system
    provider.scope = scope
    started = ok(post(client, alice, "start"))
    result = post(client, alice, "finish", state=started["state"], code="fake")
    assert result.status_code == 409 and result.json()["code"] == "GITHUB_EXCESS_SCOPE"
    assert provider.calls[-1][1] == "DELETE"
    assert not any(path == "/user" for _, _, path in provider.calls)
    assert post(client, alice, "finish", state=started["state"], code="fake").status_code == 409
    assert ok(client.get("/api/v1/github-binding", headers=alice))["binding"] is None


def test_expired_or_replaced_state_never_calls_provider(system, provider, monkeypatch):
    client, _, alice, *_ = system
    first = ok(post(client, alice, "start"))
    second = ok(post(client, alice, "start"))
    assert post(client, alice, "finish", state=first["state"], code="fake").status_code == 409
    now = time.time()
    monkeypatch.setattr(github_binding.time, "time", lambda: now + 601)
    assert post(client, alice, "finish", state=second["state"], code="fake").status_code == 409
    assert not provider.calls


def test_public_sync_unbind_purges_and_never_accesses_again(system, provider):
    client, engine, alice, *_ = system
    bind(client, alice)
    result = ok(post(client, alice, "sync"))
    assert result["binding"]["repositories"] == [
        {"name": "demo", "url": "https://github.com/public-student/demo"}
    ]
    assert "private-secret" not in json.dumps(
        ok(client.get("/api/v1/account/export", headers=alice))
    )
    assert post(client, alice, "sync").status_code == 429
    provider.fail_revoke = True
    assert ok(post(client, alice, "unbind"))["remote_revoked"] is False
    count = len(provider.calls)
    assert post(client, alice, "sync").status_code == 409
    assert len(provider.calls) == count
    with Session(engine) as db:
        assert not db.scalars(select(GrowthRecord).where(GrowthRecord.kind.like("github_%"))).all()
    assert "demo" not in json.dumps(ok(client.get("/api/v1/account/export", headers=alice)))


def test_missing_configuration_and_key_loss_still_allow_cleanup(system, provider, monkeypatch):
    client, _, alice, *_ = system
    bind(client, alice)
    monkeypatch.setenv("CAREERPILOT_OAUTH_ENCRYPTION_KEY", Fernet.generate_key().decode())
    assert post(client, alice, "sync").status_code == 409
    monkeypatch.delenv("CAREERPILOT_GITHUB_CLIENT_SECRET")
    assert ok(post(client, alice, "unbind"))["cleared"] is True
    assert post(client, alice, "start").status_code == 503
    assert not ok(client.get("/api/v1/github-binding", headers=alice))["configured"]


def test_delete_request_immediately_clears_oauth_before_worker(system, provider):
    client, engine, alice, _, _, ids = system
    bind(client, alice)
    with Session(engine) as db, db.begin():
        user = db.get(User, ids[0])
        principal = Principal(user.id, user.tenant_id)
        accounts.request_deletion(db, principal, "test")
    with Session(engine) as db:
        assert not db.scalars(select(GrowthRecord).where(GrowthRecord.kind.like("github_%"))).all()
        with pytest.raises(DomainError, match="正在删除"):
            github_binding.sync(db, principal, "test")
    assert post(client, alice, "sync").status_code == 401
    assert accounts.process_deletion(engine)


def test_timeline_owned_paginated_revision_changes_and_evidence(system):
    client, _, alice, bob, *_ = system
    setup_plan(system)
    ok(
        client.put(
            "/api/v1/profile", headers=version(alice, 1), json={**PROFILE, "weekly_minutes": 300}
        )
    )
    _, response = exam(client, alice)
    ok(response)
    goal = ok(
        client.post(
            "/api/v1/career-goals",
            headers={**alice, "Idempotency-Key": "history-goal"},
            json={"title": "First"},
        ),
        201,
    )
    ok(
        client.patch(
            f"/api/v1/career-goals/{goal['id']}",
            headers=version(alice, 1),
            json={"title": "Second"},
        )
    )
    rows, cursor = [], None
    while True:
        page = ok(
            client.get(
                "/api/v1/profile-timeline",
                headers=alice,
                params={"limit": 2, **({"cursor": json.dumps(cursor)} if cursor else {})},
            )
        )
        rows.extend(page["events"])
        cursor = page["next"]
        if not cursor:
            break
    assert len({row["key"] for row in rows}) == len(rows)
    assert {row["kind"] for row in rows} >= {"profile", "plan", "evidence", "goal_history"}
    change = next(row for row in rows if row["kind"] == "profile" and row["version"] == 2)
    assert change["changes"]["weekly_minutes"] == {"before": 600, "after": 300}
    goal_change = next(row for row in rows if row["kind"] == "goal_history" and row["version"] == 2)
    assert goal_change["changes"]["title"] == {"before": "First", "after": "Second"}
    assert ok(client.get("/api/v1/profile-timeline", headers=bob))["events"] == []
    assert (
        client.get("/api/v1/profile-timeline", headers=alice, params={"cursor": "{}"}).status_code
        == 422
    )


@pytest.mark.parametrize("operation", ["unbind", "deletion"])
def test_inflight_sync_cannot_repopulate_after_unbind_or_delete(
    system, provider, monkeypatch, operation
):
    client, engine, alice, *_ = system
    bind(client, alice)
    entered, release, requested = threading.Event(), threading.Event(), threading.Event()

    def slow_remote(host, method, path, **kwargs):
        if path.startswith("/user/repos"):
            entered.set()
            assert release.wait(4)
        return provider(host, method, path, **kwargs)

    def clear():
        requested.set()
        if operation == "unbind":
            return post(client, alice, "unbind")
        return client.post(
            "/api/v1/account/deletion", headers=alice, json={"confirmation": "DELETE MY ACCOUNT"}
        )

    monkeypatch.setattr(github_binding, "remote", slow_remote)
    with ThreadPoolExecutor(max_workers=2) as pool:
        syncing = pool.submit(post, client, alice, "sync")
        try:
            assert entered.wait(3)
            clearing = pool.submit(clear)
            assert requested.wait(2)
        finally:
            release.set()
        ok(syncing.result(timeout=5))
        ok(clearing.result(timeout=5), 200 if operation == "unbind" else 202)
    with Session(engine) as db:
        assert not db.scalars(select(GrowthRecord).where(GrowthRecord.kind.like("github_%"))).all()
    calls = len(provider.calls)
    assert post(client, alice, "sync").status_code in {401, 409}
    assert len(provider.calls) == calls


@pytest.mark.parametrize(
    "status,body,content_type",
    [
        (302, b"{}", "application/json"),
        (403, b"{}", "application/json"),
        (200, b"not-json", "application/json"),
        (200, b"{}", "text/html"),
        (200, b"x" * (2 * 1024 * 1024 + 1), "application/json"),
    ],
)
def test_provider_transport_rejects_redirect_errors_and_unbounded_content(
    monkeypatch, status, body, content_type
):
    from types import SimpleNamespace

    closed, sent = [], []
    response = SimpleNamespace(
        status=status,
        getheader=lambda name, default="": content_type if name == "Content-Type" else default,
        read=lambda size: body[:size],
    )
    connection = SimpleNamespace(
        request=lambda *args: sent.append(args),
        getresponse=lambda: response,
        close=lambda: closed.append(True),
    )
    monkeypatch.setattr(
        github_binding, "validate_public_url", lambda *args: ("api.github.com", {"93.184.216.34"})
    )
    monkeypatch.setattr(
        github_binding.http.client, "HTTPSConnection", lambda *args, **kwargs: connection
    )
    monkeypatch.setattr(
        github_binding.socket, "create_connection", lambda *args, **kwargs: object()
    )
    monkeypatch.setattr(
        github_binding.ssl,
        "create_default_context",
        lambda: SimpleNamespace(wrap_socket=lambda *args, **kwargs: object()),
    )
    with pytest.raises(DomainError):
        github_binding.remote("api.github.com", "GET", "/user", token="synthetic-token")
    assert len(sent) == 1 and closed == [True]


def test_timeline_retains_revocation_reason(system):
    client, _, alice, *_ = system
    setup_plan(system)
    _, response = exam(client, alice)
    evidence = ok(response)["evidence"]
    ok(
        client.post(
            f"/api/v1/skill-evidence/{evidence['id']}/revoke",
            headers=alice,
            json={"confirmed": True, "reason": "本人撤销"},
        )
    )
    history = ok(client.get("/api/v1/profile-timeline", headers=alice))["events"]
    assert next(e for e in history if e["kind"] == "evidence")["revoked"] is True
    assert next(e for e in history if e["kind"] == "revocation")["reason"] == "本人撤销"


def test_timeline_links_accepted_preview_and_profile_restore(system):
    from tests.test_v3_study import preview
    from tests.test_v3_workflow import decide

    client, _, alice, *_ = system
    _, plan = setup_plan(system)
    proposal = ok(preview(client, alice, plan), 201)
    ok(decide(client, alice, proposal))
    ok(
        client.put(
            "/api/v1/profile", headers=version(alice, 1), json={**PROFILE, "weekly_minutes": 300}
        )
    )
    ok(
        client.post(
            "/api/v1/profile/restore",
            headers=version(alice, 2),
            json={"target_version": 1, "confirmed": True},
        )
    )
    history = ok(client.get("/api/v1/profile-timeline", headers=alice))["events"]
    changed = next(e for e in history if e["kind"] == "plan" and e["version"] == 3)
    assert changed["preview_id"] == proposal["id"] and proposal["reason"] in changed["reason"]
    restored = next(e for e in history if e["kind"] == "profile" and e["version"] == 3)
    assert restored["reason"] == "本人恢复历史版本 v1"
