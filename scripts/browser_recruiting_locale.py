"""Disposable bilingual recruiting checks with real downloads, receipts and frozen practice."""

import io
import socket
import tempfile
import threading
import time
import zipfile
from pathlib import Path

import uvicorn
from alembic import command
from alembic.config import Config
from browser_v4_locale import MISMARKED
from fastapi.testclient import TestClient
from playwright.sync_api import expect, sync_playwright
from pypdf import PdfReader

from careerpilot import hiring_reminders
from careerpilot.api import create_app
from careerpilot.cli import provision
from careerpilot.persistence import make_engine
from careerpilot.seed import seed


def main():
    with tempfile.TemporaryDirectory(prefix="careerpilot-recruiting-browser-") as scratch:
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
                        _, token = provision(engine, f"Synthetic recruiting locale {width}")
                        records = prepare(client, token, width)
                        check_width(
                            browser, port, token, width, records, engine, client, Path(scratch)
                        )
                finally:
                    browser.close()
            print(
                "Chromium recruiting locale: facts/versions/ATS/downloads/receipts/reminders/"
                "practice/review/snapshots/originals/keyboard/late responses at 1440/375 passed"
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
        "/jobs/import", {"content": "岗位：后端实习生\n公司：合成企业\n要求：Python\n要求：SQL"}
    )
    facts = [
        post(
            "/resume-facts",
            {"kind": kind, "text": text, "source_type": "manual", "confirmed": True},
        )
        for kind, text in (
            ("name", "请确认并加入姓名"),
            ("contact", "student@example.com"),
            ("project", f"本人项目 {width} <b>原文</b>；背景行动与结果需核对。"),
        )
    ]
    first = post(
        "/resumes",
        {
            "title": "本人简历 <b>原文</b>",
            "job_id": job["id"],
            "fact_ids": [f["id"] for f in facts],
            "confirmed": True,
        },
        key="resume-a",
    )
    second = post(
        f"/resumes/{first['resume_id']}/versions",
        {"job_id": job["id"], "fact_ids": [facts[-1]["id"]], "confirmed": True},
        version=next(
            r["version"]
            for r in client.get("/api/v1/resumes", headers=headers).json()
            if r["id"] == first["resume_id"]
        ),
    )
    other = post(
        "/resumes",
        {
            "title": "另一简历",
            "job_id": job["id"],
            "fact_ids": [facts[-1]["id"]],
            "confirmed": True,
        },
        key="resume-b",
    )
    plan = post("/plans", {"job_ids": [job["id"]], "start_date": "2026-10-24"})
    plan = post(f"/plans/{plan['id']}/publish", {"confirmed": True}, version=plan["version"])
    return {
        "first": first,
        "second": second,
        "other": other,
        "job": job,
        "facts": facts,
        "plan": plan,
        "post": post,
        "headers": headers,
    }


