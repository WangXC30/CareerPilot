"""V2 first batch: conservative eligibility, evidence refresh and private comparisons."""

import copy
from datetime import date

import pytest
from sqlalchemy.orm import Session

from careerpilot import accounts, growth, matching
from careerpilot.v1_models import SkillEvidence
from tests.test_v1_release import PROFILE, exam, ok, version

JD = (
    "岗位：后端工程师\n公司：匹配验收\n地点：上海\n学历：本科及以上\n2027届\n"
    "实习时长：3个月\n工作类型：实习\n工作许可：不限\n资格：不限\n"
    "要求：Python\nSQL 优先\n发布日期：2026-10-20\n薪资：10000-15000元/月"
)


def setup(system, content=JD):
    client, _, alice, *_ = system
    ok(client.put("/api/v1/profile", headers=version(alice, 0), json=PROFILE))
    ok(
        client.put(
            "/api/v1/matching-constraints",
            headers=version(alice, 0),
            json={"available_months": 4, "confirmed": True},
        )
    )
    job = ok(client.post("/api/v1/jobs/import", headers=alice, json={"content": content}), 201)
    return ok(
        client.post(
            f"/api/v1/jobs/{job['id']}/status",
            headers=version(alice, 1),
            json={"status": "verified", "reason": "本地合同核对", "confirmed": True},
        )
    )


def get_match(client, headers, job):
    return ok(client.get(f"/api/v1/jobs/{job['id']}/match", headers=headers))


def test_eligibility_and_missing_information_never_claim_high_match(system):
    client, _, alice, *_ = system
    job = setup(system)
    result = get_match(client, alice, job)
    assert result["eligibility"] == "PASS" and result["high_match_eligible"]
    assert all(c["state"] == "PASS" for c in result["checks"])
    unknown = ok(
        client.post(
            "/api/v1/jobs/import", headers=alice, json={"content": "岗位：条件未知\n要求：Python"}
        ),
        201,
    )
    result = get_match(client, alice, unknown)
    assert result["eligibility"] == "UNKNOWN" and not result["high_match_eligible"]
    assert any(c["key"] == "education" and c["state"] == "UNKNOWN" for c in result["checks"])
    groups = ok(client.get("/api/v1/job-matches", headers=alice))
    assert [r["job_id"] for r in groups["eligible"]] == [job["id"]]
    assert [r["job_id"] for r in groups["needs_review"]] == [unknown["id"]]


@pytest.mark.parametrize(
    ("old", "new", "key"),
    [
        ("学历：本科及以上", "学历：硕士及以上", "education"),
        ("地点：上海", "地点：北京", "location"),
        ("2027届", "2026届", "graduation_cohort"),
        ("实习时长：3个月", "实习时长：6个月", "duration"),
    ],
)
def test_hard_fail_is_excluded_even_with_verified_skills(system, old, new, key):
    client, _, alice, *_ = system
    job = setup(system, JD.replace(old, new))
    exam(client, alice)
    result = get_match(client, alice, job)
    assert result["eligibility"] == "FAIL" and not result["high_match_eligible"]
    assert next(c for c in result["checks"] if c["key"] == key)["state"] == "FAIL"
    groups = ok(client.get("/api/v1/job-matches", headers=alice))
    assert not groups["eligible"] and groups["excluded"][0]["job_id"] == job["id"]


@pytest.mark.parametrize(
    ("old", "new", "key"),
    [
        ("学历：本科及以上", "学历：本科优先", "education"),
        ("2027届", "2026、2027届毕业生", "graduation_cohort"),
        ("实习时长：3个月", "实习时长：3-6个月，每周4天", "duration"),
        ("地点：上海", "地点：上海或北京，可调配", "location"),
    ],
)
def test_ambiguous_conditions_remain_unknown(system, old, new, key):
    client, _, alice, *_ = system
    job = setup(system, JD.replace(old, new))
    result = get_match(client, alice, job)
    assert next(c for c in result["checks"] if c["key"] == key)["state"] == "UNKNOWN"
    assert not result["high_match_eligible"]


