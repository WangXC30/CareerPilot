"""Disposable bilingual project UI checks with explicitly simulated receipt production."""

import socket
import tempfile
import threading
import time
from pathlib import Path

import uvicorn
from alembic import command
from alembic.config import Config
from browser_v4_locale import MISMARKED
from fastapi.testclient import TestClient
from playwright.sync_api import expect, sync_playwright
from sqlalchemy.orm import Session

from careerpilot import projects
from careerpilot.api import create_app
from careerpilot.cli import provision
from careerpilot.persistence import make_engine
from careerpilot.seed import seed

SOLUTION = """def solve(records):
    result = {}
    for item in records:
        city, salary = item.get("city"), item.get("salary")
        if isinstance(city, str) and city and type(salary) is int and salary >= 0:
            row = result.setdefault(city, {"count": 0, "total_salary": 0})
            row["count"] += 1
            row["total_salary"] += salary
    return result
"""


def main():
    with tempfile.TemporaryDirectory(prefix="careerpilot-project-browser-") as scratch:
        url = "sqlite:///" + (Path(scratch) / "browser.db").as_posix()
        config = Config("alembic.ini")
        config.attributes["database_url"] = url
        command.upgrade(config, "head")
        engine = make_engine(url)
        seed(engine)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        server = uvicorn.Server(
            uvicorn.Config(create_app(url), host="127.0.0.1", port=port, log_level="error")
        )
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        try:
            for _ in range(100):
                if server.started:
                    break
                time.sleep(0.05)
            assert server.started
            with TestClient(create_app(url)) as client, sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                try:
                    for width in (1440, 375):
                        _, token = provision(engine, f"Synthetic project locale {width}")
                        records = prepare(client, token, width)
                        check_width(
                            browser, port, token, width, records, engine, client, Path(scratch)
                        )
                finally:
                    browser.close()
            print(
                "Chromium project locale: recommendations/code/receipt states/artifacts/"
                "STAR/plan/withdrawal/originals/keyboard/stale requests at 1440/375 passed; "
                "receipt producer simulated, real isolation exercised separately"
            )
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            engine.dispose()


def prepare(client, token, width):
    headers = {"Authorization": "Bearer " + token}

    def post(path, body, *, version=None, key=None):
        extra = {"If-Match": f'"{version}"'} if version is not None else {}
        if key:
            extra["Idempotency-Key"] = key
        response = client.post("/api/v1" + path, headers={**headers, **extra}, json=body)
        assert response.is_success, response.text
        return response.json()

    response = client.put(
        "/api/v1/profile",
        headers={**headers, "If-Match": '"0"'},
        json={
            "education": "bachelor",
            "major": "计算机",
            "academic_year": "大三",
            "graduation_date": "2027-06-30",
            "preferred_cities": ["上海"],
            "weekly_minutes": 600,
            "timezone": "America/New_York",
            "confirmed": True,
        },
    )
    assert response.is_success, response.text
    job = post(
        "/jobs/import", {"content": f"岗位：本人岗位 <b>{width}</b>\n要求：Python\n要求：SQL"}
    )
    plan = post("/plans", {"job_ids": [job["id"]], "start_date": "2026-10-24"})
    plan = post(f"/plans/{plan['id']}/publish", {"confirmed": True}, version=plan["version"])
    plan = post(
        f"/plans/{plan['id']}/tasks/{plan['tasks'][0]['id']}",
        {"action": "lock"},
        version=plan["version"],
    )
    return {"job": job, "plan": plan, "post": post, "headers": headers}


