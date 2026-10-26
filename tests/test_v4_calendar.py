"""Calendar consent, time handling and immutable private review snapshots."""

import pytest

from tests.test_v1_release import setup_plan, version
from tests.test_v4_collaboration import join, ok
from tests.test_v4_local_tools import owned

EVENT = {
    "title": "本人面试",
    "kind": "interview",
    "date": "2026-12-01",
    "time": "14:30",
    "timezone": "Asia/Shanghai",
    "confirmed": True,
}


def sync(client, headers, ids):
    return ok(
        client.post(
            "/api/v1/calendar-syncs", headers=headers, json={"event_ids": ids, "confirmed": True}
        ),
        201,
    )


def test_only_selected_events_export_with_unicode_folding_and_utc_time(system):
    client, _, alice, bob, *_ = system
    job, _ = setup_plan(system)
    event = ok(
        client.post(
            "/api/v1/calendar-events",
            headers=alice,
            json={**EVENT, "title": "中文面试😀" * 20 + "\nATTENDEE:bad@example.test"},
        ),
        201,
    )
    events = ok(client.get("/api/v1/calendar-events", headers=alice))
    assert {e["id"] for e in events} == {event["id"], job["id"]}
    assert ok(client.get("/api/v1/calendar-events", headers=bob)) == []
    selected = sync(client, alice, [event["id"]])
    endpoint = f"/api/v1/calendar-syncs/{selected['id']}/export"
    assert client.get(endpoint, headers=bob).status_code == 404
    exported = client.get(endpoint, headers=alice)
    assert exported.status_code == 200 and exported.headers["cache-control"] == "no-store"
    assert all(len(line.encode()) <= 75 for line in exported.text.split("\r\n"))
    unfolded = exported.text.replace("\r\n ", "")
    assert "DTSTART:20261201T063000Z" in unfolded
    assert "中文面试😀" * 20 + "\\nATTENDEE:bad@example.test" in unfolded
    assert "\r\nATTENDEE:" not in unfolded
    assert unfolded.count("BEGIN:VEVENT") == 1
    assert job["id"] not in unfolded
    assert (
        client.post(
            "/api/v1/calendar-syncs",
            headers=bob,
            json={"event_ids": [event["id"]], "confirmed": True},
        ).status_code
        == 404
    )


def test_calendar_edit_remove_and_individual_grant_revocation_use_cas(system):
    client, _, alice, bob, *_ = system
    first = ok(client.post("/api/v1/calendar-events", headers=alice, json=EVENT), 201)
    second = ok(
        client.post(
            "/api/v1/calendar-events", headers=alice, json={**EVENT, "title": "另一场面试"}
        ),
        201,
    )
    selected = sync(client, alice, [first["id"], second["id"]])
    endpoint = f"/api/v1/calendar-syncs/{selected['id']}"
    remove = endpoint + "/events/" + first["id"]
    assert client.delete(remove, headers=version(bob, 1)).status_code == 404
    assert client.delete(remove, headers=version(alice, 9)).status_code == 412
    updated = ok(client.delete(remove, headers=version(alice, 1)))
    assert [e["id"] for e in updated["events"]] == [second["id"]]
    assert first["id"] not in client.get(endpoint + "/export", headers=alice).text
    event_endpoint = "/api/v1/calendar-events/" + second["id"]
    assert client.put(event_endpoint, headers=version(bob, 1), json=EVENT).status_code == 404
    ok(client.put(event_endpoint, headers=version(alice, 1), json={**EVENT, "time": None}))
    assert client.get(endpoint + "/export", headers=alice).status_code == 409
    assert client.put(event_endpoint, headers=version(alice, 1), json=EVENT).status_code == 412
    fresh = sync(client, alice, [second["id"]])
    export = f"/api/v1/calendar-syncs/{fresh['id']}/export"
    assert "DTSTART;VALUE=DATE:20261201" in client.get(export, headers=alice).text
    ok(client.delete(event_endpoint, headers=version(alice, 2)))
    assert client.get(export, headers=alice).status_code == 409
    ok(client.delete(endpoint + "/events/" + second["id"], headers=version(alice, 2)))
    assert client.get(endpoint + "/export", headers=alice).status_code == 404
    assert all(
        e["id"] != second["id"] for e in ok(client.get("/api/v1/calendar-events", headers=alice))
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"confirmed": False},
        {"time": "25:00"},
        {"date": "2026-02-30"},
        {"timezone": "Invalid/zone"},
        {"date": "2026-03-08", "time": "02:30", "timezone": "America/New_York"},
        {"date": "2026-11-01", "time": "01:30", "timezone": "America/New_York"},
    ],
)
def test_calendar_requires_consent_and_unambiguous_valid_time(system, changes):
    client, _, alice, *_ = system
    assert (
        client.post("/api/v1/calendar-events", headers=alice, json={**EVENT, **changes}).status_code
        == 422
    )
    assert ok(client.get("/api/v1/calendar-events", headers=alice)) == []


