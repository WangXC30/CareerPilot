"""Practice is a private, reviewable artifact, never an assertion of verified ability."""

import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from careerpilot import accounts, growth, learning, project_evidence
from careerpilot.v1_models import SkillEvidence
from tests.test_v1_release import action, exam, ok, setup_plan, version
from tests.test_v2_hiring import fact, resume
from tests.test_v2_matching import get_match
from tests.test_v2_matching import setup as matching_setup
from tests.test_v2_opportunities import job, principal_for


def session(client, headers, *, j=None, v=None, facts=None, key="interview-1"):
    j = j or job(client, headers)
    if v is None:
        f = fact(client, headers, "使用 Python 完成课程中的数据整理练习。", "project")
        facts = [f["id"]]
        v = resume(client, headers, [f], j["id"])
    return ok(
        client.post(
            "/api/v1/interviews",
            headers={**headers, "Idempotency-Key": key},
            json={
                "job_id": j["id"],
                "resume_version_id": v["id"],
                "fact_ids": facts or [],
                "confirmed": True,
            },
        ),
        201,
    )


def answer(
    client,
    headers,
    s,
    *,
    text="背景是课程任务，我先检查输入，结果发现字段异常；验证日志后记录了未知问题。",
    choice=0,
    skipped=False,
):
    q = s["current_question"]
    return client.post(
        f"/api/v1/interviews/{s['id']}/answers",
        headers=version(headers, s["version"]),
        json={
            "question_id": q["id"],
            "text": text,
            "choice": choice if q["kind"] == "foundation" else None,
            "skipped": skipped,
        },
    )


def complete(client, headers, s=None, *, skipped=False):
    s = s or session(client, headers)
    while s["status"] == "running":
        s = ok(answer(client, headers, s, skipped=skipped))
    return s


def evaluate(client, headers, s):
    return ok(
        client.post(
            f"/api/v1/interviews/{s['id']}/evaluate",
            headers=version(headers, s["version"]),
            json={"confirmed": True},
        ),
        201,
    )


def review(client, headers, e, revision=0, **kwargs):
    suggestion = e["suggested_review"][0]
    return ok(
        client.put(
            f"/api/v1/interview-evaluations/{e['id']}/review",
            headers=version(headers, revision),
            json={
                "status": "confirmed",
                "summary": "结构规则只作为练习参考，我选择复查此知识点。",
                "decisions": [{**suggestion, "minutes": 30}],
                "confirmed": True,
                **kwargs,
            },
        )
    )


def preview(client, headers, r, plan, key="review-preview"):
    return client.post(
        f"/api/v1/interview-reviews/{r['id']}/plan-preview",
        headers={**version(headers, r["version"]), "Idempotency-Key": key},
        json={"plan_id": plan["id"], "confirmed": True},
    )


def test_session_frozen_context_followups_history_and_no_hidden_answers(system):
    client, _, alice, bob, *_ = system
    s = session(client, alice)
    assert s["current_question"]["kind"] == "foundation"
    assert "expected_choice" not in json.dumps(s) and "explanation" not in json.dumps(s)
    assert client.get(f"/api/v1/interviews/{s['id']}", headers=bob).status_code == 404
    assert client.get(f"/api/v1/interviews/{s['id']}/history", headers=bob).status_code == 404
    assert (
        client.post(
            f"/api/v1/interviews/{s['id']}/evaluate",
            headers=version(alice, 1),
            json={"confirmed": True},
        ).status_code
        == 409
    )
    first = s
    s = ok(answer(client, alice, s, choice=1))
    assert s["current_question"]["id"] == "project"
    assert answer(client, alice, first).status_code == 412
    s = ok(answer(client, alice, s, text="我先记录未确定的指标，不把它当成已验证结果。"))
    assert s["current_question"]["kind"] == "followup"
    assert "未确定的指标" in s["current_question"]["prompt"]
    assert s["current_question"]["references"][0]["quote"] == s["answers"][-1]["text"]
    s = complete(client, alice, s)
    assert s["question_count"] == 6 and s["answered_count"] == 6 and s["status"] == "completed"
    history = ok(client.get(f"/api/v1/interviews/{s['id']}/history", headers=alice))
    assert len(history) == 7 and "expected_choice" not in json.dumps(history)
    assert s["resume_version_id"] == first["resume_version_id"]
    assert (
        client.post(
            f"/api/v1/interviews/{s['id']}/answers",
            headers=version(alice, s["version"]),
            json={"question_id": "foundation", "choice": 1},
        ).status_code
        == 409
    )


