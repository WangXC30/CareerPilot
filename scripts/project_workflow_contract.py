"""Shared PostgreSQL/restore contract, using explicitly simulated evaluator verdicts.

Real code execution is verified separately by project_drill.py. A separate synthetic
owner keeps unrelated users' pending plan previews unchanged during this contract.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from careerpilot import projects
from careerpilot.cli import provision


def checked(response, status=200):
    assert response.status_code == status, response.text
    return response.json()


def create_project_workflow(client, engine):
    _, token = provision(engine, "Synthetic project workflow contract")
    headers = {"Authorization": "Bearer " + token}
    checked(
        client.put(
            "/api/v1/profile",
            headers={**headers, "If-Match": '"0"'},
            json={
                "education": "bachelor",
                "major": "计算机",
                "academic_year": "大三",
                "graduation_date": "2027-06-30",
                "preferred_cities": ["上海"],
                "weekly_minutes": 90,
                "timezone": "Asia/Shanghai",
                "confirmed": True,
            },
        )
    )
    job = checked(
        client.post(
            "/api/v1/jobs/import",
            headers=headers,
            json={"content": "岗位：合成项目练习\n要求：Python"},
        ),
        201,
    )
    plan = checked(
        client.post(
            "/api/v1/plans",
            headers=headers,
            json={
                "job_ids": [job["id"]],
                "start_date": datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat(),
            },
        ),
        201,
    )
    plan = checked(
        client.post(
            f"/api/v1/plans/{plan['id']}/publish",
            headers={**headers, "If-Match": '"1"'},
            json={"confirmed": True},
        )
    )
    plan = checked(
        client.post(
            f"/api/v1/plans/{plan['id']}/tasks/{plan['tasks'][0]['id']}",
            headers={**headers, "If-Match": f'"{plan["version"]}"'},
            json={"action": "lock"},
        )
    )
    p = checked(
        client.post(
            "/api/v1/projects",
            headers={**headers, "Idempotency-Key": "project"},
            json={"job_id": job["id"], "template_id": projects.TEMPLATE["id"], "confirmed": True},
        ),
        201,
    )
    asset = checked(
        client.post(
            f"/api/v1/projects/{p['id']}/assets",
            headers={**headers, "Idempotency-Key": "asset"},
            json={
                "kind": "text",
                "filename": "README.md",
                "content": "合成验收笔记，记录输入边界与仍未知的业务指标",
            },
        ),
        201,
    )
    for milestone in ("design", "reflection"):
        p = checked(
            client.patch(
                f"/api/v1/projects/{p['id']}/milestones/{milestone}",
                headers={**headers, "If-Match": f'"{p["version"]}"'},
                json={
                    "status": "completed",
                    "note": "本人记录实际过程；不作为能力证明",
                    "asset_ids": [asset["id"]],
                    "confirmed": True,
                },
            )
        )
    narrative = checked(
        client.put(
            f"/api/v1/projects/{p['id']}/narrative",
            headers={**headers, "If-Match": '"0"'},
            json={
                "status": "confirmed",
                "situation": "合成课程场景",
                "task": "数据检查练习",
                "action": "本人整理异常输入与测试清单",
                "result": "",
                "asset_ids": [asset["id"]],
                "confirmed": True,
            },
        )
    )
    artifact = checked(
        client.post(
            f"/api/v1/projects/{p['id']}/submissions",
            headers={**headers, "If-Match": f'"{p["version"]}"', "Idempotency-Key": "code"},
            json={
                "code": "# Contract fixture; no real execution claimed.\ndef solve(x): return {}"
            },
        ),
        201,
    )
    p = checked(client.get(f"/api/v1/projects/{p['id']}", headers=headers))
    preview = checked(
        client.post(
            f"/api/v1/projects/{p['id']}/plan-preview",
            headers={**headers, "If-Match": f'"{p["version"]}"', "Idempotency-Key": "preview"},
            json={"plan_id": plan["id"], "confirmed": True},
        ),
        201,
    )
    return {
        "headers": headers,
        "project": p,
        "asset": asset,
        "narrative": narrative,
        "artifact": artifact,
        "plan": plan,
        "preview": preview,
    }


def verify_project_workflow(client, engine, context):
    headers, p, preview = context["headers"], context["project"], context["preview"]
    downloaded = client.get(
        f"/api/v1/project-assets/{context['asset']['id']}/download", headers=headers
    )
    assert downloaded.status_code == 200 and "合成验收笔记" in downloaded.text
    response = client.post(
        f"/api/v1/plan-previews/{preview['id']}/accept",
        headers={**headers, "If-Match": '"1"'},
        json={"confirmed": True},
    )
    checked(response)
    assert (
        client.post(
            f"/api/v1/plan-previews/{preview['id']}/accept",
            headers={**headers, "If-Match": '"1"'},
            json={"confirmed": True},
        ).status_code
        == 409
    )
    plan = checked(client.get(f"/api/v1/plans/{context['plan']['id']}", headers=headers))
    assert sum(t["minutes"] for t in plan["tasks"] if t.get("project_id") == p["id"]) == 180
    assert next(t for t in plan["tasks"] if t["id"] == context["plan"]["tasks"][0]["id"])["locked"]
    assert all(v <= 90 for v in plan["validation"]["weekly_minutes"].values())
    with Session(engine) as db, db.begin():
        work = projects.claim(db)
        assert work and work["receipt"]["artifact_id"] == context["artifact"]["id"]
        receipt = work["receipt"]
        projects.complete(
            db,
            receipt["id"],
            receipt["lease_token"],
            {
                "status": "passed",
                "test_version": projects.TEMPLATE["test_version"],
                "image_id": "sha256:" + "c" * 64,
                "results": [
                    {"id": c[0], "passed": True, "reason": "SIMULATED_CONTRACT_VERDICT"}
                    for c in projects.CASES
                ],
            },
        )
    checked(
        client.post(
            f"/api/v1/projects/{p['id']}/reviews",
            headers={**headers, "If-Match": f'"{p["version"]}"', "Idempotency-Key": "review"},
            json={
                "decision": "accept",
                "note": "仅验证存储/恢复合同，评测回执为显式模拟",
                "confirmed": True,
            },
        ),
        201,
    )
    fact = checked(
        client.post(
            f"/api/v1/projects/{p['id']}/narrative-fact",
            headers={**headers, "If-Match": '"1"', "Idempotency-Key": "fact"},
            json={"confirmed": True},
        ),
        201,
    )
    assert "结果：" not in fact["text"]
    facts = checked(client.get("/api/v1/resume-facts", headers=headers))
    assert next(f for f in facts if f["id"] == fact["id"])["source_status"] == "current"
    checked(
        client.post(
            f"/api/v1/project-assets/{context['asset']['id']}/withdraw",
            headers={**headers, "If-Match": '"1"'},
            json={"confirmed": True},
        )
    )
    detail = checked(client.get(f"/api/v1/projects/{p['id']}", headers=headers))
    assert detail["progress"][0]["status"] == "needs_review"
    assert not detail["reviews"][-1]["current"]
    facts = checked(client.get("/api/v1/resume-facts", headers=headers))
    assert next(f for f in facts if f["id"] == fact["id"])["source_status"] == "source_withdrawn"
