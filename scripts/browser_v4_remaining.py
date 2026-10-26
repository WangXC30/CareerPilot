"""Temporary Chromium checks for practice, index search, subscriptions and locale scope."""

import socket
import tempfile
import threading
import time
from datetime import date
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


def checked(response):
    assert response.status_code in {200, 201}, response.status_code
    return response.json()


def main():
    with tempfile.TemporaryDirectory(prefix="careerpilot-remaining-browser-") as directory:
        url = "sqlite:///" + (Path(directory) / "browser.db").as_posix()
        config = Config("alembic.ini")
        config.attributes["database_url"] = url
        command.upgrade(config, "head")
        engine = make_engine(url)
        seed(engine)
        uid, token = provision(engine, "Synthetic remaining tasks")
        headers = {"Authorization": "Bearer " + token}
        with TestClient(create_app(url)) as client:
            fact = checked(
                client.post(
                    "/api/v1/resume-facts",
                    headers=headers,
                    json={
                        "kind": "experience",
                        "source_type": "manual",
                        "text": "完成 2 个合成项目",
                        "confirmed": True,
                    },
                )
            )
            resume = checked(
                client.post(
                    "/api/v1/resumes",
                    headers={**headers, "Idempotency-Key": "browser-brand"},
                    json={
                        "title": "browser brand",
                        "fact_ids": [fact["id"]],
                        "confirmed": True,
                    },
                )
            )
            checked(
                client.post(
                    "/api/v1/resume-translations",
                    headers=headers,
                    json={
                        "resume_version_id": resume["id"],
                        "locale": "en-US",
                        "confirmed": True,
                        "mappings": [
                            {"fact_id": fact["id"], "text": "Completed 2 synthetic projects"}
                        ],
                    },
                )
            )
        with Session(engine) as db:
            user = db.get(User, uid)
            growth.save(
                db,
                Principal(uid, user.tenant_id),
                "media_index",
                {
                    "title": "Python 本人资料",
                    "kind": "pdf",
                    "license_basis": "本人合成样本",
                    "expires_at": int(time.time()) + 3600,
                    "notice": "synthetic",
                    "segments": [
                        {"anchor": "page:1", "text": "本人正文 <img src=x onerror=alert(1)>"}
                    ],
                },
                "browser",
            )
            db.commit()
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
                        page.on("pageerror", lambda e: errors.append(str(e)))
                        page.goto(f"http://127.0.0.1:{port}/app/#local-tools")
                        page.locator("#connect").click()
                        page.locator("#token").fill(token)
                        page.locator("#account-form button[type=submit]").click()
                        expect(page.locator("#practice-add")).to_be_visible()
                        form = page.locator("#practice-add")
                        title = f"本人算法练习 {width}"
                        form.locator('[name="title"]').fill(title)
                        form.locator('[name="kind"]').select_option("algorithm")
                        form.locator('[name="tags"]').fill("数组,边界")
                        form.locator('[name="practiced_on"]').fill(date.today().isoformat())
                        form.locator('[name="reflection"]').fill("本人中文内容应保持原样")
                        form.locator("button").click()
                        item = page.locator("[data-practice]").filter(has_text=title)
                        expect(item).to_have_count(1)
                        search = page.locator("#media-search")
                        search.locator('[name="q"]').fill("python")
                        search.locator("button").click()
                        expect(page.locator("#media-results summary")).to_contain_text("Python")
                        page.locator("#media-results summary").click()
                        expect(page.locator("#media-results")).to_contain_text("本人正文")
                        assert page.locator("#media-results img").count() == 0
                        sub = page.locator("#content-subscribe")
                        sub.locator('[name="keyword"]').fill("python")
                        sub.locator('[type="checkbox"]').check()
                        sub.locator("button").click()
                        expect(page.locator("[data-content-cancel]")).to_have_count(1)
                        page.locator("[data-content-cancel]").click()
                        expect(page.locator("[data-content-cancel]")).to_have_count(0)
                        page.locator("#ui-locale").select_option("en-US")
                        expect(page.locator("#title")).to_have_text("Local tools")
                        expect(page.locator("#practice-tools")).to_contain_text(
                            "本人中文内容应保持原样"
                        )
                        expect(page.locator("#view")).to_have_attribute("lang", "en-US")
                        page.locator("#ui-locale").focus()
                        page.keyboard.press("Tab")
                        assert page.evaluate("document.activeElement !== document.body")
                        assert page.evaluate(
                            "document.documentElement.scrollWidth <= innerWidth + 1"
                        )
                        page.locator("#ui-locale").select_option("zh-CN")
                        item = page.locator("[data-practice]").filter(has_text=title)
                        item.locator("summary").click()
                        item.locator("button").click()
                        expect(
                            page.locator("[data-practice]").filter(has_text=title)
                        ).to_have_count(0)
                        page.evaluate("location.hash='settings'")
                        expect(page.locator("#profile-form")).to_be_visible()
                        page.locator("#ui-locale").select_option("en-US")
                        profile_form = page.locator("#profile-form")
                        expect(profile_form.get_by_label("Major", exact=True)).to_be_visible()
                        expect(profile_form.get_by_label("Education", exact=True)).to_be_visible()
                        expect(
                            profile_form.get_by_role("button", name="Preview profile changes")
                        ).to_be_visible()
                        expect(profile_form.locator("..")).to_have_attribute("lang", "en-US")
                        expect(page.locator("#profile-preview")).to_have_attribute("lang", "en-US")
                        profile_form.get_by_label("Major", exact=True).focus()
                        page.keyboard.press("Tab")
                        expect(
                            profile_form.get_by_label("Academic year", exact=True)
                        ).to_be_focused()
                        page.locator("#ui-locale").select_option("zh-CN")
                        expect(profile_form.get_by_label("专业", exact=True)).to_be_visible()
                        page.evaluate("location.hash='directions'")
                        expect(page.locator("#direction-extras")).to_contain_text(
                            "本人授权媒体索引"
                        )
                        brand = page.locator(".brand-create")
                        headline = f"本人双语主页 {width}"
                        brand.locator('[name="headline"]').fill(headline)
                        brand.locator('[name="fact_ids"]').check()
                        brand.locator('input[type="checkbox"]:not([name])').check()
                        brand.locator("button").click()
                        expect(
                            page.locator("#direction-extras details").filter(has_text=headline)
                        ).to_have_count(1)
                        page.evaluate("location.hash='collaboration'")
                        option = page.locator("#share-preview-form option").filter(
                            has_text=headline
                        )
                        expect(option).to_have_count(1)
                        page.locator('#share-preview-form select[name="source"]').select_option(
                            option.get_attribute("value")
                        )
                        page.locator('#share-field-options input[value="items"]').check()
                        page.locator("#share-preview-form button").click()
                        expect(page.locator("#share-preview pre")).to_contain_text(
                            "Completed 2 synthetic projects"
                        )
                        page.locator('#share-confirm-form input[name="public"]').check()
                        page.locator("#share-confirm-form button").click()
                        expect(page.locator("#space-ticket")).to_contain_text("share.html#")
                        share_url = page.locator("#space-ticket").inner_text().split("：", 1)[1]
                        public = browser.new_page()
                        public.goto(share_url)
                        expect(public.locator("#shared-content")).to_contain_text(
                            "Completed 2 synthetic projects"
                        )
                        page.locator("[data-share-revoke]").click()
                        expect(page.locator("[data-share-revoke]")).to_have_count(0)
                        public.goto(share_url)
                        expect(public.locator("#share-status")).to_contain_text("撤销")
                        public.close()
                        assert not errors, errors
                        page.close()
                finally:
                    browser.close()
            print(
                "Chromium remaining tasks: practice/search/cancel/XSS/locale/keyboard "
                "at 1440/375 passed"
            )
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            engine.dispose()


if __name__ == "__main__":
    main()
