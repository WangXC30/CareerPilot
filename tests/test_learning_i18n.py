"""English actions use the same controlled tools and preserve frozen Chinese records."""

import json
import re

import pytest
from sqlalchemy.orm import Session

from careerpilot.assistant import intent_from
from careerpilot.domain import DomainError
from careerpilot.growth_models import GrowthRecord
from careerpilot.runtime import run_once
from tests.test_v1_release import setup_plan


@pytest.mark.parametrize(
    ("message", "intent"),
    [
        ("Please PLAN my learning", "plan"),
        ("Reschedule my study", "replan"),
        ("Adjust my plan", "replan"),
        ("Find Python tutorials", "resources"),
        ("Show skill gaps", "diagnose"),
        ("Review the job requirements", "diagnose"),
        ("Display my progress", "progress"),
        ("Explore planetary science", "help"),
        ("Apply concepts in my course", "help"),
        ("帮我生成计划", "plan"),
        ("调整计划", "replan"),
    ],
)
def test_english_intent_boundaries_and_chinese_compatibility(message, intent):
    assert intent_from(message) == intent


@pytest.mark.parametrize(
    "message",
    ["Apply for a job", "SEND AN EMAIL", "Delete my account", "Read the token", "请投递简历"],
)
def test_external_or_privileged_actions_stay_denied(message):
    with pytest.raises(DomainError) as denied:
        intent_from(message)
    assert denied.value.code == "TOOL_DENIED"


def test_english_assistant_runs_real_tools_without_rewriting_snapshot(system):
    client, engine, alice, bob, *_ = system
    job, _ = setup_plan(system)
    message = "Please PLAN my learning. 本人原文 <b>保留</b>"
    body = {"message": message, "job_ids": [job["id"]]}
    queued = client.post(
        "/api/v1/assistant/runs",
        headers={**alice, "Idempotency-Key": "english-tool"},
        json=body,
    )
    assert queued.status_code == 202, queued.text
    path = "/api/v1/assistant/runs/" + queued.json()["id"]
    assert run_once(engine)
    en = client.get(path, headers={**alice, "Accept-Language": "en-GB"})
    zh = client.get(path, headers=alice)
    assert en.status_code == zh.status_code == 200
    assert en.headers["content-language"] == "en-US"
    assert "Accept-Language" in en.headers["vary"]
    data = en.json()
    assert data["steps"][0]["tool"] == "growth.plan"
    assert data["status"] == "success"
    assert data["input"]["message"] == message
    assert not re.search(r"[\u4e00-\u9fff]", data["summary"])
    assert str(len(data["result"]["tasks"])) in data["summary"]
    assert data["result"] == zh.json()["result"]
    with Session(engine) as db:
        stored = json.loads(db.get(GrowthRecord, data["id"]).payload_json)
    assert stored["summary"] == zh.json()["summary"] != data["summary"]
    assert stored["input"]["message"] == message
    assert client.get(path, headers={**bob, "Accept-Language": "en-US"}).status_code == 404


def test_english_assistant_failure_stays_failure_and_keeps_original(system):
    client, engine, alice, *_ = system
    queued = client.post(
        "/api/v1/assistant/runs",
        headers={**alice, "Idempotency-Key": "english-denied"},
        json={"message": "Send an email", "intent": "auto"},
    ).json()
    assert run_once(engine)
    path = "/api/v1/assistant/runs/" + queued["id"]
    en = client.get(path, headers={**alice, "Accept-Language": "en-US"}).json()
    zh = client.get(path, headers=alice).json()
    assert en["status"] == "failed"
    assert en["result"]["code"] == zh["result"]["code"] == "TOOL_DENIED"
    assert en["steps"] == zh["steps"]
    assert not re.search(r"[\u4e00-\u9fff]", en["summary"])
    with Session(engine) as db:
        stored = json.loads(db.get(GrowthRecord, queued["id"]).payload_json)
    assert stored["summary"] == zh["summary"]


def test_conflicting_pace_explanation_localizes_only_response(system):
    client, engine, alice, *_ = system
    _, plan = setup_plan(system)
    for task in plan["tasks"]:
        updated = client.post(
            f"/api/v1/plans/{plan['id']}/tasks/{task['id']}",
            headers={**alice, "If-Match": f'"{plan["version"]}"'},
            json={"action": "lock"},
        )
        assert updated.status_code == 200, updated.text
        plan = updated.json()
    result = client.post(
        f"/api/v1/plans/{plan['id']}/mode-preview",
        headers={
            **alice,
            "Accept-Language": "en-US",
            "If-Match": f'"{plan["version"]}"',
            "Idempotency-Key": "english-conflict",
        },
        json={"mode": "light", "confirmed": True},
    )
    assert result.status_code == 201, result.text
    assert result.headers["content-language"] == "en-US"
    assert "Accept-Language" in result.headers["vary"]
    preview = result.json()
    assert preview["validation_error"]["message_locale"] == "en-US"
    assert not re.search(r"[\u4e00-\u9fff]", preview["validation_error"]["message"])
    with Session(engine) as db:
        stored = json.loads(db.get(GrowthRecord, preview["id"]).payload_json)
    assert "message_locale" not in stored["validation_error"]
    assert re.search(r"[\u4e00-\u9fff]", stored["validation_error"]["message"])
    listed = client.get(
        "/api/v1/plan-previews", headers={**alice, "Accept-Language": "en-US"}
    ).json()
    assert (
        next(p for p in listed if p["id"] == preview["id"])["validation_error"]
        == preview["validation_error"]
    )
    assert (
        client.get(f"/api/v1/plans/{plan['id']}", headers=alice).json()["version"]
        == plan["version"]
    )


def test_source_search_translates_notice_and_preserves_catalogue(system):
    client, _, alice, *_ = system
    path = "/api/v1/web/search?q=Python"
    en = client.get(path, headers={**alice, "Accept-Language": "en-US"}).json()
    zh = client.get(path, headers=alice).json()
    assert en["results"] == zh["results"]
    assert not re.search(r"[\u4e00-\u9fff]", en["notice"])
    assert en["notice"] != zh["notice"]
