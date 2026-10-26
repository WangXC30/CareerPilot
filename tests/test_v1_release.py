"""V1 user journeys and failure boundaries, exercised through public HTTP contracts."""

import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from careerpilot import accounts, assessment, catalogue, growth, web_sources
from careerpilot.api import create_app
from careerpilot.persistence import User
from careerpilot.runtime import run_once
from careerpilot.v1_models import Credential, SkillEvidence

PROFILE = {
    "education": "bachelor",
    "major": "计算机",
    "academic_year": "大三",
    "graduation_date": "2027-06-30",
    "preferred_cities": ["上海"],
    "weekly_minutes": 600,
    "timezone": "Asia/Shanghai",
    "confirmed": True,
}
JD = (
    "岗位：后端实习生\n公司：测试公司\n地点：上海\n学历：本科\n2027届\n要求：Python 和 SQL\n"
    "薪资：300元/天\n工作类型：实习\n实习时长：3个月\n截止日期：2027-06-30"
)


def ok(response, status=200):
    assert response.status_code == status, response.text
    return response.json() if status != 204 else None


def version(headers, number):
    return {**headers, "If-Match": f'"{number}"'}


def setup_plan(system, *, budget=600, content=JD):
    client, _, alice, *_ = system
    ok(
        client.put(
            "/api/v1/profile", headers=version(alice, 0), json={**PROFILE, "weekly_minutes": budget}
        )
    )
    job = ok(client.post("/api/v1/jobs/import", headers=alice, json={"content": content}), 201)
    draft = ok(
        client.post(
            "/api/v1/plans",
            headers=alice,
            json={"job_ids": [job["id"]], "start_date": "2026-10-20"},
        ),
        201,
    )
    plan = ok(
        client.post(
            f"/api/v1/plans/{draft['id']}/publish",
            headers=version(alice, 1),
            json={"confirmed": True},
        )
    )
    return job, plan


def action(client, headers, plan, task_id, kind, **kwargs):
    return client.post(
        f"/api/v1/plans/{plan['id']}/tasks/{task_id}",
        headers=version(headers, plan["version"]),
        json={"action": kind, **kwargs},
    )


def exam(client, headers, skill="python", answers=None):
    created = ok(client.post("/api/v1/assessments", headers=headers, json={"skill_id": skill}), 201)
    assert all("answer" not in q and "expected_choice" not in q for q in created["questions"])
    response = client.post(
        f"/api/v1/assessments/{created['id']}/submit",
        headers=version(headers, 1),
        json={"answers": answers or [1, 0, 2]},
    )
    return created, response


def test_email_account_login_export_logout_and_password_storage(system):
    client, engine, alice, _, _, _ = system
    payload = {
        "email": "Student@Example.com",
        "password": "exam-only-passphrase!",
        "display_name": "学生",
        "privacy_accepted": True,
    }
    response = ok(client.post("/api/v1/auth/register", json=payload), 201)
    headers = {"Authorization": "Bearer " + response["access_token"]}
    ok(client.put("/api/v1/profile", headers=version(headers, 0), json=PROFILE))
    exported = ok(client.get("/api/v1/account/export", headers=headers))
    assert exported["data"]["growth_records"][0]["kind"] == "profile"
    assert "password_hash" not in json.dumps(exported) and "session_tokens" not in exported["data"]
    assert client.get("/api/v1/profile", headers=alice).json()["version"] == 0
    with Session(engine) as db:
        credential = db.scalar(select(Credential))
        assert credential.email == "student@example.com"
        assert (
            payload["password"] not in credential.password_hash
            and len(credential.password_hash) == 128
        )
    ok(client.post("/api/v1/auth/logout", headers=headers), 204)
    assert client.get("/api/v1/me", headers=headers).status_code == 401
    logged = ok(
        client.post(
            "/api/v1/auth/login", json={"email": payload["email"], "password": payload["password"]}
        )
    )
    assert logged["access_token"] != response["access_token"]
    assert client.post("/api/v1/auth/register", json=payload).status_code == 409


def test_registration_requires_consent_and_limits_password_guessing(system):
    client, *_ = system
    base = {"email": "nobody@example.com", "password": "incorrect-guess-123"}
    assert (
        client.post(
            "/api/v1/auth/register", json={**base, "display_name": "x", "privacy_accepted": False}
        ).status_code
        == 422
    )
    responses = [client.post("/api/v1/auth/login", json=base).status_code for _ in range(11)]
    assert responses[:10] == [401] * 10 and responses[-1] == 429


