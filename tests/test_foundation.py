import hashlib
import time

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import func, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from careerpilot.api import create_app
from careerpilot.persistence import AuditEvent, Base, CareerGoal, SessionToken, User
from scripts.check_traceability import build


def create(client, headers, title="后端开发", key="request-1"):
    return client.post(
        "/api/v1/career-goals", headers={**headers, "Idempotency-Key": key}, json={"title": title}
    )


def test_health_and_openapi(system):
    client, *_ = system
    assert client.get("/health/live").status_code == 200
    assert client.get("/health/ready").json()["schema_revision"] == "0001"
    schema = client.get("/openapi.json").json()
    assert schema["openapi"].startswith("3.1")
    operation = schema["paths"]["/api/v1/career-goals"]["post"]
    assert operation["security"] == [{"HTTPBearer": []}]
    assert "201" in operation["responses"]


def test_unmigrated_database_not_ready(tmp_path):
    with TestClient(create_app(f"sqlite:///{(tmp_path / 'empty.db').as_posix()}")) as client:
        assert client.get("/health/ready").status_code == 503
        assert client.get("/health/live").status_code == 200


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer invalid"}])
def test_requires_authenticated_identity(system, headers):
    client, *_ = system
    response = client.get("/api/v1/me", headers=headers)
    assert response.status_code == 401
    assert response.json()["trace_id"] == response.headers["X-Trace-ID"]


def test_isolation_read_write_and_list(system):
    client, _, alice, bob, _, _ = system
    response = create(client, alice)
    assert response.status_code == 201, response.text
    goal_id = response.json()["id"]
    assert client.get(f"/api/v1/career-goals/{goal_id}", headers=bob).status_code == 404
    assert (
        client.patch(
            f"/api/v1/career-goals/{goal_id}",
            headers={**bob, "If-Match": '"1"'},
            json={"title": "hijacked"},
        ).status_code
        == 404
    )
    assert client.get("/api/v1/career-goals", headers=bob).json() == []
    assert len(client.get("/api/v1/career-goals", headers=alice).json()) == 1


def test_idempotency_conflict_and_audit(system):
    client, engine, alice, bob, _, _ = system
    first = create(client, alice).json()
    assert create(client, alice).json() == first
    assert create(client, alice, title="different").status_code == 409
    assert create(client, bob).json()["id"] != first["id"]
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(CareerGoal)) == 2
        assert db.scalar(select(func.count()).select_from(AuditEvent)) == 2


def test_optimistic_lock_and_redacted_validation(system):
    client, engine, alice, _, _, _ = system
    goal_id = create(client, alice).json()["id"]
    path = f"/api/v1/career-goals/{goal_id}"
    headers = {**alice, "If-Match": '"1"'}
    assert client.patch(path, headers=headers, json={"title": "AI 工程师"}).json()["version"] == 2
    assert client.patch(path, headers=headers, json={"title": "stale"}).status_code == 412
    assert client.get(path, headers=alice).json()["title"] == "AI 工程师"
    response = client.patch(path, headers=headers, json={"title": "x", "user_id": "private-canary"})
    assert response.status_code == 422
    assert "private-canary" not in response.text
    with Session(engine) as db:
        events = db.scalars(select(AuditEvent).order_by(AuditEvent.created_at)).all()
        assert [e.revision for e in events] == [1, 2]


def test_expired_and_revoked_tokens(system):
    client, engine, alice, bob, _, _ = system
    assert client.post("/api/v1/auth/logout", headers=alice).status_code == 204
    assert client.get("/api/v1/me", headers=alice).status_code == 401
    with Session(engine) as db, db.begin():
        token = db.get(SessionToken, hashlib.sha256(bob["Authorization"][7:].encode()).hexdigest())
        token.expires_at = int(time.time()) - 1
    assert client.get("/api/v1/me", headers=bob).status_code == 401


def test_database_constraints(system):
    _, engine, _, _, _, (alice_id, bob_id) = system
    with Session(engine) as db:
        alice, bob = db.get(User, alice_id), db.get(User, bob_id)
        alice_tenant, bob_tenant = alice.tenant_id, bob.tenant_id
    with pytest.raises(IntegrityError), Session(engine) as db, db.begin():
        db.add(CareerGoal(user_id=alice_id, tenant_id=bob_tenant, title="bad owner"))
    with pytest.raises(IntegrityError), Session(engine) as db, db.begin():
        db.add_all(
            [
                CareerGoal(
                    user_id=alice_id, tenant_id=alice_tenant, title="active", status="active"
                )
                for _ in range(2)
            ]
        )
    with pytest.raises(IntegrityError), Session(engine) as db, db.begin():
        db.add(
            CareerGoal(user_id=alice_id, tenant_id=alice_tenant, title="bad state", status="bad")
        )


def test_migration_roundtrip_and_metadata(system):
    _, engine, _, _, config, _ = system
    assert set(Base.metadata.tables) <= set(inspect(engine).get_table_names())
    command.check(config)
    command.downgrade(config, "base")
    assert inspect(engine).get_table_names() == ["alembic_version"]
    command.upgrade(config, "head")
    command.check(config)


def test_restart_persistence_and_pagination(system):
    client, engine, alice, _, config, _ = system
    ids = {create(client, alice, key=f"key-{i}").json()["id"] for i in range(3)}
    with TestClient(create_app(config.attributes["database_url"])) as restarted:
        first = restarted.get("/api/v1/career-goals?limit=2", headers=alice).json()
        second = restarted.get(
            f"/api/v1/career-goals?limit=2&cursor={first[-1]['id']}", headers=alice
        ).json()
        assert {g["id"] for g in first + second} == ids
        assert len(first) == 2 and len(second) == 1
        assert restarted.get("/api/v1/career-goals?limit=101", headers=alice).status_code == 422


def test_requirement_traceability():
    assert build()["counts"] == {"FR-": 110, "NFR-": 18, "E2E-": 18}