def test_session_create_idempotency_foreign_context_and_no_arbitrary_project(system):
    client, _, alice, bob, *_ = system
    j = job(client, alice)
    f = fact(client, alice, "真实练习", "project")
    v = resume(client, alice, [f], j["id"])
    s = session(client, alice, j=j, v=v, facts=[f["id"]])
    assert session(client, alice, j=j, v=v, facts=[f["id"]])["id"] == s["id"]
    body = {"job_id": j["id"], "resume_version_id": v["id"], "fact_ids": [], "confirmed": True}
    assert (
        client.post(
            "/api/v1/interviews", headers={**alice, "Idempotency-Key": "interview-1"}, json=body
        ).status_code
        == 409
    )
    assert (
        client.post(
            "/api/v1/interviews", headers={**bob, "Idempotency-Key": "b"}, json=body
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/api/v1/interviews",
            headers={**alice, "Idempotency-Key": "other"},
            json={**body, "fact_ids": ["invented"]},
        ).status_code
        == 422
    )
    assert ok(client.get("/api/v1/interviews", headers=bob)) == []


def test_skip_and_missing_supported_skills_keep_correctness_unknown(system):
    client, _, alice, *_ = system
    j = job(client, alice, "岗位：未收录知识岗位\n职责：待核对领域任务")
    v = resume(client, alice, job_id=j["id"])
    s = session(client, alice, j=j, v=v)
    assert s["current_question"]["kind"] == "open_foundation"
    s = complete(client, alice, s, skipped=True)
    assert s["question_count"] == 4
    e = evaluate(client, alice, s)
    assert all(d["score"] is None for d in e["dimensions"].values())
    assert e["suggested_review"] == []


def test_evaluation_has_spans_uncertainty_idempotency_and_no_skill_mutation(system):
    client, _, alice, bob, *_ = system
    exam(client, alice)
    before = ok(client.get("/api/v1/skill-evidence", headers=alice))
    s = complete(client, alice)
    e = evaluate(client, alice, s)
    assert e["dimensions"]["correctness"]["score"] == 0
    assert e["dimensions"]["correctness"]["evaluated_questions"] == 1
    assert e["dimensions"]["project_evidence"]["score"] is None
    assert all(f["correctness"] is None for f in e["feedback"][1:])
    for f in e["feedback"]:
        response = next(a for a in s["answers"] if a["question_id"] == f["question_id"])
        for span in f["spans"]:
            assert response["text"][span["start"] : span["end"]] == span["quote"]
    assert evaluate(client, alice, s)["id"] == e["id"]
    assert ok(client.get("/api/v1/skill-evidence", headers=alice)) == before
    assert (
        next(
            t for t in ok(client.get("/api/v1/skills", headers=alice)) if t["skill_id"] == "python"
        )["status"]
        == "verified"
    )
    assert client.get(f"/api/v1/interview-evaluations/{e['id']}", headers=bob).status_code == 404
    assert ok(client.get("/api/v1/resume-facts", headers=alice))
    assert len(ok(client.get("/api/v1/memories", headers=alice))) == 0


def test_stale_fact_stops_new_project_question_and_abandon_is_terminal(system):
    client, _, alice, *_ = system
    s = session(client, alice)
    f = ok(client.get("/api/v1/resume-facts", headers=alice))[0]
    ok(
        client.post(
            f"/api/v1/resume-facts/{f['id']}/revoke",
            headers=alice,
            json={"reason": "撤销本次陈述", "confirmed": True},
        )
    )
    assert answer(client, alice, s).status_code == 409
    s = ok(
        client.post(
            f"/api/v1/interviews/{s['id']}/abandon",
            headers=version(alice, s["version"]),
            json={"confirmed": True},
        )
    )
    assert s["status"] == "abandoned"
    assert (
        client.post(
            f"/api/v1/interviews/{s['id']}/abandon",
            headers=version(alice, s["version"]),
            json={"confirmed": True},
        ).status_code
        == 409
    )