def test_deletion_revokes_sessions_and_clears_all_owned_objects(system):
    client, engine, alice, bob, _, (alice_id, _) = system
    setup_plan(system)
    exam(client, alice)
    exported = ok(client.get("/api/v1/account/export", headers=alice))
    assert exported["data"]["skill_evidence"]
    assert (
        client.post(
            "/api/v1/account/deletion", headers=alice, json={"confirmation": "delete"}
        ).status_code
        == 422
    )
    request = ok(
        client.post(
            "/api/v1/account/deletion", headers=alice, json={"confirmation": "DELETE MY ACCOUNT"}
        ),
        202,
    )
    assert client.get("/api/v1/me", headers=alice).status_code == 401
    assert accounts.process_deletion(engine)
    status = ok(
        client.get(
            f"/api/v1/account/deletion/{request['id']}",
            headers={"X-Deletion-Receipt": request["receipt"]},
        )
    )
    assert status["status"] == "completed"
    assert (
        client.get(
            f"/api/v1/account/deletion/{request['id']}", headers={"X-Deletion-Receipt": "wrong"}
        ).status_code
        == 404
    )
    with Session(engine) as db:
        assert db.get(User, alice_id) is None
        assert (
            db.scalar(select(growth.GrowthRecord).where(growth.GrowthRecord.user_id == alice_id))
            is None
        )
        assert db.scalar(select(SkillEvidence).where(SkillEvidence.user_id == alice_id)) is None
    assert client.get("/api/v1/me", headers=bob).status_code == 200


def test_catalogue_aliases_dag_and_editor_permissions(system):
    from careerpilot.platform_models import RoleGrant

    client, engine, alice, _, _, (alice_id, _) = system
    with Session(engine) as db:
        assert catalogue.canonical(db, "torch") == "pytorch"
    assert (
        client.put(
            "/api/v1/catalogue/skill/python/dependencies",
            headers=version(alice, 1),
            json={"requires": ["pytorch"]},
        ).status_code
        == 403
    )
    with Session(engine) as db, db.begin():
        user = db.get(User, alice_id)
        db.get(RoleGrant, (alice_id, user.tenant_id)).role = "operator"
    assert (
        client.put(
            "/api/v1/catalogue/skill/python/dependencies",
            headers=version(alice, 1),
            json={"requires": ["pytorch"]},
        ).status_code
        == 422
    )
    assert (
        client.put(
            "/api/v1/catalogue/skill/python/dependencies",
            headers=version(alice, 1),
            json={"requires": ["nonexistent"]},
        ).status_code
        == 422
    )
    assert ok(client.get("/api/v1/catalogue/career", headers=alice))[0]["source_url"]
    assert all(
        r["reviewed_at"] and r["url"]
        for r in ok(client.get("/api/v1/catalogue/resource", headers=alice))
    )


def test_gap_evidence_references_and_constrained_multiweek_plan(system):
    client, _, alice, bob, _, _ = system
    job, plan = setup_plan(system, budget=120, content=JD + "\n要求：FastAPI")
    diagnosis = ok(
        client.post("/api/v1/gap-analysis", headers=alice, json={"job_ids": [job["id"]]})
    )
    python = next(s for s in diagnosis["skills"] if s["skill_id"] == "python")
    assert python["sources"][0]["job_id"] == job["id"] and python["status"] == "unassessed"
    assert all(total <= 120 for total in plan["validation"]["weekly_minutes"].values())
    assert len(plan["validation"]["weekly_minutes"]) >= 3
    by_id = {t["id"]: t for t in plan["tasks"]}
    for task in plan["tasks"]:
        assert task["resources"] and task["definition_of_done"] and task["verification"]
        assert all(by_id[parent]["date"] <= task["date"] for parent in task["requires"])
    assert (
        client.post("/api/v1/gap-analysis", headers=bob, json={"job_ids": [job["id"]]}).status_code
        == 404
    )
    assert client.get(f"/api/v1/plans/{plan['id']}", headers=bob).status_code == 404