def check_width(browser, port, token, width, records, engine, client, scratch):
    context = browser.new_context(
        viewport={"width": width, "height": 1000}, timezone_id="America/Los_Angeles"
    )
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    first, second, other = (records[k] for k in ("first", "second", "other"))
    post, headers = records["post"], records["headers"]

    def get(path):
        response = client.get("/api/v1" + path, headers=headers)
        assert response.is_success, response.text
        return response.json()

    def originals():
        assert page.evaluate(MISMARKED) == [], page.evaluate(MISMARKED)
        assert page.locator("#view b:not([data-original-content])").count() == 0
        assert page.evaluate("document.documentElement.scrollWidth<=innerWidth+1")

    try:
        page.goto(f"http://127.0.0.1:{port}/app/#recruiting")
        page.locator("#ui-locale").select_option("en-US")
        page.locator("#connect").click()
        page.locator("#token").fill(token)
        page.locator("#account-form").get_by_role("button", name="Connect", exact=True).click()
        expect(page.locator("#interview-create-form")).to_be_attached()
        form = page.locator("#resume-form")
        expect(form.get_by_label("Choose resume", exact=True)).to_be_attached()
        page.get_by_role("link", name="Organize facts ↓").focus()
        page.keyboard.press("Enter")
        expect(page.get_by_role("heading", name="Confirmed facts", exact=True)).to_be_focused()
        originals()
        library = page.locator("#fact-library")
        expect(library.get_by_text("请确认并加入姓名", exact=True)).to_be_visible()
        library.locator("summary").first.click()
        library.get_by_role("button", name="Edit and save as a new fact").first.click()
        expect(page.locator('#fact-form [name="text"]')).to_be_focused()
        expect(page.locator('#fact-form [name="confirmed"]')).not_to_be_checked()

        # Import fields have explicit accessible labels and retain adversarial source text.
        page.get_by_text("Import a text resume and confirm each statement", exact=True).click()
        importer = page.locator("#resume-import-form")
        importer.get_by_label("Text resume", exact=True).set_input_files(
            {
                "name": "original.txt",
                "mimeType": "text/plain",
                "buffer": "项目经历\n原文导入 <b>保留</b>".encode(),
            }
        )
        importer.get_by_role("button", name="Preview resume candidates").click()
        preview = page.locator("#resume-import-preview")
        expect(preview).to_contain_text("Line extraction and section suggestions are unconfirmed")
        expect(preview.locator("legend").first).to_be_focused()
        expect(preview.get_by_label("Section", exact=True).first).to_be_attached()
        originals()
        confirm_import = preview.locator("#resume-import-confirm")
        confirm_import.locator('[type="checkbox"]').last.check()
        confirm_import.get_by_role("button", name="Confirm and import selected facts").click()
        expect(page.locator("#notice")).to_contain_text("Saved to your private application records")
        assert any(f["text"] == "原文导入 <b>保留</b>" for f in get("/resume-facts"))

        form.get_by_label("Choose resume", exact=True).select_option(first["resume_id"])
        page.get_by_role("button", name="Inspect version 2", exact=True).click()
        inspection = page.locator("#resume-inspection")
        expect(inspection).to_contain_text("Confirm and add your name")
        expect(inspection.get_by_role("heading").first).to_be_focused()
        page.get_by_role("button", name="Inspect version 1", exact=True).click()
        expect(inspection).to_contain_text("Resume version 1")
        expect(inspection).to_contain_text("请确认并加入姓名")
        originals()
        saved_version = get(f"/resume-versions/{first['id']}")
        for fmt, button in (("docx", "Export Word"), ("pdf", "Export PDF")):
            with page.expect_download() as downloading:
                inspection.get_by_role("button", name=button, exact=True).click()
            artifact = scratch / f"resume-{width}.{fmt}"
            downloading.value.save_as(artifact)
            if fmt == "docx":
                with zipfile.ZipFile(artifact) as package:
                    text = package.read("word/document.xml").decode()
                assert "请确认并加入姓名" in text and "本人项目" in text
                assert "Confirm and add your name" not in text
            else:
                assert PdfReader(io.BytesIO(artifact.read_bytes())).pages
            artifact.unlink()

        # A late inspection cannot restore an older binding or export panel.
        pending = []
        pattern = f"**/api/v1/resume-versions/{second['id']}"
        page.route(pattern, lambda route: pending.append(route))
        with page.expect_request(pattern):
            page.get_by_role("button", name="Inspect version 2", exact=True).click()
        expect(page.locator("#application-resume")).to_have_value("")
        expect(inspection.get_by_role("button", name="Export Word")).to_have_count(0)
        page.get_by_role("button", name="Inspect version 1", exact=True).click()
        expect(inspection).to_contain_text("Resume version 1")
        pending.pop().fulfill(
            body=client.get(f"/api/v1/resume-versions/{second['id']}", headers=headers).text,
            content_type="application/json",
        )
        page.unroute(pattern)
        expect(page.locator("#application-resume")).to_have_value(first["id"])

        # Fixed draft controls are English; the generated body remains its stored original.
        draft = inspection.locator("#material-draft")
        draft.get_by_label("Material type", exact=True).select_option("email")
        draft.locator('[type="checkbox"]').check()
        draft.get_by_role("button", name="Generate material draft", exact=True).click()
        expect(page.get_by_text("Email · Concise", exact=True)).to_be_visible()
        body = get("/application-drafts")[0]["text"]
        page.get_by_text("Email · Concise", exact=True).click()
        expect(page.locator("pre [data-original-content]").filter(has_text=body)).to_have_count(1)
        # The draft save refreshes the page, so explicitly choose the application version again.
        form.get_by_label("Choose resume", exact=True).select_option(first["resume_id"])
        page.get_by_role("button", name="Inspect version 1", exact=True).click()

        apply = page.locator("#application-form")
        apply.locator('[type="checkbox"]').check()
        apply.get_by_role("button", name="Create application", exact=True).click()
        card = page.locator(".application-card")
        expect(card.locator(".tag")).to_have_text("Draft")
        for target, status in (
            ("prepared", "Prepared"),
            ("awaiting_user_submission", "Awaiting your submission"),
        ):
            card.get_by_text("Advance application status", exact=True).click()
            transition = card.locator(".application-transition")
            transition.get_by_label("Next step", exact=True).select_option(target)
            for checkbox in transition.locator('[type="checkbox"]').all():
                checkbox.check()
            transition.get_by_role("button", name="Confirm status update").click()
            expect(card.locator(".tag")).to_have_text(status)
        receipt = card.locator(".application-receipt")
        receipt.locator('[type="checkbox"]').check()
        receipt.get_by_role("button", name="Confirm submission result").click()
        expect(card).to_contain_text("Submission result unverified")
        receipt.get_by_label(
            "Original receipt text (can be saved privately first)", exact=True
        ).fill("投递成功 合成企业 后端实习生 编号：LOCALE-123")
        receipt.get_by_role("button", name="Save receipt material").click()
        expect(receipt.get_by_label("Choose material", exact=True)).not_to_have_value("")
        receipt.get_by_label("Receipt code", exact=True).fill("LOCALE-123")
        receipt.locator('[name="submitted_at"]').fill("2026-03-08T02:30")
        receipt.locator('[type="checkbox"]').check()
        receipt.get_by_role("button", name="Confirm submission result").click()
        expect(page.locator("#notice")).to_contain_text("browser-local time does not exist")
        expect(card.locator(".tag")).to_have_text("Awaiting your submission")
        receipt.locator('[name="submitted_at"]').fill("2026-03-08T03:30")
        receipt.get_by_role("button", name="Confirm submission result").click()
        expect(card.locator(".tag")).to_have_text("Submitted · receipt confirmed by you")
        application = get("/applications")[0]
        assert application["resume_version_id"] == first["id"]
        assert application["receipt"]["submitted_at"].startswith("2026-03-08T10:30")
        card.get_by_role("button", name="View event history").click()
        expect(card.locator(".app-history h4")).to_be_focused()

        card.get_by_text("Add in-app reminder", exact=True).click()
        reminder = card.locator(".application-reminder")
        reminder.get_by_label("Reminder type", exact=True).select_option("interview")
        expect(reminder.get_by_label("IANA time zone", exact=True)).to_have_value(
            "America/New_York"
        )
        reminder.get_by_label("Local time", exact=True).fill("2029-11-04T01:30")
        for field in ("quiet_start", "quiet_end"):
            reminder.locator(f'[name="{field}"]').fill("0")
        reminder.locator('[type="checkbox"]').check()
        reminder.get_by_role("button", name="Save reminder", exact=True).click()
        notices = page.locator("#hiring-notices")
        expect(notices).to_contain_text("GMT-4")
        notices.get_by_role("button", name="Pause reminder", exact=True).click()
        expect(notices).to_contain_text("Paused")
        notices.get_by_role("button", name="Resume reminder", exact=True).click()
        due = get("/application-reminders")[0]
        expect(notices).to_contain_text("Active")
        assert hiring_reminders.schedule_due(engine, now=due["due_at"]) == 1
        page.locator("#ui-locale").select_option("zh-CN")
        expect(page.locator("#fact-form").get_by_label("所属栏目", exact=True)).to_be_attached()
        page.locator("#ui-locale").select_option("en-US")
        expect(notices).to_contain_text("Delivered")
        expect(notices).to_contain_text("You perform source-site actions yourself")
        notices.get_by_role("button", name="Mark as read", exact=True).click()
        expect(notices.get_by_role("button", name="Mark as read", exact=True)).to_have_count(0)
        assert get(f"/resume-versions/{first['id']}") == saved_version
        originals()

        page.locator("#interview-create summary").click()
        create = page.locator("#interview-create-form")
        create.get_by_label("Resume", exact=True).select_option(first["resume_id"])
        create.get_by_label("Immutable version to use", exact=True).select_option(first["id"])
        expect(page.locator("#interview-project-facts")).to_contain_text(
            "Sources are currently valid"
        )
        page.locator('#interview-project-facts [name="fact_ids"]').check()
        create.locator('[type="checkbox"]').last.check()
        create.get_by_role("button", name="Create text practice", exact=True).click()
        expect(page.locator("#interview-answer-form")).to_be_visible()
        session = get("/interviews")[0]
        while session["status"] == "running":
            question = session["current_question"]
            answer = page.locator("#interview-answer-form")
            expect(answer).to_have_attribute("data-question-id", question["id"])
            originals()
            if question["kind"] == "foundation":
                answer.locator('[name="choice"][value="0"]').check()
            else:
                answer.get_by_label("Your answer", exact=True).fill(
                    "背景是课程任务。我先检查字段，结果发现异常并用日志验证。未知仍需核对。<b>原文</b>"
                )
            with page.expect_response("**/api/v1/interviews/*/answers"):
                answer.get_by_role("button", name="Submit this answer", exact=True).click()
            session = get(f"/interviews/{session['id']}")
        evaluation = page.locator("#interview-evaluation")
        expect(evaluation).to_contain_text("Objective scoring only for basic multiple-choice")
        expect(evaluation).to_contain_text("Text-structure marker proxy")
        # Read the canonical evaluation through the same completed-session endpoint.
        original_evaluation = post(
            f"/interviews/{session['id']}/evaluate", {"confirmed": True}, version=session["version"]
        )
        review = page.locator("#interview-review-form")
        summary = "本人复盘 <b>保留</b>"
        review.get_by_label("My review and revisions", exact=True).fill(summary)
        review.locator('[type="checkbox"]').last.check()
        review.get_by_role("button", name="Confirm and save review", exact=True).click()
        expect(evaluation).to_contain_text("Review version 1")
        page.get_by_role("button", name="Preview adding review tasks", exact=True).click()
        plan_preview = page.locator("#interview-plan-preview")
        expect(plan_preview.get_by_role("heading")).to_be_focused()
        expect(plan_preview).to_contain_text("Interview review confirmed")
        assert get(f"/plans/{records['plan']['id']}")["version"] == records["plan"]["version"]
        plan_preview.get_by_role("button", name="Keep current plan", exact=True).click()
        page.locator("#ui-locale").select_option("zh-CN")
        expect(page.locator('#interview-review-form [name="summary"]')).to_have_value(summary)
        page.locator("#ui-locale").select_option("en-US")
        expect(page.locator('#interview-review-form [name="summary"]')).to_have_value(summary)
        assert get(f"/interview-evaluations/{original_evaluation['id']}") == original_evaluation
        assert get("/skill-evidence") == []
        originals()

        # A slow completed-session evaluation must not overwrite another session's answer form.
        alternative = post(
            "/interviews",
            {
                "job_id": records["job"]["id"],
                "resume_version_id": other["id"],
                "fact_ids": [],
                "confirmed": True,
            },
            key="alternate",
        )
        page.locator("#ui-locale").select_option("zh-CN")
        page.locator("#ui-locale").select_option("en-US")
        expect(page.locator("#interview-evaluation")).to_contain_text("Review version 1")
        pending = []
        pattern = f"**/api/v1/interviews/{session['id']}/evaluate"
        page.route(pattern, lambda route: pending.append(route))
        with page.expect_request(pattern):
            page.locator(f'[data-open-interview="{session["id"]}"]').click()
        page.locator(f'[data-open-interview="{alternative["id"]}"]').click()
        expect(page.locator("#interview-answer-form")).to_have_attribute(
            "data-question-id", alternative["current_question"]["id"]
        )
        pending.pop().fulfill(status=201, json=original_evaluation)
        page.unroute(pattern)
        expect(page.locator("#interview-answer-form")).to_be_visible()
        expect(page.locator("#interview-review-form")).to_have_count(0)
        assert not errors, errors
    finally:
        context.close()


if __name__ == "__main__":
    main()
