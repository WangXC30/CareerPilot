"""Disposable English V4 journeys, owner text, time zones and superseded responses."""

import socket
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import uvicorn
from alembic import command
from alembic.config import Config
from browser_v4_locale import CONTRAST, MISMARKED
from fastapi.testclient import TestClient
from playwright.sync_api import expect, sync_playwright
from sqlalchemy.orm import Session

from careerpilot import growth
from careerpilot.api import create_app
from careerpilot.cli import provision
from careerpilot.domain import Principal
from careerpilot.persistence import User, make_engine
from careerpilot.seed import seed


def main():
    with tempfile.TemporaryDirectory(prefix="careerpilot-remaining-locale-") as scratch:
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
                        uid, token = provision(engine, f"Synthetic remaining locale {width}")
                        records = prepare(client, engine, uid, token)
                        check_width(browser, port, token, width, records)
                        _, admin = provision(
                            engine, f"Synthetic locale admin {width}", role="admin"
                        )
                        _, reviewer = provision(
                            engine, f"Synthetic locale reviewer {width}", role="admin"
                        )
                        check_operations(browser, port, admin, reviewer, width)
                finally:
                    browser.close()
            print(
                "Chromium remaining locale: directions/scenarios/trends/exams/tutoring/"
                "translations/brand/media/calendar/application/Offers/practice/experiments/"
                "operations/originals/time zones/keyboard/late responses at 1440/375 passed"
            )
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            engine.dispose()


def prepare(client, engine, uid, token):
    headers = {"Authorization": "Bearer " + token}

    def post(path, body, key=None):
        response = client.post(
            "/api/v1" + path,
            headers={**headers, **({"Idempotency-Key": key} if key else {})},
            json=body,
        )
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
    original = "开始练习 <b>本人 2 个项目</b>"
    fact = post(
        "/resume-facts",
        {"kind": "experience", "source_type": "manual", "text": original, "confirmed": True},
    )
    resume = post(
        "/resumes",
        {"title": "本人中文简历", "fact_ids": [fact["id"]], "confirmed": True},
        "locale-resume",
    )
    post(
        "/resume-translations",
        {
            "resume_version_id": resume["id"],
            "locale": "en-US",
            "mappings": [{"fact_id": fact["id"], "text": "My 2 projects"}],
            "confirmed": True,
        },
    )
    directions = [
        post(
            "/direction-views",
            {"title": title, "fact_ids": [fact["id"]], "project_ids": [], "confirmed": True},
        )
        for title in ("本人方向 A", "本人方向 B")
    ]
    applications = []
    for index in range(2):
        job = post("/jobs/import", {"content": f"岗位：本人岗位 {index}\n要求：Python"})
        applications.append(
            post(
                "/applications",
                {"job_id": job["id"], "resume_version_id": resume["id"], "confirmed": True},
                f"locale-application-{index}",
            )
        )
    with Session(engine) as db, db.begin():
        user = db.get(User, uid)
        media = growth.save(
            db,
            Principal(uid, user.tenant_id),
            "media_index",
            {
                "title": "本人索引",
                "kind": "pdf",
                "license_basis": "本人授权",
                "expires_at": int(time.time()) + 3600,
                "notice": (
                    "仅保存授权文字索引，原文件处理后删除。"
                    "视频仅提取首帧文字，不转写音轨；OCR 可能错误。"
                ),
                "segments": [{"anchor": "page:1", "text": "本人原文 <img src=x onerror=alert(1)>"}],
            },
            "synthetic",
        )
    return {
        "directions": directions,
        "media": media,
        "applications": applications,
        "original": original,
    }


def connect(page, port, token, route):
    page.goto(f"http://127.0.0.1:{port}/app/#{route}")
    page.locator("#ui-locale").select_option("en-US")
    page.locator("#connect").click()
    page.locator("#token").fill(token)
    page.get_by_role("button", name="Connect", exact=True).click()


def audit(page):
    assert page.evaluate(MISMARKED) == [], page.evaluate(MISMARKED)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
    assert not page.locator("#notice .error").count(), page.locator("#notice").inner_text()
    assert page.locator("#view img").count() == 0
    assert page.evaluate(CONTRAST) == [], page.evaluate(CONTRAST)


