"""Exercise V4 pages in Chromium against a disposable local database."""

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
from sqlalchemy.orm import Session

from careerpilot import growth
from careerpilot.api import create_app
from careerpilot.cli import provision
from careerpilot.domain import Principal
from careerpilot.persistence import User, make_engine
from careerpilot.seed import seed


def main():
    with tempfile.TemporaryDirectory(prefix="careerpilot-v4-browser-") as directory:
        url = "sqlite:///" + (Path(directory) / "browser.db").as_posix()
        config = Config("alembic.ini")
        config.attributes["database_url"] = url
        command.upgrade(config, "head")
        engine = make_engine(url)
        seed(engine)
        user_id, token = provision(engine, "V4 browser")
        headers = {"Authorization": "Bearer " + token}
        with Session(engine) as db:
            user = db.get(User, user_id)
            asset = growth.save(
                db,
                Principal(user.id, user.tenant_id),
                "project_asset",
                {
                    "status": "active",
                    "title": "公开演练",
                    "content": '<img src=x onerror="window.__unsafe=true">',
                },
                "browser",
            )
            db.commit()
        with TestClient(create_app(url)) as client:
            fact_response = client.post(
                "/api/v1/resume-facts",
                headers=headers,
                json={
                    "kind": "experience",
                    "source_type": "manual",
                    "text": "2024 年完成 2 个本地合成项目",
                    "confirmed": True,
                },
            )
            assert fact_response.status_code == 201, fact_response.text
            original_fact = fact_response.json()
            response = client.post(
                "/api/v1/object-shares",
                headers=headers,
                json={
                    "kind": "project_asset",
                    "source_id": asset["id"],
                    "source_version": asset["version"],
                    "fields": ["title", "content"],
                    "public": True,
                    "confirmed": True,
                },
            )
            assert response.status_code == 201, response.text
            public_share = response.json()
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
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                try:
                    for width in (1440, 375):
                        page = browser.new_page(viewport={"width": width, "height": 900})
                        errors = []
                        page.on("pageerror", lambda error: errors.append(str(error)))
                        page.goto(f"http://127.0.0.1:{port}/app/")
                        page.locator("#connect").click()
                        page.locator("#token").fill(token)
                        page.locator("#account-form button[type=submit]").click()
                        expect(page.locator("#account-dialog")).not_to_be_visible()
                        if width == 375:
                            assert page.locator("#sidebar").evaluate("element=>element.inert")
                            page.locator("#menu").focus()
                            page.keyboard.press("Shift+Tab")
                            assert page.evaluate(
                                "!document.querySelector('#sidebar').contains(document.activeElement)"
                            )
                            page.locator("#menu").focus()
                            page.keyboard.press("Space")
                            assert not page.locator("#sidebar").evaluate("element=>element.inert")
                            page.keyboard.press("Escape")
                            expect(page.locator("#menu")).to_be_focused()
                        for route, title in (
                            ("directions", "按方向组织资料"),
                            ("collaboration", "我的协作空间"),
                            ("local-tools", "授权资料索引"),
                        ):
                            page.goto(f"http://127.0.0.1:{port}/app/#{route}")
                            expect(page.get_by_role("heading", name=title)).to_be_visible()
                            assert not page.locator("#notice .error").count(), route
                        page.goto(f"http://127.0.0.1:{port}/app/#directions")
                        expect(page.locator("#v4-direction")).to_be_visible()
                        direction_title = f"后端方向 {width}"
                        edited_title = f"数据方向 {width}"
                        page.locator("#v4-direction input[name=title]").fill(direction_title)
                        page.locator("#v4-direction input[name=fact_ids]").first.check()
                        page.locator("#v4-direction button").click()
                        row = page.locator("#view p").filter(has_text=direction_title)
                        row.get_by_role("button", name="查看与回滚").click()
                        page.locator("#v4-direction-edit input[name=title]").fill(edited_title)
                        page.locator("#v4-direction-edit button").click()
                        page.locator("#view p").filter(has_text=edited_title).get_by_role(
                            "button", name="查看与回滚"
                        ).click()
                        page.locator('[data-v4-restore][data-target="1"]').click()
                        expect(
                            page.locator("#view p").filter(has_text=direction_title)
                        ).to_contain_text("v3")
                        assert (
                            page.evaluate(
                                "async id => (await api('/resume-facts')).find(f=>f.id===id).text",
                                original_fact["id"],
                            )
                            == original_fact["text"]
                        )
                        page.locator("#v4-scenario input[name=career_ids]").first.check()
                        page.locator("#v4-scenario button[value=routes]").click()
                        expect(page.locator("#v4-result li").first).to_be_visible()
                        page.locator('#v4-trend input[name="keyword"]').fill(f"Python {width}")
                        page.locator('#v4-trend select[name="interval_hours"]').select_option("6")
                        page.locator('#v4-trend input[type="checkbox"]').check()
                        page.locator("#v4-trend button").click()
                        trend = page.locator("[data-trend]").filter(has_text=f"Python {width}")
                        trend.locator(":scope > summary").click()
                        expect(trend).to_contain_text("每 6 小时核对")
                        trend.locator("[data-v4-refresh]").click()
                        expect(trend).to_contain_text("尚无新的样本变化")
                        trend.locator(":scope > summary").click()
                        trend.locator("[data-v4-pause]").click()
                        expect(trend.locator(":scope > summary")).to_contain_text("已取消")
                        page.goto(f"http://127.0.0.1:{port}/app/#collaboration")
                        page.locator("#space-create input[name=name]").fill("测试协作空间")
                        page.locator("#space-create button").click()
                        expect(page.get_by_text("测试协作空间")).to_be_visible()
                        page.goto(f"http://127.0.0.1:{port}/app/#local-tools")
                        page.locator("#tool-offers input[name=nameA]").fill("方案一")
                        page.locator("#tool-offers input[name=nameB]").fill("方案二")
                        page.locator("#tool-offers button").click()
                        expect(page.locator("#tool-result")).to_contain_text("方案一")
                        page.locator("#run-agent-experiment").click()
                        expect(page.locator("#tool-result")).to_contain_text("多模型协作保持关闭")
                        page.goto(f"http://127.0.0.1:{port}/app/#directions")
                        page.goto(f"http://127.0.0.1:{port}/app/#local-tools")
                        page.get_by_text("查看本地对照与步骤回放").click()
                        expect(page.get_by_text("extractor：find risk terms")).to_be_visible()
                        assert page.evaluate(
                            "document.documentElement.scrollWidth <= window.innerWidth + 2"
                        ), width
                        page.keyboard.press("Tab")
                        assert page.evaluate("document.activeElement !== document.body")
                        assert not errors, errors
                        page.close()
                    page = browser.new_page()
                    public_url = (
                        f"http://127.0.0.1:{port}/app/share.html#"
                        f"{public_share['id']}:{public_share['ticket']}"
                    )
                    page.goto(public_url)
                    expect(page.locator("#shared-content")).to_contain_text("onerror")
                    assert page.evaluate("window.__unsafe !== true")
                    assert page.evaluate("location.hash === ''")
                    with TestClient(create_app(url)) as client:
                        assert (
                            client.delete(
                                "/api/v1/object-shares/" + public_share["id"],
                                headers=headers,
                            ).status_code
                            == 200
                        )
                    page.goto(public_url)
                    expect(page.locator("#share-status")).to_contain_text("撤销")
                    assert page.locator("#shared-content").inner_text() == ""
                    page.goto(f"http://127.0.0.1:{port}/app/share.html#%broken")
                    expect(page.locator("#share-status")).not_to_contain_text("正在核对")
                    assert page.evaluate("location.hash === ''")
                    page.close()
                finally:
                    browser.close()
            print(
                "V4 Chromium: routes/actions at 2 widths; public ticket, XSS and revocation passed"
            )
        finally:
            server.should_exit = True
            thread.join(timeout=10)


if __name__ == "__main__":
    main()