def test_no_budget_or_impossible_deadline_cannot_publish(system):
    client, _, alice, *_ = system
    ok(
        client.put(
            "/api/v1/profile", headers=version(alice, 0), json={**PROFILE, "weekly_minutes": 0}
        )
    )
    job = ok(client.post("/api/v1/jobs/import", headers=alice, json={"content": JD}), 201)
    body = {"job_ids": [job["id"]], "start_date": "2026-10-20"}
    assert client.post("/api/v1/plans", headers=alice, json=body).status_code == 422
    ok(
        client.put(
            "/api/v1/profile", headers=version(alice, 1), json={**PROFILE, "weekly_minutes": 60}
        )
    )
    assert (
        client.post(
            "/api/v1/plans", headers=alice, json={**body, "deadline": "2026-10-20"}
        ).status_code
        == 422
    )
    assert ok(client.get("/api/v1/plans", headers=alice)) == []


def test_task_start_pause_split_complete_and_budget_conflicts(system):
    client, _, alice, _, _, _ = system
    _, plan = setup_plan(system, budget=180, content="岗位：Python 实习\n要求：Python")
    first = plan["tasks"][0]
    plan = ok(action(client, alice, plan, first["id"], "split", minutes=30))
    assert len(plan["tasks"]) == 3
    first = plan["tasks"][0]
    assert action(client, alice, plan, plan["tasks"][1]["id"], "start").status_code == 422
    plan = ok(action(client, alice, plan, first["id"], "start"))
    plan = ok(action(client, alice, plan, first["id"], "pause"))
    plan = ok(action(client, alice, plan, first["id"], "start"))
    assert action(client, alice, plan, first["id"], "complete").status_code == 422
    plan = ok(action(client, alice, plan, first["id"], "complete", outcome="完成练习并记录错误"))
    status = next(
        s for s in ok(client.get("/api/v1/skills", headers=alice)) if s["skill_id"] == "python"
    )
    assert status["status"] == "supported"
    assert action(client, alice, plan, first["id"], "start").status_code == 409
    assert (
        action(client, alice, plan, plan["tasks"][1]["id"], "defer", date="2027-01-01").status_code
        == 422
    )


def test_failed_assessment_proposes_remedy_without_upgrading_or_overwriting_locked_work(system):
    client, _, alice, _, _, _ = system
    _, plan = setup_plan(system)
    locked = plan["tasks"][-1]
    plan = ok(action(client, alice, plan, locked["id"], "lock"))
    locked = next(t for t in plan["tasks"] if t["id"] == locked["id"])
    before = ok(client.get(f"/api/v1/plans/{plan['id']}", headers=alice))
    _, response = exam(client, alice, answers=[0, 0, 0])
    result = ok(response)
    assert result["status"] == "failed" and result["evidence"]["conclusion"] == "failed"
    preview = result["replan_preview"]
    assert preview["reason"] == "EvidenceChanged" and any(
        t.get("remedial") for t in preview["diff"]["added"]
    )
    assert next(t for t in preview["candidate"]["tasks"] if t["id"] == locked["id"]) == locked
    assert ok(client.get(f"/api/v1/plans/{plan['id']}", headers=alice)) == before
    state = next(
        s for s in ok(client.get("/api/v1/skills", headers=alice)) if s["skill_id"] == "python"
    )
    assert state["status"] == "needs_reassessment"
    ok(
        client.post(
            f"/api/v1/plan-previews/{preview['id']}/reject",
            headers=version(alice, 1),
            json={"confirmed": True},
        )
    )
    assert ok(client.get(f"/api/v1/plans/{plan['id']}", headers=alice)) == before


def test_pass_evidence_replan_accept_restore_and_revocation(system):
    client, _, alice, bob, _, _ = system
    _, plan = setup_plan(system)
    _, response = exam(client, alice)
    result = ok(response)
    assert result["status"] == "passed" and result["evidence"]["rubric_version"]
    preview = result["replan_preview"]
    assert any(t["skill_id"] == "python" for t in preview["diff"]["removed"])
    path = f"/api/v1/plan-previews/{preview['id']}/accept"
    assert client.post(path, headers=version(bob, 1), json={"confirmed": True}).status_code == 404
    ok(client.post(path, headers=version(alice, 1), json={"confirmed": True}))
    new_plan = ok(client.get(f"/api/v1/plans/{plan['id']}", headers=alice))
    assert not any(t["skill_id"] == "python" for t in new_plan["tasks"])
    restored = ok(
        client.post(
            f"/api/v1/plans/{plan['id']}/restore",
            headers=version(alice, new_plan["version"]),
            json={"target_version": plan["version"], "confirmed": True},
        )
    )
    assert restored["tasks"] == plan["tasks"]
    ok(
        client.post(
            f"/api/v1/skill-evidence/{result['evidence']['id']}/revoke",
            headers=alice,
            json={"reason": "需要重新考核", "confirmed": True},
        )
    )
    assert (
        next(
            s
            for s in client.get("/api/v1/skills", headers=alice).json()
            if s["skill_id"] == "python"
        )["status"]
        == "needs_reassessment"
    )