def test_answer_input_proof_and_credentials_rejected(system):
    client, _, alice, *_ = system
    s = session(client, alice)
    path = f"/api/v1/interviews/{s['id']}/answers"
    for body in [
        {"question_id": "wrong", "choice": 1},
        {"question_id": "foundation"},
        {"question_id": "foundation", "choice": True},
        {"question_id": "foundation", "choice": 1, "verified": True},
        {"question_id": "foundation", "choice": 1, "text": "sk_" + "x" * 24},
    ]:
        assert client.post(path, headers=version(alice, 1), json=body).status_code in {409, 422}


def test_two_parallel_answers_commit_exactly_one_turn(system):
    client, _, alice, *_ = system
    s = session(client, alice)
    with ThreadPoolExecutor(max_workers=2) as pool:
        codes = list(pool.map(lambda _: answer(client, alice, s).status_code, range(2)))
    assert sorted(codes) == [200, 412]
    assert ok(client.get(f"/api/v1/interviews/{s['id']}", headers=alice))["answered_count"] == 1


def test_project_failure_does_not_downgrade_verified_foundation(system):
    client, engine, alice, _, _, ids = system
    exam(client, alice)
    seed_project(engine, principal_for(engine, ids[0]), conclusion="failed")
    assert (
        next(
            s for s in ok(client.get("/api/v1/skills", headers=alice)) if s["skill_id"] == "python"
        )["status"]
        == "verified"
    )


def test_review_edits_withdrawal_stale_preview_and_atomic_plan_commit(system):
    client, _, alice, bob, *_ = system
    j, plan = setup_plan(system, budget=120)
    plan = ok(action(client, alice, plan, plan["tasks"][0]["id"], "lock"))
    fixed = [t for t in plan["tasks"] if t["locked"]]
    v = resume(client, alice, job_id=j["id"])
    s = complete(client, alice, session(client, alice, j=j, v=v))
    e = evaluate(client, alice, s)
    r = review(client, alice, e)
    p = ok(preview(client, alice, r, plan), 201)
    assert (
        ok(client.get(f"/api/v1/plans/{plan['id']}", headers=alice))["version"] == plan["version"]
    )
    assert p["validation_error"] is None and p["diff"]["added"] and not p["diff"]["removed"]
    assert all(t in p["candidate"]["tasks"] for t in fixed)
    assert max(p["candidate"]["validation"]["weekly_minutes"].values()) <= 120
    assert preview(client, bob, r, plan).status_code == 404
    r = review(client, alice, e, r["version"], summary="改为先复查知识点，再回看证据。")
    assert (
        client.post(
            f"/api/v1/plan-previews/{p['id']}/accept",
            headers=version(alice, 1),
            json={"confirmed": True},
        ).status_code
        == 412
    )
    assert (
        ok(client.get(f"/api/v1/plans/{plan['id']}", headers=alice))["version"] == plan["version"]
    )
    p = ok(preview(client, alice, r, plan, key="edited-preview"), 201)
    ok(
        client.post(
            f"/api/v1/plan-previews/{p['id']}/accept",
            headers=version(alice, 1),
            json={"confirmed": True},
        )
    )
    updated = ok(client.get(f"/api/v1/plans/{plan['id']}", headers=alice))
    assert all(t in updated["tasks"] for t in fixed)
    assert all(
        t["verification"] == "practice_review_not_capability_verification"
        for t in updated["tasks"]
        if t.get("interview_review_id")
    )
    assert preview(client, alice, r, updated, key="no-duplicate").status_code == 409
    r = review(client, alice, e, r["version"], status="withdrawn", decisions=[])
    assert preview(client, alice, r, updated, key="withdrawn").status_code == 422


