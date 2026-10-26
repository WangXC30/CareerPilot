"""Display-only translations preserve matching semantics, original text and snapshots."""

import copy
import json
import re

import pytest
from sqlalchemy.orm import Session

from careerpilot import matching_i18n
from careerpilot.growth_models import GrowthRecord
from careerpilot.runtime import run_once
from tests.test_v1_release import ok, version
from tests.test_v2_matching import JD, setup
from tests.test_v2_opportunities import CONFIG, job


def canonical(value):
    if isinstance(value, dict):
        return {key: canonical(item) for key, item in value.items() if key != "_ui"}
    if isinstance(value, list):
        return [canonical(item) for item in value]
    return value


@pytest.mark.parametrize(
    "replacement",
    [
        {},
        {"本科及以上": "硕士及以上", "上海": "北京", "2027届": "2026届"},
        {"本科及以上": "本科优先", "2027届": "2026、2027届", "3个月": "3-6个月"},
        {"3个月": "6个月", "工作许可：不限": "工作许可：许可甲", "资格：不限": "资格：证书甲"},
        {"工作类型：实习": "工作类型：全职", "实习时长：3个月\n": ""},
        {"2027届": "届别：不限届别", "本科及以上": "不限学历"},
        {"发布日期：2026-10-20": "发布日期：2026-10-20\n截止日期：2000-01-01"},
    ],
)
def test_english_explanations_keep_all_eligibility_values_and_score_components(
    system, replacement, monkeypatch
):
    client, _, alice, *_ = system
    content = JD
    for old, new in replacement.items():
        content = content.replace(old, new)
    record = setup(system, content)
    monkeypatch.setattr("careerpilot.learning.utc_now", lambda: "2026-10-24T00:00:00+00:00")
    path = f"/api/v1/jobs/{record['id']}/match"
    zh = ok(client.get(path, headers=alice))
    response = client.get(path, headers={**alice, "Accept-Language": "en-GB"})
    en = ok(response)
    assert response.headers["content-language"] == "en-US"
    assert "Accept-Language" in response.headers["vary"]
    assert canonical(en) == zh
    assert all(en["_ui"]["checks"].values())
    assert not any(re.search(r"[\u4e00-\u9fff]", text) for text in en["_ui"]["checks"].values())
    for component in ("project_evidence", "preferences", "freshness"):
        assert en["_ui"]["components"][component]


@pytest.mark.parametrize("mode", ["best", "stretch", "entry", "explore"])
def test_recommendations_and_comparisons_localize_explanations_only(system, monkeypatch, mode):
    client, _, alice, *_ = system
    setup(system)
    job(client, alice, "岗位：未知样例\n要求：Python")
    monkeypatch.setattr("careerpilot.growth.utc_now", lambda: "2026-10-24T00:00:00+00:00")
    monkeypatch.setattr("careerpilot.learning.utc_now", lambda: "2026-10-24T00:00:00+00:00")
    path = f"/api/v1/job-recommendations?mode={mode}"
    zh = ok(client.get(path, headers=alice))
    en = ok(client.get(path, headers={**alice, "Accept-Language": "en-US"}))
    assert canonical(en) == zh
    assert en["_ui"]["notice"] and en["_ui"]["feedback_basis"]
    for key in ("eligible", "needs_review"):
        for item in en[key]:
            assert item["_ui"]["mode_reason"]
            if mode in {"entry", "stretch"}:
                count = item["components"]["required_skills"]["basis"]["denominator"]
                assert str(count) in item["_ui"]["mode_reason"]
    career_path = "/api/v1/career-comparison"
    career_zh = ok(client.get(career_path, headers=alice))
    career_en = ok(client.get(career_path, headers={**alice, "Accept-Language": "en-US"}))
    assert canonical(career_en) == career_zh
    for career in career_en["careers"]:
        assert all(career["_ui"]["reasons"]) and all(career["_ui"]["cautions"])
        assert career["_ui"]["cost_basis"] and career["_ui"]["sample_basis"]


