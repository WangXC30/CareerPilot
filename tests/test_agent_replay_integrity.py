"""Frozen local replay must fail closed without invoking any provider."""

import pytest

from careerpilot import agent_experiments as experiments
from careerpilot.domain import DomainError


@pytest.fixture
def frozen(monkeypatch):
    monkeypatch.setattr(experiments.growth, "save", lambda db, owner, kind, value, trace: value)
    value = experiments.evaluate(None, None, None)
    monkeypatch.setattr(experiments.growth, "owned", lambda *args: value)
    monkeypatch.setattr(experiments.growth, "data", lambda row: row)
    return value


def test_changed_predictions_with_same_score_are_not_identical(frozen):
    predictions = frozen["results"][0]["predictions"]
    predictions[0] = False
    predictions[4] = False
    result = experiments.replay(None, None, "fixture")
    assert result["results"][0]["correct"] == frozen["results"][0]["correct"]
    assert result["results"][0]["identical"] is False
    assert result["enabled"] is False


@pytest.mark.parametrize(
    "malformation", ["short", "integer", "missing", "duplicate", "digest", "version"]
)
def test_malformed_replay_is_domain_conflict(frozen, malformation):
    if malformation == "short":
        frozen["results"][0]["predictions"].pop()
    elif malformation == "integer":
        frozen["results"][0]["predictions"][0] = 1
    elif malformation == "missing":
        del frozen["results"][0]["predictions"]
    elif malformation == "duplicate":
        frozen["results"][1]["mode"] = frozen["results"][0]["mode"]
    elif malformation == "digest":
        frozen["dataset_sha256"] = "changed"
    else:
        frozen["implementation_version"] = "unknown"
    with pytest.raises(DomainError) as error:
        experiments.replay(None, None, "fixture")
    assert error.value.status == 409


def test_legacy_predictions_remain_replayable(frozen):
    del frozen["dataset_sha256"]
    del frozen["implementation_version"]
    assert all(row["identical"] for row in experiments.replay(None, None, "fixture")["results"])
