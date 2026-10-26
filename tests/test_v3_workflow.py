"""Owned artifacts, editable project statements and constrained planning contracts."""

import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy.orm import Session

from careerpilot import growth, learning, project_evidence, resumes
from tests.test_v1_release import action, exam, ok, setup_plan, version
from tests.test_v3_projects import create, finish, principal_for, submit


def get(client, headers, p):
    return ok(client.get(f"/api/v1/projects/{p['id']}", headers=headers))


def asset(client, headers, p, key="asset-1", **kwargs):
    return client.post(
        f"/api/v1/projects/{p['id']}/assets",
        headers={**headers, "Idempotency-Key": key},
        json={"kind": "text", "filename": "README.md", "content": "实际边界与测试笔记", **kwargs},
    )


def milestone(client, headers, p, key="design", **kwargs):
    return client.patch(
        f"/api/v1/projects/{p['id']}/milestones/{key}",
        headers=version(headers, p["version"]),
        json={
            "status": "completed",
            "note": "实际过程记录",
            "asset_ids": [],
            "confirmed": True,
            **kwargs,
        },
    )


def narrative(client, headers, p, expected=0, **kwargs):
    return client.put(
        f"/api/v1/projects/{p['id']}/narrative",
        headers=version(headers, expected),
        json={
            "status": "confirmed",
            "situation": "课程数据练习",
            "task": "整理岗位记录",
            "action": "本人实现输入检查并复查失败样例",
            "result": "",
            "asset_ids": [],
            "confirmed": True,
            **kwargs,
        },
    )


def plan_project(client, headers, p, plan, key="project-preview"):
    return client.post(
        f"/api/v1/projects/{p['id']}/plan-preview",
        headers={**version(headers, p["version"]), "Idempotency-Key": key},
        json={"plan_id": plan["id"], "confirmed": True},
    )


def decide(client, headers, preview, decision="accept"):
    return client.post(
        f"/api/v1/plan-previews/{preview['id']}/{decision}",
        headers=version(headers, preview["version"]),
        json={"confirmed": True},
    )


def project_with_plan(system, budget=180):
    client, _, headers, *_ = system
    job, plan = setup_plan(system, budget=budget)
    p = ok(
        client.post(
            "/api/v1/projects",
            headers={**headers, "Idempotency-Key": "planned-project"},
            json={
                "job_id": job["id"],
                "template_id": "python-record-summary.v1",
                "confirmed": True,
            },
        ),
        201,
    )
    return p, plan


def test_artifacts_are_private_scanned_idempotent_and_revocable(system):
    client, _, alice, bob, *_ = system
    p = create(client, alice)
    a = ok(asset(client, alice, p), 201)
    assert ok(asset(client, alice, p), 201)["id"] == a["id"]
    assert asset(client, alice, p, content="changed").status_code == 409
    assert client.get(f"/api/v1/project-assets/{a['id']}/download", headers=bob).status_code == 404
    assert (
        client.get(f"/api/v1/project-assets/{a['id']}/download", headers=alice).text == a["content"]
    )
    assert asset(client, alice, p, key="unsafe", filename="../../secret.md").status_code == 422
    assert (
        asset(
            client, alice, p, key="virus", content="EICAR-STANDARD-ANTIVIRUS-TEST-FILE"
        ).status_code
        == 422
    )
    assert asset(client, alice, p, key="large", content="中" * 25000).status_code == 422
    link = ok(
        asset(
            client,
            alice,
            p,
            key="link",
            kind="github_reference",
            content="",
            url="https://github.com/example/demo/commit/" + "a" * 40,
        ),
        201,
    )
    assert link["verification"] == "user_provided_not_fetched_or_verified"
    assert (
        asset(
            client,
            alice,
            p,
            key="bad-link",
            kind="github_reference",
            content="",
            url="https://github.com@127.0.0.1/repo/pull/1",
        ).status_code
        == 422
    )
    ok(
        client.post(
            f"/api/v1/project-assets/{a['id']}/withdraw",
            headers=version(alice, 1),
            json={"confirmed": True},
        )
    )
    assert (
        client.get(f"/api/v1/project-assets/{a['id']}/download", headers=alice).status_code == 409
    )