def test_assessment_replay_forgery_wrong_version_and_evaluator_outage(system, monkeypatch):
    client, _, alice, bob, _, _ = system
    created = ok(
        client.post("/api/v1/assessments", headers=alice, json={"skill_id": "python"}), 201
    )
    path = f"/api/v1/assessments/{created['id']}/submit"
    assert (
        client.post(path, headers=version(bob, 1), json={"answers": [1, 0, 2]}).status_code == 404
    )
    assert (
        client.post(path, headers=version(alice, 99), json={"answers": [1, 0, 2]}).status_code
        == 412
    )
    assert (
        client.post(
            path, headers=version(alice, 1), json={"answers": [1, 0, 2], "passed": True}
        ).status_code
        == 422
    )
    good = ok(client.post(path, headers=version(alice, 1), json={"answers": [1, 0, 2]}))
    assert (
        ok(client.post(path, headers=version(alice, 1), json={"answers": [1, 0, 2]}))["id"]
        == good["id"]
    )
    assert len(ok(client.get("/api/v1/skill-evidence", headers=alice))) == 1

    def outage(*_):
        raise RuntimeError("private exception must not leak")

    monkeypatch.setattr(assessment, "grade", outage)
    _, result = exam(client, alice, skill="sql")
    assert ok(result)["status"] == "system_error"
    assert "private exception" not in result.text
    assert len(ok(client.get("/api/v1/skill-evidence", headers=alice))) == 1


def test_stale_plan_previews_and_publish_are_rejected(system):
    client, _, alice, _, _, _ = system
    _, plan = setup_plan(system)
    preview = ok(client.post(f"/api/v1/plans/{plan['id']}/replan", headers=alice))
    plan = ok(action(client, alice, plan, plan["tasks"][0]["id"], "lock"))
    assert (
        client.post(
            f"/api/v1/plan-previews/{preview['id']}/accept",
            headers=version(alice, 1),
            json={"confirmed": True},
        ).status_code
        == 412
    )
    profile = ok(client.get("/api/v1/profile", headers=alice))
    ok(
        client.put(
            "/api/v1/profile",
            headers=version(alice, profile["version"]),
            json={**PROFILE, "weekly_minutes": 60},
        )
    )
    assert ok(client.get(f"/api/v1/plans/{plan['id']}", headers=alice))["tasks"] == plan["tasks"]
    assert any(
        p["reason"] == "ProfileChanged"
        for p in client.get("/api/v1/plan-previews", headers=alice).json()
    )


def test_goal_switch_keeps_plan_until_accepting_replan_and_protects_tasks(system):
    client, _, alice, _, _, _ = system
    _, plan = setup_plan(system)
    plan = ok(action(client, alice, plan, plan["tasks"][0]["id"], "lock"))
    goal = ok(
        client.post(
            "/api/v1/career-goals",
            headers={**alice, "Idempotency-Key": "goal"},
            json={"title": "AI 应用工程师"},
        ),
        201,
    )
    preview = ok(client.post(f"/api/v1/career-goals/{goal['id']}/preview", headers=alice))
    assert preview["plan_impact"]["protected_tasks"] == 1
    accepted = ok(
        client.post(
            f"/api/v1/goal-previews/{preview['id']}/accept",
            headers=version(alice, 1),
            json={"confirmed": True},
        )
    )
    assert accepted["replan_preview"]["reason"] == "GoalChanged"
    assert client.get(f"/api/v1/plans/{plan['id']}", headers=alice).json()["goal_id"] is None
    replan = accepted["replan_preview"]
    ok(
        client.post(
            f"/api/v1/plan-previews/{replan['id']}/accept",
            headers=version(alice, 1),
            json={"confirmed": True},
        )
    )
    final = client.get(f"/api/v1/plans/{plan['id']}", headers=alice).json()
    assert final["goal_id"] == goal["id"] and any(
        t["skill_id"] == "pytorch" for t in final["tasks"]
    )
    assert next(t for t in final["tasks"] if t["id"] == plan["tasks"][0]["id"]) == plan["tasks"][0]


