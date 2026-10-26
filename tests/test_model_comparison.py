"""Configured model-workflow contracts; every provider here is explicitly simulated."""

import asyncio
import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from careerpilot.domain import DomainError
from careerpilot.gateway import Gateway, ProviderReply
from careerpilot.growth_models import GrowthRecord
from careerpilot.model_comparison import Dataset, compare, replay
from careerpilot.platform_models import Consent, ModelUsage
from tests.test_gateway import principal_of


def dataset():
    return Dataset.model_validate(
        {
            "version": "contract-only.v1",
            "source": "synthetic",
            "rights_confirmed": True,
            "examples": [
                {"id": "risk", "group": "positive", "text": "要求先交费", "expected": True},
                {"id": "negation", "group": "negative", "text": "不收取押金", "expected": False},
            ],
        }
    )


class SimulatedProvider:
    external = False

    def __init__(self, reviewed=False, failure=None):
        self.model_id = "simulated-reviewer.v1" if reviewed else "simulated-primary.v1"
        self.reviewed, self.failure = reviewed, failure
        self.inputs = []

    async def generate(self, envelope, max_tokens):
        payload = json.loads(json.loads(envelope)["untrusted_data"])
        self.inputs.append(payload)
        if self.failure == "timeout":
            await asyncio.sleep(1)
        if self.failure == "usage":
            return ProviderReply('{"risk":true}', True, 4)
        if self.failure == "schema":
            return ProviderReply('{"risk":"not-a-boolean"}', 10, 4)
        if self.failure == "provider":
            raise RuntimeError("private-provider-exception-canary")
        return ProviderReply(
            json.dumps({"risk": not (self.reviewed and "不" in payload["text"])}), 10, 4
        )


def setup(system, **kwargs):
    _, engine, *_ = system
    first, second = SimulatedProvider(), SimulatedProvider(reviewed=True)
    gates = [
        Gateway(engine, item, daily_tokens=1_000_000, task_tokens=1_000_000, **kwargs)
        for item in (first, second)
    ]
    return first, second, gates


def run(system, gates, **kwargs):
    return asyncio.run(compare(dataset(), principal_of(system), *gates, repeats=2, **kwargs))


def test_paired_repeated_workflows_record_cost_versions_and_no_raw_content(system):
    first, second, gates = setup(system)
    report = run(system, gates)
    assert len(first.inputs) == 8 and len(second.inputs) == 4
    assert all("expected" not in item for item in first.inputs + second.inputs)
    assert all("candidate_risk" not in item for item in first.inputs)
    assert all(type(item["candidate_risk"]) is bool for item in second.inputs)
    assert report["summary"]["repeat_quality_gains"] == [0.5, 0.5]
    assert report["summary"]["groups"]["negative"]["single"]["correct"] == 0
    assert report["summary"]["groups"]["negative"]["reviewed"]["correct"] == 2
    single, reviewed = (report["summary"]["arms"][key] for key in ("single", "reviewed"))
    assert (single["accuracy"], reviewed["accuracy"]) == (0.5, 1)
    assert single["estimated_cost_micros"] == 56 and reviewed["estimated_cost_micros"] == 112
    assert single["input_tokens"] == 40 and reviewed["input_tokens"] == 80
    assert single["mean_latency_ms"] >= 0 and reviewed["mean_latency_ms"] >= 0
    assert all(item["complete"] and item["cost_complete"] for item in (single, reviewed))
    order = [(row["repeat"], row["case"], row["arm"]) for row in report["rows"]]
    assert order[:4] == [(0, 0, "single"), (0, 0, "reviewed"), (0, 1, "reviewed"), (0, 1, "single")]
    assert replay(dataset(), report) == {"identical": True, "enabled": False}
    assert report["enabled"] is False and report["source"] == "synthetic"
    assert "要求先交费" not in json.dumps(report, ensure_ascii=False)
    assert "不收取押金" not in json.dumps(report, ensure_ascii=False)
    with Session(gates[0].engine) as db:
        usages = list(db.scalars(select(ModelUsage)))
        assert len(usages) == 12
        assert {item.prompt_version for item in usages} == {"risk-decision.v1", "risk-review.v1"}
        assert all(item.schema_version == "risk-label.v1" for item in usages)


