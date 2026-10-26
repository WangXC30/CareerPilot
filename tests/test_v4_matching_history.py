"""Frozen catalogue/weight replay across independently reviewed public releases."""

from datetime import date

from sqlalchemy.orm import Session

from careerpilot import catalogue, growth, matching
from careerpilot.domain import Principal
from careerpilot.persistence import User
from tests.test_growth import write_profile
from tests.test_v2_operations import propose, publish, review, roles
from tests.test_v4_collaboration import ok


def release_skill(client, operator, admin, aliases):
    entry = next(
        e
        for e in ok(client.get("/api/v1/catalogue/skill", headers=operator))
        if e["id"] == "python"
    )
    fields = {
        k: v for k, v in entry.items() if k not in {"id", "version", "kind", "updated_at", "stale"}
    }
    proposal = ok(
        propose(
            client,
            operator,
            {**fields, "aliases": aliases},
            base=entry["version"],
            entry_id="python",
            entry_kind="skill",
        ),
        201,
    )
    assert proposal["base_payload"]["aliases"] == entry["aliases"]
    assert proposal["changes"]["aliases"] == {"before": entry["aliases"], "after": aliases}
    ok(publish(client, admin, ok(review(client, admin, proposal))))
    return proposal


def create_snapshot(client, headers, job):
    return ok(
        client.post(
            "/api/v1/matching-snapshots",
            headers=headers,
            json={"job_id": job["id"], "confirmed": True},
        ),
        201,
    )


def test_history_replays_frozen_catalogue_after_alias_profile_job_and_weight_changes(system):
    client, engine, alice, bob, _, ids = system
    roles(system)
    ok(write_profile(client, alice))
    job = ok(
        client.post(
            "/api/v1/jobs/import", headers=alice, json={"content": "岗位：本地回归\n要求：PyLocal"}
        ),
        201,
    )
    before = create_snapshot(client, alice, job)
    assert before["result"]["skills"] == []
    old = release_skill(client, alice, bob, ["Python", "python", "PyLocal"])
    after = create_snapshot(client, alice, job)
    assert after["catalogue_snapshot"]["python"]["version"] == 2
    assert after["result"]["skills"][0]["evidence"]["quote"] == "PyLocal"
    assert after["result"]["skills"][0]["requirement"] == "required"
    # User and public weights change independently after these snapshots.
    ok(write_profile(client, alice, version=1, preferred_cities=["北京"]))
    policy = ok(
        propose(
            client,
            alice,
            {
                "weights": {**matching.WEIGHTS, "required_skills": 40, "project_evidence": 20},
                "rollout_percent": 100,
                "basis": "本地回归权重",
            },
            base=0,
            entry_id="matching-policy",
            entry_kind="matching_policy",
        ),
        201,
    )
    ok(publish(client, bob, ok(review(client, bob, policy))))
    with Session(engine) as db:
        user = db.get(User, ids[0])
        principal = Principal(user.id, user.tenant_id)
        row = growth.owned(db, principal, job["id"], "job")
        growth.save(
            db, principal, "job", {**growth.data(row), "status": "closed"}, "test", row, row.version
        )
        db.commit()
    for snapshot in (before, after):
        result = ok(
            client.get(f"/api/v1/matching-snapshots/{snapshot['id']}/replay", headers=alice)
        )
        assert result["identical"] and result["result"] == snapshot["result"]
        assert result["weight_version"] == matching.WEIGHT_VERSION
        assert (
            client.get(
                f"/api/v1/matching-snapshots/{snapshot['id']}/replay", headers=bob
            ).status_code
            == 404
        )
    rollback = ok(
        client.post(
            "/api/v1/admin/catalogue/python/rollback-proposal",
            headers={**alice, "If-Match": '"2"'},
            json={"target_version": 1, "note": "回滚别名", "confirmed": True},
        ),
        201,
    )
    ok(publish(client, bob, ok(review(client, bob, rollback))))
    current = create_snapshot(client, alice, job)
    assert (
        current["result"]["skills"] == []
        and current["result"]["weight_version"] == "matching-policy:1"
    )
    assert ok(client.get(f"/api/v1/matching-snapshots/{after['id']}/replay", headers=alice))[
        "identical"
    ]
    # The original diff remains anchored to its base even after a later rollback.
    proposals = ok(client.get("/api/v1/admin/catalogue-proposals", headers=bob))
    stored = next(p for p in proposals if p["id"] == old["id"])
    assert stored["changes"]["aliases"]["after"] == ["Python", "python", "PyLocal"]
    history = ok(client.get("/api/v1/matching-snapshots", headers=alice))
    assert len(history) == 3 and all(
        "context" not in item and "job" not in item for item in history
    )
    assert ok(client.get("/api/v1/matching-snapshots", headers=bob)) == []