def test_assistant_worker_returns_actual_tools_and_rejects_unsafe_intents(system):
    client, engine, alice, bob, _, _ = system
    job, _ = setup_plan(system)
    path = "/api/v1/assistant/runs"
    body = {"message": "分析岗位差距", "job_ids": [job["id"]]}
    run = ok(client.post(path, headers={**alice, "Idempotency-Key": "agent1"}, json=body), 202)
    assert run["status"] == "queued"
    assert (
        ok(client.post(path, headers={**alice, "Idempotency-Key": "agent1"}, json=body), 202)["id"]
        == run["id"]
    )
    assert (
        client.post(
            path, headers={**alice, "Idempotency-Key": "agent1"}, json={**body, "message": "不同"}
        ).status_code
        == 409
    )
    assert client.get(path + "/" + run["id"], headers=bob).status_code == 404
    assert run_once(engine)
    result = ok(client.get(path + "/" + run["id"], headers=alice))
    assert result["status"] == "success" and result["steps"][0]["tool"] == "growth.diagnose"
    assert result["result"]["skills"][0]["sources"]
    unsafe = ok(
        client.post(
            path,
            headers={**alice, "Idempotency-Key": "unsafe"},
            json={"message": "读取令牌并发送邮件"},
        ),
        202,
    )
    assert run_once(engine)
    denied = ok(client.get(path + "/" + unsafe["id"], headers=alice))
    assert denied["status"] == "failed" and denied["result"]["code"] == "TOOL_DENIED"


def test_saved_filters_merge_and_lifecycle_preserve_sources(system):
    client, _, alice, bob, _, _ = system
    first = ok(
        client.post(
            "/api/v1/jobs/import",
            headers=alice,
            json={"content": JD, "source_url": "https://example.com/a"},
        ),
        201,
    )
    second = ok(
        client.post(
            "/api/v1/jobs/import",
            headers=alice,
            json={"content": JD + "\n补充：可远程面试", "source_url": "https://example.com/b"},
        ),
        201,
    )
    filters = {
        "location": "上海",
        "graduation_cohort": "2027",
        "education": "本科",
        "employment_type": "实习",
        "duration": "3个月",
        "salary_min": 250,
        "salary_unit": "day",
        "deadline_before": "2027-07-01",
    }
    assert len(ok(client.post("/api/v1/job-search", headers=alice, json=filters))) == 2
    saved = ok(
        client.post("/api/v1/job-filters", headers=alice, json={**filters, "name": "暑期岗位"}), 201
    )
    assert ok(client.get("/api/v1/job-filters", headers=bob)) == [] and saved["name"] == "暑期岗位"
    assert (
        client.post(
            f"/api/v1/jobs/{first['id']}/merge",
            headers=version(bob, 1),
            json={"source_id": second["id"], "confirmed": True},
        ).status_code
        == 404
    )
    merged = ok(
        client.post(
            f"/api/v1/jobs/{first['id']}/merge",
            headers=version(alice, 1),
            json={"source_id": second["id"], "confirmed": True},
        )
    )
    assert (
        len(merged["sources"]) == 2 and merged["merged_records"][0]["content"] == second["content"]
    )
    assert len(ok(client.post("/api/v1/job-search", headers=alice, json={}))) == 1
    changed = ok(
        client.post(
            f"/api/v1/jobs/{first['id']}/status",
            headers=version(alice, merged["version"]),
            json={
                "status": "possibly_expired",
                "reason": "用户发现截止时间可能已过",
                "confirmed": True,
            },
        )
    )
    assert changed["status"] == "possibly_expired"


def test_restart_retains_active_plan_evidence_and_preview_history(system):
    client, _, alice, _, config, _ = system
    _, plan = setup_plan(system)
    _, response = exam(client, alice, answers=[0, 0, 0])
    preview = ok(response)["replan_preview"]
    with TestClient(create_app(config.attributes["database_url"])) as restarted:
        assert (
            restarted.get(f"/api/v1/plans/{plan['id']}", headers=alice).json()["version"]
            == plan["version"]
        )
        assert any(
            p["id"] == preview["id"]
            for p in restarted.get("/api/v1/plan-previews", headers=alice).json()
        )
        assert len(restarted.get("/api/v1/skill-evidence", headers=alice).json()) == 1


