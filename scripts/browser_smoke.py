"""Playwright platform and V1 growth checks against a disposable private workspace."""

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
from playwright.sync_api import expect, sync_playwright

from careerpilot.api import create_app
from careerpilot.persistence import make_engine
from careerpilot.seed import seed

try:
    from .temporary_artifacts import temporary_artifacts
except ImportError:
    from temporary_artifacts import temporary_artifacts


def run(output: Path | None = None):
    with tempfile.TemporaryDirectory(prefix="careerpilot-ui-") as directory:
        url = f"sqlite:///{(Path(directory) / 'ui.db').as_posix()}"
        config = Config("alembic.ini")
        config.attributes["database_url"] = url
        command.upgrade(config, "head")
        engine = make_engine(url)
        seed(engine)
        engine.dispose()
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        server = uvicorn.Server(
            uvicorn.Config(
                create_app(url), host="127.0.0.1", port=port, log_level="error", access_log=False
            )
        )
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{port}"
        try:
            with httpx.Client(base_url=base, trust_env=False) as client:
                for _ in range(100):
                    try:
                        if client.get("/health/ready").status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(0.05)
                else:
                    raise RuntimeError("UI server did not start")
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                page = browser.new_page(
                    viewport={"width": 1440, "height": 1000}, device_scale_factor=1
                )
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(base + "/app/")
                expect(page.get_by_role("heading", name="成长工作台", exact=True)).to_be_visible()
                expect(page.locator("#primary-nav > a")).to_have_count(5)
                expect(page.locator("#extra-nav")).not_to_have_attribute("open", "")
                expect(page.locator("#assistant-form")).to_be_visible()
                expect(page.locator("#connect, #account-dialog, #login-form")).to_have_count(0)
                expect(page.locator('a[href="#admin"]')).to_have_count(0)
                if output:
                    output.mkdir(parents=True, exist_ok=True)
                    page.screenshot(
                        path=str(output / "workspace-desktop.png"),
                        full_page=True,
                        animations="disabled",
                    )
                for route, title in [
                    ("explore", "职业探索"),
                    ("market", "岗位市场"),
                    ("skills", "能力地图"),
                    ("learning", "学习中心"),
                    ("portfolio", "作品集"),
                    ("recruiting", "求职中心"),
                    ("settings", "我的资料与设置"),
                ]:
                    page.goto(base + "/app/#" + route)
                    expect(page.locator("h1")).to_have_text(title)
                    parent = {
                        "market": "explore",
                        "skills": "explore",
                        "portfolio": "learning",
                    }.get(route, route)
                    expect(page.locator("#primary-nav a.active")).to_have_attribute(
                        "data-page", parent
                    )
                    expect(page.locator("#extra-nav")).not_to_have_attribute("open", "")
                page.goto(base + "/app/#learning")
                page.locator("#page-options summary").click()
                for state in ["loading", "empty", "error"]:
                    page.locator("#preview-state").select_option(state)
                    if state == "loading":
                        expect(page.get_by_role("status", name="加载中")).to_be_visible()
                    elif state == "empty":
                        expect(page.get_by_role("heading", name="从第一步开始")).to_be_visible()
                    else:
                        expect(page.get_by_role("heading", name="暂时无法加载")).to_be_visible()
                page.locator("#preview-state").select_option("default")
                assert page.evaluate("localStorage.length") == 0
                page.goto(base + "/app/#explore")
                page.reload()  # A fragment navigation alone does not reload the document.
                page.locator("#goal-title").fill("浏览器验证目标")
                page.get_by_role("button", name="保存目标草稿").click()
                expect(page.locator("#goals")).to_contain_text("浏览器验证目标")
                page.get_by_role("button", name="预览设为主目标").click()
                expect(page.locator("#goal-impact")).to_contain_text("确认后切换主目标")
                page.get_by_role("button", name="取消切换").click()
                expect(page.locator("#goals .tag")).to_contain_text("备选草稿")
                page.get_by_role("button", name="预览设为主目标").click()
                page.get_by_role("button", name="确认切换主目标").click()
                expect(page.locator("#goals .tag")).to_contain_text("主目标")
                page.evaluate("location.hash='settings'")
                profile_form = page.locator("#profile-form")
                profile_form.locator("[name=major]").fill("计算机科学")
                profile_form.locator("[name=academic_year]").fill("大三")
                profile_form.locator("[name=graduation_date]").fill("2027-06-30")
                profile_form.locator("[name=preferred_cities]").fill("上海")
                profile_form.locator("[name=weekly_minutes]").fill("600")
                page.get_by_role("button", name="预览档案变更").click()
                expect(page.locator("#profile-preview")).to_contain_text("确认档案变更")
                page.get_by_role("button", name="确认保存档案").click()
                expect(page.locator("#notice")).to_contain_text("档案已保存")
                profile_form.locator("[name=weekly_minutes]").fill("300")
                page.get_by_role("button", name="预览档案变更").click()
                page.get_by_role("button", name="确认保存档案").click()
                expect(page.locator("#notice")).to_contain_text("档案已保存")
                page.locator("#profile-history summary").click()
                page.get_by_role("button", name="恢复此版本").click()
                expect(profile_form.locator("[name=weekly_minutes]")).to_have_value("600")
                page.locator("#material-form [name=content]").fill(
                    "熟悉 Python\n完成数据库课程，尚未通过测验"
                )
                page.get_by_role("button", name="解析并预览材料").click()
                expect(page.locator("#material-preview")).to_contain_text("待逐项确认")
                page.locator("#confirm-material-form input[name=selected]").first.check()
                page.get_by_label("第 1 项陈述", exact=True).fill("正在学习 Python")
                page.get_by_role("button", name="确认所选陈述并写入记忆").click()
                expect(page.locator("#notice")).to_contain_text("技能仍为未验证")
                if output:
                    page.screenshot(
                        path=str(output / "profile-v1-desktop.png"),
                        full_page=True,
                        animations="disabled",
                    )
                page.evaluate("location.hash='skills'")
                expect(page.locator(".memory-form")).to_contain_text("未验证技能")
                expect(page.locator(".memory-form [name=text]")).to_have_value("正在学习 Python")
                page.locator(".memory-form [name=text]").fill("已完成 Python 入门练习")
                page.get_by_role("button", name="确认修改记忆").click()
                expect(page.locator("#notice")).to_contain_text("记忆已更新")
                page.evaluate("location.hash='market'")
                page.locator("#job-form [name=content]").fill(
                    "😀 岗位原文\n岗位：后端实习生\n地点：上海\n要求：掌握 Python\n优先：Docker"
                )
                page.locator("#job-form [name=source_url]").fill("https://example.com/jobs/1")
                page.get_by_role("button", name="导入并提取要求").click()
                expect(page.locator("#job-detail")).to_contain_text("后端实习生")
                expect(page.locator("#job-detail")).to_contain_text("未知")
                page.get_by_role("button", name="原文：Python", exact=True).click()
                expect(page.locator("#jd-original mark")).to_have_text("Python")
                page.get_by_role("button", name="查看原文版本历史").click()
                expect(page.locator("#job-history")).to_contain_text("v1")
                if output:
                    page.screenshot(
                        path=str(output / "job-v1-desktop.png"),
                        full_page=True,
                        animations="disabled",
                    )
                page.set_viewport_size({"width": 375, "height": 812})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                if output:
                    page.screenshot(
                        path=str(output / "job-v1-mobile.png"),
                        full_page=True,
                        animations="disabled",
                    )
                page.evaluate("location.hash='settings'")
                expect(page.locator("#profile-form")).to_be_visible()
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                page.set_viewport_size({"width": 375, "height": 812})
                page.evaluate("location.hash='dashboard'")
                expect(page.locator("h1")).to_have_text("成长工作台")
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                page.get_by_role("button", name="展开导航").click()
                expect(page.locator("#sidebar")).to_have_class("open")
                page.get_by_role("link", name="目标与岗位", exact=True).click()
                expect(page.locator("#primary-nav a.active")).to_have_attribute(
                    "data-page", "explore"
                )
                page.locator("#section-nav").get_by_role("link", name="能力地图").click()
                expect(page.locator("h1")).to_have_text("能力地图")
                expect(page.locator("#sidebar")).not_to_have_class("open")
                if output:
                    page.screenshot(
                        path=str(output / "workspace-mobile.png"),
                        full_page=True,
                        animations="disabled",
                    )
                assert not errors, errors
                browser.close()
            return {
                "browser": "Chromium",
                "routes": 8,
                "states": ["empty", "loading", "error"],
                "primary_navigation": 5,
                "grouped_navigation": True,
                "local_workspace_without_login": True,
                "real_goal_write": True,
                "v1_profile_preview_save_restore": True,
                "v1_material_confirm_and_memory_edit": True,
                "v1_jd_import_evidence_and_history": True,
                "v1_goal_preview_cancel_accept": True,
                "operations_ui_absent": True,
                "mobile_width": 375,
                "console_errors": 0,
            }
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            if thread.is_alive():
                raise RuntimeError("UI server did not stop")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    with temporary_artifacts(args.output) as args.output:
        report = run(args.output)
        if args.output:
            (args.output / "browser-report.json").write_text(
                json.dumps(report, indent=2), encoding="utf-8"
            )
        print(json.dumps(report))
