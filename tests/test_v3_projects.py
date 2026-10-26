"""Project producer contracts; simulated verdicts are separate from the Docker drill."""

import hashlib
import json

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from careerpilot import accounts, growth, learning, project_evidence, projects, resumes
from careerpilot.domain import DomainError, Principal
from careerpilot.persistence import SessionToken
from scripts.project_sandbox import sandbox_command
from tests.test_v1_release import PROFILE, ok, version
from tests.test_v2_opportunities import job

SOLUTION = """def solve(records):
    result = {}
    for item in records:
        city, salary = item.get("city"), item.get("salary")
        if isinstance(city, str) and city and type(salary) is int and salary >= 0:
            record = result.setdefault(city, {"count": 0, "total_salary": 0})
            record["count"] += 1
            record["total_salary"] += salary
    return result
"""


def principal_for(db, headers):
    digest = hashlib.sha256(headers["Authorization"].removeprefix("Bearer ").encode()).hexdigest()
    token = db.get(SessionToken, digest)
    return Principal(token.user_id, token.tenant_id)


def create(client, headers, key="project-1"):
    # Reuse the actual API's profile validation rather than seeding private tables.
    ok(client.put("/api/v1/profile", headers=version(headers, 0), json=PROFILE))
    j = job(client, headers)
    return ok(
        client.post(
            "/api/v1/projects",
            headers={**headers, "Idempotency-Key": key},
            json={"job_id": j["id"], "template_id": projects.TEMPLATE["id"], "confirmed": True},
        ),
        201,
    )


def submit(client, headers, p, code=SOLUTION, key="code-1"):
    return client.post(
        f"/api/v1/projects/{p['id']}/submissions",
        headers={**version(headers, p["version"]), "Idempotency-Key": key},
        json={"code": code},
    )


def verdict(status="passed"):
    return {
        "status": status,
        "test_version": projects.TEMPLATE["test_version"],
        "image_id": "sha256:" + "a" * 64,
        "results": [
            {"id": c[0], "passed": status == "passed", "reason": "CONTRACT_FIXTURE"}
            for c in projects.CASES
        ]
        if status in {"passed", "user_failed"}
        else [],
    }


def finish(engine, status="passed"):
    with Session(engine) as db, db.begin():
        work = projects.claim(db)
        assert work
        r = work["receipt"]
        return projects.complete(db, r["id"], r["lease_token"], verdict(status))


def test_private_queue_ownership_idempotency_and_no_verdict_api(system):
    client, engine, alice, bob, *_ = system
    p = create(client, alice)
    response = submit(client, alice, p)
    artifact = ok(response, 201)
    assert ok(submit(client, alice, p), 201)["id"] == artifact["id"]
    assert submit(client, alice, p, code="def solve(x): return None").status_code == 409
    assert client.get(f"/api/v1/projects/{p['id']}", headers=bob).status_code == 404
    assert submit(client, bob, p, key="bob").status_code == 404
    assert (
        client.post(
            f"/api/v1/project-assessments/{artifact['assessment_id']}/complete",
            headers=alice,
            json=verdict(),
        ).status_code
        == 404
    )
    with Session(engine) as db:
        principal = principal_for(db, alice)
        assert project_evidence.current(db, principal) == []
    with Session(engine) as db, db.begin():
        projects.claim(db)
    public = ok(client.get(f"/api/v1/projects/{p['id']}", headers=alice))
    assert "lease_token" not in public["assessments"][0]
    assert '"lease_token":' not in json.dumps(
        ok(client.get("/api/v1/account/export", headers=alice))
    )


def test_passed_producer_fact_and_withdrawal(system):
    client, engine, alice, bob, *_ = system
    p = create(client, alice)
    artifact = ok(submit(client, alice, p), 201)
    receipt = finish(engine)
    with Session(engine) as db:
        principal = principal_for(db, alice)
        evidence = project_evidence.current(db, principal)
        assert evidence[0]["artifact_hash"] == artifact["content_hash"]
        assert evidence[0]["assessment_version"] == receipt["version"]
        assert all(s["status"] != "verified" for s in learning.snapshots(db, principal))
    draft = ok(client.get(f"/api/v1/project-submissions/{artifact['id']}/statement", headers=alice))
    assert "性能指标" in draft["unknown"]
    f = ok(
        client.post(
            f"/api/v1/project-submissions/{artifact['id']}/resume-fact",
            headers={**alice, "Idempotency-Key": "fact-1"},
            json={"confirmed": True},
        ),
        201,
    )
    with Session(engine) as db:
        assert resumes.fact(db, principal_for(db, alice), f["id"])["source_status"] == "current"
        assert (
            f["id"] in project_evidence.current(db, principal_for(db, alice))[0]["resume_fact_ids"]
        )
    assert (
        client.get(
            f"/api/v1/project-submissions/{artifact['id']}/statement", headers=bob
        ).status_code
        == 404
    )
    ok(
        client.post(
            f"/api/v1/project-submissions/{artifact['id']}/withdraw",
            headers=version(alice, 1),
            json={"confirmed": True},
        )
    )
    with Session(engine) as db:
        principal = principal_for(db, alice)
        assert project_evidence.current(db, principal) == []
        assert resumes.fact(db, principal, f["id"])["source_status"] == "source_withdrawn"