def test_unapproved_external_adapters_never_run(system):
    first, second, gates = setup(system)
    first.external = True
    with pytest.raises(DomainError) as error:
        run(system, gates)
    assert error.value.code == "CONSENT_REQUIRED"
    assert not first.inputs and not second.inputs
    report = run(system, gates, allow_external=True)
    assert report["stopped"] == "CONSENT_REQUIRED"
    assert not first.inputs and not second.inputs
    assert all(
        item["cases"] == 4 and item["correct"] == 0 for item in report["summary"]["arms"].values()
    )


def test_live_consent_revocation_discards_result_and_stops_comparison(system):
    first, second, gates = setup(system)
    second.external = True
    principal = principal_of(system)
    with Session(gates[0].engine) as db, db.begin():
        db.add(
            Consent(
                user_id=principal.user_id,
                tenant_id=principal.tenant_id,
                purpose="external_model",
                granted=1,
            )
        )
    original = second.generate

    async def revoke(envelope, max_tokens):
        reply = await original(envelope, max_tokens)
        with Session(gates[0].engine) as db, db.begin():
            db.get(Consent, (principal.user_id, principal.tenant_id, "external_model")).granted = 0
        return reply

    second.generate = revoke
    report = run(system, gates, allow_external=True)
    assert report["stopped"] == "CONSENT_REVOKED"
    assert len(first.inputs) == 2 and len(second.inputs) == 1
    assert report["rows"][1]["prediction"] is None
    assert report["rows"][1]["error"] == "CONSENT_REVOKED"
    assert any(row["error"] == "NOT_RUN" for row in report["rows"])
    assert replay(dataset(), report)["identical"]


def test_budget_reserves_upper_bound_before_call_and_preserves_denominator(system):
    first, second, gates = setup(system)
    report = run(system, gates, budget_micros=1)
    assert report["stopped"] == "BUDGET_LIMIT"
    assert all(
        item["mean_latency_ms"] is None and item["latency_samples"] == 0
        for item in report["summary"]["arms"].values()
    )
    assert not first.inputs and not second.inputs
    assert all(
        item["cases"] == 4 and item["errors"] == 4 and not item["complete"]
        for item in report["summary"]["arms"].values()
    )
    assert replay(dataset(), report)["identical"]


def test_model_budget_includes_provider_system_and_chat_overhead(system):
    first, second, gates = setup(system)
    first.input_token_overhead = second.input_token_overhead = 8192
    report = run(system, gates, budget_micros=8192)
    assert report["stopped"] == "BUDGET_LIMIT"
    assert not first.inputs and not second.inputs


@pytest.mark.parametrize("failure", ["timeout", "schema", "provider", "usage"])
def test_incomplete_or_unpriced_failures_stop_without_leaking_provider_content(system, failure):
    first, second, gates = setup(system, timeout=0.01)
    first.failure = failure
    report = run(system, gates)
    assert report["stopped"] == "COST_UNKNOWN"
    assert len(first.inputs) == 1 and not second.inputs
    assert report["summary"]["arms"]["single"]["cases"] == 4
    assert not report["summary"]["arms"]["single"]["cost_complete"]
    assert report["rows"][0]["prediction"] is None
    assert "private-provider-exception-canary" not in repr(report)
    assert replay(dataset(), report)["identical"]


def test_replay_requires_complete_strict_observations_and_detects_modified_scores(system):
    _, _, gates = setup(system)
    report = run(system, gates)
    altered = copy.deepcopy(report)
    altered["summary"]["arms"]["single"]["correct"] = 4
    assert replay(dataset(), altered) == {"identical": False, "enabled": False}
    for action in (
        lambda r: r["rows"].pop(),
        lambda r: r["rows"].append(r["rows"][0]),
        lambda r: r["rows"][0].update(prediction=1),
        lambda r: r.update(evaluator_version="unknown"),
        lambda r: r["rows"][0].update(elapsed_ms=float("nan")),
    ):
        altered = copy.deepcopy(report)
        action(altered)
        with pytest.raises(ValueError):
            replay(dataset(), altered)
    changed = dataset().model_copy(deep=True)
    changed.examples[0].expected = False
    with pytest.raises(ValueError):
        replay(changed, report)


def test_dataset_and_limits_are_strict_and_reject_embedded_credentials(system):
    raw = dataset().model_dump()
    raw["examples"][0]["expected"] = 1
    with pytest.raises(ValidationError):
        Dataset.model_validate(raw)
    raw = dataset().model_dump()
    raw["examples"].append(raw["examples"][0])
    with pytest.raises(ValidationError):
        Dataset.model_validate(raw)
    raw = dataset().model_dump()
    raw["examples"][0]["text"] = "Bearer " + "synthetic" * 3
    with pytest.raises(DomainError):
        Dataset.model_validate(raw)
    _, _, gates = setup(system)
    with pytest.raises(ValueError):
        run(system, gates, budget_micros=-1)


