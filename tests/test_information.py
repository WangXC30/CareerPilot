"""Personal information maintenance uses real API, source status and snapshots."""

from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from careerpilot import growth
from careerpilot.growth_models import GrowthRecord, GrowthRevision
from tests.test_growth import import_job, material, write_profile
from tests.test_v1_release import ok, version
from tests.test_v2_hiring import application, fact, resume
from tests.test_v2_opportunities import principal_for


def deletion(client, headers, kind, item, *, preview=None):
    path = f"/api/v1/information/{kind}/{item['id']}"
    preview = preview or ok(client.get(path + "/deletion-preview", headers=headers))
    return client.request(
        "DELETE",
        path,
        headers=version(headers, item["version"]),
        json={"confirmed": True, "fingerprint": preview["fingerprint"]},
    )


def test_note_create_search_edit_history_delete_and_isolation(system):
    client, engine, alice, bob, *_ = system
    body = {"text": "Python 资料 unique-note", "category": "self_report", "confirmed": True}
    note = ok(client.post("/api/v1/memories", headers=alice, json=body), 201)
    assert note["skill_status"] == "unverified"
    assert client.get("/api/v1/information?q=UNIQUE-NOTE", headers=bob).json() == []
    found = client.get("/api/v1/information?q=UNIQUE-NOTE", headers=alice).json()
    assert found[0]["id"] == note["id"] and found[0]["record_kind"] == "memory"
    assert (
        client.put(
            f"/api/v1/materials/{note['id']}",
            headers=version(bob, 1),
            json={"filename": "a.md", "content": "x", "confirmed": True},
        ).status_code
        == 404
    )
    changed = ok(
        client.patch(
            f"/api/v1/memories/{note['id']}",
            headers=version(alice, 1),
            json={**body, "text": "已修改的资料"},
        )
    )
    assert changed["version"] == 2
    assert (
        client.patch(
            f"/api/v1/memories/{note['id']}", headers=version(alice, 1), json=body
        ).status_code
        == 412
    )
    history = client.get(f"/api/v1/information/memory/{note['id']}/history", headers=alice).json()
    assert [h["version"] for h in history] == [2, 1]
    assert (
        client.get(f"/api/v1/information/memory/{note['id']}/history", headers=bob).status_code
        == 404
    )
    assert deletion(client, alice, "memory", changed).status_code == 204
    with Session(engine) as db:
        assert db.get(GrowthRecord, note["id"]) is None
        assert (
            db.scalar(select(GrowthRevision).where(GrowthRevision.record_id == note["id"])) is None
        )


def test_delete_requires_confirmation_and_current_dependency_preview(system):
    client, _, alice, *_ = system
    m = material(client, alice)
    preview = ok(
        client.get(f"/api/v1/information/material/{m['id']}/deletion-preview", headers=alice)
    )
    result = client.post(
        f"/api/v1/materials/{m['id']}/confirm",
        headers=version(alice, 1),
        json={"confirmed": True, "claims": [{**m["candidates"][0], "category": "self_report"}]},
    )
    ok(result)
    current = result.json()["material"]
    assert deletion(client, alice, "material", current, preview=preview).status_code == 412
    path = f"/api/v1/information/material/{m['id']}"
    fresh = ok(client.get(path + "/deletion-preview", headers=alice))
    assert len(fresh["records"]) == 2
    assert (
        client.request(
            "DELETE",
            path,
            headers=version(alice, current["version"]),
            json={"fingerprint": fresh["fingerprint"]},
        ).status_code
        == 422
    )
    assert deletion(client, alice, "material", current).status_code == 204
    assert client.get("/api/v1/materials", headers=alice).json() == []
    assert client.get("/api/v1/memories", headers=alice).json() == []


def test_material_revision_reconfirms_sources_and_keeps_resume_snapshot(system):
    client, _, alice, *_ = system
    m = material(client, alice, "旧的经历")
    first = ok(
        client.post(
            f"/api/v1/materials/{m['id']}/confirm",
            headers=version(alice, 1),
            json={"confirmed": True, "claims": [{**m["candidates"][0], "category": "self_report"}]},
        )
    )
    memory = first["memories"][0]
    f = ok(
        client.post(
            "/api/v1/resume-facts",
            headers=alice,
            json={
                "kind": "experience",
                "source_type": "memory",
                "source_id": memory["id"],
                "source_version": 1,
                "confirmed": True,
            },
        ),
        201,
    )
    v = resume(client, alice, facts=[f])
    revised = ok(
        client.put(
            f"/api/v1/materials/{m['id']}",
            headers=version(alice, 2),
            json={"filename": "revised.md", "content": "新的经历", "confirmed": True},
        )
    )
    assert revised["version"] == 3 and revised["status"] == "pending_confirmation"
    facts = client.get("/api/v1/resume-facts", headers=alice).json()
    assert facts[0]["source_status"] == "source_withdrawn"
    old = client.get(f"/api/v1/resume-versions/{v['id']}", headers=alice).json()
    assert old["facts"][0]["text"] == "旧的经历" and not old["source_status"]["valid"]
    confirmed = ok(
        client.post(
            f"/api/v1/materials/{m['id']}/confirm",
            headers=version(alice, 3),
            json={
                "confirmed": True,
                "claims": [{**revised["candidates"][0], "category": "self_report"}],
            },
        )
    )
    assert confirmed["memories"][0]["source_version"] == 3