def test_evidence_pass_revoke_expire_and_snapshot_isolation(system):
    client, engine, alice, bob, *_ = system
    job = setup(system)
    before = get_match(client, alice, job)
    snapshot = ok(
        client.post("/api/v1/job-matches/recompute", headers=alice, json={"job_ids": [job["id"]]}),
        201,
    )
    _, submitted = exam(client, alice)
    ok(submitted)
    after = get_match(client, alice, job)
    assert after["components"]["required_skills"]["points"] == 35
    assert after["score"] > before["score"]
    assert after["components"]["project_evidence"]["ratio"] is None
    assert after["skills"][0]["state"]["evidence_ids"]
    frozen = ok(client.get(f"/api/v1/job-match-snapshots/{snapshot['id']}", headers=alice))
    assert frozen == snapshot and frozen["eligible"][0]["score"] == before["score"]
    assert (
        client.get(f"/api/v1/job-match-snapshots/{snapshot['id']}", headers=bob).status_code == 404
    )
    assert client.get(f"/api/v1/jobs/{job['id']}/match", headers=bob).status_code == 404
    assert (
        client.post(
            "/api/v1/job-matches/recompute", headers=bob, json={"job_ids": [job["id"]]}
        ).status_code
        == 404
    )
    evidence = ok(client.get("/api/v1/skill-evidence", headers=alice))[-1]
    ok(
        client.post(
            f"/api/v1/skill-evidence/{evidence['id']}/revoke",
            headers=alice,
            json={"confirmed": True, "reason": "重新核对"},
        )
    )
    assert get_match(client, alice, job)["components"]["required_skills"]["points"] == 0
    exam(client, alice)
    with Session(engine) as db, db.begin():
        for row in db.query(SkillEvidence):
            row.expires_at = "2000-01-01T00:00:00+00:00"
    assert get_match(client, alice, job)["components"]["required_skills"]["points"] == 0


def test_constraints_require_confirmation_versions_and_owner(system):
    client, _, alice, bob, *_ = system
    job = setup(system, JD.replace("工作许可：不限", "工作许可：本地许可"))
    assert get_match(client, alice, job)["eligibility"] == "UNKNOWN"
    assert (
        client.put(
            "/api/v1/matching-constraints", headers=version(alice, 1), json={"confirmed": False}
        ).status_code
        == 422
    )
    limits = ok(
        client.put(
            "/api/v1/matching-constraints",
            headers=version(alice, 1),
            json={"available_months": 4, "work_authorizations": [], "confirmed": True},
        )
    )
    assert get_match(client, alice, job)["eligibility"] == "FAIL"
    assert (
        client.put(
            "/api/v1/matching-constraints", headers=version(alice, 1), json={"confirmed": True}
        ).status_code
        == 412
    )
    assert ok(client.get("/api/v1/matching-constraints", headers=bob))["version"] == 0
    ok(
        client.put(
            "/api/v1/matching-constraints",
            headers=version(alice, limits["version"]),
            json={"available_months": 4, "work_authorizations": ["本地许可"], "confirmed": True},
        )
    )
    assert get_match(client, alice, job)["eligibility"] == "PASS"
    assert (
        client.put(
            "/api/v1/matching-constraints",
            headers=version(alice, 3),
            json={"preferred_career_ids": ["invented"], "confirmed": True},
        ).status_code
        == 422
    )
    assert client.get("/api/v1/job-matches").status_code == 401


def test_closed_jobs_and_deadlines_never_appear_eligible(system):
    client, _, alice, *_ = system
    job = setup(system)
    ok(
        client.post(
            f"/api/v1/jobs/{job['id']}/status",
            headers=version(alice, 2),
            json={"status": "closed", "reason": "关闭", "confirmed": True},
        )
    )
    assert get_match(client, alice, job)["eligibility"] == "FAIL"
    expired = ok(
        client.post(
            "/api/v1/jobs/import", headers=alice, json={"content": JD + "\n截止日期：2000-01-01"}
        ),
        201,
    )
    assert get_match(client, alice, expired)["eligibility"] == "FAIL"


def test_no_profile_or_projects_never_invents_evidence_or_salary_conversion(system):
    client, _, alice, *_ = system
    job = ok(
        client.post(
            "/api/v1/jobs/import",
            headers=alice,
            json={"content": JD.replace("10000-15000元/月", "300元/天")},
        ),
        201,
    )
    result = get_match(client, alice, job)
    assert result["eligibility"] == "UNKNOWN" and not result["high_match_eligible"]
    assert result["components"]["preferences"]["basis"]["salary_ratio"] is None
    assert result["components"]["project_evidence"]["basis"]["project_ids"] == []


def test_career_comparison_sources_cost_transfer_and_explicit_interests(system):
    client, _, alice, bob, *_ = system
    cold = ok(client.get("/api/v1/career-comparison", headers=bob))
    assert len(cold["careers"]) == 4
    assert all(
        c["sample_count"] == 0 and c["minimum_budget_weeks"] is None for c in cold["careers"]
    )
    job = setup(system)
    before = ok(client.get("/api/v1/career-comparison", headers=alice))
    backend = next(c for c in before["careers"] if c["career_id"] == "career-backend")
    assert backend["sample_count"] == backend["sample_denominator"] == 1
    assert backend["samples"][0]["job_id"] == job["id"]
    assert backend["foundation_minutes"] > 0 and backend["source_url"].startswith("https://")
    exam(client, alice)
    after = ok(client.get("/api/v1/career-comparison", headers=alice))
    changed = next(c for c in after["careers"] if c["career_id"] == "career-backend")
    assert changed["foundation_minutes"] < backend["foundation_minutes"]
    assert changed["transferable_skills"][0]["skill_id"] == "python"
    ok(
        client.put(
            "/api/v1/matching-constraints",
            headers=version(alice, 1),
            json={"preferred_career_ids": ["career-frontend"], "confirmed": True},
        )
    )
    assert (
        ok(client.get("/api/v1/career-comparison", headers=alice))["careers"][0]["career_id"]
        == "career-frontend"
    )
    assert all(
        c["sample_count"] == 0
        for c in ok(client.get("/api/v1/career-comparison", headers=bob))["careers"]
    )


