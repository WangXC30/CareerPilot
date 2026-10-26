import socket
import time

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.orm import Session

from careerpilot.domain import DomainError, Principal
from careerpilot.persistence import User
from careerpilot.platform_models import RoleGrant, SourceRecord
from careerpilot.ports import ScopedProfileAdapter
from careerpilot.safety import SecretVault, redact, require_source, scan_upload, validate_public_url
from careerpilot.seed import seed
from careerpilot.states import TRANSITIONS, validate_transition
from careerpilot.tools import ToolSpec, authorize_tool, issue_approval
from scripts.evaluate import evaluate


def principal_of(system):
    _, engine, _, _, _, (alice, _) = system
    with Session(engine) as db:
        return Principal(alice, db.get(User, alice).tenant_id)


def test_six_domain_adapter_has_no_cross_owner_access():
    class Reader:
        calls = []

        def read_for_owner(self, owner_key, object_id):
            self.calls.append((owner_key, object_id))
            return {"summary": "owned fact"}

    reader = Reader()
    adapter = ScopedProfileAdapter(
        reader, {("tenant-a", "alice"): "legacy-owner"}, {"memory-1": ("tenant-a", "alice")}
    )
    assert adapter.read(Principal("alice", "tenant-a"), "memory-1") == {"summary": "owned fact"}
    with pytest.raises(DomainError):
        adapter.read(Principal("bob", "tenant-a", "admin"), "memory-1")
    assert reader.calls == [("legacy-owner", "memory-1")]


def test_operator_can_only_see_aggregate_metadata(system):
    client, engine, alice, bob, _, (alice_id, _) = system
    assert client.get("/api/v1/admin/operations", headers=alice).status_code == 403
    with Session(engine) as db, db.begin():
        grant = db.get(RoleGrant, (alice_id, db.get(User, alice_id).tenant_id))
        grant.role = "operator"
    seed(engine)
    assert client.get("/api/v1/admin/source-health", headers=alice).status_code == 200
    assert (
        client.get("/api/v1/admin/operations", headers=alice).json()["private_content_visible"]
        is False
    )
    goal = client.post(
        "/api/v1/career-goals",
        headers={**bob, "Idempotency-Key": "private"},
        json={"title": "private-canary"},
    ).json()
    assert client.get(f"/api/v1/career-goals/{goal['id']}", headers=alice).status_code == 404
    assert "private-canary" not in client.get("/api/v1/admin/operations", headers=alice).text


def test_source_registry_fails_closed(system):
    _, engine, *_ = system
    seed(engine)
    with Session(engine) as db:
        require_source(db.get(SourceRecord, "user_import"), "import", int(time.time()))
        for source_id in ["missing", "github", "xiaohongshu", "douyin"]:
            with pytest.raises(DomainError):
                require_source(db.get(SourceRecord, source_id), "search", int(time.time()))
        with pytest.raises(DomainError):
            require_source(db.get(SourceRecord, "user_import"), "send", int(time.time()))


def test_consent_upload_and_redaction(system):
    client, _, alice, *_ = system
    assert (
        client.put("/api/v1/consents/external_model", headers=alice, json={"granted": True}).json()[
            "version"
        ]
        == 1
    )
    assert (
        client.put(
            "/api/v1/consents/external_model", headers=alice, json={"granted": False}
        ).json()["version"]
        == 2
    )
    assert client.get("/api/v1/consents", headers=alice).json()[0]["granted"] is False
    response = client.post(
        "/api/v1/uploads/scan?filename=resume.txt",
        headers={**alice, "Content-Type": "text/plain"},
        content="Python project",
    )
    assert response.status_code == 200 and response.json()["persisted"] is False
    for data, name in [
        (b"MZ executable", "evil.exe"),
        (b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE", "x.txt"),
        (b"\xff", "x.txt"),
    ]:
        with pytest.raises(DomainError):
            scan_upload(data, name)
    assert "[REDACTED]" in redact("Bearer abcdefghijklmnopqrstuvwxyz")


def test_secret_vault_requires_external_key(tmp_path):
    key_file = tmp_path / "vault.key"
    key_file.write_bytes(Fernet.generate_key())
    vault = SecretVault(str(key_file))
    encrypted = vault.encrypt("private-credential")
    assert "private-credential" not in encrypted
    assert vault.decrypt(encrypted) == "private-credential"


@pytest.mark.parametrize(
    "address", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "::ffff:127.0.0.1"]
)
def test_ssrf_blocks_resolved_private_addresses(address):
    def resolve(*args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", (address, 443))]

    with pytest.raises(DomainError):
        validate_public_url("https://allowed.example/data", {"allowed.example"}, resolve)


def test_approval_is_bound_to_identity_payload_and_once(system):
    _, engine, *_ = system
    principal = principal_of(system)
    spec = ToolSpec(
        "application.send",
        3,
        {
            "type": "object",
            "properties": {"resume": {"type": "string"}},
            "required": ["resume"],
            "additionalProperties": False,
        },
        frozenset({"student"}),
    )
    payload = {"resume": "version-1"}
    with Session(engine) as db, db.begin():
        approval = issue_approval(db, principal, spec.name, "job-1", payload)
    with pytest.raises(DomainError), Session(engine) as db, db.begin():
        authorize_tool(
            db, principal, spec, {"resume": "changed"}, target="job-1", approval_id=approval
        )
    with Session(engine) as db, db.begin():
        authorize_tool(db, principal, spec, payload, target="job-1", approval_id=approval)
    with pytest.raises(DomainError), Session(engine) as db, db.begin():
        authorize_tool(db, principal, spec, payload, target="job-1", approval_id=approval)
    with pytest.raises(DomainError), Session(engine) as db:
        authorize_tool(db, principal, spec, payload)


def test_state_policies_reject_unverified_skill_and_false_submission(system):
    assert set(TRANSITIONS) == {"goal", "skill", "learning_task", "application", "agent_task"}
    with pytest.raises(DomainError):
        validate_transition(
            "skill", "evidence_supported", "verified", {"type": "ai_inferred", "passed": True}
        )
    with pytest.raises(DomainError):
        validate_transition("application", "awaiting_user_submission", "submitted")
    validate_transition(
        "skill",
        "evidence_supported",
        "verified",
        {
            "type": "assessment_result",
            "passed": True,
            "reference": "test-1",
            "rubric_version": "v1",
            "valid": True,
        },
    )
    client, _, alice, bob, *_ = system
    row = client.post("/api/v1/state-records", headers=alice, json={"kind": "skill"}).json()
    path = f"/api/v1/state-records/{row['id']}/transition"
    headers = {**alice, "If-Match": '"1"'}
    assert (
        client.post(
            path, headers={**bob, "If-Match": '"1"'}, json={"target": "declared", "reason": "self"}
        ).status_code
        == 404
    )
    assert (
        client.post(
            path, headers=headers, json={"target": "declared", "reason": "self"}
        ).status_code
        == 200
    )
    assert (
        client.post(
            path, headers=headers, json={"target": "learning", "reason": "start"}
        ).status_code
        == 412
    )


def test_six_category_golden_set_and_budget():
    report = evaluate()
    categories = {r["category"] for r in report["results"]}
    assert {
        "jd",
        "resume",
        "skill_evidence",
        "learning",
        "hard_constraints",
        "feedback",
    } <= categories
    assert report["accuracy"] == report["citation_alignment"] == 1
    assert report["false_inclusion_rate"] == 0 and report["cost_micros"] == 0
    assert report["mode"] == "offline_deterministic_rules"