def test_simultaneous_plan_publish_keeps_one_active_plan(system):
    client, _, alice, _, _, _ = system
    ok(client.put("/api/v1/profile", headers=version(alice, 0), json=PROFILE))
    job = ok(client.post("/api/v1/jobs/import", headers=alice, json={"content": JD}), 201)
    drafts = [
        ok(
            client.post(
                "/api/v1/plans",
                headers=alice,
                json={"job_ids": [job["id"]], "start_date": "2026-10-20"},
            ),
            201,
        )
        for _ in range(2)
    ]
    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(
            pool.map(
                lambda p: client.post(
                    f"/api/v1/plans/{p['id']}/publish",
                    headers=version(alice, 1),
                    json={"confirmed": True},
                ),
                drafts,
            )
        )
    assert all(r.status_code == 200 for r in result)
    assert (
        sum(p["status"] == "active" for p in client.get("/api/v1/plans", headers=alice).json()) == 1
    )


def test_web_requires_consent_and_ssrf_denials_do_not_change_job_status(system):
    client, _, alice, _, _, _ = system
    job = ok(client.post("/api/v1/jobs/import", headers=alice, json={"content": JD}), 201)
    assert (
        client.post(
            "/api/v1/web/fetch", headers=alice, json={"url": "https://docs.python.org/3/"}
        ).status_code
        == 403
    )
    ok(client.put("/api/v1/consents/public_web", headers=alice, json={"granted": True}))
    for url in ["http://docs.python.org/", "https://127.0.0.1/", "https://unlisted.example.com/"]:
        assert client.post("/api/v1/web/fetch", headers=alice, json={"url": url}).status_code == 422
    assert client.get(f"/api/v1/jobs/{job['id']}", headers=alice).json()["status"] == "unverified"
    ok(client.put("/api/v1/consents/public_web", headers=alice, json={"granted": False}))
    assert (
        client.post(
            "/api/v1/web/fetch", headers=alice, json={"url": "https://docs.python.org/3/"}
        ).status_code
        == 403
    )


def test_web_pins_ip_checks_every_redirect_and_strips_scripts():
    def resolver(*args, **kwargs):
        return [(2, 1, 6, "", ("93.184.216.34", 443))]

    class Response:
        status = 200

        def getheader(self, name, default=None):
            return {"Content-Type": "text/html"}.get(name, default)

        def read(self, _):
            return b"<h1>Official guide</h1><script>send_private_tokens()</script><p>Example</p>"

    calls = []

    class Connection:
        def request(self, *args, **kwargs):
            calls.append((args, kwargs))

        def getresponse(self):
            return Response()

        def close(self):
            pass

    def factory(host, address):
        assert host == "docs.python.org" and address == "93.184.216.34"
        return Connection()

    result = web_sources.fetch_public(
        "https://docs.python.org/3/", resolver=resolver, connection_factory=factory
    )
    assert "Official guide" in result["excerpt"] and "private_tokens" not in result["excerpt"]
    assert calls[0][1]["headers"]["Host"] == "docs.python.org"
    Response.status = 302
    Response.getheader = lambda self, name, default=None: (
        "https://127.0.0.1/" if name == "Location" else default
    )
    with pytest.raises(Exception) as blocked:
        web_sources.fetch_public(
            "https://docs.python.org/3/", resolver=resolver, connection_factory=factory
        )
    assert blocked.value.code == "UNSAFE_URL"