def test_review_requires_related_skill_and_rejection_preserves_plan(system):
    client, _, alice, bob, *_ = system
    j, plan = setup_plan(system)
    s = complete(
        client, alice, session(client, alice, j=j, v=resume(client, alice, job_id=j["id"]))
    )
    e = evaluate(client, alice, s)
    args = {
        "status": "confirmed",
        "summary": "复盘",
        "confirmed": True,
        "decisions": [{"question_id": "foundation", "skill_id": "docker", "minutes": 30}],
    }
    path = f"/api/v1/interview-evaluations/{e['id']}/review"
    assert client.put(path, headers=version(alice, 0), json=args).status_code == 422
    assert client.put(path, headers=version(bob, 0), json=args).status_code == 404
    r = review(client, alice, e)
    p = ok(preview(client, alice, r, plan), 201)
    ok(
        client.post(
            f"/api/v1/plan-previews/{p['id']}/reject",
            headers=version(alice, 1),
            json={"confirmed": True},
        )
    )
    assert (
        ok(client.get(f"/api/v1/plans/{plan['id']}", headers=alice))["version"] == plan["version"]
    )


@pytest.mark.parametrize("locale", ["zh-CN", "en-US"])
def test_review_deadline_conflict_blocks_acceptance(system, locale):
    client, engine, alice, _, _, ids = system
    j, plan = setup_plan(system, budget=30)
    with Session(engine) as db, db.begin():
        row = db.get(growth.GrowthRecord, plan["id"])
        payload = json.loads(row.payload_json)
        payload["deadline"] = "2026-10-20"
        row.payload_json = growth.dump(payload)
    s = complete(
        client, alice, session(client, alice, j=j, v=resume(client, alice, job_id=j["id"]))
    )
    e = evaluate(client, alice, s)
    r = review(client, alice, e)
    localized = {**alice, "Accept-Language": locale}
    response = preview(client, localized, r, plan)
    p = ok(response, 201)
    assert p["validation_error"]["code"] == "DEADLINE_CONFLICT"
    assert response.headers["Content-Language"] == locale
    assert response.headers["Vary"] == "Accept-Language"
    assert p["validation_error"]["message_locale"] == locale
    if locale == "en-US":
        assert p["validation_error"]["message"] == (
            "The plan cannot be completed before the deadline."
        )
    with Session(engine) as db:
        stored = growth.data(db.get(growth.GrowthRecord, p["id"]))
        assert "message_locale" not in stored["validation_error"]
        assert stored["candidate"] == p["candidate"]
        assert stored["diff"] == p["diff"]
    replay = ok(preview(client, {**alice, "Accept-Language": "zh-CN"}, r, plan), 201)
    assert replay["id"] == p["id"] and replay["version"] == p["version"]
    assert replay["validation_error"]["message"] == stored["validation_error"]["message"]
    assert (
        client.post(
            f"/api/v1/plan-previews/{p['id']}/accept",
            headers=version(alice, 1),
            json={"confirmed": True},
        ).status_code
        == 422
    )


def seed_project(engine, principal, *, conclusion="passed", source_owner=None):
    """Trusted producer simulation only; this test does not run or validate project code."""
    with Session(engine) as db, db.begin():
        artifact = growth.save(
            db,
            principal,
            "project_submission",
            {"status": "submitted", "content_hash": "a" * 64, "skill_ids": ["python"]},
            "test.contract",
        )
        receipt = growth.save(
            db,
            source_owner or principal,
            "project_assessment",
            {
                "artifact_id": artifact["id"],
                "artifact_version": 1,
                "artifact_hash": artifact["content_hash"],
                "status": conclusion,
                "skill_ids": ["python"],
                "rubric_version": project_evidence.RUBRIC,
                "evaluator_version": project_evidence.EVALUATOR,
                "resume_fact_ids": [],
            },
            "test.contract",
        )
        evidence = learning.add_evidence(
            db,
            principal,
            "python",
            "assessment_result",
            receipt["id"],
            1,
            conclusion,
            "test.contract",
            project_evidence.RUBRIC,
        )
        db.get(SkillEvidence, evidence["id"]).scope = "project"
        return artifact, receipt, evidence