def test_progress_revisions_do_not_promote_tests_and_missing_sources_are_visible(system):
    client, engine, alice, bob, *_ = system
    p = create(client, alice)
    a = ok(asset(client, alice, p), 201)
    assert milestone(client, alice, p, key="implementation").status_code == 422
    assert milestone(client, alice, p, note="").status_code == 422
    assert milestone(client, bob, p).status_code == 404
    changed = ok(milestone(client, alice, p, asset_ids=[a["id"]]))
    assert milestone(client, alice, p).status_code == 412
    detail = get(client, alice, p)
    assert detail["progress"][0]["status"] == "completed"
    assert detail["progress"][1]["status"] == "unverified"
    assert len(ok(client.get(f"/api/v1/projects/{p['id']}/history", headers=alice))) == 2
    ok(
        client.post(
            f"/api/v1/project-assets/{a['id']}/withdraw",
            headers=version(alice, 1),
            json={"confirmed": True},
        )
    )
    assert get(client, alice, p)["progress"][0]["status"] == "needs_review"
    with Session(engine) as db:
        assert learning.ledger(db, principal_for(db, alice)) == []
    assert changed["version"] == p["version"] + 1


def test_star_unknowns_confirmation_versioning_and_receipt_link(system):
    client, engine, alice, bob, *_ = system
    p = create(client, alice)
    n = ok(narrative(client, alice, p))
    d = ok(client.get(f"/api/v1/projects/{p['id']}/narrative-preview", headers=alice))
    assert "结果" in d["unknown"] and "提升" not in d["text"]
    endpoint = f"/api/v1/projects/{p['id']}/narrative-fact"
    kwargs = {
        "headers": {**version(alice, n["version"]), "Idempotency-Key": "star-fact"},
        "json": {"confirmed": True},
    }
    assert client.post(endpoint, **kwargs).status_code == 422
    ok(submit(client, alice, p), 201)
    finish(engine)
    f = ok(client.post(endpoint, **kwargs), 201)
    assert ok(client.post(endpoint, **kwargs), 201)["id"] == f["id"]
    assert "结果：" not in f["text"] and "代码测试：" in f["text"]
    with Session(engine) as db:
        principal = principal_for(db, alice)
        assert resumes.fact_status(db, principal, f) == "current"
        assert f["id"] in project_evidence.current(db, principal)[0]["resume_fact_ids"]
    assert narrative(client, bob, p).status_code == 404
    ok(narrative(client, alice, p, expected=n["version"], result="仅记录本地测试结果"))
    assert narrative(client, alice, p, expected=n["version"]).status_code == 412
    with Session(engine) as db:
        principal = principal_for(db, alice)
        assert resumes.fact_status(db, principal, f) == "needs_reconfirmation"
        assert f["id"] not in project_evidence.current(db, principal)[0]["resume_fact_ids"]
    assert len(ok(client.get(f"/api/v1/projects/{p['id']}/narrative/history", headers=alice))) == 2


def test_asset_withdrawal_invalidates_confirmed_narrative_facts(system):
    client, engine, alice, *_ = system
    p = create(client, alice)
    a = ok(asset(client, alice, p), 201)
    n = ok(narrative(client, alice, p, asset_ids=[a["id"]]))
    ok(submit(client, alice, p), 201)
    finish(engine)
    f = ok(
        client.post(
            f"/api/v1/projects/{p['id']}/narrative-fact",
            headers={**version(alice, n["version"]), "Idempotency-Key": "fact"},
            json={"confirmed": True},
        ),
        201,
    )
    ok(
        client.post(
            f"/api/v1/project-assets/{a['id']}/withdraw",
            headers=version(alice, 1),
            json={"confirmed": True},
        )
    )
    with Session(engine) as db:
        assert resumes.fact_status(db, principal_for(db, alice), f) == "source_withdrawn"


