"""Independent public review, rollback, redacted support and risk audit."""

import json

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from careerpilot import catalogue
from careerpilot.persistence import AuditEvent, User
from careerpilot.platform_models import RoleGrant
from tests.test_v1_release import ok, version
from tests.test_v2_opportunities import job


def roles(system):
    _, engine, *_ = system
    for user_id, role in zip(system[5], ("operator", "admin"), strict=True):
        with Session(engine) as db, db.begin():
            user = db.get(User, user_id)
            db.get(RoleGrant, (user_id, user.tenant_id)).role = role


def payload(client, headers):
    return next(
        r
        for r in ok(client.get("/api/v1/catalogue/resource", headers=headers))
        if r["id"] == "resource-python"
    )


def propose(client, headers, data=None, base=1, **kwargs):
    r = data or payload(client, headers)
    fields = {
        k: v for k, v in r.items() if k not in {"id", "version", "kind", "updated_at", "stale"}
    }
    return client.post(
        "/api/v1/admin/catalogue-proposals",
        headers=headers,
        json={
            "entry_id": "resource-python",
            "entry_kind": "resource",
            "base_version": base,
            "payload": fields,
            "note": "公开来源维护，不包含私人材料",
            "confirmed": True,
            "public_only_confirmed": True,
            **kwargs,
        },
    )


def review(client, headers, p, accepted=True):
    return client.post(
        f"/api/v1/admin/catalogue-proposals/{p['id']}/review",
        headers=version(headers, p["version"]),
        json={"accepted": accepted, "note": "已独立核对公共字段", "confirmed": True},
    )


def publish(client, headers, p, revision=None):
    return client.post(
        f"/api/v1/admin/catalogue-proposals/{p['id']}/publish",
        headers=version(headers, p["version"] if revision is None else revision),
        json={"confirmed": True},
    )


def test_public_changes_need_role_independent_review_admin_publish_and_cas(system):
    client, engine, alice, bob, *_ = system
    assert propose(client, alice).status_code == 403
    roles(system)
    original = payload(client, alice)
    draft = ok(
        propose(
            client,
            alice,
            {**original, "status": "unavailable", "alternative": "暂回官方教程首页核对"},
        ),
        201,
    )
    assert payload(client, alice) == original
    assert review(client, alice, draft).status_code == 403
    assert publish(client, bob, draft).status_code == 409
    approved = ok(review(client, bob, draft))
    assert publish(client, alice, approved).status_code == 403
    assert publish(client, bob, approved, revision=1).status_code == 412
    assert payload(client, alice) == original
    published = ok(publish(client, bob, approved))
    assert published["status"] == "published"
    assert (
        payload(client, alice)["status"] == "unavailable" and payload(client, alice)["version"] == 2
    )
    assert publish(client, bob, published).status_code == 409
    with Session(engine) as db:
        assert list(
            db.scalars(select(AuditEvent).where(AuditEvent.action == "catalogue.published"))
        )


def test_rollback_is_another_reviewed_publish_and_retains_versions(system):
    client, _, alice, bob, *_ = system
    roles(system)
    original = payload(client, alice)
    p = ok(propose(client, alice, {**original, "title": "重新命名的官方入口"}), 201)
    ok(publish(client, bob, ok(review(client, bob, p))))
    history = ok(client.get("/api/v1/admin/catalogue/resource-python/history", headers=bob))
    assert {r["entry_version"] for r in history} == {1, 2}
    rollback = ok(
        client.post(
            "/api/v1/admin/catalogue/resource-python/rollback-proposal",
            headers=version(alice, 2),
            json={"target_version": 1, "note": "恢复原公共标题", "confirmed": True},
        ),
        201,
    )
    assert payload(client, alice)["title"] != original["title"]
    ok(publish(client, bob, ok(review(client, bob, rollback))))
    restored = payload(client, alice)
    assert restored["version"] == 3 and restored["title"] == original["title"]


def test_stale_proposal_cannot_overwrite_new_public_version(system):
    client, _, alice, bob, *_ = system
    roles(system)
    a = ok(propose(client, alice), 201)
    b = ok(propose(client, alice), 201)
    a, b = ok(review(client, bob, a)), ok(review(client, bob, b))
    ok(publish(client, bob, a))
    assert publish(client, bob, b).status_code == 412


