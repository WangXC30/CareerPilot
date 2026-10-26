"""Immutable hiring documents, receipt boundaries and atomic private reminders."""

import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from io import BytesIO
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from careerpilot import accounts, growth, hiring_reminders
from tests.test_v1_release import PROFILE, ok, version
from tests.test_v2_opportunities import job, principal_for

CHECKLIST = {k: True for k in ("facts", "contact", "requirements", "privacy", "source_reviewed")}


def fact(client, headers, text="使用 Python 编写课程练习。", kind="experience", **kwargs):
    return ok(
        client.post(
            "/api/v1/resume-facts",
            headers=headers,
            json={"kind": kind, "source_type": "manual", "text": text, "confirmed": True, **kwargs},
        ),
        201,
    )


def resume(client, headers, facts=None, job_id=None, key="resume-1"):
    facts = facts or [
        fact(client, headers, "合成学生", "name"),
        fact(client, headers, "student@example.com", "contact"),
        fact(client, headers),
    ]
    return ok(
        client.post(
            "/api/v1/resumes",
            headers={**headers, "Idempotency-Key": key},
            json={
                "title": "学生简历",
                "fact_ids": [f["id"] for f in facts],
                "job_id": job_id,
                "confirmed": True,
            },
        ),
        201,
    )


def application(client, headers, v=None, j=None, key="application-1"):
    j = j or job(client, headers)
    v = v or resume(client, headers, job_id=j["id"])
    return ok(
        client.post(
            "/api/v1/applications",
            headers={**headers, "Idempotency-Key": key},
            json={"job_id": j["id"], "resume_version_id": v["id"], "confirmed": True},
        ),
        201,
    )


def transition(client, headers, a, target, **kwargs):
    return client.post(
        f"/api/v1/applications/{a['id']}/transition",
        headers=version(headers, a["version"]),
        json={"target": target, "confirmed": True, **kwargs},
    )


def awaiting(client, headers):
    a = application(client, headers)
    a = ok(transition(client, headers, a, "prepared", checklist=CHECKLIST))
    return ok(transition(client, headers, a, "awaiting_user_submission"))


def material(client, headers, text):
    return ok(
        client.post(
            "/api/v1/materials?filename=receipt.txt", headers=headers, content=text.encode()
        ),
        201,
    )


def receipt_body(m, **kwargs):
    return {
        "material_id": m["id"],
        "material_version": m["version"],
        "receipt_code": "TEST-123",
        "submitted_at": datetime.now(timezone.utc).isoformat(),
        "confirmed": True,
        **kwargs,
    }


def reminder(client, headers, a, **kwargs):
    local = (
        datetime.fromtimestamp(time.time() + 3600, timezone.utc)
        .replace(tzinfo=None)
        .isoformat(timespec="seconds")
    )
    return ok(
        client.post(
            f"/api/v1/applications/{a['id']}/reminders",
            headers={**headers, "Idempotency-Key": kwargs.pop("key", "reminder-1")},
            json={
                "kind": "follow_up",
                "local_time": local,
                "timezone": "UTC",
                "quiet_start": 0,
                "quiet_end": 0,
                "confirmed": True,
                **kwargs,
            },
        ),
        201,
    )


def test_versions_append_restore_tailor_and_fixed_application(system):
    client, _, alice, bob, *_ = system
    j = job(client, alice)
    v = resume(client, alice, job_id=j["id"])
    a = application(client, alice, v, j)
    root_id = v["resume_id"]
    edited = ok(
        client.post(
            f"/api/v1/resumes/{root_id}/tailor",
            headers=version(alice, 2),
            json={"job_id": j["id"], "confirmed": True},
        ),
        201,
    )
    assert edited["sequence"] == 2
    assert sorted(f["text"] for f in edited["facts"]) == sorted(f["text"] for f in v["facts"])
    assert edited["parent_version_id"] == v["id"]
    assert (
        client.post(
            f"/api/v1/resumes/{root_id}/tailor",
            headers=version(alice, 2),
            json={"job_id": j["id"], "confirmed": True},
        ).status_code
        == 412
    )
    restored = ok(
        client.post(
            f"/api/v1/resumes/{root_id}/restore",
            headers=version(alice, 3),
            json={"version_id": v["id"], "confirmed": True},
        ),
        201,
    )
    assert restored["sequence"] == 3 and restored["restored_from"] == v["id"]
    old = ok(client.get(f"/api/v1/resume-versions/{v['id']}", headers=alice))
    assert {k: old[k] for k in v} == v
    assert (
        ok(client.get("/api/v1/applications", headers=alice))[0]["resume_version_id"]
        == a["resume_version_id"]
        == v["id"]
    )
    assert client.get(f"/api/v1/resumes/{root_id}/versions", headers=bob).status_code == 404
    assert client.get(f"/api/v1/resume-versions/{v['id']}/ats", headers=bob).status_code == 404
    assert (
        client.post(f"/api/v1/resume-versions/{v['id']}/export/docx", headers=bob).status_code
        == 404
    )