@pytest.mark.parametrize("status", ["user_failed", "system_error", "timeout", "manual_review"])
def test_failure_categories_and_no_false_evidence(system, status):
    client, engine, alice, *_ = system
    p = create(client, alice)
    ok(submit(client, alice, p), 201)
    finish(engine, status)
    with Session(engine) as db:
        principal = principal_for(db, alice)
        ledger = learning.ledger(db, principal)
        assert len(ledger) == int(status == "user_failed")
        assert project_evidence.current(db, principal) == []


def test_failed_resubmission_blocks_old_pass_system_outage_preserves_it(system):
    client, engine, alice, *_ = system
    p = create(client, alice)
    ok(submit(client, alice, p), 201)
    finish(engine)
    for index, status in enumerate(["system_error", "user_failed"]):
        p = ok(client.get(f"/api/v1/projects/{p['id']}", headers=alice))
        ok(submit(client, alice, p, key=f"retry-{index}"), 201)
        finish(engine, status)
        with Session(engine) as db:
            assert bool(project_evidence.current(db, principal_for(db, alice))) == (
                status == "system_error"
            )


def test_stale_lease_and_superseded_submission_cannot_write_pass(system):
    client, engine, alice, *_ = system
    p = create(client, alice)
    ok(submit(client, alice, p), 201)
    with Session(engine) as db, db.begin():
        first = projects.claim(db, now=100)["receipt"]
    with Session(engine) as db, db.begin():
        second = projects.claim(db, now=221)["receipt"]
        assert second["lease_token"] != first["lease_token"]
    with Session(engine) as db, db.begin(), pytest.raises(DomainError, match="租约"):
        projects.complete(db, first["id"], first["lease_token"], verdict(), now=222)
    p = ok(client.get(f"/api/v1/projects/{p['id']}", headers=alice))
    ok(submit(client, alice, p, key="newer"), 201)
    with Session(engine) as db, db.begin():
        result = projects.complete(db, second["id"], second["lease_token"], verdict(), now=222)
        assert result["status"] == "cancelled"
        assert not learning.ledger(db, principal_for(db, alice))


def test_expired_attempt_budget_and_delete(system):
    client, engine, alice, *_ = system
    p = create(client, alice)
    ok(submit(client, alice, p), 201)
    with Session(engine) as db, db.begin():
        for now in (0, 121, 242):
            assert projects.claim(db, now=now)
        assert projects.claim(db, now=363) is None
        row = db.scalar(
            select(growth.GrowthRecord).where(growth.GrowthRecord.kind == "project_assessment")
        )
        assert projects.payload(row)["status"] == "system_error"
    with Session(engine) as db, db.begin():
        principal = principal_for(db, alice)
        accounts.request_deletion(db, principal, "delete-project-test")
    accounts.process_deletion(engine)
    assert client.get("/api/v1/projects", headers=alice).status_code == 401


def test_sandbox_command_has_no_mounts_or_secrets():
    command = sandbox_command("sha256:" + "a" * 64, "test")
    for flag in [
        "--network",
        "--read-only",
        "--user",
        "--cap-drop",
        "--security-opt",
        "--cpus",
        "--memory",
        "--pids-limit",
        "--tmpfs",
    ]:
        assert flag in command
    assert command[command.index("--network") + 1] == "none"
    assert not set(command) & {"-v", "--volume", "--mount", "--privileged", "--env", "-e"}


def test_input_limits_stale_version_and_incomplete_verdict(system):
    client, engine, alice, *_ = system
    p = create(client, alice)
    assert submit(client, alice, p, code="\x00").status_code == 422
    assert submit(client, alice, p, code="中" * 16384).status_code == 422
    assert (
        client.post(
            f"/api/v1/projects/{p['id']}/submissions",
            headers={
                **version(alice, p["version"]),
                "Idempotency-Key": "invalid-utf8",
                "Content-Type": "application/json",
            },
            content=json.dumps({"code": "\ud800"}).encode("ascii"),
        ).status_code
        == 422
    )
    assert submit(client, alice, {**p, "version": 99}).status_code == 412
    ok(submit(client, alice, p), 201)
    with Session(engine) as db, db.begin():
        receipt = projects.claim(db)["receipt"]
    with Session(engine) as db, db.begin(), pytest.raises(DomainError, match="完整测试"):
        projects.complete(db, receipt["id"], receipt["lease_token"], {**verdict(), "results": []})