def test_project_consumer_valid_contract_and_foundation_isolation(system):
    client, engine, alice, _, _, ids = system
    j = matching_setup(system)
    before = get_match(client, alice, j)
    p = principal_for(engine, ids[0])
    artifact, receipt, e = seed_project(engine, p)
    result = get_match(client, alice, j)
    assert result["components"]["project_evidence"]["ratio"] == 1
    assert result["score"] == before["score"] + 25
    assert result["components"]["project_evidence"]["basis"]["project_ids"] == [artifact["id"]]
    assert (
        next(
            s for s in ok(client.get("/api/v1/skills", headers=alice)) if s["skill_id"] == "python"
        )["status"]
        == "unassessed"
    )
    snapshot = ok(
        client.post("/api/v1/job-matches/recompute", headers=alice, json={"job_ids": [j["id"]]}),
        201,
    )
    with Session(engine) as db, db.begin():
        row = db.get(growth.GrowthRecord, artifact["id"])
        growth.save(
            db,
            p,
            "project_submission",
            {**json.loads(row.payload_json), "content_hash": "b" * 64},
            "changed",
            row,
            1,
        )
    assert get_match(client, alice, j)["components"]["project_evidence"]["ratio"] is None
    assert (
        ok(client.get(f"/api/v1/job-match-snapshots/{snapshot['id']}", headers=alice)) == snapshot
    )


@pytest.mark.parametrize(
    "invalid",
    [
        "expired",
        "revoked",
        "wrong_owner",
        "wrong_evaluator",
        "wrong_rubric",
        "failed_newer",
        "source_version",
    ],
)
def test_project_consumer_rejects_invalid_or_superseded_receipts(system, invalid):
    client, engine, alice, _, _, ids = system
    j = matching_setup(system)
    p = principal_for(engine, ids[0])
    other = principal_for(engine, ids[1])
    artifact, receipt, e = seed_project(
        engine, p, source_owner=other if invalid == "wrong_owner" else None
    )
    with Session(engine) as db, db.begin():
        if invalid == "expired":
            db.get(SkillEvidence, e["id"]).expires_at = "2000-01-01T00:00:00+00:00"
        elif invalid == "revoked":
            from careerpilot.v1_models import EvidenceRevocation

            db.add(
                EvidenceRevocation(
                    evidence_id=e["id"], user_id=p.user_id, tenant_id=p.tenant_id, reason="撤销"
                )
            )
        elif invalid in {"wrong_evaluator", "wrong_rubric", "source_version"}:
            row = db.get(growth.GrowthRecord, receipt["id"])
            payload = json.loads(row.payload_json)
            if invalid == "source_version":
                row.version = 2
            else:
                payload[
                    "evaluator_version" if invalid == "wrong_evaluator" else "rubric_version"
                ] = "unsupported"
            row.payload_json = growth.dump(payload)
    if invalid == "failed_newer":
        seed_project(engine, p, conclusion="failed")
    assert get_match(client, alice, j)["components"]["project_evidence"]["ratio"] is None


def test_interviews_and_reviews_are_exported_and_deleted_with_account(system):
    client, engine, alice, _, _, ids = system
    s = complete(client, alice)
    e = evaluate(client, alice, s)
    review(client, alice, e)
    with Session(engine) as db:
        exported = json.dumps(accounts.export_data(db, principal_for(engine, ids[0])))
        assert "interview_session" in exported and "interview_review" in exported
    ok(
        client.post(
            "/api/v1/account/deletion", headers=alice, json={"confirmation": "DELETE MY ACCOUNT"}
        ),
        202,
    )
    accounts.process_deletion(engine)
    with Session(engine) as db:
        assert (
            list(
                db.scalars(select(growth.GrowthRecord).where(growth.GrowthRecord.user_id == ids[0]))
            )
            == []
        )
