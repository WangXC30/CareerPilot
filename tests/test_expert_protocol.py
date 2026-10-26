"""Closed-world draft protocol checks; no human review or model benefit is inferred."""

import copy

import pytest
from pydantic import ValidationError

from careerpilot.expert_protocol import Case, digest, score, validate_decision
from scripts.prepare_expert_review import cases


def decision(case):
    return {"action": case.expected_action, "citations": [{"id": "evidence", "version": 1}]}


def test_four_specialists_no_expected_answers_or_side_effect_authority_in_handoffs():
    suite = cases()
    assert len(suite) == 16 and len({case.id for case in suite}) == 16
    assert {case.specialist for case in suite} == {"career", "learning", "job", "interview"}
    for case in suite:
        request = case.handoff()
        assert "expected_action" not in request and "required_sources" not in request
        assert "read-only" in request["task"]
        assert case.output_schema()["additionalProperties"] is False
        assert score(case, decision(case))


@pytest.mark.parametrize(
    "change", ["action", "invented", "stale", "duplicate", "extra", "boolean_version"]
)
def test_unrecognized_action_fabricated_stale_duplicate_and_extra_claims_reject(change):
    case = cases()[0]
    value = decision(case)
    if change == "action":
        value["action"] = "publish_and_promote_skill"
    if change == "invented":
        value["citations"][0]["id"] = "another-owner-source"
    if change == "stale":
        value["citations"][0]["version"] = 2
    if change == "duplicate":
        value["citations"] *= 2
    if change == "extra":
        value["reasoning_content"] = "private-reasoning-canary"
    if change == "boolean_version":
        value["citations"][0]["version"] = True
    with pytest.raises((ValueError, ValidationError)):
        validate_decision(case, value)


def test_citations_do_not_make_wrong_action_correct_and_labels_bind_digest():
    original = cases()
    changed = copy.deepcopy(original)
    changed[0].expected_action = "compare_directions"
    assert digest(original) != digest(changed)
    assert score(original[0], decision(changed[0])) is False
    assert Case.model_validate(original[0].model_dump()).handoff() == original[0].handoff()
