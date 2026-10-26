"""Disposable regional calendar regression using real API persistence and ICS downloads."""

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

from careerpilot.api import create_app
from careerpilot.cli import provision
from careerpilot.persistence import make_engine
from careerpilot.seed import seed


def main():
    with tempfile.TemporaryDirectory(prefix="careerpilot-calendar-regions-") as scratch:
        url = "sqlite:///" + (Path(scratch) / "calendar.db").as_posix()
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
                    for locale in ("zh-CN", "en-US"):
                        for width in (1440, 375):
                            _, token = provision(
                                engine, f"Synthetic regional calendar {locale} {width}"
                            )
                            headers = {"Authorization": "Bearer " + token}
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
                                    "timezone": "Asia/Kathmandu",
                                    "confirmed": True,
                                },
                            )
                            assert response.is_success, response.text
                            records = []
                            for zone, day, expected in (
                                ("Australia/Lord_Howe", "2026-12-01", "2026-11-30T23:30:00+00:00"),
                                ("Australia/Lord_Howe", "2026-06-01", "2026-06-01T00:00:00+00:00"),
                                ("Etc/UTC", "2026-12-01", "2026-12-01T10:30:00+00:00"),
                            ):
                                response = client.post(
                                    "/api/v1/calendar-events",
                                    headers=headers,
                                    json={
                                        "title": "本人原有事件 " + zone,
                                        "kind": "interview",
                                        "date": day,
                                        "time": "10:30",
                                        "timezone": zone,
                                        "confirmed": True,
                                    },
                                )
                                assert response.status_code == 201, response.text
                                assert response.json()["starts_at"] == expected
                                records.append(response.json())
                            check_case(browser, port, token, locale, width, records, Path(scratch))
                finally:
                    browser.close()
            print(
                "Chromium calendar regions: profile zone/aliases/edit preservation/half-hour DST/"
                "quarter-hour UTC/ICS/stale exports/keyboard at 1440/375 in zh/en passed"
            )
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            engine.dispose()


