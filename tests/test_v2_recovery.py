"""Manual intervention and retention boundaries around durable review execution."""

import json
import time

import pytest

from careerpilot import growth, notifications, review_pipeline
from careerpilot.runtime import run_once
from tests.test_v1_release import ok, setup_plan, version
from tests.test_v2_progress import configure, get_pipeline, pipeline


def test_pipeline_manual_retry_after_dead_letter_keeps_completed_step(system, monkeypatch):
    client, engine, alice, *_ = system
    _, p = setup_plan(system)
    r = ok(pipeline(client, alice, p), 202)
    original = review_pipeline.execute_step

    def unavailable(db, principal, run, stage, trace):
        if stage == "source_references":
            raise OSError("offline")
        return original(db, principal, run, stage, trace)

    monkeypatch.setattr(review_pipeline, "execute_step", unavailable)
    now = int(time.time())
    for offset in (0, 3, 8):
        run_once(engine, now=now + offset)
    failed = get_pipeline(client, alice, r)
    assert failed["task"]["status"] == "failed" and failed["completed_steps"] == 1
    t = failed["task"]
    ok(client.post(f"/api/v1/agent-tasks/{t['id']}/retry", headers=version(alice, t["version"])))
    monkeypatch.setattr(review_pipeline, "execute_step", original)
    run_once(engine, now=now + 10)
    resumed = get_pipeline(client, alice, r)
    assert resumed["task"]["status"] == "success" and resumed["steps"][0] == failed["steps"][0]
    assert len(ok(client.get("/api/v1/plan-previews", headers=alice))) == 1


def test_optional_public_excerpt_stays_in_deletable_source_library(system, monkeypatch):
    client, engine, alice, *_ = system
    _, p = setup_plan(system)
    url = "https://docs.python.org/3/"
    assert pipeline(client, alice, p, reference_url=url).status_code == 403
    ok(client.put("/api/v1/consents/public_web", headers=alice, json={"granted": True}))
    monkeypatch.setattr(
        review_pipeline.web_sources,
        "fetch",
        lambda *_: {
            "source_url": url,
            "excerpt": "uniquely-deletable-public-excerpt",
            "retrieved_at": growth.utc_now(),
            "sha256": "a" * 64,
        },
    )
    r = ok(pipeline(client, alice, p, reference_url=url), 202)
    run_once(engine)
    result = get_pipeline(client, alice, r)
    ref = result["steps"][1]["result"]["public_reference"]
    assert ref["source_url"] == url and ref["retrieved_at"]
    assert "uniquely-deletable-public-excerpt" not in json.dumps(result)
    source = ok(client.get(f"/api/v1/source-documents/{ref['source_id']}", headers=alice))
    assert source["text"] == "uniquely-deletable-public-excerpt"
    assert (
        client.delete(
            f"/api/v1/source-documents/{source['id']}", headers=version(alice, 1)
        ).status_code
        == 204
    )
    assert "uniquely-deletable-public-excerpt" not in json.dumps(get_pipeline(client, alice, r))


def test_notification_failure_rolls_back_notice_and_due_slot(system, monkeypatch):
    client, engine, alice, *_ = system
    config = ok(configure(client, alice))
    due = min(config["next_runs"].values())
    original = notifications.content
    monkeypatch.setattr(
        notifications, "content", lambda *_: (_ for _ in ()).throw(RuntimeError("interrupted"))
    )
    with pytest.raises(RuntimeError):
        notifications.schedule_due(engine, due)
    assert ok(client.get("/api/v1/notification-center", headers=alice))["items"] == []
    assert (
        ok(client.get("/api/v1/notification-settings", headers=alice))["next_runs"]
        == config["next_runs"]
    )
    monkeypatch.setattr(notifications, "content", original)
    assert notifications.schedule_due(engine, due) == 1
    assert notifications.schedule_due(engine, due) == 0
