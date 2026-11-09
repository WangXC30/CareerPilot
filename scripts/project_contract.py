"""Synthetic browser/DB half of the real Docker evaluation drill."""

import argparse
import json
import socket
import threading
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import uvicorn
from alembic import command
from alembic.config import Config
from playwright.sync_api import sync_playwright
from sqlalchemy.orm import Session

from careerpilot import accounts, growth, learning, project_evidence, projects, resumes
from careerpilot.api import create_app
from careerpilot.cli import provision
from careerpilot.domain import Principal
from careerpilot.persistence import User, database_url, make_engine
from careerpilot.seed import seed
from tests.test_v1_release import PROFILE
from tests.test_v2_opportunities import JD
from tests.test_v3_projects import SOLUTION

CONTEXT = Path("/tmp/project-drill-context.json")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["prepare", "verify"])
    args = parser.parse_args()
    url = database_url()
    engine = make_engine(url)
    if args.action == "prepare":
        config = Config("alembic.ini")
        config.attributes["database_url"] = url
        command.upgrade(config, "head")
        seed(engine)
        user_id, token = provision(engine, "Synthetic sandbox student")
        context = {"user_id": user_id, "token": token}
    else:
        context = json.loads(CONTEXT.read_text())
    headers = {"Authorization": "Bearer " + context["token"]}
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(url, local_only=False),
            host="127.0.0.1",
            port=port,
            log_level="error",
            access_log=False,
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    try:
        with httpx.Client(base_url=base, headers=headers, trust_env=False) as client:
            for _ in range(100):
                try:
                    if client.get("/health/ready").status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                time.sleep(0.05)
            if args.action == "prepare":
                assert (
                    client.put(
                        "/api/v1/profile", headers={"If-Match": '"0"'}, json=PROFILE
                    ).status_code
                    == 200
                )
                job = client.post("/api/v1/jobs/import", json={"content": JD}).json()
                context["job_id"] = job["id"]
                plan = client.post(
                    "/api/v1/plans",
                    json={
                        "job_ids": [job["id"]],
                        "start_date": datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat(),
                    },
                ).json()
                plan = client.post(
                    f"/api/v1/plans/{plan['id']}/publish",
                    headers={"If-Match": '"1"'},
                    json={"confirmed": True},
                ).json()
                plan = client.post(
                    f"/api/v1/plans/{plan['id']}/tasks/{plan['tasks'][0]['id']}",
                    headers={"If-Match": f'"{plan["version"]}"'},
                    json={"action": "lock"},
                ).json()
                context["plan_id"], context["locked_task"] = plan["id"], plan["tasks"][0]
            errors = []
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(base + "/app/#portfolio")
                page.locator("#connect").click()
                page.locator("#token").fill(context["token"])
                page.locator("#account-form button[type=submit]").click()
                page.locator("#project-recommend").wait_for()
                if args.action == "prepare":
                    page.locator("#project-recommend button").click()
                    page.locator("[data-project-create]").click()
                    page.locator("#project-asset").wait_for(state="attached")
                    page.get_by_text("保存文本成果或 GitHub 引用", exact=True).click()
                    page.locator('#project-asset input[name="file"]').set_input_files(
                        {
                            "name": "README.md",
                            "mimeType": "text/markdown",
                            "buffer": "# 练习记录\n记录输入边界；业务收益尚未知。".encode(),
                        }
                    )
                    page.locator("#project-asset button").click()
                    page.get_by_text("私人项目成果已保存。", exact=True).wait_for()
                    stage = page.locator('[data-milestone="design"]')
                    stage.locator('select[name="status"]').select_option("completed")
                    stage.locator('textarea[name="note"]').fill(
                        "检查异常输入 <img src=x onerror=alert(1)>，未产生业务指标。"
                    )
                    stage.locator('input[type="checkbox"]').check()
                    stage.locator("button").click()
                    page.get_by_text(
                        "阶段记录已保存，能力验证仍以有效评测证据为准。", exact=True
                    ).wait_for()
                    page.locator("#project-plan button").click()
                    page.locator('[data-project-plan-decision="accept"]').click()
                    page.get_by_text(
                        "项目任务已加入计划，预算和先修校验通过。", exact=True
                    ).wait_for()
                    form = page.locator("#project-narrative")
                    form.locator('select[name="status"]').select_option("confirmed")
                    for field, value in {
                        "situation": "课程练习",
                        "task": "整理岗位输入",
                        "action": "本人实现异常输入过滤与城市汇总",
                    }.items():
                        form.locator(f'textarea[name="{field}"]').fill(value)
                    form.locator('input[type="checkbox"]').check()
                    form.locator("button").click()
                    page.get_by_text(
                        "陈述版本已保存。已有简历陈述保留原文本，来源改版后需重新确认。", exact=True
                    ).wait_for()
                    reflection = page.locator('[data-milestone="reflection"]')
                    reflection.locator('select[name="status"]').select_option("completed")
                    reflection.locator('textarea[name="note"]').fill(
                        "已记录代码与边界，等待评测；不推断业务收益。"
                    )
                    reflection.locator("button").click()
                    page.get_by_text(
                        "阶段记录已保存，能力验证仍以有效评测证据为准。", exact=True
                    ).wait_for()
                    page.locator("#project-code textarea").fill(SOLUTION)
                    page.locator("#project-code button").click()
                    page.get_by_role("heading", name="等待评测", exact=True).wait_for()
                    items = client.get("/api/v1/projects").json()
                    context["project_id"] = items[0]["id"]
                    CONTEXT.write_text(json.dumps(context), encoding="utf-8")
                else:
                    page.locator("[data-project-open]").click()
                    page.get_by_role("heading", name="有限测试通过", exact=True).wait_for()
                    page.locator("#project-star-preview").click()
                    page.locator("#project-star-fact").click()
                    page.get_by_text(
                        "已保存这版 STAR 简历陈述，来源版本可追溯。", exact=True
                    ).wait_for()
                    page.locator('#project-review select[name="decision"]').select_option("accept")
                    page.locator('#project-review textarea[name="note"]').fill(
                        "五项有限测试通过，个人过程已记录，真实业务收益仍未知。"
                    )
                    page.locator("#project-review button").click()
                    page.get_by_text("本次阶段复核已保存。", exact=True).wait_for()
                    assert page.locator("img[src=x]").count() == 0
                    for width in [1440, 375]:
                        page.set_viewport_size({"width": width, "height": 1000})
                        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                        page.evaluate("scrollTo(0, 0)")
                        page.screenshot(path=f"/tmp/project-{width}.png", animations="disabled")
                        for section, selector in {
                            "stages": ".project-stage",
                            "star": "#project-narrative",
                        }.items():
                            page.locator(selector).first.evaluate(
                                "el => scrollTo(0, el.getBoundingClientRect().top + scrollY - 120)"
                            )
                            page.screenshot(
                                path=f"/tmp/project-{section}-{width}.png", animations="disabled"
                            )
                assert not errors, errors
                browser.close()
            if args.action == "verify":
                result = client.get(f"/api/v1/jobs/{context['job_id']}/match").json()
                assert result["components"]["project_evidence"]["ratio"] == 1.0, result
                plan = client.get(f"/api/v1/plans/{context['plan_id']}").json()
                assert (
                    next(t for t in plan["tasks"] if t["id"] == context["locked_task"]["id"])
                    == context["locked_task"]
                )
                assert sum(t["minutes"] for t in plan["tasks"] if t.get("project_id")) == 180
                project = client.get(f"/api/v1/projects/{context['project_id']}").json()
                assert project["reviews"][-1]["current"]
                with Session(engine) as db, db.begin():
                    user = db.get(User, context["user_id"])
                    principal = Principal(user.id, user.tenant_id)
                    evidence = project_evidence.current(db, principal)
                    assert len(evidence) == 1
                    facts = [
                        growth.data(r)
                        for r in db.scalars(growth.owner_query(principal, "resume_fact"))
                    ]
                    assert (
                        len(facts) == 1
                        and resumes.fact_status(db, principal, facts[0]) == "current"
                    )
                    assert all(s["status"] != "verified" for s in learning.snapshots(db, principal))
                    receipt = growth.data(
                        growth.owned(
                            db, principal, evidence[0]["assessment_id"], "project_assessment"
                        )
                    )
                    assert receipt["image_id"].startswith("sha256:")
                    artifact = growth.owned(
                        db, principal, evidence[0]["artifact_id"], "project_submission"
                    )
                    projects.withdraw(db, principal, artifact.id, artifact.version, "drill")
                    assert resumes.fact_status(db, principal, facts[0]) == "source_withdrawn"
                    assert project_evidence.current(db, principal) == []
                    request = accounts.request_deletion(db, principal, "synthetic-drill-cleanup")
                    assert request["status"] == "queued"
                assert accounts.process_deletion(engine)
                print(
                    json.dumps(
                        {
                            "browser": "Chromium",
                            "real_sandbox_receipt": True,
                            "project_match_ratio": 1.0,
                            "resume_source_withdrawal": True,
                            "foundation_not_promoted": True,
                            "mobile_width": 375,
                            "console_errors": len(errors),
                            "synthetic_account_deleted": True,
                            "project_milestones_assets_star_plan_and_review": True,
                            "locked_task_preserved": True,
                        }
                    )
                )
            else:
                print(json.dumps({"queued": True, "browser": "Chromium"}))
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        engine.dispose()


if __name__ == "__main__":
    main()