def test_resume_idempotency_no_duplicates_conflicting_body_and_foreign_facts(system):
    client, _, alice, bob, *_ = system
    facts = [fact(client, alice)]
    v = resume(client, alice, facts)
    assert resume(client, alice, facts)["id"] == v["id"]
    assert (
        client.post(
            "/api/v1/resumes",
            headers={**alice, "Idempotency-Key": "resume-1"},
            json={"title": "changed", "fact_ids": [facts[0]["id"]], "confirmed": True},
        ).status_code
        == 409
    )
    assert (
        client.post(
            "/api/v1/resumes",
            headers={**bob, "Idempotency-Key": "b"},
            json={"title": "x", "fact_ids": [facts[0]["id"]], "confirmed": True},
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/api/v1/resumes",
            headers={**alice, "Idempotency-Key": "dup"},
            json={"title": "x", "fact_ids": [facts[0]["id"]] * 2, "confirmed": True},
        ).status_code
        == 422
    )


@pytest.mark.parametrize("change", ["edit", "delete", "inference", "revoke"])
def test_memory_source_change_blocks_new_use_but_keeps_old_snapshot(system, change):
    client, _, alice, *_ = system
    text = "使用 Python 课程实践"
    m = material(client, alice, text)
    memory = ok(
        client.post(
            f"/api/v1/materials/{m['id']}/confirm",
            headers=version(alice, 1),
            json={
                "confirmed": True,
                "claims": [{"text": text, "start": 0, "end": len(text), "category": "self_report"}],
            },
        )
    )["memories"][0]
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
    v = resume(client, alice, [f])
    if change == "delete":
        assert (
            client.delete(f"/api/v1/memories/{memory['id']}", headers=version(alice, 1)).status_code
            == 204
        )
    elif change == "revoke":
        ok(
            client.post(
                f"/api/v1/resume-facts/{f['id']}/revoke",
                headers=alice,
                json={"reason": "不再使用", "confirmed": True},
            )
        )
    else:
        ok(
            client.patch(
                f"/api/v1/memories/{memory['id']}",
                headers=version(alice, 1),
                json={
                    "text": text + "更新",
                    "category": "inference" if change == "inference" else "self_report",
                    "confirmed": True,
                },
            )
        )
    old = ok(client.get(f"/api/v1/resume-versions/{v['id']}", headers=alice))
    assert old["facts"] == v["facts"] and not old["source_status"]["valid"]
    assert (
        client.post(f"/api/v1/resume-versions/{v['id']}/export/docx", headers=alice).status_code
        == 409
    )
    assert (
        client.post(
            f"/api/v1/resumes/{v['resume_id']}/restore",
            headers=version(alice, 2),
            json={"version_id": v["id"], "confirmed": True},
        ).status_code
        == 409
    )


def test_profile_fact_version_and_source_input_contracts(system):
    client, _, alice, *_ = system
    ok(
        client.put(
            "/api/v1/profile", headers=version(alice, 0), json={**PROFILE, "confirmed": True}
        )
    )
    f = ok(
        client.post(
            "/api/v1/resume-facts",
            headers=alice,
            json={
                "kind": "education",
                "source_type": "profile",
                "profile_field": "major",
                "source_version": 1,
                "confirmed": True,
            },
        ),
        201,
    )
    assert f["text"] == "专业：" + PROFILE["major"]
    ok(
        client.put(
            "/api/v1/profile",
            headers=version(alice, 1),
            json={**PROFILE, "major": "其他", "confirmed": True},
        )
    )
    assert (
        ok(client.get("/api/v1/resume-facts", headers=alice))[0]["source_status"]
        == "needs_reconfirmation"
    )
    for body in [
        {"source_type": "manual", "text": "x", "source_id": "fake"},
        {"source_type": "memory", "source_id": "fake", "source_version": 1, "text": "invented"},
        {"source_type": "profile", "profile_field": "major", "source_version": 2, "kind": "skill"},
    ]:
        assert (
            client.post(
                "/api/v1/resume-facts",
                headers=alice,
                json={"kind": "education", "confirmed": True, **body},
            ).status_code
            == 422
        )


