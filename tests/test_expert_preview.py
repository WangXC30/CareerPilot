"""Transient specialist UI/API contracts. All model providers here are simulated."""

import copy
import json

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from careerpilot.expert_comparison import preview
from careerpilot.expert_protocol import digest
from careerpilot.growth_models import GrowthRecord
from careerpilot.platform_models import ModelUsage
from scripts import compare_expert_models as cli
from scripts.prepare_expert_review import cases
from tests.test_expert_comparison import Simulated, run, setup

PATH = "/api/v1/agent-experiments/expert-preview"


def payload(system, limited=False):
    suite, first, second, gates = setup(system)
    report = run(system, suite, gates, **({"budget_micros": 1} if limited else {}))
    return (
        {"cases": [case.model_dump() for case in suite], "report": report, "confirmed": True},
        suite,
        first,
        second,
    )


def persisted(engine):
    with Session(engine) as db:
        return list(db.scalars(select(ModelUsage.id))), list(db.scalars(select(GrowthRecord.id)))


def test_authenticated_projection_has_no_inputs_notes_model_calls_or_business_writes(system):
    client, engine, alice, *_ = system
    body, suite, first, second = payload(system)
    body["report"]["enabled"] = True
    body["report"]["private_reason"] = "private-report-canary"
    before = persisted(engine)
    counts = len(first.inputs), len(second.inputs)
    assert client.post(PATH, json=body).status_code == 401
    response = client.post(PATH, headers=alice, json=body)
    assert response.status_code == 200, response.text
    value = response.json()
    assert value["identical"] and value["enabled"] is False
    assert len(value["rows"]) == 16 and value["summary"]["arms"]["single"]["correct"] == 8
    assert "private-report-canary" not in response.text
    assert suite[0].request not in response.text and suite[0].sources[0].text not in response.text
    assert "expected_action" not in response.text and "required_sources" not in response.text
    assert "text" not in value["cases"][0]["sources"][0]
    assert value["rows"][0]["steps"][0]["decision"] == value["rows"][0]["decision"]
    assert persisted(engine) == before and (len(first.inputs), len(second.inputs)) == counts
    body["confirmed"] = False
    assert client.post(PATH, headers=alice, json=body).status_code == 422


@pytest.mark.parametrize(
    "change",
    [
        "hash",
        "missing",
        "duplicate",
        "stale",
        "bool_latency",
        "failure",
        "step_source",
        "step_final",
    ],
)
def test_invalid_or_inconsistent_frozen_steps_reject_without_echoing_inputs(system, change):
    client, _, alice, *_ = system
    body, *_ = payload(system)
    report = body["report"]
    if change == "hash":
        report["dataset_sha256"] = "0" * 64
    if change == "missing":
        report["rows"].pop()
    if change == "duplicate":
        report["rows"][1] = report["rows"][0]
    if change == "stale":
        body["cases"][0]["sources"][0]["version"] = 2
    if change == "bool_latency":
        report["rows"][0]["elapsed_ms"] = True
    if change == "failure":
        report["rows"][0]["decision"] = None
        report["rows"][0]["error"] = "MODEL_TIMEOUT"
    if change == "step_source":
        report["rows"][0]["steps"][0]["decision"]["citations"][0]["version"] = 2
    if change == "step_final":
        report["rows"][0]["steps"][0]["decision"]["action"] = "compare_directions"
    response = client.post(PATH, headers=alice, json=body)
    assert response.status_code == 422
    assert "我自称" not in response.text


def correction():
    return {
        "repeat": 0,
        "case": 0,
        "arm": "single",
        "decision": {
            "action": "compare_directions",
            "citations": [{"id": "evidence", "version": 1}],
        },
        "note": "private-human-basis-canary",
    }


