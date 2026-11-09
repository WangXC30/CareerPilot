"""Interview review is sourced and never creates assessment evidence."""

from careerpilot import interview_knowledge


def test_review_cards_have_unique_questions_answers_and_primary_sources():
    cards = interview_knowledge.cards()
    assert len(cards) >= 100
    assert len({card["id"] for card in cards}) == len(cards)
    assert len({card["question"] for card in cards}) == len(cards)
    assert {card["topic"] for card in cards} >= {
        "操作系统",
        "计算机网络",
        "数据库原理",
        "HTTP",
        "Docker",
        "Kubernetes",
        "Git",
        "应用安全",
        "Java",
        "Go",
        "数据结构与算法",
        "Python",
        "JavaScript",
    }
    assert all(card["answer"] and card["source_url"].startswith("https://") for card in cards)


def test_review_endpoint_is_read_only_and_authenticated(system):
    client, _, headers, *_ = system
    before = client.get("/api/v1/skill-evidence", headers=headers).json()
    response = client.get("/api/v1/interview-knowledge", headers=headers)
    assert response.status_code == 200
    assert len(response.json()["items"]) == len(interview_knowledge.CARDS)
    assert client.get("/api/v1/skill-evidence", headers=headers).json() == before
    assert client.get("/api/v1/interview-knowledge").status_code in {401, 403}