@pytest.mark.parametrize(
    "extra",
    [
        {"private_user_id": "never-public"},
        {"title": "sk_" + "x" * 24},
        {"url": "http://127.0.0.1/private"},
        {"skill_id": "unknown-skill"},
        {"reviewed_at": "invalid"},
        {"reviewed_at": "2026-10-21"},
    ],
)
def test_public_payload_rejects_private_fields_credentials_and_unsafe_refs(system, extra):
    client, _, alice, *_ = system
    roles(system)
    fields = payload(client, alice)
    assert propose(client, alice, {**fields, **extra}).status_code == 422


def test_public_skill_cycles_and_legacy_publish_bypass_are_rejected(system):
    client, engine, alice, *_ = system
    roles(system)
    with Session(engine) as db:
        original = catalogue.require_skill(db, "python")
    fields = {
        k: v
        for k, v in original.items()
        if k not in {"id", "version", "kind", "updated_at", "stale"}
    }
    body = {
        "entry_id": "python",
        "entry_kind": "skill",
        "base_version": 1,
        "payload": {**fields, "requires": ["pytorch"]},
        "note": "循环不应发布",
        "confirmed": True,
        "public_only_confirmed": True,
    }
    assert (
        client.post("/api/v1/admin/catalogue-proposals", headers=alice, json=body).status_code
        == 422
    )
    assert (
        client.put(
            "/api/v1/catalogue/skill/python/dependencies",
            headers=version(alice, 1),
            json={"requires": []},
        ).status_code
        == 409
    )
    with Session(engine) as db:
        assert catalogue.require_skill(db, "python")["version"] == 1


def test_ticket_queue_only_exposes_same_tenant_metadata_and_responses(system):
    client, _, alice, bob, *_ = system
    j = job(client, alice)
    ticket = ok(
        client.post(
            "/api/v1/support-tickets",
            headers=alice,
            json={
                "category": "expired_job",
                "job_id": j["id"],
                "details": "PRIVATE-REPORT-TEXT 不得向运营队列展示",
                "confirmed": True,
            },
        ),
        201,
    )
    assert client.get("/api/v1/admin/support-queue", headers=alice).status_code == 403
    roles(system)
    queue = ok(client.get("/api/v1/admin/support-queue", headers=alice))
    assert [q["id"] for q in queue] == [ticket["id"]]
    assert "PRIVATE-REPORT" not in json.dumps(queue) and j["id"] not in json.dumps(queue)
    assert ok(client.get("/api/v1/admin/support-queue", headers=bob)) == []
    assert ok(client.get("/api/v1/support-tickets", headers=bob)) == []
    path = f"/api/v1/admin/support-queue/{ticket['id']}/respond"
    data = {
        "status": "in_progress",
        "response": "请本人核对源站有效性；运营未读取私人岗位原文",
        "confirmed": True,
    }
    assert client.post(path, headers=version(bob, 1), json=data).status_code == 404
    ok(client.post(path, headers=version(alice, 1), json=data))
    assert client.post(path, headers=version(alice, 1), json=data).status_code == 412
    own = ok(client.get("/api/v1/support-tickets", headers=alice))[0]
    assert own["responses"][0]["text"] == data["response"]
    assert own["status"] == "in_progress"


def test_denied_access_has_durable_audit_without_private_route_values(system):
    client, engine, alice, *_ = system
    assert client.get("/api/v1/admin/quality-monitor", headers=alice).status_code == 403
    roles(system)
    monitor = ok(client.get("/api/v1/admin/quality-monitor", headers=alice))
    assert (
        monitor["risk_alerts"]["denied_access_count"] >= 1
        and not monitor["private_content_visible"]
    )
    audit = ok(client.get("/api/v1/admin/public-operation-audit", headers=alice))
    assert any(r["action"] == "access.denied" for r in audit)
    with Session(engine) as db:
        denied = list(db.scalars(select(AuditEvent).where(AuditEvent.action == "access.denied")))
        assert all(r.aggregate_id == "" for r in denied)