def test_fact_edit_and_delete_invalidate_old_version_without_losing_snapshot(system):
    client, _, alice, *_ = system
    f = fact(client, alice, "旧的事实")
    v = resume(client, alice, facts=[f])
    updated = ok(
        client.put(
            f"/api/v1/resume-facts/{f['id']}",
            headers=version(alice, 1),
            json={
                "kind": "experience",
                "source_type": "manual",
                "text": "新事实",
                "confirmed": True,
            },
        )
    )
    old = client.get(f"/api/v1/resume-versions/{v['id']}", headers=alice).json()
    assert old["facts"][0]["text"] == "旧的事实"
    assert old["source_status"]["facts"][0]["source_status"] == "needs_reconfirmation"
    assert (
        client.post(
            f"/api/v1/resume-versions/{v['id']}/drafts",
            headers=alice,
            json={"kind": "introduction", "tone": "plain", "confirmed": True},
        ).status_code
        == 409
    )
    assert (
        client.post(
            f"/api/v1/resume-versions/{v['id']}/export/docx",
            headers=alice,
            json={"confirmed": True},
        ).status_code
        == 409
    )
    job = ok(import_job(client, alice), 201)
    assert (
        client.post(
            "/api/v1/applications",
            headers={**alice, "Idempotency-Key": "stale-application"},
            json={"job_id": job["id"], "resume_version_id": v["id"], "confirmed": True},
        ).status_code
        == 409
    )
    assert deletion(client, alice, "resume_fact", updated).status_code == 204
    old = client.get(f"/api/v1/resume-versions/{v['id']}", headers=alice).json()
    assert old["source_status"]["facts"][0]["source_status"] == "source_deleted"


def test_deleted_profile_recreation_cannot_revalidate_old_facts(system):
    client, _, alice, *_ = system
    profile = ok(write_profile(client, alice))
    f = ok(
        client.post(
            "/api/v1/resume-facts",
            headers=alice,
            json={
                "kind": "education",
                "source_type": "profile",
                "source_version": 1,
                "profile_field": "major",
                "confirmed": True,
            },
        ),
        201,
    )
    assert deletion(client, alice, "profile", profile).status_code == 204
    recreated = ok(write_profile(client, alice))
    assert recreated["id"] != profile["id"]
    assert client.get("/api/v1/resume-facts", headers=alice).json()[0]["id"] == f["id"]
    assert (
        client.get("/api/v1/resume-facts", headers=alice).json()[0]["source_status"]
        == "source_deleted"
    )


def test_resume_delete_blocks_application_and_removes_owned_versions_afterward(system):
    client, _, alice, *_ = system
    j = ok(import_job(client, alice), 201)
    v = resume(client, alice, job_id=j["id"])
    app = application(client, alice, v=v, j=j)
    root = client.get("/api/v1/resumes", headers=alice).json()[0]
    preview = client.get(
        f"/api/v1/information/resume/{root['id']}/deletion-preview", headers=alice
    ).json()
    assert any(r["id"] == app["id"] for r in preview["blockers"])
    assert deletion(client, alice, "resume", root).status_code == 409
    assert deletion(client, alice, "job_application", app).status_code == 204
    assert deletion(client, alice, "resume", root).status_code == 204
    assert client.get(f"/api/v1/resume-versions/{v['id']}", headers=alice).status_code == 404


def test_resume_rename_does_not_skip_content_sequences(system):
    client, _, alice, *_ = system
    f = fact(client, alice)
    v = resume(client, alice, facts=[f])
    root = client.get("/api/v1/resumes", headers=alice).json()[0]
    renamed = ok(
        client.put(
            f"/api/v1/resumes/{root['id']}/details",
            headers=version(alice, root["version"]),
            json={"title": "新名称", "confirmed": True},
        )
    )
    revised = ok(
        client.post(
            f"/api/v1/resumes/{root['id']}/versions",
            headers=version(alice, renamed["version"]),
            json={"fact_ids": [f["id"]], "confirmed": True},
        ),
        201,
    )
    assert revised["sequence"] == v["sequence"] + 1


def test_job_revision_reextracts_evidence_and_keeps_history(system):
    client, _, alice, *_ = system
    j = ok(import_job(client, alice), 201)
    revised = ok(
        client.put(
            f"/api/v1/jobs/{j['id']}",
            headers=version(alice, 1),
            json={"content": "岗位：前端实习生\n要求：熟悉 React", "confirmed": True},
        )
    )
    assert revised["id"] == j["id"] and revised["version"] == 2
    assert revised["fields"]["title"]["value"] == "前端实习生"
    assert [s["skill_id"] for s in revised["skills"]] == ["react"]
    assert len(client.get(f"/api/v1/jobs/{j['id']}/history", headers=alice).json()) == 2


