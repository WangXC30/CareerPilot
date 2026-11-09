"""Interview practice has verifiable feedback and creates no skill evidence."""

from careerpilot import interview_quiz


def test_practice_answers_map_to_displayed_choices():
    public = interview_quiz.public_questions()
    assert len(public) >= 296
    assert len({q["id"] for q in public}) == len(public)
    assert len({q["topic"] for q in public}) >= 10
    assert {q["topic"] for q in public} >= {"HTML", "SQL", "CSS", "Linux", "云计算", "DevOps"}
    positions = set()
    for item, original in zip(public, interview_quiz.QUESTIONS, strict=True):
        assert "answer" not in item and len(set(item["choices"])) == 4
        correct = item["choices"].index(original[3][original[4]])
        positions.add(correct)
        for choice in range(4):
            response = interview_quiz.answer(item["id"], choice)
            assert response["correct"] == (choice == correct)
            assert response["correct_choice"] == correct
            assert response["explanation"] and response["source_url"].startswith("https://")
    assert len(positions) == 4


def test_reviewed_python_computations_and_attribution():
    import json

    items = json.loads(interview_quiz._reviewed_data)
    python = [item for item in items if item["topic"] == "Python"]
    expected = {
        "print(list(range(3, 6)))": str(list(range(3, 6))),
        "print(len('Hello world!'))": str(len("Hello world!")),
        "print(len(set(list))-len(list))": str(
            len(set(["rahul", "monica", "george", "ross", "george", "ross"])) - 6
        ),
    }
    for fragment, result in expected.items():
        match = next(item for item in python if fragment in item["question"])
        assert match["choices"][match["correct"]] == result
        assert "freeCodeCamp" in match["attribution"] and match["reference"].startswith(
            "https://docs.python.org/"
        )
    assert not any("linking a CSS stylesheet to an HTML file" in item["question"] for item in items)


def test_codeprep_attribution_and_python_results():
    question = next(
        q for q in interview_quiz.QUESTIONS if q[0] == "codeprep-py-mcq-bool-is-int-subclass"
    )
    response = interview_quiz.answer(question[0], interview_quiz.order(question).index(question[4]))
    assert isinstance(True, int) and response["correct"]
    assert response["author_url"] == "https://codeprep.dev"
    assert response["license_url"] == "https://creativecommons.org/licenses/by/4.0/"
    assert "unmodified" in response["attribution"]


def test_practice_http_validation_and_no_evidence(system):
    client, _, headers, *_ = system
    before = client.get("/api/v1/skill-evidence", headers=headers).json()
    response = client.get("/api/v1/interview-practice", headers=headers)
    assert response.status_code == 200
    question = response.json()["items"][0]
    assert "correct_choice" not in question
    path = f"/api/v1/interview-practice/{question['id']}/answer"
    assert client.post(path, headers=headers, json={"choice": 3}).status_code == 200
    assert client.post(path, headers=headers, json={"choice": 4}).status_code == 422
    assert client.post(path, headers=headers, json={"choice": True}).status_code == 422
    assert (
        client.post(
            "/api/v1/interview-practice/missing/answer", headers=headers, json={"choice": 0}
        ).status_code
        == 404
    )
    assert client.get("/api/v1/interview-practice").status_code in {401, 403}
    assert client.get("/api/v1/skill-evidence", headers=headers).json() == before
