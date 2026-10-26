"""Synthetic pending peer review and private tutoring through database recovery."""


def checked(response, status=200):
    assert response.status_code == status, response.status_code
    return response.json()


def create_learning(client, alice, bob, bob_id):
    exam = checked(
        client.post(
            "/api/v1/adaptive-exams",
            headers=alice,
            json={"skill_ids": ["python"], "confirmed": True},
        ),
        201,
    )
    for choice in (1, 0, 0):
        exam = checked(
            client.post(
                f"/api/v1/adaptive-exams/{exam['id']}/answer",
                headers={**alice, "If-Match": f'"{exam["version"]}"'},
                json={"choice": choice, "confirmed": True},
            )
        )
    assert exam["status"] == "completed" and exam["summary"]["correct"] == 0
    exam = checked(
        client.post(
            f"/api/v1/adaptive-exams/{exam['id']}/appeal",
            headers={**alice, "If-Match": f'"{exam["version"]}"'},
            json={"text": "本人请同伴核对计时背景", "confirmed": True},
        )
    )
    session = checked(
        client.post(
            "/api/v1/teaching-sessions",
            headers=alice,
            json={"exam_id": exam["id"], "answer_index": 0, "confirmed": True},
        ),
        201,
    )
    session = checked(
        client.post(
            f"/api/v1/teaching-sessions/{session['id']}/turns",
            headers={**alice, "If-Match": '"1"'},
            json={
                "attempt": "恢复前本人推理",
                "focus": "condition",
                "action": "hint",
                "confirmed": True,
            },
        )
    )
    # Separate from the workspace whose pending invitation is exercised after recovery.
    space = checked(
        client.post(
            "/api/v1/workspaces",
            headers=alice,
            json={"name": "恢复合同复核空间", "confirmed": True},
        ),
        201,
    )
    invite = checked(
        client.post(
            f"/api/v1/workspaces/{space['id']}/invitations",
            headers=alice,
            json={"recipient_id": bob_id, "confirmed": True},
        ),
        201,
    )
    checked(
        client.post(
            f"/api/v1/workspace-invitations/{invite['id']}/accept",
            headers=bob,
            json={"ticket": invite["ticket"], "confirmed": True},
        )
    )
    body = {"workspace_id": space["id"], "reviewer_id": bob_id, "confirmed": True}
    endpoint = f"/api/v1/adaptive-exams/{exam['id']}/review-requests"
    revoked = checked(client.post(endpoint, headers=alice, json=body), 201)
    checked(
        client.delete(
            "/api/v1/practice-reviews/" + revoked["id"], headers={**alice, "If-Match": '"1"'}
        )
    )
    pending = checked(client.post(endpoint, headers=alice, json=body), 201)
    return {"exam": exam, "session": session, "revoked": revoked, "pending": pending}


def verify_learning(client, alice, bob, state):
    session = state["session"]
    endpoint = "/api/v1/teaching-sessions/" + session["id"]
    assert checked(client.get(endpoint, headers=alice)) == session
    assert client.get(endpoint, headers=bob).status_code == 404
    continued = checked(
        client.post(
            endpoint + "/turns",
            headers={**alice, "If-Match": '"2"'},
            json={
                "attempt": "恢复后补充反例",
                "action": "hint",
                "focus": "counterexample",
                "confirmed": True,
            },
        )
    )
    assert continued["level"] == 3 and len(continued["turns"]) == 3
    assert (
        client.get("/api/v1/practice-reviews/" + state["revoked"]["id"], headers=bob).status_code
        == 404
    )
    endpoint = "/api/v1/practice-reviews/" + state["pending"]["id"]
    assert checked(client.get(endpoint, headers=bob))["can_review"]
    decision = checked(
        client.post(
            endpoint + "/decision",
            headers={**bob, "If-Match": '"1"'},
            json={
                "conclusion": "inconclusive",
                "note": "人工核对信息不足，不作诚信判断",
                "confirmed": True,
            },
        )
    )
    assert decision["decision"]["penalty"] is None and not decision["decision"]["grade_changed"]
    assert (
        next(
            e
            for e in checked(client.get("/api/v1/adaptive-exams", headers=alice))
            if e["id"] == state["exam"]["id"]
        )
        == state["exam"]
    )
    checked(client.delete(endpoint, headers={**alice, "If-Match": '"2"'}))
    assert client.get(endpoint, headers=bob).status_code == 404
    print(
        "V4 learning persistence: private tutor continuation, revoked/pending peer review, "
        "no grade/penalty changes passed"
    )