def test_input_dataset_is_frozen_before_first_model_await(system):
    first, _, gates = setup(system)
    source = dataset()
    frozen = source.model_copy(deep=True)
    original = first.generate

    async def mutate(envelope, max_tokens):
        source.examples[0].expected = False
        return await original(envelope, max_tokens)

    first.generate = mutate
    result = asyncio.run(compare(source, principal_of(system), *gates, repeats=2))
    assert result["dataset_sha256"] == frozen.digest()
    assert replay(frozen, result)["identical"]
    with pytest.raises(ValueError):
        replay(source, result)


def test_transient_preview_is_authenticated_rescores_and_never_persists_inputs(system):
    client, engine, alice, bob, *_ = system
    first, second, gates = setup(system)
    report = run(system, gates)
    path = "/api/v1/agent-experiments/model-preview"
    body = {"dataset": dataset().model_dump(), "report": report, "confirmed": True}
    with Session(engine) as db:
        usages = len(list(db.scalars(select(ModelUsage))))
        records = len(list(db.scalars(select(GrowthRecord))))
    assert client.post(path, json=body).status_code == 401
    response = client.post(path, headers=alice, json=body)
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["identical"] and preview["enabled"] is False
    assert preview["rows"][0]["status"] == "success"
    assert "要求先交费" not in response.text and "不收取押金" not in response.text
    assert len(first.inputs) == 8 and len(second.inputs) == 4
    # Another account can review only content it submits; no saved trace URL exists.
    assert client.post(path, headers=bob, json=body).status_code == 200
    with Session(engine) as db:
        assert len(list(db.scalars(select(ModelUsage)))) == usages
        assert len(list(db.scalars(select(GrowthRecord)))) == records
    altered = copy.deepcopy(body)
    altered["report"]["summary"]["arms"]["single"]["correct"] = 4
    checked = client.post(path, headers=alice, json=altered)
    assert checked.status_code == 200 and checked.json()["identical"] is False
    assert checked.json()["summary"]["arms"]["single"]["correct"] == 2
    altered["report"]["rows"].pop()
    rejected = client.post(path, headers={**alice, "Accept-Language": "en-US"}, json=altered)
    assert rejected.status_code == 422 and rejected.json()["code"] == "MODEL_PREVIEW_INVALID"
    assert rejected.json()["message"].startswith("Invalid dataset")


def test_manual_correction_is_separate_from_model_quality_and_never_persists_notes(system):
    client, engine, alice, *_ = system
    first, second, gates = setup(system)
    report = run(system, gates)
    original = copy.deepcopy(report)
    correction = {
        "repeat": 0,
        "case": 1,
        "arm": "single",
        "prediction": False,
        "note": "本人核对否定语义 private-human-note-canary",
    }
    body = {
        "dataset": dataset().model_dump(),
        "report": report,
        "corrections": [correction],
        "confirmed": True,
    }
    path = "/api/v1/agent-experiments/model-preview"
    before = (len(first.inputs), len(second.inputs))
    with Session(engine) as db:
        usage_ids = list(db.scalars(select(ModelUsage.id)))
        record_ids = list(db.scalars(select(GrowthRecord.id)))
    response = client.post(path, headers=alice, json=body)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["identical"] and result["enabled"] is False
    assert result["summary"] == original["summary"]
    assert result["summary"]["arms"]["single"]["correct"] == 2
    assert result["human_assisted"]["arms"]["single"] == {"cases": 4, "correct": 3, "corrected": 1}
    assert result["human_assisted"]["enabled"] is False
    assert "private-human-note-canary" not in response.text
    assert report == original and replay(dataset(), report)["identical"]
    assert (len(first.inputs), len(second.inputs)) == before
    with Session(engine) as db:
        assert list(db.scalars(select(ModelUsage.id))) == usage_ids
        assert list(db.scalars(select(GrowthRecord.id))) == record_ids
    body["corrections"] = []
    reset = client.post(path, headers=alice, json=body).json()
    assert reset["human_assisted"]["correction_count"] == 0
    assert reset["human_assisted"]["arms"]["single"]["correct"] == 2