def test_review_history_freezes_source_versions_without_changing_goal_or_plan(system):
    client, _, alice, bob, *_ = system
    _, plan = setup_plan(system)
    goal = ok(
        client.post(
            "/api/v1/career-goals",
            headers={**alice, "Idempotency-Key": "review-goal"},
            json={"title": "原方向"},
        ),
        201,
    )
    before_goals = ok(client.get("/api/v1/career-goals", headers=alice))
    before_plans = ok(client.get("/api/v1/plans", headers=alice))
    body = {
        "start": "2026-10-01",
        "end": "2026-12-31",
        "reflection": "需要重新核对目标",
        "confirmed": True,
    }
    review = ok(client.post("/api/v1/quarter-reviews", headers=alice, json=body), 201)
    assert review["plan_versions"] == [{"id": plan["id"], "version": plan["version"]}]
    assert review["goal_snapshot"][0]["id"] == goal["id"]
    assert review["suggestion"]["automatic_goal_change"] is False
    assert ok(client.get("/api/v1/career-goals", headers=alice)) == before_goals
    assert ok(client.get("/api/v1/plans", headers=alice)) == before_plans
    endpoint = "/api/v1/quarter-reviews/" + review["id"]
    assert client.get(endpoint, headers=bob).status_code == 404
    assert ok(client.get("/api/v1/quarter-reviews", headers=bob)) == []
    # Later business records must not rewrite the saved review.
    owned(system, "milestone", {"title": "后续里程碑", "target_date": "2027-01-01"})
    preview = ok(client.post(f"/api/v1/career-goals/{goal['id']}/preview", headers=alice))
    ok(
        client.post(
            f"/api/v1/goal-previews/{preview['id']}/accept",
            headers=version(alice, preview["version"]),
            json={"confirmed": True},
        )
    )
    assert ok(client.get("/api/v1/career-goals", headers=alice))[0]["status"] == "active"
    assert ok(client.get("/api/v1/plans", headers=alice)) == before_plans
    assert len(ok(client.get(f"/api/v1/plans/{plan['id']}/history", headers=alice))) >= 2
    assert ok(client.get(endpoint, headers=alice)) == review
    assert ok(client.get("/api/v1/quarter-reviews", headers=alice)) == [review]
    assert (
        client.post(
            "/api/v1/quarter-reviews", headers=alice, json={**body, "start": "2027-01-01"}
        ).status_code
        == 422
    )


def test_advisor_can_only_read_selected_plan_projection_until_revoked(system):
    client, _, alice, bob, _, ids = system
    _, plan = setup_plan(system)
    space = ok(
        client.post(
            "/api/v1/workspaces", headers=alice, json={"name": "进度顾问", "confirmed": True}
        ),
        201,
    )
    join(client, alice, bob, ids, space)
    shared = ok(
        client.post(
            "/api/v1/object-shares",
            headers=alice,
            json={
                "kind": "plan",
                "source_id": plan["id"],
                "source_version": plan["version"],
                "fields": ["tasks"],
                "workspace_id": space["id"],
                "confirmed": True,
            },
        ),
        201,
    )
    endpoint = "/api/v1/object-shares/" + shared["id"]
    content = ok(client.get(endpoint, headers=bob))["content"]
    assert set(content) == {"tasks"}
    assert all(set(task) == {"title", "status", "minutes", "date"} for task in content["tasks"])
    ok(
        client.post(
            endpoint + "/comments",
            headers=bob,
            json={"text": "建议先核对学习预算", "confirmed": True},
        ),
        201,
    )
    assert len(ok(client.get(endpoint + "/comments", headers=alice))) == 1
    ok(client.delete(endpoint, headers=alice))
    assert client.get(endpoint, headers=bob).status_code == 404
    assert client.get(endpoint + "/comments", headers=bob).status_code == 404
    assert (
        client.post(
            endpoint + "/comments", headers=bob, json={"text": "撤销后不能评论", "confirmed": True}
        ).status_code
        == 404
    )