def test_future_publication_and_duplicate_mentions_cannot_inflate_score(system):
    client, _, alice, *_ = system
    job = setup(system, JD.replace("2026-10-20", "2099-01-01") + "\n要求：Python Python")
    result = get_match(client, alice, job)
    assert result["components"]["freshness"]["ratio"] is None
    assert result["components"]["required_skills"]["basis"]["denominator"] == 1


def test_rule_reproducible_at_fixed_date_and_source_spans(system):
    client, _, alice, *_ = system
    job = setup(system)
    ctx = {
        "profile": {**PROFILE, "expected_monthly_salary": 12000},
        "constraints": {**matching.DEFAULTS, "available_months": 4},
        "states": {},
    }
    a = matching.evaluate(copy.deepcopy(job), ctx, date(2026, 10, 21))
    b = matching.evaluate(copy.deepcopy(job), ctx, date(2026, 10, 21))
    assert a == b
    assert a["components"]["preferences"]["basis"]["salary_ratio"] == 0.5
    for c in a["checks"]:
        if c["job_evidence"]:
            span = c["job_evidence"]
            assert job["content"][span["start"] : span["end"]] == span["quote"]
    assert "录用概率" in a["notice"] and growth.dump(a)


def test_failed_reassessment_and_profile_changes_refresh_without_changing_snapshot(system):
    client, _, alice, *_ = system
    job = setup(system)
    exam(client, alice)
    assert get_match(client, alice, job)["components"]["required_skills"]["points"] == 35
    _, response = exam(client, alice, answers=[0, 0, 0])
    assert ok(response)["status"] == "failed"
    assert get_match(client, alice, job)["components"]["required_skills"]["points"] == 0
    ok(
        client.put(
            "/api/v1/profile",
            headers=version(alice, 1),
            json={**PROFILE, "preferred_cities": ["北京"]},
        )
    )
    assert get_match(client, alice, job)["eligibility"] == "FAIL"
    ok(
        client.put(
            "/api/v1/matching-constraints",
            headers=version(alice, 1),
            json={"cities_hard": False, "available_months": 4, "confirmed": True},
        )
    )
    assert get_match(client, alice, job)["eligibility"] == "PASS"


def test_new_private_records_export_and_deletion_are_covered(system):
    client, engine, alice, bob, *_ = system
    job = setup(system)
    snapshot = ok(
        client.post("/api/v1/job-matches/recompute", headers=alice, json={"job_ids": [job["id"]]}),
        201,
    )
    exported = ok(client.get("/api/v1/account/export", headers=alice))
    kinds = {row["kind"] for row in exported["data"]["growth_records"]}
    assert {"match_snapshot", "matching_constraints"} <= kinds
    ok(
        client.post(
            "/api/v1/account/deletion", headers=alice, json={"confirmation": "DELETE MY ACCOUNT"}
        ),
        202,
    )
    assert accounts.process_deletion(engine)
    with Session(engine) as db:
        assert db.get(growth.GrowthRecord, snapshot["id"]) is None
    assert client.get("/api/v1/career-comparison", headers=bob).status_code == 200


@pytest.mark.parametrize("value", ["2027届\n2026届", "2027届，专业不限", "2027届或有工作经验"])
def test_cohort_additional_conditions_do_not_get_silently_dropped(system, value):
    client, _, alice, *_ = system
    job = setup(system, JD.replace("2027届", value))
    checks = get_match(client, alice, job)["checks"]
    assert next(c for c in checks if c["key"] == "graduation_cohort")["state"] == "UNKNOWN"


@pytest.mark.parametrize("line", ["签证：需核对资格", "签证资格：需核对资格"])
def test_alternative_visa_labels_cannot_be_hidden_by_unlimited_work_permit(system, line):
    client, _, alice, *_ = system
    job = setup(system, JD + "\n" + line)
    checks = get_match(client, alice, job)["checks"]
    assert next(c for c in checks if c["key"] == "work_authorization")["state"] == "UNKNOWN"