def test_project_plan_preview_budget_lock_prerequisites_and_explicit_decision(system):
    client, engine, alice, bob, *_ = system
    p, plan = project_with_plan(system, budget=60)
    first = plan["tasks"][0]
    plan = ok(action(client, alice, plan, first["id"], "lock"))
    preview = ok(plan_project(client, alice, p, plan), 201)
    assert sum(t["minutes"] for t in preview["diff"]["added"]) == 180
    assert all(v <= 60 for v in preview["candidate"]["validation"]["weekly_minutes"].values())
    assert next(t for t in preview["candidate"]["tasks"] if t["id"] == first["id"])["locked"]
    assert (
        ok(client.get(f"/api/v1/plans/{plan['id']}", headers=alice))["version"] == plan["version"]
    )
    assert decide(client, bob, preview).status_code == 404
    ok(decide(client, alice, preview, "reject"))
    assert (
        ok(client.get(f"/api/v1/plans/{plan['id']}", headers=alice))["version"] == plan["version"]
    )
    preview = ok(plan_project(client, alice, p, plan, key="accepted"), 201)
    ok(decide(client, alice, preview))
    assert decide(client, alice, preview).status_code == 409
    current = ok(client.get(f"/api/v1/plans/{plan['id']}", headers=alice))
    assert len([t for t in current["tasks"] if t.get("project_id")]) == 4
    assert plan_project(client, alice, p, current, key="repeat").status_code == 409
    with Session(engine) as db:
        assert not learning.ledger(db, principal_for(db, alice))


@pytest.mark.parametrize("change", ["milestone", "asset", "narrative", "submission"])
def test_project_source_changes_invalidate_plan_acceptance(system, change):
    client, _, alice, *_ = system
    p, plan = project_with_plan(system)
    preview = ok(plan_project(client, alice, p, plan), 201)
    if change == "milestone":
        ok(milestone(client, alice, p))
    elif change == "asset":
        ok(asset(client, alice, p), 201)
    elif change == "narrative":
        ok(narrative(client, alice, p))
    else:
        ok(submit(client, alice, p), 201)
    assert decide(client, alice, preview).status_code == 412
    assert next(
        v
        for v in ok(client.get("/api/v1/plan-previews", headers=alice))
        if v["id"] == preview["id"]
    )["stale"]


@pytest.mark.parametrize("locale", ["zh-CN", "en-US"])
def test_project_plan_deadline_failure_stays_preview_only(system, locale):
    client, _, alice, *_ = system
    p, plan = project_with_plan(system, budget=30)
    # Tight deadline after all current work: project work cannot fit in that week.
    with Session(system[1]) as db, db.begin():
        principal = principal_for(db, alice)
        row = growth.owned(db, principal, plan["id"], "plan")
        value = json.loads(row.payload_json)
        value["deadline"] = max(t["date"] for t in value["tasks"])
        growth.save(db, principal, "plan", value, "deadline-fixture", row, row.version)
    response = plan_project(client, {**alice, "Accept-Language": locale}, p, plan)
    preview = ok(response, 201)
    assert preview["validation_error"]["code"] == "DEADLINE_CONFLICT"
    assert response.headers["Content-Language"] == locale
    assert response.headers["Vary"] == "Accept-Language"
    assert preview["validation_error"]["message_locale"] == locale
    with Session(system[1]) as db:
        stored = growth.data(db.get(growth.GrowthRecord, preview["id"]))
    assert "message_locale" not in stored["validation_error"]
    assert stored["candidate"] == preview["candidate"]
    assert stored["diff"] == preview["diff"]
    if locale == "en-US":
        assert preview["validation_error"]["message"] == (
            "The plan cannot be completed before the deadline."
        )
    replay = ok(plan_project(client, alice, p, plan), 201)
    assert replay["id"] == preview["id"] and replay["version"] == preview["version"]
    assert replay["validation_error"]["message"] == stored["validation_error"]["message"]
    assert decide(client, alice, preview).status_code == 422
    ok(decide(client, alice, preview, "reject"))