def test_memory_conflict_resolution_and_goal_milestones(system):
    client, _, alice, bob, _, _ = system
    memories = []
    for text in ["我会 Python", "Python 仍需学习"]:
        material = ok(
            client.post(
                "/api/v1/materials?filename=notes.txt", headers=alice, content=text.encode()
            ),
            201,
        )
        confirmed = ok(
            client.post(
                f"/api/v1/materials/{material['id']}/confirm",
                headers=version(alice, 1),
                json={"confirmed": True, "claims": material["candidates"]},
            )
        )
        memories.append(confirmed["memories"][0])
    assert ok(client.get("/api/v1/memory-conflicts", headers=alice))
    body = {
        "preferred_id": memories[1]["id"],
        "superseded_id": memories[0]["id"],
        "confirmed": True,
    }
    assert (
        client.post(
            "/api/v1/memory-conflicts/resolve", headers=version(bob, 1), json=body
        ).status_code
        == 404
    )
    ok(client.post("/api/v1/memory-conflicts/resolve", headers=version(alice, 1), json=body))
    assert ok(client.get("/api/v1/memory-conflicts", headers=alice)) == []
    goal = ok(
        client.post(
            "/api/v1/career-goals",
            headers={**alice, "Idempotency-Key": "milestone"},
            json={"title": "后端工程师"},
        ),
        201,
    )
    milestone = ok(
        client.post(
            f"/api/v1/career-goals/{goal['id']}/milestones",
            headers=alice,
            json={"title": "完成基础 SQL 测验", "target_date": "2026-12-01", "confirmed": True},
        ),
        201,
    )
    assert client.get("/api/v1/goal-milestones", headers=bob).json() == []
    updated = ok(
        client.patch(
            f"/api/v1/goal-milestones/{milestone['id']}",
            headers=version(alice, 1),
            json={"completed": True, "confirmed": True},
        )
    )
    assert updated["completed"] and updated["version"] == 2


def test_draft_cannot_reuse_revoked_capability_and_expired_evidence_is_not_verified(
    system, monkeypatch
):
    from careerpilot import learning

    client, _, alice, _, _, _ = system
    ok(client.put("/api/v1/profile", headers=version(alice, 0), json=PROFILE))
    _, scored = exam(client, alice)
    evidence = ok(scored)["evidence"]
    job = ok(
        client.post(
            "/api/v1/jobs/import", headers=alice, json={"content": "岗位：后端\n要求：FastAPI"}
        ),
        201,
    )
    draft = ok(
        client.post(
            "/api/v1/plans",
            headers=alice,
            json={"job_ids": [job["id"]], "start_date": "2026-10-20"},
        ),
        201,
    )
    assert "python" in draft["verified_skills"]
    ok(
        client.post(
            f"/api/v1/skill-evidence/{evidence['id']}/revoke",
            headers=alice,
            json={"reason": "撤销已过时结果", "confirmed": True},
        )
    )
    assert (
        client.post(
            f"/api/v1/plans/{draft['id']}/publish",
            headers=version(alice, 1),
            json={"confirmed": True},
        ).status_code
        == 412
    )
    assert (
        next(
            s
            for s in client.get("/api/v1/skills", headers=alice).json()
            if s["skill_id"] == "python"
        )["status"]
        != "verified"
    )
    exam(client, alice)
    monkeypatch.setattr(learning, "utc_now", lambda: "2030-01-01T00:00:00+00:00")
    assert (
        next(
            s
            for s in client.get("/api/v1/skills", headers=alice).json()
            if s["skill_id"] == "python"
        )["status"]
        == "needs_reassessment"
    )


def test_expired_job_is_only_possibly_expired_and_invalid_date_is_unknown(system):
    client, _, alice, _, _, _ = system
    expired = ok(
        client.post(
            "/api/v1/jobs/import",
            headers=alice,
            json={"content": "岗位：实习\n截止日期：2020-01-01\n要求：Python"},
        ),
        201,
    )
    assert expired["status"] == "possibly_expired"
    invalid = ok(
        client.post(
            "/api/v1/jobs/import",
            headers=alice,
            json={"content": "岗位：实习\n截止日期：2026-02-30\n要求：SQL"},
        ),
        201,
    )
    assert invalid["status"] == "unverified" and invalid["fields"]["deadline"]["state"] == "unknown"


def test_assistant_can_read_progress_and_find_named_resources_before_profile(system):
    client, engine, alice, _, _, _ = system
    for index, message in enumerate(["查看当前进展", "给我 Python 学习资源"]):
        queued = ok(
            client.post(
                "/api/v1/assistant/runs",
                headers={**alice, "Idempotency-Key": f"new-user-{index}"},
                json={"message": message},
            ),
            202,
        )
        assert run_once(engine)
        result = ok(client.get("/api/v1/assistant/runs/" + queued["id"], headers=alice))
        assert result["status"] == "success"
        if index == 1:
            assert result["steps"][0]["arguments"]["query"] == "python"
            assert result["result"]["results"][0]["skill_id"] == "python"
