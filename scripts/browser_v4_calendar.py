"""Real Chromium calendar selections/revocation and review history at two widths."""

import socket
import tempfile
import threading
import time
from pathlib import Path

import uvicorn
from alembic import command
from alembic.config import Config
from browser_identity import connect_browser
from fastapi.testclient import TestClient
from playwright.sync_api import expect, sync_playwright

from careerpilot.api import create_app
from careerpilot.cli import provision
from careerpilot.persistence import make_engine
from careerpilot.seed import seed


def ok(response, status=200):
    assert response.status_code == status, response.status_code
    return response.json()


def connect(page, base, token):
    page.goto(base + "#local-tools")
    connect_browser(page, token)
    expect(page.locator("#tool-calendar")).to_be_visible()


def main():
    with tempfile.TemporaryDirectory(prefix="careerpilot-calendar-browser-") as directory:
        url = "sqlite:///" + (Path(directory) / "calendar.db").as_posix()
        config = Config("alembic.ini")
        config.attributes["database_url"] = url
        command.upgrade(config, "head")
        engine = make_engine(url)
        seed(engine)
        _, token = provision(engine, "Synthetic calendar owner")
        _, other_token = provision(engine, "Synthetic calendar stranger")
        headers = {"Authorization": "Bearer " + token}
        with TestClient(create_app(url, local_only=False)) as client:
            goal = ok(
                client.post(
                    "/api/v1/career-goals",
                    headers={**headers, "Idempotency-Key": "calendar-goal"},
                    json={"title": "本人合成方向"},
                ),
                201,
            )
            job = ok(
                client.post(
                    "/api/v1/jobs/import",
                    headers=headers,
                    json={"content": "岗位：合成截止日期\n要求：Python\n截止日期：2027-06-30"},
                ),
                201,
            )
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        server = uvicorn.Server(
            uvicorn.Config(
                create_app(url, local_only=False), host="127.0.0.1", port=port, log_level="error"
            )
        )
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        try:
            for _ in range(100):
                if server.started:
                    break
                time.sleep(0.05)
            assert server.started
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                try:
                    base = f"http://127.0.0.1:{port}/app/"
                    for index, width in enumerate((1440, 375)):
                        page = browser.new_page(viewport={"width": width, "height": 900})
                        errors = []
                        page.on("pageerror", lambda error: errors.append(str(error)))
                        connect(page, base, token)
                        page.locator("#calendar-add > summary").click()
                        form = page.locator("#calendar-add form")
                        title = f"本人面试 {width}"
                        form.locator('[name="title"]').fill(title)
                        form.locator('[name="date"]').fill("2026-12-01")
                        form.locator('[name="time"]').fill("14:30")
                        form.locator("button").click()
                        expect(page.locator("#tool-result")).to_contain_text("本人事件已保存")
                        event_id = page.evaluate(
                            "async title=>(await api('/calendar-events'))"
                            ".find(e=>e.title===title).id",
                            title,
                        )
                        page.locator(f'#tool-calendar input[value="{event_id}"]').check()
                        page.locator(f'#tool-calendar input[value="{job["id"]}"]').check()
                        page.locator("#tool-calendar button").click()
                        expect(page.locator("#tool-result")).to_contain_text("已选择 2 个事件")
                        grant_id = page.evaluate(
                            "async id=>(await api('/calendar-syncs'))"
                            ".find(s=>s.events.some(e=>e.id===id)).id",
                            event_id,
                        )
                        grant = page.locator(f'[data-calendar-sync="{grant_id}"]')
                        with page.expect_download() as info:
                            grant.locator("[data-calendar-export]").click()
                        path = Path(directory) / f"calendar-{width}.ics"
                        info.value.save_as(path)
                        exported = path.read_bytes().decode().replace("\r\n ", "")
                        assert "DTSTART:20261201T063000Z" in exported and title in exported
                        assert exported.count("BEGIN:VEVENT") == 2
                        grant.locator(f'[data-calendar-item-remove="{job["id"]}"]').click()
                        expect(page.locator("#tool-result")).to_contain_text("已从此导出移除")
                        with TestClient(create_app(url, local_only=False)) as client:
                            content = client.get(
                                f"/api/v1/calendar-syncs/{grant_id}/export", headers=headers
                            )
                            assert content.status_code == 200 and job["id"] not in content.text
                        event_detail = page.locator(f'[data-calendar-event="{event_id}"]')
                        event_detail.locator(":scope > summary").click()
                        event_detail.locator('[name="time"]').fill("15:30")
                        event_detail.locator("form button").click()
                        expect(page.locator("#tool-result")).to_contain_text("本人事件已保存")
                        page.locator(f'[data-calendar-export="{grant_id}"]').click()
                        expect(page.locator("#notice")).to_contain_text("日历已撤销或事件已更新")
                        page.locator(f'[data-calendar-revoke="{grant_id}"]').click()
                        expect(page.locator(f'[data-calendar-sync="{grant_id}"]')).to_contain_text(
                            "已撤销"
                        )
                        review_form = page.locator("#tool-review")
                        review_form.locator('[name="start"]').fill("2026-10-01")
                        review_form.locator('[name="end"]').fill("2026-12-31")
                        reflection = f"本人复盘 {width}：先核对方向再决定"
                        review_form.locator('[name="reflection"]').fill(reflection)
                        review_form.locator("button").click()
                        expect(page.locator("#tool-result")).to_contain_text("复盘已保存")
                        review = page.locator("[data-quarter-review]").filter(has_text=reflection)
                        review.locator(":scope > summary").click()
                        expect(review).to_contain_text("不会自动调整目标")
                        expect(review).to_contain_text("本人合成方向")
                        before = page.evaluate("async () => await api('/career-goals')")
                        review.locator('a[href="#explore"]').click()
                        page.locator(f'[data-goal-preview="{goal["id"]}"]').click()
                        expect(page.locator("#accept-goal")).to_be_visible()
                        assert page.evaluate("async () => await api('/career-goals')") == before
                        page.locator("#cancel-goal").click()
                        assert page.evaluate("async () => await api('/career-goals')") == before
                        page.reload()
                        page.evaluate("location.hash='local-tools'")
                        connect_browser(page, token)
                        expect(
                            page.locator("[data-quarter-review]").filter(has_text=reflection)
                        ).to_have_count(1)
                        assert page.evaluate(
                            "document.documentElement.scrollWidth <= innerWidth + 1"
                        )
                        assert not errors, errors
                        page.close()
                    stranger = browser.new_page()
                    connect(stranger, base, other_token)
                    assert (
                        stranger.locator(
                            "[data-quarter-review],[data-calendar-sync],[data-calendar-event]"
                        ).count()
                        == 0
                    )
                    stranger.close()
                finally:
                    browser.close()
            print(
                "Chromium calendar: selected ICS, removal, stale/revoked grants, "
                "private reviews and explicit goal preview passed at 1440/375px"
            )
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            engine.dispose()


if __name__ == "__main__":
    main()