def test_snapshot_overlay_is_not_persisted_and_does_not_translate_owner_text(system):
    client, engine, alice, bob, *_ = system
    marker = "岗位明确不限学历"
    record = setup(system, JD.replace("后端工程师", marker).replace("匹配验收", marker))
    ok(
        client.put(
            f"/api/v1/jobs/{record['id']}/feedback",
            headers=version(alice, 0),
            json={"label": "like", "reason": marker, "confirmed": True},
        )
    )
    en_headers = {**alice, "Accept-Language": "en-US"}
    snapshot = ok(
        client.post("/api/v1/job-matches/recompute", headers=en_headers, json={"job_ids": []}),
        201,
    )
    item = next(item for group in ("eligible", "needs_review") for item in snapshot[group])
    assert item["title"] == item["company"] == item["feedback"]["reason"] == marker
    with Session(engine) as db:
        row = db.get(GrowthRecord, snapshot["id"])
        stored = json.loads(row.payload_json)
    assert stored == canonical(stored)
    assert canonical(snapshot) == {
        "id": snapshot["id"],
        "created_at": snapshot["created_at"],
        "version": snapshot["version"],
        **stored,
    }
    path = f"/api/v1/job-match-snapshots/{snapshot['id']}"
    before = ok(client.get(path, headers=en_headers))
    profile = ok(client.get("/api/v1/profile", headers=alice))
    profile_body = {
        key: value for key, value in profile.items() if key not in {"id", "version", "created_at"}
    }
    ok(
        client.put(
            "/api/v1/profile",
            headers=version(alice, profile["version"]),
            json={**profile_body, "preferred_cities": ["北京"], "confirmed": True},
        )
    )
    assert ok(client.get(path, headers=en_headers)) == before
    assert client.get(path, headers={**bob, "Accept-Language": "en-US"}).status_code == 404
    other = job(client, alice, "岗位：本人对比\n要求：Python")
    compared = ok(
        client.post(
            "/api/v1/jobs/compare",
            headers=en_headers,
            json={"job_ids": [record["id"], other["id"]]},
        )
    )
    assert compared["jobs"][0]["fields"]["title"]["value"] == marker
    assert compared["_ui"]["notice"]
    assert compared["jobs"][0]["match"]["_ui"]["checks"]


def test_subscriptions_and_digests_preserve_original_names_and_scope(system):
    client, engine, alice, bob, *_ = system
    job(client, alice)
    marker = "仅增量检查本人已导入的本地岗位库；未搜索外部招聘平台，未实时读取源站。"
    created = ok(
        client.post(
            "/api/v1/job-subscriptions",
            headers={**alice, "Idempotency-Key": "locale-sub"},
            json={**CONFIG, "name": marker},
        ),
        201,
    )
    run = ok(
        client.post(
            f"/api/v1/job-subscriptions/{created['id']}/check",
            headers={**alice, "Idempotency-Key": "locale-run"},
            json={"confirmed": True},
        ),
        202,
    )
    assert run_once(engine)
    en_headers = {**alice, "Accept-Language": "en-US"}
    values = ok(client.get("/api/v1/job-subscriptions", headers=en_headers))
    assert values[0]["name"] == marker
    assert values[0]["scope"] == marker
    assert "local job library" in values[0]["_ui"]["scope"]
    digests = ok(client.get("/api/v1/job-notifications", headers=en_headers))
    assert digests[0]["subscription_name"] == marker
    assert digests[0]["_ui"]["scope"]
    with Session(engine) as db:
        for record_id in (created["id"], run["id"], digests[0]["id"]):
            stored = json.loads(db.get(GrowthRecord, record_id).payload_json)
            assert canonical(stored) == stored
    assert (
        ok(client.get("/api/v1/job-subscriptions", headers={**bob, "Accept-Language": "en-US"}))
        == []
    )
    assert (
        ok(client.get("/api/v1/job-notifications", headers={**bob, "Accept-Language": "en-US"}))
        == []
    )


def test_unknown_explanations_are_not_guessed_and_overlay_does_not_mutate_input():
    assert matching_i18n.explanation("未知的未来规则原文") is None
    original = {"scope": "未知的未来范围", "name": "岗位明确不限学历"}
    before = copy.deepcopy(original)
    result = matching_i18n.present(original, "en-US", "subscription")
    assert original == before
    assert result["_ui"]["scope"] is None
    assert result["name"] == before["name"]
