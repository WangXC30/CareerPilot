"""Corrupt or expired source reviews cannot authorize imports or external access."""

import json
import time

import pytest
from sqlalchemy.orm import Session

from careerpilot.domain import DomainError
from careerpilot.persistence import User
from careerpilot.platform_models import RoleGrant, SourceRecord
from careerpilot.safety import require_source, source_scopes
from careerpilot.seed import seed


def reviewed_source(**changes):
    values = {
        "id": "user_import",
        "enabled": 1,
        "access_basis": "User-authorized local processing",
        "reviewed_by": "Local review",
        "reviewed_until": 200,
        "rate_per_minute": 1,
        "retention_days": 30,
        "scopes_json": '["import"]',
    }
    return SourceRecord(**(values | changes))


@pytest.mark.parametrize(
    "raw",
    [
        '"import send"',
        '{"import": false}',
        "null",
        "true",
        "3",
        "{",
        '["import", 1]',
        '["import", {}]',
        '["import", "import"]',
        '["import", " "]',
        '["import", "send all"]',
        json.dumps(["import"] + [f"scope{i}" for i in range(32)]),
        " " * 4001,
        "[" * 1100 + "]" * 1100,
    ],
)
def test_malformed_review_scopes_never_grant_access(raw):
    source = reviewed_source(scopes_json=raw)
    assert source_scopes(source) == []
    with pytest.raises(DomainError) as denied:
        require_source(source, "import", 100)
    assert denied.value.code == "SOURCE_SCOPE_DENIED"


@pytest.mark.parametrize(
    "changes",
    [
        {"enabled": 0},
        {"enabled": 2},
        {"reviewed_by": " \n"},
        {"access_basis": "\t"},
        {"reviewed_until": 100},
        {"reviewed_until": 99},
        {"rate_per_minute": 0},
        {"retention_days": 0},
    ],
)
def test_incomplete_or_expired_review_is_disabled(changes):
    with pytest.raises(DomainError) as denied:
        require_source(reviewed_source(**changes), "import", 100)
    assert denied.value.code == "SOURCE_DISABLED"


def test_valid_scopes_are_exact_and_do_not_mutate_the_review():
    source = reviewed_source(scopes_json='["import", "metadata"]')
    require_source(source, "import", 100)
    require_source(source, "metadata", 100)
    for operation in ("port", "send", "", "IMPORT"):
        with pytest.raises(DomainError):
            require_source(source, operation, 100)
    assert source.scopes_json == '["import", "metadata"]'


def make_operator(engine, user_id):
    with Session(engine) as db, db.begin():
        tenant = db.get(User, user_id).tenant_id
        db.get(RoleGrant, (user_id, tenant)).role = "operator"


def test_malformed_review_blocks_http_import_and_enable_without_breaking_health(system):
    client, engine, alice, _, _, (alice_id, _) = system
    make_operator(engine, alice_id)
    with Session(engine) as db, db.begin():
        row = db.get(SourceRecord, "user_import")
        row.scopes_json = '{"import": false}'
        row.enabled = 0
    denied = client.post("/api/v1/admin/sources/user_import/enable", headers=alice)
    assert denied.status_code == 403
    with Session(engine) as db:
        assert db.get(SourceRecord, "user_import").enabled == 0
    with Session(engine) as db, db.begin():
        db.get(SourceRecord, "user_import").enabled = 1
    denied = client.post(
        "/api/v1/jobs/import", headers=alice, json={"content": "岗位：Python 工程师"}
    )
    assert denied.status_code == 403
    assert denied.json()["code"] == "SOURCE_SCOPE_DENIED"
    assert client.get("/api/v1/jobs", headers=alice).json() == []
    with Session(engine) as db, db.begin():
        db.get(SourceRecord, "user_import").scopes_json = "{"
    health = client.get("/api/v1/admin/source-health", headers=alice)
    assert health.status_code == 200
    assert next(row for row in health.json() if row["id"] == "user_import")["scopes"] == []


def test_reseeding_does_not_renew_or_reenable_revoked_review(system):
    client, engine, alice, _, _, (alice_id, _) = system
    make_operator(engine, alice_id)
    expired = int(time.time()) - 1
    with Session(engine) as db, db.begin():
        row = db.get(SourceRecord, "user_import")
        row.enabled, row.reviewed_until = 0, expired
    seed(engine)
    assert client.post("/api/v1/admin/sources/user_import/enable", headers=alice).status_code == 403
    with Session(engine) as db:
        row = db.get(SourceRecord, "user_import")
        assert row.enabled == 0 and row.reviewed_until == expired


@pytest.mark.parametrize("source_id", ["github", "recruitment", "xiaohongshu", "douyin"])
def test_operator_cannot_enable_unreviewed_external_channels(system, source_id):
    client, engine, alice, _, _, (alice_id, _) = system
    make_operator(engine, alice_id)
    assert (
        client.post(f"/api/v1/admin/sources/{source_id}/enable", headers=alice).status_code == 403
    )
    with Session(engine) as db:
        assert db.get(SourceRecord, source_id).enabled == 0
