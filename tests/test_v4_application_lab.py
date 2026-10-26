"""One-shot local application approval, uncertain outcomes and explicit Offer decisions."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event

from sqlalchemy.orm import Session

from careerpilot import application_lab
from careerpilot.platform_models import Approval
from tests.test_v2_hiring import CHECKLIST, application, transition
from tests.test_v4_collaboration import ok


def authorize(client, headers, app):
    preview = ok(
        client.post(
            "/api/v1/application-lab/preview",
            headers=headers,
            json={"application_id": app["id"], "confirmed": True},
        )
    )
    approval = ok(
        client.post(
            "/api/v1/application-lab/approve",
            headers=headers,
            json={
                "application_id": app["id"],
                "preview_hash": preview["preview_hash"],
                "confirmed": True,
            },
        )
    )
    return preview, approval


def submit(client, headers, app, approval):
    return client.post(
        "/api/v1/application-lab/submit",
        headers=headers,
        json={
            "application_id": app["id"],
            "approval_id": approval["approval_id"],
            "confirmed": True,
        },
    )


def test_timeout_has_private_history_and_manual_review_never_invents_success(system, monkeypatch):
    client, _, alice, bob, _, _ = system
    app = application(client, alice)
    _, approval = authorize(client, alice, app)

    def timeout(_):
        raise TimeoutError("secret provider failure must never be reflected")

    monkeypatch.setattr(application_lab, "simulator", timeout)
    result = ok(submit(client, alice, app, approval))
    assert result["status"] == "unknown_needs_manual_review"
    assert result["receipt"] is None and result["retry_allowed"] is False
    assert ok(client.get("/api/v1/application-lab/runs", headers=bob)) == []
    history = client.get("/api/v1/application-lab/runs", headers=alice)
    assert "secret provider failure" not in history.text
    assert ok(history)[0]["id"] == result["id"]
    endpoint = "/api/v1/application-lab/runs/" + result["id"] + "/review"
    body = {"note": "本人确认只是本地演练，未取得模拟回执", "confirmed": True}
    assert client.post(endpoint, headers={**bob, "If-Match": '"2"'}, json=body).status_code == 404
    assert client.post(endpoint, headers={**alice, "If-Match": '"1"'}, json=body).status_code == 412
    reviewed = ok(
        client.post(endpoint, headers={**alice, "If-Match": f'"{result["version"]}"'}, json=body)
    )
    assert reviewed["status"] == "reviewed_unknown" and reviewed["receipt"] is None
    assert reviewed["manual_review"]["basis"] == "owner_review"
    assert submit(client, alice, app, approval).status_code == 403
    preview = ok(
        client.post(
            "/api/v1/application-lab/preview",
            headers=alice,
            json={"application_id": app["id"], "confirmed": True},
        )
    )
    assert (
        client.post(
            "/api/v1/application-lab/approve",
            headers=alice,
            json={
                "application_id": app["id"],
                "preview_hash": preview["preview_hash"],
                "confirmed": True,
            },
        ).status_code
        == 409
    )
    assert ok(client.get("/api/v1/applications", headers=alice))[0]["status"] == "draft"


def test_two_approvals_cannot_execute_the_same_application_version_concurrently(
    system, monkeypatch
):
    client, _, alice, _, _, _ = system
    app = application(client, alice)
    _, first = authorize(client, alice, app)
    _, second = authorize(client, alice, app)
    entered, release = Event(), Event()
    calls = []

    def connector(payload):
        calls.append(payload)
        entered.set()
        assert release.wait(10)
        return {"kind": "simulated_receipt", "material_hash": payload["material_hash"]}

    monkeypatch.setattr(application_lab, "simulator", connector)
    with ThreadPoolExecutor(max_workers=1) as executor:
        executing = executor.submit(submit, client, alice, app, first)
        try:
            assert entered.wait(10)
            assert submit(client, alice, app, second).status_code == 409
        finally:
            release.set()
        assert ok(executing.result())["status"] == "simulated"
    assert len(calls) == 1
    assert submit(client, alice, app, second).status_code == 409
    assert len(ok(client.get("/api/v1/application-lab/runs", headers=alice))) == 1


def test_expired_approval_never_calls_simulator(system, monkeypatch):
    client, engine, alice, _, _, _ = system
    app = application(client, alice)
    _, approval = authorize(client, alice, app)
    with Session(engine) as db:
        db.get(Approval, approval["approval_id"]).expires_at = 0
        db.commit()
    calls = []
    monkeypatch.setattr(application_lab, "simulator", lambda value: calls.append(value))
    assert submit(client, alice, app, approval).status_code == 403
    assert not calls
    assert ok(client.get("/api/v1/application-lab/runs", headers=alice)) == []


def test_approval_can_be_revoked_before_execution_without_affecting_others(system):
    client, _, alice, bob, _, _ = system
    app = application(client, alice)
    _, approval = authorize(client, alice, app)
    endpoint = "/api/v1/application-lab/approvals/" + approval["approval_id"]
    assert client.delete(endpoint, headers=bob).status_code == 404
    assert ok(client.delete(endpoint, headers=alice))["status"] == "revoked"
    assert submit(client, alice, app, approval).status_code == 403
    assert ok(client.get("/api/v1/application-lab/runs", headers=alice)) == []
    _, renewed = authorize(client, alice, app)
    assert ok(submit(client, alice, app, renewed))["status"] == "simulated"


def test_approval_binds_application_material_and_job_versions(system):
    client, _, alice, _, _, _ = system
    app = application(client, alice)
    old, approval = authorize(client, alice, app)
    assert old["preview"]["job_version"] and old["preview"]["job_content_hash"]
    ok(transition(client, alice, app, "prepared", checklist=CHECKLIST))
    assert submit(client, alice, app, approval).status_code == 403
    assert (
        client.post(
            "/api/v1/application-lab/approve",
            headers=alice,
            json={
                "application_id": app["id"],
                "preview_hash": old["preview_hash"],
                "confirmed": True,
            },
        ).status_code
        == 412
    )
    assert ok(client.get("/api/v1/application-lab/runs", headers=alice)) == []


def test_offer_decision_is_owner_reviewed_versioned_and_never_external_acceptance(system):
    client, _, alice, bob, _, _ = system
    comparison = ok(
        client.post(
            "/api/v1/offer-comparisons",
            headers=alice,
            json={
                "offers": [
                    {
                        "name": "A",
                        "facts": {"compensation": "10000 元/月", "contract_terms": None},
                        "source_basis": "本人提供的合成合同",
                        "ratings": {"growth": 4},
                        "confirmed": True,
                    },
                    {"name": "B", "ratings": {"growth": 3}, "confirmed": True},
                ],
                "weights": {
                    "compensation": 30,
                    "growth": 25,
                    "location": 15,
                    "stability": 15,
                    "work_life": 15,
                },
                "confirmed": True,
            },
        ),
        201,
    )
    assert comparison["offers"][0]["facts"]["compensation"] == "10000 元/月"
    assert comparison["offers"][0]["facts_status"] == "owner_provided_unverified"
    assert comparison["offers"][0]["ratings_basis"] == "owner_subjective"
    assert comparison["offers"][0]["score_lower"] == 20
    assert ok(client.get("/api/v1/offer-comparisons", headers=bob)) == []
    endpoint = "/api/v1/offer-comparisons/" + comparison["id"] + "/decision"
    body = {"offer_index": 0, "note": "本人已核对区间与未知项", "confirmed": True}
    assert client.post(endpoint, headers={**bob, "If-Match": '"1"'}, json=body).status_code == 404
    assert (
        client.post(
            endpoint, headers={**alice, "If-Match": '"1"'}, json={**body, "offer_index": 9}
        ).status_code
        == 422
    )
    result = ok(client.post(endpoint, headers={**alice, "If-Match": '"1"'}, json=body))
    assert result["decision"]["name"] == "A" and result["decision"]["external_acceptance"] is False
    assert client.post(endpoint, headers={**alice, "If-Match": '"1"'}, json=body).status_code == 412
    assert (
        ok(client.get("/api/v1/offer-comparisons", headers=alice))[0]["decision"]
        == result["decision"]
    )