@pytest.mark.parametrize(
    "change",
    [
        {"repeat": 9},
        {"case": 99},
        {"prediction": 1},
        {"repeat": True},
        {"note": "   "},
        {"expected": False},
        {"note": "Bearer " + "synthetic" * 3},
    ],
)
def test_manual_correction_rejects_unknown_targets_types_labels_and_credentials(system, change):
    client, _, alice, *_ = system
    _, _, gates = setup(system)
    report = run(system, gates)
    correction = {
        "repeat": 0,
        "case": 1,
        "arm": "single",
        "prediction": False,
        "note": "本人核对依据",
        **change,
    }
    response = client.post(
        "/api/v1/agent-experiments/model-preview",
        headers=alice,
        json={
            "dataset": dataset().model_dump(),
            "report": report,
            "corrections": [correction],
            "confirmed": True,
        },
    )
    assert response.status_code == 422


def test_duplicate_corrections_reject_and_recovering_unrun_observation_is_only_human(system):
    client, _, alice, *_ = system
    first, second, gates = setup(system)
    report = run(system, gates, budget_micros=1)
    correction = {
        "repeat": 0,
        "case": 0,
        "arm": "single",
        "prediction": True,
        "note": "原调用未执行，本人仅标注参考判断",
    }
    body = {
        "dataset": dataset().model_dump(),
        "report": report,
        "corrections": [correction, correction],
        "confirmed": True,
    }
    path = "/api/v1/agent-experiments/model-preview"
    assert client.post(path, headers=alice, json=body).status_code == 422
    body["corrections"] = [correction]
    response = client.post(path, headers=alice, json=body)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["human_assisted"]["arms"]["single"]["correct"] == 1
    assert result["summary"]["arms"]["single"]["correct"] == 0
    assert result["summary"]["arms"]["single"]["mean_latency_ms"] is None
    assert result["summary"]["arms"]["single"]["metered_attempts"] == 0
    assert result["rows"][0]["prediction"] is None
    assert not first.inputs and not second.inputs and result["enabled"] is False


def test_cli_runs_configured_adapter_and_cleans_temporary_usage_on_success_and_failure(tmp_path):
    adapter = tmp_path / "synthetic_adapter.py"
    adapter.write_text(
        "from careerpilot.gateway import ProviderReply\n"
        "class Simulated:\n"
        " model_id='simulated-cli.v1'\n external=False\n"
        " async def generate(self,envelope,max_tokens):\n"
        "  return ProviderReply('{\"risk\":true}',10,4)\n"
        "def build(): return Simulated()\n"
        "def fail(): raise RuntimeError('private-factory-exception-canary')\n",
        encoding="utf-8",
    )
    path = tmp_path / "authorized-dataset.json"
    raw = dataset().model_dump()
    raw["examples"][0]["text"] = "private-model-input-canary"
    path.write_text(json.dumps(raw), encoding="utf-8")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join([str(tmp_path), str(Path.cwd() / "src")]),
        "TMPDIR": str(scratch),
        "TMP": str(scratch),
        "TEMP": str(scratch),
    }
    command = [
        sys.executable,
        "scripts/compare_agent_models.py",
        "--dataset",
        str(path),
        "--primary",
        "synthetic_adapter:build",
        "--reviewer",
        "synthetic_adapter:build",
        "--primary-price-micros",
        "1",
        "--reviewer-price-micros",
        "2",
        "--repeats",
        "2",
        "--budget-micros",
        "100000",
    ]
    result = subprocess.run(command, capture_output=True, text=True, env=env, check=False)
    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["frozen_replay_identical"] and summary["enabled"] is False
    assert "rows" not in summary and summary["primary_model"] == "simulated-cli.v1"
    detailed = subprocess.run(command + ["--show-steps"], capture_output=True, text=True, env=env)
    assert detailed.returncode == 0 and len(json.loads(detailed.stdout)["rows"]) == 8
    assert "private-model-input-canary" not in detailed.stdout + detailed.stderr
    assert not list(scratch.iterdir())
    assert "private-model-input-canary" not in result.stdout + result.stderr
    assert not list(scratch.iterdir())
    limited = subprocess.run(command[:-1] + ["1"], capture_output=True, text=True, env=env)
    assert limited.returncode == 3 and json.loads(limited.stdout)["stopped"] == "BUDGET_LIMIT"
    assert not list(scratch.iterdir())
    command[command.index("--primary") + 1] = "synthetic_adapter:fail"
    failed = subprocess.run(command, capture_output=True, text=True, env=env)
    assert failed.returncode == 2 and "private-factory-exception-canary" not in failed.stderr
    assert not list(scratch.iterdir())
