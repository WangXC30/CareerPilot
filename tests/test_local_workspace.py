from concurrent.futures import ThreadPoolExecutor

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from careerpilot.accounts import process_deletion
from careerpilot.api import create_app
from careerpilot.cli import provision
from careerpilot.local_workspace import principal
from careerpilot.persistence import LocalWorkspace, SessionToken, User, make_engine
from careerpilot.platform_models import RoleGrant
from careerpilot.seed import seed


def database(tmp_path):
    url = f"sqlite:///{(tmp_path / 'personal.db').as_posix()}"
    config = Config("alembic.ini")
    config.attributes["database_url"] = url
    command.upgrade(config, "head")
    engine = make_engine(url)
    seed(engine)
    return url, engine


def test_local_workspace_needs_no_credentials_and_survives_restart(tmp_path):
    url, engine = database(tmp_path)
    try:
        with TestClient(create_app(url)) as client:
            workspace = client.get("/api/v1/local-workspace").json()
            assert workspace["requires_login"] is False
            assert "role" not in client.get("/api/v1/me").json()
            response = client.post(
                "/api/v1/career-goals",
                headers={"Idempotency-Key": "personal-goal"},
                json={"title": "本地成长目标"},
            )
            assert response.status_code == 201, response.text
            goal_id = response.json()["id"]
        with TestClient(create_app(url)) as restarted:
            assert restarted.get("/api/v1/local-workspace").json() == workspace
            assert restarted.get(f"/api/v1/career-goals/{goal_id}").status_code == 200
        with Session(engine) as db:
            assert db.scalar(select(func.count()).select_from(User)) == 1
            assert db.scalar(select(func.count()).select_from(SessionToken)) == 0
            assert db.scalar(select(func.count()).select_from(RoleGrant)) == 0
    finally:
        engine.dispose()


def test_existing_owner_is_reused_and_auth_and_operations_are_unavailable(tmp_path):
    url, engine = database(tmp_path)
    try:
        user_id, credential = provision(engine, "Existing personal records", role="admin")
        with TestClient(create_app(url)) as client:
            assert client.get("/api/v1/local-workspace").json()["user_id"] == user_id
            assert (
                "role"
                not in client.get(
                    "/api/v1/me", headers={"Authorization": f"Bearer {credential}"}
                ).json()
            )
            schema = client.get("/openapi.json").json()["paths"]
            for path in (
                "/api/v1/auth/login",
                "/api/v1/auth/register",
                "/api/v1/auth/logout",
                "/api/v1/admin/source-health",
                "/api/v1/account/sessions",
            ):
                assert path not in schema
                assert client.post(path, json={}).status_code == 404
            assert "security" not in schema["/api/v1/career-goals"]["get"]
    finally:
        engine.dispose()


def test_local_boundary_rejects_cross_origin_and_untrusted_hosts(tmp_path):
    url, engine = database(tmp_path)
    try:
        with TestClient(create_app(url)) as client:
            for headers in (
                {"Origin": "https://example.com"},
                {"Origin": "null"},
                {"Host": "example.com"},
                {"Sec-Fetch-Site": "cross-site"},
            ):
                assert client.get("/api/v1/local-workspace", headers=headers).status_code == 403
            assert (
                client.get(
                    "/api/v1/local-workspace", headers={"Origin": "http://testserver"}
                ).status_code
                == 200
            )
    finally:
        engine.dispose()


def test_ambiguous_history_is_not_automatically_merged(tmp_path):
    url, engine = database(tmp_path)
    try:
        provision(engine, "One history")
        provision(engine, "Another history")
        with TestClient(create_app(url)) as client:
            response = client.get("/api/v1/local-workspace")
            assert response.status_code == 409
            assert response.json()["code"] == "LOCAL_WORKSPACE_AMBIGUOUS"
        with Session(engine) as db:
            assert db.scalar(select(func.count()).select_from(User)) == 2
            assert db.scalar(select(func.count()).select_from(LocalWorkspace)) == 0
    finally:
        engine.dispose()


def test_parallel_first_open_uses_one_workspace(tmp_path):
    _, engine = database(tmp_path)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            owners = list(pool.map(lambda _: principal(engine), range(2)))
        assert owners[0] == owners[1]
        with Session(engine) as db:
            assert db.scalar(select(func.count()).select_from(User)) == 1
    finally:
        engine.dispose()


def test_clear_local_data_waits_for_worker_then_creates_fresh_workspace(tmp_path):
    url, engine = database(tmp_path)
    try:
        with TestClient(create_app(url)) as client:
            old = client.get("/api/v1/local-workspace").json()["user_id"]
            assert (
                client.post(
                    "/api/v1/local-workspace/reset", json={"confirmation": "wrong"}
                ).status_code
                == 422
            )
            response = client.post(
                "/api/v1/local-workspace/reset", json={"confirmation": "CLEAR LOCAL DATA"}
            )
            assert response.status_code == 202, response.text
            assert client.get("/api/v1/local-workspace").status_code == 409
            assert process_deletion(engine)
            assert client.get("/api/v1/local-workspace").json()["user_id"] != old
            stale = client.post(
                "/api/v1/career-goals",
                headers={"X-Workspace-Id": old, "Idempotency-Key": "old-tab"},
                json={"title": "stale input"},
            )
            assert stale.status_code == 409
            assert stale.json()["code"] == "LOCAL_WORKSPACE_CHANGED"
            assert client.get("/api/v1/career-goals").json() == []
    finally:
        engine.dispose()