def test_project_learning_completion_never_writes_foundation_evidence(system):
    client, engine, alice, *_ = system
    p, plan = project_with_plan(system)
    for task in list(plan["tasks"]):
        plan = ok(action(client, alice, plan, task["id"], "start"))
        plan = ok(action(client, alice, plan, task["id"], "complete", outcome="完成基础材料练习"))
    preview = ok(plan_project(client, alice, p, plan), 201)
    ok(decide(client, alice, preview))
    plan = ok(client.get(f"/api/v1/plans/{plan['id']}", headers=alice))
    task = next(t for t in plan["tasks"] if t.get("project_id"))
    with Session(engine) as db:
        before = learning.ledger(db, principal_for(db, alice))
    plan = ok(action(client, alice, plan, task["id"], "start"))
    plan = ok(action(client, alice, plan, task["id"], "complete", outcome="仅记录项目过程"))
    with Session(engine) as db:
        assert learning.ledger(db, principal_for(db, alice)) == before
    _, response = exam(client, alice)
    ok(response)
    preview = ok(
        client.post(
            f"/api/v1/plans/{plan['id']}/replan",
            headers=alice,
            json={"reason": "验证项目任务继续保留"},
        ),
    )
    assert any(
        t.get("project_id") and t["status"] == "pending" for t in preview["candidate"]["tasks"]
    )


def test_current_test_required_for_accepting_review_and_withdrawal_marks_it_historical(system):
    client, engine, alice, *_ = system
    p = create(client, alice)
    endpoint = f"/api/v1/projects/{p['id']}/reviews"

    def review(p, key, decision="accept"):
        return client.post(
            endpoint,
            headers={**version(alice, p["version"]), "Idempotency-Key": key},
            json={"decision": decision, "note": "依据实际材料核对阶段结果", "confirmed": True},
        )

    assert review(p, "premature").status_code == 422
    ok(review(p, "declined", "request_changes"), 201)
    p = ok(milestone(client, alice, p))
    p = ok(milestone(client, alice, p, key="reflection"))
    a = ok(submit(client, alice, p), 201)
    finish(engine)
    p = get(client, alice, p)
    r = ok(review(p, "accepted"), 201)
    assert r["decision"] == "accept"
    assert get(client, alice, p)["reviews"][-1]["current"]
    ok(review(p, "latest-declined", "request_changes"), 201)
    reviews = get(client, alice, p)["reviews"]
    assert reviews[-1]["current"] and not reviews[-2]["current"]
    ok(
        client.post(
            f"/api/v1/project-submissions/{a['id']}/withdraw",
            headers=version(alice, 1),
            json={"confirmed": True},
        )
    )
    assert not get(client, alice, p)["reviews"][-1]["current"]


def test_concurrent_plan_accept_only_adds_project_once(system):
    client, _, alice, *_ = system
    p, plan = project_with_plan(system)
    a = ok(plan_project(client, alice, p, plan, key="a"), 201)
    b = ok(plan_project(client, alice, p, plan, key="b"), 201)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda preview: decide(client, alice, preview), [a, b]))
    assert sorted(r.status_code for r in results) == [200, 412]
    plan = ok(client.get(f"/api/v1/plans/{plan['id']}", headers=alice))
    assert sum(t["minutes"] for t in plan["tasks"] if t.get("project_id")) == 180


def test_interview_receipt_link_and_stale_narrative_block(system):
    from tests.test_v2_hiring import resume
    from tests.test_v2_interviews import answer, complete, evaluate, session

    client, engine, alice, *_ = system
    p = create(client, alice)
    a = ok(asset(client, alice, p), 201)
    n = ok(narrative(client, alice, p, asset_ids=[a["id"]]))
    ok(submit(client, alice, p), 201)
    finish(engine)
    f = ok(
        client.post(
            f"/api/v1/projects/{p['id']}/narrative-fact",
            headers={**version(alice, n["version"]), "Idempotency-Key": "interview-fact"},
            json={"confirmed": True},
        ),
        201,
    )
    j = ok(client.get(f"/api/v1/jobs/{p['job_id']}", headers=alice))
    v = resume(client, alice, [f], j["id"])
    first = session(client, alice, j=j, v=v, facts=[f["id"]], key="current")
    second = session(client, alice, j=j, v=v, facts=[f["id"]], key="before-withdrawal")
    with Session(engine) as db:
        before = learning.ledger(db, principal_for(db, alice))
    evaluation = evaluate(client, alice, complete(client, alice, first))
    assert evaluation["dimensions"]["project_evidence"]["status"] == "receipt_supported"
    ok(
        client.post(
            f"/api/v1/project-assets/{a['id']}/withdraw",
            headers=version(alice, 1),
            json={"confirmed": True},
        )
    )
    assert answer(client, alice, second).status_code == 409
    with Session(engine) as db:
        assert learning.ledger(db, principal_for(db, alice)) == before
