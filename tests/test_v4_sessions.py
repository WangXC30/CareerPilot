"""Session management never returns credentials and respects owner boundaries."""

import hashlib
import time

from sqlalchemy.orm import Session

from careerpilot import accounts
from careerpilot.persistence import SessionToken, User


def test_session_isolation_and_immediate_revocation(system):
    client, engine, alice, bob, _, ids = system
    with Session(engine) as db:
        user = db.get(User, ids[0])
        raw = accounts.session_token(db, user.id, user.tenant_id)["access_token"]
        db.commit()
    other = {"Authorization": "Bearer " + raw}
    response = client.get("/api/v1/account/sessions", headers=alice)
    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 2
    assert sum(row["current"] for row in rows) == 1
    assert raw not in response.text
    assert hashlib.sha256(raw.encode()).hexdigest() not in response.text
    target = next(row for row in rows if not row["current"])["id"]
    endpoint = "/api/v1/account/sessions/" + target
    assert client.delete(endpoint, headers=bob).status_code == 404
    assert client.get("/api/v1/account/export", headers=other).status_code == 200
    assert client.delete(endpoint, headers=alice).status_code == 200
    assert client.get("/api/v1/account/export", headers=other).status_code == 401
    assert client.delete(endpoint, headers=alice).status_code == 404
    assert client.get("/api/v1/account/export", headers=alice).status_code == 200


def test_current_session_can_be_revoked(system):
    client, _, alice, bob, _, _ = system
    rows = client.get("/api/v1/account/sessions", headers=alice).json()
    assert rows[0]["current"]
    assert (
        client.delete("/api/v1/account/sessions/" + rows[0]["id"], headers=alice).status_code == 200
    )
    assert client.get("/api/v1/account/sessions", headers=alice).status_code == 401
    assert client.get("/api/v1/account/sessions", headers=bob).status_code == 200


def test_expired_sessions_hidden_and_anonymous_denied(system):
    client, engine, alice, _, _, ids = system
    with Session(engine) as db:
        user = db.get(User, ids[0])
        raw = accounts.session_token(db, user.id, user.tenant_id)["access_token"]
        db.flush()
        row = db.get(SessionToken, hashlib.sha256(raw.encode()).hexdigest())
        row.expires_at = int(time.time()) - 1
        db.commit()
    assert len(client.get("/api/v1/account/sessions", headers=alice).json()) == 1
    assert client.get("/api/v1/account/sessions").status_code == 401
    assert client.delete("/api/v1/account/sessions/unknown").status_code == 401


def test_device_labels_are_coarse_and_new_client_is_flagged(system):
    client, engine, alice, _, _, ids = system
    with Session(engine) as db:
        user = db.get(User, ids[0])
        raw = accounts.session_token(
            db, user.id, user.tenant_id, "Mozilla Windows Chrome/1 private-fingerprint"
        )["access_token"]
        db.commit()
    rows = client.get("/api/v1/account/sessions", headers=alice).json()
    current = next(r for r in rows if r["device_label"] == "Chrome / Windows")
    assert current["risk_notice"]
    assert current["created_at"]
    assert "private-fingerprint" not in str(rows)
    assert accounts.device_description("<script>alert(1)</script>") == "未知浏览器 / 未知系统"
    assert raw not in str(rows)
