"""Simulated-provider contracts for the new draft specialist workflow evaluator."""

import asyncio
import copy
import json

import pytest

from careerpilot.domain import DomainError
from careerpilot.expert_comparison import compare, replay
from careerpilot.expert_protocol import digest
from careerpilot.gateway import Gateway, ProviderReply
from scripts.prepare_expert_review import cases
from tests.test_gateway import principal_of


class Simulated:
    external = False
    model_id = "simulated-specialist.v1"
    input_token_overhead = 1024

    def __init__(self, fail=None):
        self.inputs = []
        self.fail = fail

    async def generate(self, envelope, max_tokens):
        payload = json.loads(json.loads(envelope)["untrusted_data"])
        self.inputs.append(payload)
        if self.fail == "timeout":
            raise TimeoutError("private-canary")
        action = {
            "career": "request_evidence",
            "learning": "reduce_scope",
            "job": "request_fact_check",
            "interview": "give_practice_feedback",
        }[payload["specialist"]]
        return ProviderReply(
            json.dumps(
                {
                    "action": action,
                    "citations": [
                        {"id": "invalid" if self.fail == "grounding" else "evidence", "version": 1}
                    ],
                }
            ),
            40,
            7,
        )


def setup(system, fail=None):
    suite = [cases()[index] for index in (0, 4, 8, 12)]
    first, second = Simulated(fail), Simulated()
    engine = system[1]
    gates = [
        Gateway(engine, p, micros_per_token=2, daily_tokens=1000000, task_tokens=1000000)
        for p in (first, second)
    ]
    return suite, first, second, gates


def run(system, suite, gates, **kwargs):
    return asyncio.run(
        compare(suite, digest(suite), principal_of(system), *gates, repeats=2, **kwargs)
    )


def test_real_handoff_contract_same_evidence_no_answer_leak_sanitized_frozen_replay(system):
    suite, first, second, gates = setup(system)
    report = run(system, suite, gates)
    assert len(first.inputs) == 16 and len(second.inputs) == 8
    assert all(
        "expected_action" not in p and "required_sources" not in p
        for p in first.inputs + second.inputs
    )
    assert all("candidate_decision" not in p for p in first.inputs)
    assert all(
        "candidate_decision" in p and p["agent_role"] == p["specialist"] for p in second.inputs
    )
    assert all(p["agent_role"] == "generalist" for p in first.inputs)
    assert report["summary"]["repeat_quality_gains"] == [0, 0]
    single, reviewed = (report["summary"]["arms"][a] for a in ("single", "reviewed"))
    assert single["correct"] == reviewed["correct"] == 8
    assert reviewed["estimated_cost_micros"] == single["estimated_cost_micros"] * 2
    assert "我自称" not in json.dumps(report, ensure_ascii=False)
    assert report["enabled"] is False and replay(suite, report)["identical"]


def test_stale_review_hash_rejects_before_provider_call(system):
    suite, first, second, gates = setup(system)
    with pytest.raises(ValueError):
        asyncio.run(compare(suite, "0" * 64, principal_of(system), *gates))
    assert not first.inputs and not second.inputs


def test_chat_overhead_budget_and_full_failed_denominator(system):
    suite, first, second, gates = setup(system)
    report = run(system, suite, gates, budget_micros=1)
    assert report["stopped"] == "BUDGET_LIMIT" and not first.inputs and not second.inputs
    assert all(
        part["correct"] == 0 and part["cases"] == 8 and part["mean_latency_ms"] is None
        for part in report["summary"]["arms"].values()
    )
    assert replay(suite, report)["identical"]


def test_grounding_failure_is_known_cost_no_decision_or_content_published(system):
    suite, first, second, gates = setup(system, "grounding")
    report = run(system, suite, gates)
    assert report["stopped"] is None and not second.inputs
    assert all(
        row["error"] == "MODEL_GROUNDING_ERROR" and row["decision"] is None
        for row in report["rows"]
    )
    assert all(
        part["correct"] == 0 and part["cost_complete"]
        for part in report["summary"]["arms"].values()
    )
    assert replay(suite, report)["identical"]


def test_unknown_cost_stops_calls_and_keeps_unrun_rows(system):
    suite, first, second, gates = setup(system, "timeout")
    report = run(system, suite, gates)
    assert report["stopped"] == "COST_UNKNOWN" and len(first.inputs) == 1 and not second.inputs
    assert sum(row["error"] == "NOT_RUN" for row in report["rows"]) == 15
    assert "private-canary" not in json.dumps(report)
    assert replay(suite, report)["identical"]


def test_external_consent_checked_before_start_and_before_every_call(system):
    suite, first, second, gates = setup(system)
    first.external = True
    with pytest.raises(DomainError):
        run(system, suite, gates)
    report = run(system, suite, gates, allow_external=True)
    assert report["stopped"] == "CONSENT_REQUIRED" and not first.inputs and not second.inputs


def test_replay_rejects_missing_duplicates_stale_evidence_and_failed_decisions(system):
    suite, first, second, gates = setup(system)
    report = run(system, suite, gates)
    for change in ("missing", "duplicate", "stale", "failed"):
        changed = copy.deepcopy(report)
        if change == "missing":
            changed["rows"].pop()
        if change == "duplicate":
            changed["rows"][1] = changed["rows"][0]
        if change == "stale":
            changed["rows"][0]["decision"]["citations"][0]["version"] = 2
        if change == "failed":
            changed["rows"][0]["error"] = "MODEL_GROUNDING_ERROR"
        with pytest.raises(ValueError):
            replay(suite, changed)
    changed = copy.deepcopy(report)
    changed["summary"]["arms"]["single"]["correct"] = 0
    assert replay(suite, changed)["identical"] is False