def check_case(browser, port, token, locale, width, records, scratch):
    context = browser.new_context(
        viewport={"width": width, "height": 950}, timezone_id="America/Los_Angeles"
    )
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    zh = locale == "zh-CN"
    saved = "本人事件已保存" if zh else "My event saved"
    add_name = "确认添加本人事件" if zh else "Confirm my event"
    edit_name = "保存事件修改" if zh else "Save event changes"
    try:
        page.goto(f"http://127.0.0.1:{port}/app/#local-tools")
        page.locator("#ui-locale").select_option(locale)
        page.locator("#connect").click()
        page.locator("#token").fill(token)
        page.locator("#account-form button[type=submit]").click()
        page.locator("#calendar-add > summary").click()
        form = page.locator("#calendar-add form")
        zone = form.get_by_label("时区" if zh else "Time zone", exact=True)
        # The old five-option select silently selected Shanghai for this profile.
        expect(zone).to_have_value("Asia/Kathmandu")
        for item in records:
            detail = page.locator(f'[data-calendar-event="{item["id"]}"]')
            detail.locator(":scope > summary").click()
            expect(detail.locator('[name="timezone"]')).to_have_value(item["timezone"])
            detail.locator('[name="title"]').fill("本人仅修改标题 " + item["timezone"])
            detail.get_by_role("button", name=edit_name, exact=True).click()
            expect(page.locator("#tool-result")).to_contain_text(saved)
            persisted = page.evaluate(
                "async id=>(await api('/calendar-events')).find(e=>e.id===id)", item["id"]
            )
            assert persisted["timezone"] == item["timezone"]
            assert persisted["starts_at"] == item["starts_at"]
            assert persisted["time"] == item["time"]

        page.locator("#calendar-add > summary").click()
        form = page.locator("#calendar-add form")
        form.locator('[name="title"]').fill("本人任意时区事件")
        form.locator('[name="date"]').fill("2026-12-01")
        form.locator('[name="time"]').fill("10:30")
        zone = form.get_by_label("时区" if zh else "Time zone", exact=True)
        zone.focus()
        page.keyboard.press("Tab")
        expect(form.get_by_role("button", name=add_name, exact=True)).to_be_focused()
        zone.fill("Invalid/zone")
        form.get_by_role("button", name=add_name, exact=True).click()
        expect(page.locator("#notice")).to_contain_text(
            "请选择有效时区" if zh else "Choose a valid time zone"
        )
        expect(zone).to_have_value("Invalid/zone")
        expect(form.locator('[name="title"]')).to_have_value("本人任意时区事件")

        # Lord Howe has a thirty-minute DST change, unlike New York's one hour.
        zone.fill("Australia/Lord_Howe")
        for day, clock, message in (
            ("2026-10-04", "02:15", "此时区没有该时间" if zh else "This local time does not exist"),
            ("2026-04-05", "01:45", "此时间出现两次" if zh else "This local time occurs twice"),
        ):
            form.locator('[name="date"]').fill(day)
            form.locator('[name="time"]').fill(clock)
            form.get_by_role("button", name=add_name, exact=True).click()
            expect(page.locator("#notice")).to_contain_text(message)
            expect(zone).to_have_value("Australia/Lord_Howe")
        form.locator('[name="date"]').fill("2026-12-01")
        form.locator('[name="time"]').fill("10:30")
        zone.fill("Asia/Kathmandu")
        form.get_by_role("button", name=add_name, exact=True).click()
        expect(page.locator("#tool-result")).to_contain_text(saved)
        event = page.evaluate(
            "async ()=>(await api('/calendar-events')).find(e=>e.title==='本人任意时区事件')"
        )
        assert event["starts_at"] == "2026-12-01T04:45:00+00:00"

        page.locator(f'#tool-calendar [value="{event["id"]}"]').check()
        page.locator(f'#tool-calendar [value="{records[0]["id"]}"]').check()
        page.locator("#tool-calendar button").click()
        expect(page.locator("[data-calendar-export]")).to_have_count(1)
        link = page.locator("[data-calendar-export]")
        grant_id = link.get_attribute("data-calendar-export")
        with page.expect_download() as download:
            link.click()
        path = scratch / f"{locale}-{width}.ics"
        download.value.save_as(path)
        content = path.read_text(encoding="utf-8")
        assert "DTSTART:20261201T044500Z" in content
        assert "DTSTART:20261130T233000Z" in content
        assert content.count("BEGIN:VEVENT") == 2
        assert all(item["id"] not in content for item in records[1:])

        # Switching UI language must not canonicalize aliases or change event times.
        before = page.evaluate("async()=>await api('/calendar-events')")
        page.locator("#ui-locale").select_option("en-US" if zh else "zh-CN")
        expect(page.locator("#calendar-add")).to_be_attached()
        assert page.evaluate("async()=>await api('/calendar-events')") == before
        detail = page.locator(f'[data-calendar-event="{event["id"]}"]')
        detail.locator(":scope > summary").click()
        expect(detail.locator('[name="timezone"]')).to_have_value("Asia/Kathmandu")
        detail.locator('[name="time"]').fill("")
        detail.locator("form button").click()
        expect(page.locator("#tool-result")).not_to_be_empty()
        page.wait_for_function(
            "async id=>(await api('/calendar-events')).find(e=>e.id===id).time===null",
            arg=event["id"],
        )
        result = page.evaluate(
            "async id=>{const r=await fetch('/api/v1/calendar-syncs/'+id+'/export',"
            "{headers:{Authorization:'Bearer '+token}});return r.status;}",
            grant_id,
        )
        assert result == 409
        # Re-authorize the edited all-day event; its civil date must not shift.
        page.locator(f'#tool-calendar [value="{event["id"]}"]').check()
        page.locator("#tool-calendar button").click()
        expect(page.locator("[data-calendar-export]")).to_have_count(2)
        syncs = page.evaluate("async()=>await api('/calendar-syncs')")
        fresh = next(item for item in syncs if item["id"] != grant_id)
        response = page.evaluate(
            "async id=>(await fetch('/api/v1/calendar-syncs/'+id+'/export',"
            "{headers:{Authorization:'Bearer '+token}})).text()",
            fresh["id"],
        )
        assert "DTSTART;VALUE=DATE:20261201" in response
        assert not errors, errors
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
    finally:
        context.close()


if __name__ == "__main__":
    main()