def check_width(browser, port, token, width, records, engine, client, scratch):
    context = browser.new_context(
        viewport={"width": width, "height": 1000}, timezone_id="Pacific/Auckland"
    )
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    post, headers = records["post"], records["headers"]

    def get(path):
        response = client.get("/api/v1" + path, headers=headers)
        assert response.is_success, response.text
        return response.json()

    def originals():
        assert page.evaluate(MISMARKED) == [], page.evaluate(MISMARKED)
        assert page.locator("#view b,#view img").count() == 0
        assert page.evaluate("document.documentElement.scrollWidth<=innerWidth+1"), page.evaluate(
            "document.documentElement.scrollWidth"
        )

    try:
        page.goto(f"http://127.0.0.1:{port}/app/#portfolio")
        page.locator("#ui-locale").select_option("en-US")
        page.locator("#connect").click()
        page.locator("#token").fill(token)
        page.locator("#account-form").get_by_role("button", name="Connect", exact=True).click()
        recommendations = page.locator("#project-recommend")
        expect(recommendations.get_by_label("Target job", exact=True)).to_be_visible()
        recommendations.get_by_role("button", name="View practice suggestions").focus()
        page.keyboard.press("Enter")
        expect(page.locator("#project-options h3")).to_be_focused()
        expect(page.locator("#project-options")).to_contain_text(
            "do not automatically reserve time"
        )
        page.get_by_role("button", name="Confirm and create private project").click()
        detail = page.locator("#project-detail")
        expect(detail.locator("#project-code")).to_be_visible()
        expect(detail.get_by_role("heading").first).to_be_focused()
        first = get("/projects")[0]
        originals()
        page.get_by_text("Save text artifacts or GitHub references", exact=True).click()
        artifact = detail.locator("#project-asset")
        source = "本人原文 <b>边界记录</b>\n未知业务指标"
        artifact.get_by_label("Filename", exact=True).fill("original.md")
        artifact.get_by_label("Text content", exact=True).fill(source)
        artifact.get_by_role("button", name="Save private artifact", exact=True).click()
        expect(page.locator("#notice")).to_have_text("Private project artifact saved.")
        with page.expect_download() as downloaded:
            detail.get_by_role("button", name="Download text", exact=True).click()
        sample = scratch / f"artifact-{width}.md"
        downloaded.value.save_as(sample)
        assert sample.read_text() == source
        sample.unlink()
        stage = detail.locator('[data-milestone="design"]')
        note = "项目单独估算，不会自动占用或修改学习计划"
        stage.get_by_label("Progress", exact=True).select_option("completed")
        stage.get_by_label("Actual process and results", exact=True).fill(note)
        stage.locator('[name="asset_ids"]').check()
        stage.get_by_role("button", name="Confirm and save stage record").click()
        expect(page.locator("#notice")).to_contain_text("Stage record saved")
        expect(stage.get_by_label("Actual process and results", exact=True)).to_have_value(note)
        narrative = detail.locator("#project-narrative")
        narrative.get_by_label("Statement status", exact=True).select_option("confirmed")
        for label, text in (
            ("Situation", "本人课程 <b>原文</b>"),
            ("Task", "检查输入"),
            ("Your actions", "本人实现过滤与汇总"),
        ):
            narrative.get_by_label(label, exact=True).fill(text)
        narrative.locator('[name="asset_ids"]').check()
        narrative.get_by_role("button", name="Confirm and save statement version").click()
        expect(page.locator("#notice")).to_contain_text("Statement version saved")
        page.get_by_role("button", name="Preview STAR achievement description").click()
        star = detail.locator("#project-star-result")
        expect(star.get_by_role("heading")).to_be_focused()
        expect(star).to_contain_text("本人课程 <b>原文</b>")
        expect(star).to_contain_text("Unspecified business metrics and contribution share")
        expect(star.locator("#project-star-fact")).to_have_count(0)
        assert get("/skill-evidence") == []
        originals()
        page.get_by_role("button", name="Preview project tasks and budget").click()
        preview = detail.locator("#project-plan-preview")
        expect(preview.get_by_role("heading")).to_be_focused()
        preview.get_by_role("button", name="Confirm adding to plan").click()
        expect(page.locator("#notice")).to_contain_text("Budget and prerequisite checks passed")
        changed = get(f"/plans/{records['plan']['id']}")
        assert changed["version"] == records["plan"]["version"] + 1
        locked = [t for t in records["plan"]["tasks"] if t["locked"]]
        assert all(t in changed["tasks"] for t in locked)
        code = SOLUTION + "\n# 本人代码 <b>原文</b>\n"
        detail.get_by_label("Python source code", exact=True).fill(code)
        detail.get_by_role("button", name="Save submission and queue assessment").click()
        expect(page.locator("#notice")).to_contain_text("Submission saved and queued")
        expect(detail.locator("#project-results")).to_contain_text("Queued for assessment")
        first = get(f"/projects/{first['id']}")
        assert first["submissions"][-1]["code"] == code
        assert get("/skill-evidence") == []
        # Producer contract fixture only: the separate host drill runs real isolated code.
        with Session(engine) as db, db.begin():
            work = projects.claim(db)
            receipt = work["receipt"]
            assert receipt["project_id"] == first["id"]
            projects.complete(
                db,
                receipt["id"],
                receipt["lease_token"],
                {
                    "status": "passed",
                    "test_version": projects.TEMPLATE["test_version"],
                    "image_id": "sha256:" + "a" * 64,
                    "results": [
                        {"id": case[0], "passed": True, "reason": "MATCHED"}
                        for case in projects.CASES
                    ],
                },
            )
        page.get_by_role("button", name="Refresh assessment status").click()
        expect(detail.locator("#project-results")).to_contain_text("Limited tests passed")
        expect(detail.locator("#project-results")).to_contain_text("Matches expected output")
        expect(detail.get_by_label("Python source code", exact=True)).to_have_value(code)
        page.get_by_role("button", name="Preview achievement statement", exact=True).click()
        expect(detail.locator("#project-statement")).to_contain_text("Personal contribution share")
        detail.get_by_role("button", name="Confirm and save as a resume fact").click()
        expect(page.locator("#notice")).to_contain_text("Traceable achievement statement saved")
        facts = get("/resume-facts")
        page.get_by_role("button", name="Preview STAR achievement description").click()
        page.get_by_role("button", name="Confirm and save this resume statement version").click()
        expect(page.locator("#notice")).to_contain_text("traceable source versions")
        first = get(f"/projects/{first['id']}")
        canonical = first
        page.locator("#ui-locale").select_option("zh-CN")
        page.locator(f'[data-project-open="{first["id"]}"]').click()
        expect(detail.get_by_label("Python 源代码", exact=True)).to_have_value(code)
        page.locator("#ui-locale").select_option("en-US")
        page.locator(f'[data-project-open="{first["id"]}"]').click()
        expect(detail.get_by_label("Python source code", exact=True)).to_have_value(code)
        assert get(f"/projects/{first['id']}") == canonical
        originals()
        page.get_by_text("Project revision history", exact=True).click()
        page.get_by_role("button", name="Load revision history").click()
        expect(detail.locator("#project-history-results h3")).to_be_focused()
        expect(detail.locator("#project-history-results")).to_contain_text("GMT-4")

        second = post(
            "/projects",
            {
                "job_id": records["job"]["id"],
                "template_id": projects.TEMPLATE["id"],
                "confirmed": True,
            },
            key="second",
        )
        page.locator("#ui-locale").select_option("zh-CN")
        page.locator("#ui-locale").select_option("en-US")
        page.locator(f'[data-project-open="{first["id"]}"]').click()
        expect(detail.locator("#project-narrative")).to_be_attached()
        pending = []
        pattern = f"**/api/v1/projects/{first['id']}/assets"
        page.route(pattern, lambda route: pending.append(route))
        page.get_by_text("Save text artifacts or GitHub references", exact=True).click()
        artifact.get_by_label("Text content", exact=True).fill("迟到的本人保存")
        with page.expect_request(pattern):
            artifact.get_by_role("button", name="Save private artifact", exact=True).click()
        page.locator(f'[data-project-open="{second["id"]}"]').click()
        expect(detail.locator("#project-narrative")).to_be_attached()
        detail.get_by_label("Python source code", exact=True).fill("# 当前项目未保存原文")
        write = pending.pop()
        write.fulfill(response=write.fetch())
        page.unroute(pattern)
        expect(detail.get_by_label("Python source code", exact=True)).to_have_value(
            "# 当前项目未保存原文"
        )

        # Clear old controls while fetching another project; ignore its later failure.
        pending = []
        pattern = f"**/api/v1/projects/{first['id']}"
        page.route(pattern, lambda route: pending.append(route))
        with page.expect_request(pattern):
            page.locator(f'[data-project-open="{first["id"]}"]').click()
        expect(detail.locator("#project-code")).to_have_count(0)
        page.locator(f'[data-project-open="{second["id"]}"]').click()
        expect(detail.locator("#project-code")).to_be_attached()
        detail.get_by_label("Python source code", exact=True).fill("# 后来选择保留")
        pending.pop().fulfill(status=503, json={"message": "stale project failure"})
        page.unroute(pattern)
        expect(detail.get_by_label("Python source code", exact=True)).to_have_value(
            "# 后来选择保留"
        )
        expect(page.locator("#notice")).not_to_contain_text("stale project failure")
        page.locator(f'[data-project-open="{first["id"]}"]').click()
        expect(detail.locator("#project-results")).to_contain_text("Limited tests passed")
        detail.get_by_role("button", name="Withdraw this submission").click()
        expect(page.locator("#notice")).to_contain_text(
            "Related passing evidence and resume sources are no longer valid"
        )
        assert all(
            f["source_status"] != "current"
            for f in get("/resume-facts")
            if f["id"] in {v["id"] for v in facts}
        )
        originals()
        assert not errors, errors
    finally:
        context.close()


if __name__ == "__main__":
    main()
