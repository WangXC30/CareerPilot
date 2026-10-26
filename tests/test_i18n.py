"""Locale negotiation for fixed server explanations; owner content is never translated."""

import json
import re
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from careerpilot import i18n
from careerpilot.growth_models import GrowthRecord
from tests.test_growth import PROFILE, write_profile
from tests.test_v1_release import setup_plan

SOURCE = Path(__file__).resolve().parents[1] / "src" / "careerpilot"


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (None, "zh-CN"),
        ("", "zh-CN"),
        ("en-US", "en-US"),
        ("en", "en-US"),
        ("en-GB,en;q=0.8", "en-US"),
        ("zh-CN,zh;q=0.9,en;q=0.8", "zh-CN"),
        ("fr-FR,en;q=0.5", "en-US"),
        ("en;q=0.2,zh-TW;q=0.9", "zh-CN"),
        ("en;q=0", "zh-CN"),
        ("en;q=abc", "zh-CN"),
        ("de-DE", "zh-CN"),
        ("*", "zh-CN"),
    ],
)
def test_negotiate_picks_supported_locale(header, expected):
    assert i18n.negotiate(header) == expected


def test_every_raised_error_code_has_english_explanation():
    pattern = re.compile(r'DomainError\(\s*"([A-Z0-9_]+)"')
    codes = {
        match.group(1)
        for path in SOURCE.glob("*.py")
        for match in pattern.finditer(path.read_text(encoding="utf-8"))
    }
    gateway = (SOURCE / "gateway.py").read_text(encoding="utf-8")
    codes |= set(re.findall(r'error = "([A-Z_]+)"', gateway))
    codes |= {"INTERNAL_ERROR", "VALIDATION_ERROR", "HTTP_ERROR", "CONFLICT", "NOT_READY"}
    assert len(codes) > 150
    missing = sorted(codes - i18n.ERROR_MESSAGES_EN.keys())
    assert not missing, missing
    for text in i18n.ERROR_MESSAGES_EN.values():
        assert text.strip() and not re.search(r"[\u4e00-\u9fff]", text)


def test_unknown_code_falls_back_by_status_without_echoing_message():
    secret = "私密输入 token=abc"
    assert secret not in i18n.error_message("SOMETHING_NEW", secret, 404, "en-US")
    assert i18n.error_message("SOMETHING_NEW", secret, 404, "en-US").startswith("The resource")
    assert i18n.error_message("SOMETHING_NEW", secret, 418, "en-US")
    assert i18n.error_message("NOT_FOUND", "资源不存在", 404, "zh-CN") == "资源不存在"


def test_api_errors_follow_accept_language(system):
    client, *_ = system
    chinese = client.get("/api/v1/me")
    assert chinese.status_code == 401
    assert chinese.headers["content-language"] == "zh-CN"
    assert re.search(r"[\u4e00-\u9fff]", chinese.json()["message"])
    english = client.get("/api/v1/me", headers={"Accept-Language": "en-US,en;q=0.9"})
    assert english.status_code == 401
    body = english.json()
    assert body["code"] == chinese.json()["code"]
    assert body["message"] == i18n.ERROR_MESSAGES_EN[body["code"]]
    assert english.headers["content-language"] == "en-US"
    assert "Accept-Language" in english.headers["vary"]


def test_validation_and_domain_errors_localized_without_raw_input(system):
    client, _engine, alice, *_ = system
    marker = "<script>私密</script>"
    response = client.post(
        "/api/v1/auth/login",
        headers={"Accept-Language": "en"},
        json={"email": marker, "password": 1},
    )
    assert response.status_code == 422
    assert response.json()["message"] == i18n.ERROR_MESSAGES_EN["VALIDATION_ERROR"]
    assert marker not in response.text
    failed = client.post(
        "/api/v1/auth/login",
        headers={"Accept-Language": "en-US"},
        json={"email": "nobody@example.com", "password": "x" * 12},
    )
    assert failed.status_code in {401, 429}
    assert failed.json()["message"] == i18n.ERROR_MESSAGES_EN[failed.json()["code"]]
    missing = client.get(
        "/api/v1/profile/history/missing", headers={**alice, "Accept-Language": "en-US"}
    )
    assert missing.status_code in {404, 405, 422}
    assert not re.search(r"[\u4e00-\u9fff]", missing.json()["message"])