def check_width(browser, port, token, width, records):
    context = browser.new_context(
        viewport={"width": width, "height": 950}, timezone_id="Asia/Tokyo"
    )
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        connect(page, port, token, "directions")
        expect(page.locator("#direction-extras")).to_be_attached()
        expect(page.locator("#view")).to_have_attribute("lang", "en-US")
        expect(page.get_by_role("heading", name="Organize materials by direction")).to_be_visible()
        expect(page.locator("#v4-direction")).to_contain_text(records["original"])
        assert page.locator("#v4-direction b").count() == 0
        page.locator("#v4-direction").get_by_label("Direction name", exact=True).focus()
        page.keyboard.press("Tab")
        expect(page.locator('#v4-direction [name="fact_ids"]').first).to_be_focused()
        audit(page)

        first, second = records["directions"]
        pending = []
        pattern = f"**/api/v1/direction-views/{first['id']}"
        page.route(pattern, lambda route: pending.append(route))
        with page.expect_request(pattern):
            page.locator(f'[data-v4-view="{first["id"]}"]').click()
        expect(page.locator("#v4-direction-edit")).to_have_count(0)
        page.locator(f'[data-v4-view="{second["id"]}"]').click()
        form = page.locator("#v4-direction-edit")
        expect(form).to_have_attribute("data-direction", second["id"])
        form.get_by_label("Edit direction name", exact=True).fill("本人未保存输入")
        pending.pop().fulfill(status=503, json={"message": "late direction failure"})
        page.unroute(pattern)
        expect(form.get_by_label("Edit direction name", exact=True)).to_have_value("本人未保存输入")
        expect(page.locator("#notice")).not_to_contain_text("late direction failure")

        scenario = page.locator("#v4-scenario")
        scenario.locator('[name="career_ids"]').first.check()
        scenario.get_by_role("button", name="Compare routes and assumptions").click()
        expect(page.locator("#v4-result .learning-path")).to_have_count(2)
        expect(page.locator("#v4-result h2").first).to_be_focused()
        audit(page)
        trend = page.locator("#v4-trend")
        trend.get_by_label("Keyword", exact=True).fill("Python")
        trend.locator('[type="checkbox"]').check()
        trend.get_by_role("button", name="Subscribe to changes").click()
        expect(page.locator("[data-trend]")).to_have_count(1)
        expect(page.locator("[data-trend]")).to_contain_text("GMT-4")

        exam = page.locator("#v4-exam")
        exam.locator('[name="skill_ids"]').first.check()
        exam.get_by_role("button", name="Start practice").click()
        expect(page.locator("#v4-answer")).to_be_attached()
        choice = page.evaluate("v4.exam.current.choices.length - 1")
        page.locator(f'#v4-answer [value="{choice}"]').check()
        page.locator("#v4-answer button").click()
        expect(page.locator("[data-tutor-start]")).to_have_count(1)
        page.locator("#v4-result details summary").first.click()
        page.locator("[data-tutor-start]").click()
        expect(page.locator("#tutor-followup")).to_be_attached()
        tutor = page.locator("#tutor-followup")
        tutor.get_by_label("My reasoning or example", exact=True).fill("本人推理不翻译")
        tutor.get_by_role("button", name="Save reasoning and continue").click()
        expect(page.locator("#v4-result")).to_contain_text("本人推理不翻译")
        audit(page)
        translation = page.locator("#v4-translation-load")
        translation.get_by_role("button", name="Select latest version").click()
        expect(page.locator("#v4-translation")).to_be_attached()
        expect(page.locator("#v4-translation")).to_contain_text(records["original"])
        audit(page)
        brand = page.locator(".brand-create")
        brand.get_by_label("My chosen profile title", exact=True).fill("本人主页")
        brand.locator('[name="fact_ids"]').check()
        brand.locator('input[type="checkbox"]:not([name])').check()
        brand.get_by_role("button", name="Generate private bilingual draft").click()
        expect(page.locator("#direction-extras")).to_contain_text("本人主页")

        page.evaluate("location.hash='local-tools'")
        expect(page.locator("#practice-tools")).to_be_attached()
        expect(page.locator("#view")).to_have_attribute("lang", "en-US")
        practice = page.locator("#practice-add")
        practice.get_by_label("Question title", exact=True).fill("开始练习")
        practice.get_by_label("Date", exact=True).fill(
            datetime.now(timezone.utc).date().isoformat()
        )
        practice.get_by_label("Review", exact=True).fill("本人复盘 <b>原文</b>")
        practice.get_by_role("button", name="Save my practice record").click()
        expect(page.locator("[data-practice]")).to_have_count(1)
        expect(page.locator("[data-practice] summary")).to_contain_text("开始练习")
        search = page.locator("#media-search")
        search.get_by_label("Title or indexed text").fill("本人")
        search.get_by_role("button", name="Search local materials").click()
        expect(page.locator("#media-results summary")).to_have_count(1)
        expect(page.locator("#media-results")).to_contain_text("GMT-4")
        page.locator("#media-results summary").click()
        expect(page.locator("#media-results")).to_contain_text("<img src=x onerror=alert(1)>")

        page.locator("#calendar-add summary").click()
        calendar = page.locator("#calendar-add form")
        calendar.get_by_label("Event title").fill("本人跨时区事件")
        calendar.get_by_label("Date", exact=True).fill("2027-03-15")
        calendar.get_by_label("Time (blank for all day)", exact=True).fill("10:30")
        expect(calendar.get_by_label("Time zone", exact=True)).to_have_value("America/New_York")
        calendar.get_by_role("button", name="Confirm my event").click()
        expect(page.locator("[data-calendar-event]")).to_have_count(1)
        export = page.locator("#tool-calendar")
        export.locator('[name="event_ids"]').check()
        export.get_by_role("button", name="Confirm calendar generation").click()
        expect(page.locator("[data-calendar-export]")).to_have_count(1)

        offers = page.locator("#tool-offers")
        offers.locator('[name="nameA"]').fill("本人方案 A")
        offers.locator('[name="nameB"]').fill("本人方案 B")
        offers.get_by_role("button", name="Compare ranges", exact=True).click()
        expect(page.locator("[data-offer-comparison]")).to_have_count(1)
        page.locator("[data-offer-comparison] summary").click()
        decision = page.locator(".tool-offer-decision")
        decision.get_by_label("My preference", exact=True).select_option("0")
        decision.get_by_label("My review reasoning", exact=True).fill("本人选择未知项")
        decision.locator('[type="checkbox"]').check()
        decision.get_by_role("button", name="Save my choice").click()
        expect(page.locator("[data-offer-comparison]")).to_contain_text("本人选择未知项")
        audit(page)

        app_form = page.locator("#tool-application")
        pending = []
        pattern = "**/api/v1/application-lab/preview"
        page.route(pattern, lambda route: pending.append(route))
        app_form.locator("select").select_option(records["applications"][0]["id"])
        with page.expect_request(pattern):
            app_form.get_by_role("button").click()
        app_form.locator("select").select_option(records["applications"][1]["id"])
        pending.pop().fulfill(status=503, json={"message": "late preview failure"})
        page.unroute(pattern)
        expect(page.locator("#application-approve")).to_have_count(0)
        expect(page.locator("#notice")).not_to_contain_text("late preview failure")
        app_form.get_by_role("button").click()
        expect(page.locator("#application-approve")).to_be_visible()
        page.locator("#application-approve").click()
        expect(page.locator("#application-submit")).to_be_visible()
        page.locator("#application-submit").click()
        expect(page.locator("#tool-result")).to_contain_text("Local simulation completed")
        page.locator("#run-agent-experiment").click()
        expect(page.locator("#tool-result")).to_contain_text(
            "Multi-model collaboration remains off"
        )
        audit(page)
        # Language changes preserve saved facts and do not translate colliding owner text.
        page.locator("#ui-locale").select_option("zh-CN")
        expect(page.locator("#practice-tools")).to_contain_text("本人复盘 <b>原文</b>")
        page.locator("#ui-locale").select_option("en-US")
        expect(page.locator("#practice-tools")).to_contain_text("本人复盘 <b>原文</b>")
        expect(page.locator("[data-practice] summary")).to_contain_text("开始练习")
        assert not errors, errors
    finally:
        context.close()