def test_manual_overlay_and_withdrawal_leave_original_metrics_predictions_and_usage_unchanged(
    system,
):
    client, engine, alice, *_ = system
    body, _, first, second = payload(system)
    original = copy.deepcopy(body["report"])
    before, counts = persisted(engine), (len(first.inputs), len(second.inputs))
    body["corrections"] = [correction()]
    response = client.post(PATH, headers=alice, json=body)
    assert response.status_code == 200, response.text
    value = response.json()
    assert value["human_assisted"]["arms"]["single"] == {"correct": 7, "cases": 8, "corrected": 1}
    assert value["summary"]["arms"]["single"]["correct"] == 8
    assert value["rows"][0]["decision"] == original["rows"][0]["decision"]
    assert "private-human-basis-canary" not in response.text
    assert body["report"] == original and persisted(engine) == before
    assert (len(first.inputs), len(second.inputs)) == counts
    body["corrections"] = []
    assert (
        client.post(PATH, headers=alice, json=body).json()["human_assisted"]["correction_count"]
        == 0
    )


@pytest.mark.parametrize(
    "change", ["target", "action", "source", "version", "note", "duplicate", "label"]
)
def test_bad_manual_targets_roles_sources_basis_and_extra_labels_reject(system, change):
    client, _, alice, *_ = system
    body, *_ = payload(system)
    item = correction()
    if change == "target":
        item["case"] = 99
    if change == "action":
        item["decision"]["action"] = "preview_plan"
    if change == "source":
        item["decision"]["citations"][0]["id"] = "other-owner"
    if change == "version":
        item["decision"]["citations"][0]["version"] = True
    if change == "note":
        item["note"] = "  "
    if change == "label":
        item["expected_action"] = "compare_directions"
    body["corrections"] = [item] * (2 if change == "duplicate" else 1)
    assert client.post(PATH, headers=alice, json=body).status_code == 422


def test_manual_recovery_of_unrun_row_is_never_counted_as_model_execution(system):
    body, suite, first, second = payload(system, limited=True)
    item = correction()
    item["decision"]["action"] = "request_evidence"
    result = preview(suite, body["report"], [item])
    assert result["human_assisted"]["arms"]["single"]["correct"] == 1
    assert result["summary"]["arms"]["single"]["correct"] == 0
    assert result["summary"]["arms"]["single"]["metered_attempts"] == 0
    assert result["rows"][0]["decision"] is None and not first.inputs and not second.inputs
    body["report"]["rows"][1]["input_tokens"] = 1
    with pytest.raises(ValueError):
        preview(suite, body["report"])


def test_custom_case_cli_detailed_transfer_and_cleanup_without_network(
    tmp_path, monkeypatch, capsys
):
    suite = [cases()[i] for i in (0, 4, 8, 12)]
    source = tmp_path / "authorized.json"
    source.write_text(
        json.dumps(
            {
                "source": "owner_authorized",
                "rights_confirmed": True,
                "cases": [c.model_dump() for c in suite],
            }
        ),
        encoding="utf-8",
    )
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr("tempfile.tempdir", str(scratch))
    monkeypatch.setattr(cli, "from_env", lambda: Simulated())
    monkeypatch.setattr(
        "sys.argv",
        [
            "compare_expert_models",
            "--cases",
            str(source),
            "--reviewed-sha256",
            digest(suite),
            "--budget-micros-cny",
            "1000000",
            "--token-price-micros-cny",
            "8",
            "--repeats",
            "2",
            "--allow-external",
            "--show-steps",
        ],
    )
    cli.main()
    result = json.loads(capsys.readouterr().out)
    assert result["source"] == "operator_supplied_unverified" and len(result["rows"]) == 16
    assert result["enabled"] is False and result["frozen_replay_identical"]
    assert source.exists() and not list(scratch.iterdir())

    def fail():
        raise RuntimeError("private-factory-canary")

    monkeypatch.setattr(cli, "from_env", fail)
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2
    assert "private-factory-canary" not in capsys.readouterr().err
    assert not list(scratch.iterdir())