def test_legacy_v2_replay_keeps_imported_spans_and_unknown_rule_fails_closed(system):
    client, engine, alice, _, _, ids = system
    job = ok(
        client.post(
            "/api/v1/jobs/import",
            headers=alice,
            json={"content": "岗位：旧规则样本\n要求：React.js"},
        ),
        201,
    )
    with Session(engine) as db:
        user = db.get(User, ids[0])
        principal = Principal(user.id, user.tenant_id)
        ctx = matching.context(db, principal)
        old = growth.save(
            db,
            principal,
            "matching_snapshot",
            {
                "job": job,
                "context": ctx,
                "as_of_date": "2026-10-23",
                "catalogue_snapshot": catalogue.skill_map(db),
                "rule_version": "matching.rules.v2",
                "result": matching.evaluate(
                    job, ctx, date(2026, 10, 23), rule_version="matching.rules.v2"
                ),
            },
            "test",
        )
        missing = growth.save(
            db,
            principal,
            "matching_snapshot",
            {
                **{k: v for k, v in old.items() if k not in {"id", "version", "created_at"}},
                "rule_version": "matching.rules.unavailable",
            },
            "test",
        )
        db.commit()
    legacy = ok(client.get(f"/api/v1/matching-snapshots/{old['id']}/replay", headers=alice))
    assert legacy["identical"]
    assert {s["evidence"]["quote"] for s in legacy["result"]["skills"]} == {"React", "js"}
    current = create_snapshot(client, alice, job)
    assert current["result"]["skills"][0]["evidence"]["quote"] == "React.js"
    assert len(current["result"]["skills"]) == 1
    response = client.get(f"/api/v1/matching-snapshots/{missing['id']}/replay", headers=alice)
    assert response.status_code == 409 and response.json()["code"] == "RULE_UNAVAILABLE"


def test_aliases_are_literal_and_negation_gate_blocks_unsafe_public_semantics(system):
    client, _, alice, bob, *_ = system
    roles(system)
    release_skill(client, alice, bob, ["Python", "Py.*"])
    actual = ok(
        client.post(
            "/api/v1/jobs/import",
            headers=alice,
            json={
                "content": "岗位：字面别名\n要求：PyABC\n不要求：Py.*\n优先：Python",
            },
        ),
        201,
    )
    result = create_snapshot(client, alice, actual)["result"]
    assert {s["evidence"]["quote"] for s in result["skills"]} == {"Python", "Py.*"}
    assert {s["requirement"] for s in result["skills"]} == {"mentioned", "preferred"}
    entry = next(
        e for e in ok(client.get("/api/v1/catalogue/skill", headers=alice)) if e["id"] == "python"
    )
    fields = {
        k: v for k, v in entry.items() if k not in {"id", "version", "kind", "updated_at", "stale"}
    }
    bad = propose(
        client,
        alice,
        {**fields, "aliases": ["optional"]},
        base=2,
        entry_id="python",
        entry_kind="skill",
    )
    assert bad.status_code == 422 and bad.json()["code"] == "SKILL_REGRESSION_FAILED"