def test_ats_missing_contact_does_not_invent_skills_and_drafts_are_private(system):
    client, _, alice, bob, *_ = system
    j = job(client, alice, "岗位：数据实习\n要求：SQL\n优先：Python")
    facts = [
        fact(client, alice, "合成学生", "name"),
        fact(client, alice, "课程使用 Python，联系教师 tutor@example.com。"),
    ]
    v = resume(client, alice, facts, j["id"])
    check = ok(client.get(f"/api/v1/resume-versions/{v['id']}/ats", headers=alice))
    assert not check["ready_for_preparation"] and check["missing_keywords"] == ["sql"]
    assert "CONTACT_MISSING" in {i["code"] for i in check["issues"]}
    draft = ok(
        client.post(
            f"/api/v1/resume-versions/{v['id']}/drafts",
            headers=alice,
            json={"kind": "email", "tone": "formal", "confirmed": True},
        ),
        201,
    )
    assert all(f["text"] in draft["text"] for f in facts)
    assert "SQL" not in draft["text"] and draft["sent"] is False
    assert ok(client.get("/api/v1/application-drafts", headers=bob)) == []


def test_docx_xml_text_layout_escaping_hash_and_pdf_header(system):
    client, _, alice, *_ = system
    f = fact(client, alice, "课程项目：A&B <script>alert(1)</script> 中文测试", "project")
    v = resume(client, alice, [f])
    response = client.post(f"/api/v1/resume-versions/{v['id']}/export/docx", headers=alice)
    assert response.status_code == 200
    assert response.headers["x-resume-version"] == v["id"]
    assert response.headers["x-content-sha256"] == hashlib.sha256(response.content).hexdigest()
    with ZipFile(BytesIO(response.content)) as z:
        root = ET.fromstring(z.read("word/document.xml"))
        text = "\n".join(
            n.text or ""
            for n in root.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t")
        )
        assert f["text"] in text
        assert not any("external" in n.lower() or "macro" in n.lower() for n in z.namelist())
    pdf = client.post(f"/api/v1/resume-versions/{v['id']}/export/pdf", headers=alice)
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF-")


def test_application_state_checklist_dedup_cas_and_no_generic_submitted(system):
    client, _, alice, bob, *_ = system
    a = application(client, alice)
    assert transition(client, alice, a, "prepared").status_code == 422
    assert transition(client, alice, a, "interviewing").status_code == 409
    assert transition(client, alice, a, "submitted").status_code == 422
    assert transition(client, bob, a, "withdrawn").status_code == 404
    assert client.get(f"/api/v1/applications/{a['id']}/history", headers=bob).status_code == 404
    body = {
        "job_id": a["job"]["id"],
        "resume_version_id": a["resume_version_id"],
        "confirmed": True,
    }
    assert (
        ok(
            client.post(
                "/api/v1/applications",
                headers={**alice, "Idempotency-Key": "application-1"},
                json=body,
            ),
            201,
        )["id"]
        == a["id"]
    )
    assert (
        client.post(
            "/api/v1/applications", headers={**alice, "Idempotency-Key": "another"}, json=body
        ).status_code
        == 409
    )
    prepared = ok(transition(client, alice, a, "prepared", checklist=CHECKLIST))
    assert transition(client, alice, a, "withdrawn").status_code == 412
    ok(transition(client, alice, prepared, "awaiting_user_submission"))
    assert len(ok(client.get(f"/api/v1/applications/{a['id']}/history", headers=alice))) == 3


@pytest.mark.parametrize(
    "bad",
    [
        "提交失败 测试企业 TEST-123",
        "已提交 无关企业 TEST-123",
        "提交成功 测试企业 OTHER",
        "已提交 测试企业 TEST-123 未提交",
    ],
)
def test_invalid_receipts_never_mark_submitted(system, bad):
    client, _, alice, *_ = system
    a = awaiting(client, alice)
    m = material(client, alice, bad)
    response = client.post(
        f"/api/v1/applications/{a['id']}/receipt",
        headers=version(alice, a["version"]),
        json=receipt_body(m),
    )
    assert response.status_code == 422
    assert (
        ok(client.get("/api/v1/applications", headers=alice))[0]["status"]
        == "awaiting_user_submission"
    )