def check_operations(browser, port, token, reviewer_token, width):
    page = browser.new_page(viewport={"width": width, "height": 950})
    try:
        connect(page, port, token, "admin")
        expect(page.locator("#public-catalogue-operations")).to_be_attached()
        page.locator("#public-catalogue-operations details summary").first.click()
        form = page.locator("#public-proposal-form")
        expect(form.get_by_label("Public structured fields", exact=True)).to_be_visible()
        form.get_by_label("Select public entry", exact=True).select_option("python")
        expect(form.get_by_label("Entry ID", exact=True)).to_have_value("python")
        original = form.get_by_label("Public structured fields", exact=True).input_value()
        assert "Python" in original
        audit(page)
        form.get_by_label("Select public entry", exact=True).select_option("")
        form.get_by_label("Entry ID", exact=True).fill(f"locale-resource-{width}")
        form.get_by_label("Reason for change", exact=True).fill(f"本人公共变更原文 {width}")
        form.locator('[type="checkbox"]').check()
        form.get_by_role("button", name="Submit for independent review").click()
        expect(
            page.locator("[data-public-proposal]").filter(has_text=f"本人公共变更原文 {width}")
        ).to_have_count(1)
        peer = browser.new_page(viewport={"width": width, "height": 950})
        try:
            connect(peer, port, reviewer_token, "admin")
            review = (
                peer.locator("[data-public-proposal]")
                .filter(has_text=f"本人公共变更原文 {width}")
                .last
            )
            review.get_by_label("Review basis", exact=True).fill("独立核对来源与公共字段")
            review.get_by_role("button", name="Confirm approval", exact=True).click()
            peer.get_by_role("button", name="Confirm publication of reviewed change").click()
            expect(peer.locator("#notice")).to_contain_text("Public version published")
            audit(peer)
        finally:
            peer.close()
        page.locator("#ui-locale").select_option("zh-CN")
        page.locator("#ui-locale").select_option("en-US")
        expect(page.locator("#public-catalogue-operations")).to_be_attached()
        assert not page.locator("#notice .error").count()
    finally:
        page.close()


if __name__ == "__main__":
    main()
