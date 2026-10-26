"""Chromium study pace confirmation, resource feedback and review opt-out journey."""

import argparse
import json
import socket
import tempfile
import threading
import time
from pathlib import Path

import uvicorn
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from playwright.sync_api import expect, sync_playwright
from study_contract import create_study

from careerpilot.api import create_app
from careerpilot.persistence import make_engine
from careerpilot.seed import seed

try:
    from .temporary_artifacts import temporary_artifacts
except ImportError:
    from temporary_artifacts import temporary_artifacts


def run(output=None):
    with tempfile.TemporaryDirectory(prefix="careerpilot-study-") as directory:
        url = "sqlite:///" + (Path(directory) / "study.db").as_posix()
        config = Config("alembic.ini")
        config.attributes["database_url"] = url
        command.upgrade(config, "head")
        engine = make_engine(url)
        seed(engine)
        with TestClient(create_app(url)) as client:
            state = create_study(client, engine)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        server = uvicorn.Server(
            uvicorn.Config(
                create_app(url), host="127.0.0.1", port=port, log_level="error", access_log=False
            )
        )
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        try:
            for _ in range(100):
                if server.started:
                    break
                time.sleep(0.05)
            if output:
                output.mkdir(parents=True, exist_ok=True)
            with sync_playwright() as p:
                browser = p.chromium.launch()
                page = browser.new_page(
                    viewport={"width": 1440, "height": 1050}, reduced_motion="reduce"
                )
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(base + "/app/#learning")
                page.locator("#connect").click()
                page.locator("#token").fill(
                    state["headers"]["Authorization"].removeprefix("Bearer ")
                )
                page.locator("#account-form button[type=submit]").click()
                mode = page.locator("#study-mode-form")
                expect(mode).to_be_visible()
                mode.locator("select").select_option("research_busy")
                mode.get_by_role("button").click()
                preview = page.locator("#study-mode-preview")
                expect(preview).to_contain_text("300 → 150")
                preview.get_by_role("button", name="接受此调整").click()
                expect(page.locator("#plan-view")).to_contain_text("周预算：150")
                resources = page.locator("#study-resource-feedback")
                expect(resources).to_contain_text("本地测验与错题复习")
                fallback = resources.locator('[data-resource="local-practice-python"]')
                fallback.locator("summary").click()
                fallback.get_by_role("link", name="打开学习资源").click()
                expect(page.locator("#skill-graph-form")).to_be_visible()
                page.evaluate("location.hash='learning'")
                expect(resources).to_be_visible()
                entry = resources.locator('[data-resource="resource-python"]').first
                entry.locator("summary").click()
                form = entry.locator("form")
                form.locator('[name="broken"]').uncheck()
                form.locator('[name="note"]').fill("已恢复 <img src=x onerror=alert(1)>")
                form.get_by_role("button").click()
                expect(page.locator("#notice")).to_contain_text("反馈已保存")
                expect(resources).not_to_contain_text("本地测验与错题复习")
                expect(resources.locator("img")).to_have_count(0)
                review = page.locator('[data-review-skill="python"]')
                review.locator("summary").click()
                expect(review).to_contain_text("错题 python.0")
                for width in (1440, 375):
                    page.set_viewport_size({"width": width, "height": 1050})
                    page.locator("#study-review-queue").scroll_into_view_if_needed()
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                    if output:
                        page.locator("#study-review-queue").screenshot(
                            path=str(output / f"review-{width}.png")
                        )
                review.get_by_role("button", name="重新测验 Python").click()
                exam = page.locator("#assessment-form")
                expect(exam).to_be_visible()
                for i, answer in enumerate([1, 0, 2]):
                    exam.locator(f'input[name="q{i}"][value="{answer}"]').check()
                exam.get_by_role("button", name="提交测验").click()
                expect(page.locator("#assessment-view")).to_contain_text("测验通过")
                page.evaluate("location.hash='progress'")
                page.locator("#personal-notification-settings summary").click()
                notification = page.locator("#personal-notification-form")
                notification.locator('[name="spaced_review"]').uncheck()
                notification.locator('input[type="checkbox"][required]').check()
                notification.get_by_role("button", name="保存成长通知配置").click()
                expect(page.locator("#notice")).to_contain_text("成长通知配置已保存")
                page.locator("#personal-notification-settings summary").click()
                expect(notification.locator('[name="spaced_review"]')).not_to_be_checked()
                assert not errors, errors
                browser.close()
            report = {
                "mode_preview_accept": True,
                "resource_feedback_restore": True,
                "local_fallback_preserves_session": True,
                "wrong_question_retake": True,
                "reminder_opt_out": True,
                "mobile_width": 375,
                "js_errors": errors,
                "external_requests": False,
            }
            if output:
                (output / "browser-report.json").write_text(
                    json.dumps(report, indent=2) + "\n", encoding="utf-8"
                )
            print(json.dumps(report))
        finally:
            server.should_exit = True
            thread.join(timeout=5)
            engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    with temporary_artifacts(args.output) as args.output:
        run(args.output)