def test_receipt_pending_then_user_confirmation_requires_owned_material_and_time(system):
    client, _, alice, bob, *_ = system
    a = awaiting(client, alice)
    a = ok(
        client.post(
            f"/api/v1/applications/{a['id']}/receipt",
            headers=version(alice, a["version"]),
            json={"confirmed": True, "note": "本人声称已提交"},
        )
    )
    assert a["status"] == "awaiting_user_submission" and a["verification"] == "pending_verification"
    b = material(client, bob, "投递成功 测试企业 TEST-123")
    assert (
        client.post(
            f"/api/v1/applications/{a['id']}/receipt",
            headers=version(alice, a["version"]),
            json=receipt_body(b),
        ).status_code
        == 404
    )
    m = material(client, alice, "投递成功 测试企业 TEST-123")
    for overrides in [
        {"material_version": 2},
        {"submitted_at": "2099-01-01T00:00:00+00:00"},
        {"submitted_at": "2026-01-01T00:00:00"},
    ]:
        assert client.post(
            f"/api/v1/applications/{a['id']}/receipt",
            headers=version(alice, a["version"]),
            json=receipt_body(m, **overrides),
        ).status_code in {412, 422}
    a = ok(
        client.post(
            f"/api/v1/applications/{a['id']}/receipt",
            headers=version(alice, a["version"]),
            json=receipt_body(m),
        )
    )
    assert a["status"] == "submitted" and a["verification"] == "user_confirmed_receipt"
    assert a["receipt"]["resume_version_id"] == a["resume_version_id"]
    a = ok(transition(client, alice, a, "interviewing", note="本人记录面试安排"))
    a = ok(transition(client, alice, a, "offer"))
    assert transition(client, alice, a, "draft").status_code == 422


def test_job_changes_and_withdrawn_facts_block_preparation(system):
    client, _, alice, *_ = system
    j = job(client, alice, url="https://example.com/jobs/test")
    v = resume(client, alice, job_id=j["id"])
    a = application(client, alice, v, j)
    job(client, alice, "岗位：更新岗位\n公司：测试企业", url="https://example.com/jobs/test")
    assert transition(client, alice, a, "prepared", checklist=CHECKLIST).status_code == 409
    ok(transition(client, alice, a, "withdrawn"))


def test_reminder_timezone_gap_deadline_and_input_checks(system):
    client, _, alice, *_ = system
    a = application(client, alice)
    for kwargs in [
        {"kind": "deadline"},
        {"local_time": "2020-01-01T00:00:00"},
        {"timezone": "fake/zone"},
        {"local_time": "2027-03-14T02:30:00", "timezone": "America/New_York"},
    ]:
        local = (
            datetime.fromtimestamp(time.time() + 3600, timezone.utc)
            .replace(tzinfo=None)
            .isoformat(timespec="seconds")
        )
        assert (
            client.post(
                f"/api/v1/applications/{a['id']}/reminders",
                headers={**alice, "Idempotency-Key": "invalid"},
                json={
                    "kind": "follow_up",
                    "local_time": local,
                    "timezone": "UTC",
                    "confirmed": True,
                    **kwargs,
                },
            ).status_code
            == 422
        )
    one = hiring_reminders.due_at(
        {"local_time": "2027-11-07T01:30:00", "timezone": "America/New_York"}
    )
    assert datetime.fromtimestamp(one, timezone.utc).hour == 5


