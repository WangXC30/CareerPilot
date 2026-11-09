"""Real browser: restricted-source fallback, import, compare, delete and safe text."""

import argparse
import json
import socket
import tempfile
import threading
import time
from pathlib import Path

import httpx
import uvicorn
from alembic import command
from alembic.config import Config
from browser_identity import connect_browser
from playwright.sync_api import expect, sync_playwright

from careerpilot.api import create_app
from careerpilot.cli import provision
from careerpilot.persistence import make_engine
from careerpilot.seed import seed

try:
    from .temporary_artifacts import temporary_artifacts
except ImportError:
    from temporary_artifacts import temporary_artifacts


def run(output=None):
    with tempfile.TemporaryDirectory(prefix="careerpilot-sources-") as directory:
        url = "sqlite:///" + (Path(directory) / "sources.db").as_posix()
        config = Config("alembic.ini")
        config.attributes["database_url"] = url
        command.upgrade(config, "head")
        engine = make_engine(url)
        seed(engine)
        _, token = provision(engine, "Synthetic source student")
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
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
            with httpx.Client(
                base_url=f"http://127.0.0.1:{port}/api/v1",
                headers={"Authorization": "Bearer " + token},
            ) as client:
                with sync_playwright() as p:
                    browser = p.chromium.launch()
                    page = browser.new_page(viewport={"width": 1440, "height": 1050})
                    errors = []
                    page.on("pageerror", lambda e: errors.append(str(e)))
                    page.goto(f"http://127.0.0.1:{port}/app/#sources")

                    def login():
                        connect_browser(page, token)
                        expect(page.locator("#sources-search")).to_be_visible()

                    login()
                    search = page.locator("#sources-search")
                    search.locator('[name="platform"]').select_option("douyin")
                    search.get_by_role("button").click()
                    expect(page.locator("#source-search-status")).to_contain_text("本地索引 0 条")
                    expect(page.locator("#source-restrictions")).to_contain_text(
                        "不代表已直接搜索平台"
                    )
                    for value, link, label in [
                        (
                            "三次",
                            "https://www.xiaohongshu.com/explore/test?tracking=discard",
                            "personal",
                        ),
                        ("五次", "https://www.douyin.com/video/1", "advertising"),
                    ]:
                        page.locator("#source-import summary").click()
                        form = page.locator("#source-import-form")
                        form.locator('[name="title"]').fill(
                            "合成经验 " + value + " <img src=x onerror=alert(1)>"
                        )
                        form.locator('[name="url"]').fill(link)
                        form.locator('[name="kind"]').select_option("experience")
                        form.locator('[name="skill_id"]').select_option("python")
                        form.locator('[name="declared_type"]').select_option(label)
                        form.locator('[name="published_on"]').fill("2025-01-01")
                        form.locator('[name="authorization_scope"]').select_option("local_excerpt")
                        form.locator('[name="text"]').fill(
                            "每周练习" + value + "。<script>window.__sourceInjected=1</script>"
                        )
                        form.locator('[name="claim_topic"]').fill("每周练习")
                        form.locator('[name="claim_value"]').fill(value)
                        form.locator('[name="rights"]').check()
                        form.get_by_role("button", name="确认导入来源").click()
                        expect(page.locator("#notice")).to_contain_text("来源已导入")
                    rows = client.get("/source-documents").json()
                    assert len(rows) == 2 and all("tracking" not in r["url"] for r in rows)
                    expect(page.locator('[name="source_compare"]')).to_have_count(2)
                    for checkbox in page.locator('[name="source_compare"]').all():
                        checkbox.check()
                    page.get_by_role("button", name="对照选中的 2—4 条来源").click()
                    expect(page.locator("#source-comparison")).to_contain_text("存在差异，待核对")
                    expect(page.locator("#source-comparison")).to_contain_text("三次")
                    expect(page.locator("#source-comparison")).to_contain_text("五次")
                    assert page.locator(".source-card img").count() == 0
                    assert page.locator("#source-comparison script").count() == 0
                    assert page.evaluate("window.__sourceInjected===undefined")
                    assert client.get("/skill-evidence").json() == []
                    assert client.get("/resume-facts").json() == []
                    if output:
                        output.mkdir(parents=True, exist_ok=True)
                        page.locator("#source-comparison").scroll_into_view_if_needed()
                        page.screenshot(path=output / "sources-desktop.png", animations="disabled")
                    page.set_viewport_size({"width": 375, "height": 860})
                    assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
                    if output:
                        page.locator("#source-comparison").scroll_into_view_if_needed()
                        page.screenshot(path=output / "sources-mobile.png", animations="disabled")
                    page.reload()
                    login()
                    expect(page.locator('[name="source_compare"]')).to_have_count(2)
                    card = page.locator('.source-card:has([name="source_compare"])').first
                    card.locator("details").last.locator("summary").click()
                    card.get_by_role("button", name="确认删除此来源记录").click()
                    expect(page.locator("#notice")).to_contain_text("已删除")
                    expect(page.locator('[name="source_compare"]')).to_have_count(1)
                    assert len(client.get("/source-documents").json()) == 1
                    assert not errors, errors
                    browser.close()
            result = {
                "scope": "local_index_and_user_import",
                "restricted_platform_search": False,
                "comparison_preserves_conflicting_quotes": True,
                "refresh_restores": True,
                "delete_removes_reference": True,
                "injection_inert": True,
                "capability_not_promoted": True,
                "mobile_width": 375,
                "js_errors": errors,
                "live_github_request": False,
                "full_v2_exit": False,
            }
            if output:
                (output / "browser-report.json").write_text(
                    json.dumps(result, indent=2), encoding="utf-8"
                )
            print(json.dumps(result))
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
