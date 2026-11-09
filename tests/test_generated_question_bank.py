"""Frozen generated exercises must match all independently checked executions."""

import json

from careerpilot import assessment
from scripts.build_verified_question_bank import build


def test_every_frozen_exercise_matches_rebuilt_and_executed_bank():
    rebuilt = build()
    assert rebuilt == json.loads(assessment.generated_bytes)
    assert {skill: len(items) for skill, items in rebuilt.items()} == {"python": 5000, "sql": 5000}
    for skill, questions in rebuilt.items():
        assert len({q[0] for q in questions}) == 5000
        assert all(len(q[1]) == len(set(q[1])) == 4 and 0 <= q[2] < 4 for q in questions)
        # Test the versioned evaluator at every generated index, in real six-item batches.
        bank = assessment.V4_BANK[skill]
        for start in range(18, len(bank), 6):
            indices = list(range(start, min(start + 6, len(bank))))
            if len(indices) < 6:
                indices += list(range(18, 18 + 6 - len(indices)))
            result = assessment.grade(
                skill,
                [bank[i][2] for i in indices],
                "foundation.v4",
                [f"{skill}.{i}" for i in indices],
            )
            assert result["passed"] and result["score"] == 6


def test_mixed_three_and_four_option_submission_validation(system):
    client, _, headers, *_ = system
    created = client.post(
        "/api/v1/assessments", headers=headers, json={"skill_id": "python"}
    ).json()
    four = next(i for i, q in enumerate(created["questions"]) if len(q["choices"]) == 4)
    three = next(i for i, q in enumerate(created["questions"]) if len(q["choices"]) == 3)
    path = f"/api/v1/assessments/{created['id']}/submit"
    request_headers = {**headers, "If-Match": f'"{created["version"]}"'}
    invalid = [0] * 6
    invalid[three] = 3
    assert client.post(path, headers=request_headers, json={"answers": invalid}).status_code == 422
    valid = [0] * 6
    valid[four] = 3
    assert client.post(path, headers=request_headers, json={"answers": valid}).status_code == 200