def test_reminders_concurrent_delivery_once_pause_cancel_and_private_reads(system):
    client, engine, alice, bob, *_ = system
    a = application(client, alice)
    r = reminder(client, alice, a)
    assert reminder(client, alice, a, local_time=r["local_time"])["id"] == r["id"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        counts = list(
            pool.map(lambda _: hiring_reminders.schedule_due(engine, r["due_at"] + 1), range(2))
        )
    assert sum(counts) == 1 and hiring_reminders.schedule_due(engine, r["due_at"] + 2) == 0
    notices = ok(client.get("/api/v1/application-notifications", headers=alice))
    assert len(notices) == 1 and notices[0]["application_id"] == a["id"]
    assert ok(client.get("/api/v1/application-notifications", headers=bob)) == []
    n = notices[0]
    assert (
        client.post(
            f"/api/v1/application-notifications/{n['id']}/read", headers=version(bob, 1)
        ).status_code
        == 404
    )
    assert ok(
        client.post(f"/api/v1/application-notifications/{n['id']}/read", headers=version(alice, 1))
    )["read"]
    r = reminder(client, alice, a, key="reminder-paused")
    body = {k: r[k] for k in ("kind", "local_time", "timezone", "quiet_start", "quiet_end")}
    r = ok(
        client.put(
            f"/api/v1/application-reminders/{r['id']}",
            headers=version(alice, r["version"]),
            json={**body, "status": "paused", "confirmed": True},
        )
    )
    assert hiring_reminders.schedule_due(engine, r["due_at"] + 1) == 0
    assert (
        client.put(
            f"/api/v1/application-reminders/{r['id']}",
            headers=version(alice, 1),
            json={**body, "status": "active", "confirmed": True},
        ).status_code
        == 412
    )
    r = ok(
        client.put(
            f"/api/v1/application-reminders/{r['id']}",
            headers=version(alice, r["version"]),
            json={**body, "status": "cancelled", "confirmed": True},
        )
    )
    assert hiring_reminders.schedule_due(engine, r["due_at"] + 1) == 0


def test_reminder_atomic_rollback_quiet_and_terminal_suppression(system, monkeypatch):
    client, engine, alice, *_ = system
    a = application(client, alice)
    r = reminder(client, alice, a)
    save = growth.save

    def broken(*args, **kwargs):
        if args[2] == "application_reminder":
            raise RuntimeError("injected before delivery commit")
        return save(*args, **kwargs)

    monkeypatch.setattr(growth, "save", broken)
    with pytest.raises(RuntimeError):
        hiring_reminders.schedule_due(engine, r["due_at"] + 1)
    assert ok(client.get("/api/v1/application-notifications", headers=alice)) == []
    monkeypatch.setattr(growth, "save", save)
    assert hiring_reminders.schedule_due(engine, r["due_at"] + 1) == 1
    r = reminder(client, alice, a, key="terminal")
    ok(transition(client, alice, a, "withdrawn"))
    assert hiring_reminders.schedule_due(engine, r["due_at"] + 1) == 0
    assert (
        next(
            x
            for x in ok(client.get("/api/v1/application-reminders", headers=alice))
            if x["id"] == r["id"]
        )["status"]
        == "suppressed"
    )


def test_new_hiring_data_export_and_account_deletion(system):
    client, engine, alice, _, _, ids = system
    a = application(client, alice)
    reminder(client, alice, a)
    principal = principal_for(engine, ids[0])
    with Session(engine) as db:
        export = accounts.export_data(db, principal)
        assert "resume_version" in json.dumps(export) and "application_reminder" in json.dumps(
            export
        )
    # The existing deletion worker must erase every new private GrowthRecord kind.
    response = client.post(
        "/api/v1/account/deletion", headers=alice, json={"confirmation": "DELETE MY ACCOUNT"}
    )
    assert response.status_code in {200, 202}
    accounts.process_deletion(engine)
    with Session(engine) as db:
        assert (
            list(
                db.scalars(select(growth.GrowthRecord).where(growth.GrowthRecord.user_id == ids[0]))
            )
            == []
        )


def test_text_resume_import_preview_correction_confirmation_and_provenance(system):
    client, _, alice, bob, *_ = system
    text = "姓名：合成学生\nstudent@example.com\n项目经历\n使用 Python 读取课程样例。"
    preview = ok(
        client.post(
            "/api/v1/resume-imports?filename=resume.txt", headers=alice, content=text.encode()
        ),
        201,
    )
    assert ok(client.get("/api/v1/resume-facts", headers=alice)) == []
    assert [c["suggested_kind"] for c in preview["candidates"]] == [
        "name",
        "contact",
        "project",
        "project",
    ]
    assert preview["candidates"][2]["section_heading"] is True
    claims = [
        {"text": c["text"], "start": c["start"], "end": c["end"], "kind": c["suggested_kind"]}
        for c in preview["candidates"]
        if not c["section_heading"]
    ]
    claims[0]["text"] = "合成学生"
    path = f"/api/v1/resume-imports/{preview['material']['id']}/confirm"
    assert (
        client.post(
            path, headers=version(bob, 1), json={"claims": claims, "confirmed": True}
        ).status_code
        == 404
    )
    assert (
        client.post(
            path, headers=version(alice, 2), json={"claims": claims, "confirmed": True}
        ).status_code
        == 412
    )
    result = ok(
        client.post(path, headers=version(alice, 1), json={"claims": claims, "confirmed": True}),
        201,
    )
    assert len(result["facts"]) == 3
    assert result["facts"][0]["text"] == "合成学生"
    assert result["facts"][0]["source"]["quote"] == "姓名：合成学生"
    assert result["facts"][0]["source"]["material_id"] == preview["material"]["id"]
    assert (
        client.post(
            path, headers=version(alice, 1), json={"claims": claims, "confirmed": True}
        ).status_code
        == 409
    )


def test_text_resume_import_invalid_span_rolls_back_and_long_preview_is_explicit(system):
    client, _, alice, *_ = system
    preview = ok(
        client.post(
            "/api/v1/resume-imports?filename=resume.txt",
            headers=alice,
            content="经历\n".encode() * 101,
        ),
        201,
    )
    assert preview["truncated"] and len(preview["candidates"]) == 100
    assert "100" in preview["notice"]
    path = f"/api/v1/resume-imports/{preview['material']['id']}/confirm"
    assert (
        client.post(
            path,
            headers=version(alice, 1),
            json={
                "confirmed": True,
                "claims": [{"kind": "project", "text": "x", "start": 0, "end": 99999}],
            },
        ).status_code
        == 422
    )
    assert ok(client.get("/api/v1/resume-facts", headers=alice)) == []
    assert (
        client.post(
            "/api/v1/resume-imports?filename=resume.pdf", headers=alice, content=b"%PDF"
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/resume-imports?filename=../resume.txt", headers=alice, content=b"x"
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/resume-imports?filename=resume.txt",
            headers=alice,
            content=b"x" * (1024 * 1024 + 1),
        ).status_code
        == 422
    )


def test_restore_preserves_frozen_job_and_diagnostic_keywords_after_job_update(system):
    client, _, alice, *_ = system
    j = job(client, alice, "岗位：测试岗位\n要求：SQL", url="https://example.com/updated")
    v = resume(client, alice, job_id=j["id"])
    job(client, alice, "岗位：更新岗位\n要求：Docker", url="https://example.com/updated")
    restored = ok(
        client.post(
            f"/api/v1/resumes/{v['resume_id']}/restore",
            headers=version(alice, 2),
            json={"version_id": v["id"], "confirmed": True},
        ),
        201,
    )
    assert restored["job"] == v["job"] and restored["content_hash"] == v["content_hash"]
    assert ok(client.get(f"/api/v1/resume-versions/{v['id']}/ats", headers=alice))[
        "missing_keywords"
    ] == ["sql"]
    assert (
        client.post(
            "/api/v1/applications",
            headers={**alice, "Idempotency-Key": "old-job"},
            json={"job_id": j["id"], "resume_version_id": v["id"], "confirmed": True},
        ).status_code
        == 409
    )


def test_deadline_reminder_quiet_window_and_deferral_delivery(system):
    client, engine, alice, *_ = system
    j = job(client, alice, "岗位：有截止岗位\n截止日期：2029-12-31")
    a = application(client, alice, j=j)
    body = {
        "kind": "deadline",
        "local_time": "2029-12-31T23:00:00",
        "timezone": "UTC",
        "quiet_start": 22,
        "quiet_end": 8,
        "confirmed": True,
    }
    path = f"/api/v1/applications/{a['id']}/reminders"
    assert (
        client.post(path, headers={**alice, "Idempotency-Key": "late"}, json=body).status_code
        == 422
    )
    r = ok(
        client.post(
            path,
            headers={**alice, "Idempotency-Key": "early"},
            json={**body, "local_time": "2029-12-30T23:00:00"},
        ),
        201,
    )
    assert r["next_run_at"] > r["due_at"]
    assert hiring_reminders.schedule_due(engine, r["due_at"]) == 0
    assert hiring_reminders.schedule_due(engine, r["next_run_at"]) == 1
    assert hiring_reminders.schedule_due(engine, r["next_run_at"] + 1) == 0
