"""Private practice workflow shared by database and recovery contract checks."""


def create_practice(client, headers, job_id, version_id, plan_id, prefix):
    version = client.get(f"/api/v1/resume-versions/{version_id}", headers=headers).json()
    facts = [f["id"] for f in version["facts"] if f["kind"] in {"project", "experience"}][:3]
    response = client.post(
        "/api/v1/interviews",
        headers={**headers, "Idempotency-Key": prefix + "-session"},
        json={
            "job_id": job_id,
            "resume_version_id": version_id,
            "fact_ids": facts,
            "confirmed": True,
        },
    )
    assert response.status_code == 201, response.text
    s = response.json()
    while s["status"] == "running":
        q = s["current_question"]
        response = client.post(
            f"/api/v1/interviews/{s['id']}/answers",
            headers={**headers, "If-Match": f'"{s["version"]}"'},
            json={
                "question_id": q["id"],
                "text": "背景是课程任务，我先检查输入，结果发现异常，验证日志并记录未知问题。",
                "choice": 0 if q["kind"] == "foundation" else None,
            },
        )
        assert response.status_code == 200, response.text
        s = response.json()
    response = client.post(
        f"/api/v1/interviews/{s['id']}/evaluate",
        headers={**headers, "If-Match": f'"{s["version"]}"'},
        json={"confirmed": True},
    )
    assert response.status_code == 201, response.text
    e = response.json()
    assert e["dimensions"]["project_evidence"]["score"] is None
    response = client.put(
        f"/api/v1/interview-evaluations/{e['id']}/review",
        headers={**headers, "If-Match": '"0"'},
        json={
            "status": "confirmed",
            "summary": "本人核对后复查相关知识点",
            "decisions": e["suggested_review"][:1],
            "confirmed": True,
        },
    )
    assert response.status_code == 200, response.text
    r = response.json()
    response = client.post(
        f"/api/v1/interview-reviews/{r['id']}/plan-preview",
        headers={**headers, "If-Match": '"1"', "Idempotency-Key": prefix + "-preview"},
        json={"plan_id": plan_id, "confirmed": True},
    )
    assert response.status_code == 201, response.text
    p = response.json()
    assert p["validation_error"] is None, p
    return {"session": s, "evaluation": e, "review": r, "preview": p}


def verify_practice(client, headers, saved):
    s, e, r, p = [saved[k] for k in ("session", "evaluation", "review", "preview")]
    assert client.get(f"/api/v1/interviews/{s['id']}", headers=headers).json() == s
    assert client.get(f"/api/v1/interview-evaluations/{e['id']}", headers=headers).json() == e
    assert (
        next(
            row
            for row in client.get("/api/v1/interview-reviews", headers=headers).json()
            if row["id"] == r["id"]
        )
        == r
    )
    response = client.post(
        f"/api/v1/plan-previews/{p['id']}/accept",
        headers={**headers, "If-Match": '"1"'},
        json={"confirmed": True},
    )
    assert response.status_code == 200, response.text
    plan = client.get(f"/api/v1/plans/{p['plan_id']}", headers=headers).json()
    assert any(t.get("interview_review_id") == r["id"] for t in plan["tasks"])
    assert (
        client.post(
            f"/api/v1/plan-previews/{p['id']}/accept",
            headers={**headers, "If-Match": '"1"'},
            json={"confirmed": True},
        ).status_code
        == 409
    )