def test_profile_preview_localizes_only_explanation_and_never_saves(system):
    client, _, alice, *_ = system
    payload = {**PROFILE, "major": "本人原文 <b>计算机</b>", "expected_monthly_salary": 12000}
    zh = client.post("/api/v1/profile/preview", headers=alice, json=payload)
    en = client.post(
        "/api/v1/profile/preview",
        headers={**alice, "Accept-Language": "en-GB"},
        json=payload,
    )
    assert zh.status_code == en.status_code == 200
    assert en.headers["content-language"] == "en-US"
    assert zh.headers["content-language"] == "zh-CN"
    assert "Accept-Language" in en.headers["vary"]
    assert en.json()["message"] == i18n.GROWTH_MESSAGES_EN["profile_preview"]
    assert zh.json()["message"] != en.json()["message"]
    assert en.json()["changes"] == zh.json()["changes"]
    assert en.json()["changes"]["major"]["after"] == payload["major"]
    assert client.get("/api/v1/profile", headers=alice).json()["version"] == 0
    saved = write_profile(client, alice, **payload).json()
    assert saved["major"] == payload["major"]
    preview = client.post(
        "/api/v1/profile/preview",
        headers={**alice, "Accept-Language": "en-US"},
        json={**payload, "weekly_minutes": 300},
    ).json()
    assert preview["changes"] == {"weekly_minutes": {"before": 600, "after": 300}}
    assert client.get("/api/v1/profile", headers=alice).json()["weekly_minutes"] == 600


@pytest.mark.parametrize("with_plan", [False, True])
def test_goal_preview_localization_keeps_snapshot_and_goal_text(system, with_plan):
    client, engine, alice, bob, *_ = system
    if with_plan:
        _, plan = setup_plan(system)
        task = plan["tasks"][0]
        changed = client.post(
            f"/api/v1/plans/{plan['id']}/tasks/{task['id']}",
            headers={**alice, "If-Match": f'"{plan["version"]}"'},
            json={"action": "lock"},
        )
        assert changed.status_code == 200, changed.text
    title = "本人目标 <b>后端工程师</b>"
    goal = client.post(
        "/api/v1/career-goals",
        headers={**alice, "Idempotency-Key": "locale-goal"},
        json={"title": title},
    ).json()
    path = f"/api/v1/career-goals/{goal['id']}/preview"
    response = client.post(path, headers={**alice, "Accept-Language": "en-US"})
    assert response.status_code == 200, response.text
    en = response.json()
    assert response.headers["content-language"] == "en-US"
    assert en["goal_title"] == title
    assert not re.search(r"[\u4e00-\u9fff]", en["impact"])
    if with_plan:
        assert en["plan_impact"]["protected_tasks"] == 1
        assert "locked or completed tasks retained: 1." in en["impact"]
    else:
        assert en["plan_impact"] is None
    with Session(engine) as db:
        stored = json.loads(db.get(GrowthRecord, en["id"]).payload_json)
    assert stored["goal_title"] == title
    assert re.search(r"[\u4e00-\u9fff]", stored["impact"])
    assert stored["impact"] != en["impact"]
    assert client.post(path, headers={**bob, "Accept-Language": "en-US"}).status_code == 404
    # Display locale does not affect the preview version or confirmation contract.
    accepted = client.post(
        f"/api/v1/goal-previews/{en['id']}/accept",
        headers={**alice, "If-Match": f'"{en["version"]}"'},
        json={"confirmed": True},
    )
    assert accepted.status_code == 200, accepted.text
    assert client.get("/api/v1/career-goals", headers=alice).json()[0]["title"] == title