def test_source_edit_reuses_rights_and_quote_validation(system):
    client, _, alice, *_ = system
    body = {
        "title": "来源文章",
        "text": "原始摘录",
        "kind": "article",
        "authorization_scope": "local_excerpt",
        "confirmed": True,
        "rights_confirmed": True,
    }
    source = ok(client.post("/api/v1/source-documents", headers=alice, json=body), 201)
    path = f"/api/v1/source-documents/{source['id']}"
    assert (
        client.put(
            path, headers=version(alice, 1), json={**body, "authorization_scope": "reference_only"}
        ).status_code
        == 422
    )
    revised = ok(client.put(path, headers=version(alice, 1), json={**body, "title": "新文章"}))
    assert revised["trust"] == "untrusted_reference" and not revised["capability_evidence"]
    assert deletion(client, alice, "source_document", revised).status_code == 204


def test_log_edit_enforces_daily_total(system):
    client, _, alice, *_ = system
    day = date.today().isoformat()
    a = ok(
        client.post(
            "/api/v1/study-logs",
            headers={**alice, "Idempotency-Key": "a"},
            json={"day": day, "minutes": 1000, "confirmed": True},
        ),
        201,
    )
    ok(
        client.post(
            "/api/v1/study-logs",
            headers={**alice, "Idempotency-Key": "b"},
            json={"day": day, "minutes": 400, "confirmed": True},
        ),
        201,
    )
    path = f"/api/v1/study-logs/{a['id']}"
    assert (
        client.put(
            path, headers=version(alice, 1), json={"day": day, "minutes": 1100, "confirmed": True}
        ).status_code
        == 422
    )
    revised = ok(
        client.put(
            path, headers=version(alice, 1), json={"day": day, "minutes": 900, "confirmed": True}
        )
    )
    assert revised["minutes"] == 900
    assert deletion(client, alice, "study_log", revised).status_code == 204


@pytest.mark.parametrize(
    "kind", ["provider_config", "chat_budget", "project_assessment", "conversation"]
)
def test_information_cannot_mutate_internal_records(system, kind):
    client, _, alice, *_ = system
    assert client.get(f"/api/v1/information?kind={kind}", headers=alice).status_code == 404


def test_structured_experience_cannot_be_flattened_by_memory_edit(system):
    client, _, alice, *_ = system
    experience = ok(
        client.put(
            "/api/v1/experiences/new",
            headers={**version(alice, 0), "Idempotency-Key": "exp"},
            json={"kind": "project", "title": "经历", "action": "个人行动", "confirmed": True},
        )
    )
    assert (
        client.patch(
            f"/api/v1/memories/{experience['id']}",
            headers=version(alice, 1),
            json={"text": "扁平文本", "category": "self_report", "confirmed": True},
        ).status_code
        == 422
    )
    assert deletion(client, alice, "memory", experience).status_code == 204


def test_project_deletion_removes_owned_assets_without_forging_verdict(system):
    client, engine, alice, _, _, ids = system
    principal = principal_for(engine, ids[0])
    with Session(engine) as db, db.begin():
        p = growth.save(db, principal, "practice_project", {"title": "私有项目"}, "test")
        asset = growth.save(
            db, principal, "project_asset", {"project_id": p["id"], "content": "asset"}, "test"
        )
    assert deletion(client, alice, "practice_project", p).status_code == 204
    with Session(engine) as db:
        assert db.get(GrowthRecord, asset["id"]) is None


def test_expired_source_cannot_be_read_through_information_history_or_search(system):
    client, engine, alice, _, _, ids = system
    principal = principal_for(engine, ids[0])
    with Session(engine) as db, db.begin():
        source = growth.save(
            db,
            principal,
            "source_document",
            {
                "title": "expired-private-title",
                "text": "expired-private-excerpt",
                "expires_at": "2000-01-01T00:00:00+00:00",
            },
            "test",
        )
    rows = client.get("/api/v1/information?kind=source_document", headers=alice).json()
    assert rows[0]["expired"] and "expired-private" not in str(rows)
    assert client.get("/api/v1/information?q=expired-private", headers=alice).json() == []
    assert (
        client.get(
            f"/api/v1/information/source_document/{source['id']}/history", headers=alice
        ).status_code
        == 410
    )
    assert deletion(client, alice, "source_document", source).status_code == 204


def test_material_revision_preserves_exact_original_whitespace(system):
    client, _, alice, *_ = system
    item = material(client, alice)
    text = "\n保留原文格式\n\n"
    revised = ok(
        client.put(
            f"/api/v1/materials/{item['id']}",
            headers=version(alice, 1),
            json={"filename": "original.md", "content": text, "confirmed": True},
        )
    )
    assert revised["content"] == text
