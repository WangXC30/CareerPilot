"""Workflow record management against a disposable real API and browser."""

import os
import socket
import tempfile
import threading
import time
from datetime import date, timedelta
from pathlib import Path

import httpx
import uvicorn
from alembic import command
from alembic.config import Config
from playwright.sync_api import expect, sync_playwright

from careerpilot.api import create_app
from careerpilot.persistence import make_engine
from careerpilot.seed import seed


def run():
    os.environ.setdefault(
        "CAREERPILOT_WEB_ROOT", str(Path(__file__).resolve().parents[1] / "frontend/dist")
    )
    with tempfile.TemporaryDirectory(prefix="careerpilot-business-browser-") as directory:
        url = "sqlite:///" + (Path(directory) / "business.db").as_posix()
        config = Config("alembic.ini")
        config.attributes["database_url"] = url
        command.upgrade(config, "head")
        engine = make_engine(url)
        seed(engine)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        server = uvicorn.Server(
            uvicorn.Config(
                create_app(url), host="127.0.0.1", port=port, log_level="error", access_log=False
            )
        )
        api = threading.Thread(target=server.run, daemon=True)
        api.start()
        base = f"http://127.0.0.1:{port}"
        errors = []
        try:
            with httpx.Client(base_url=base, trust_env=False) as client:
                for _ in range(100):
                    try:
                        if client.get("/health/ready").status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(0.1)
                else:
                    raise RuntimeError("Temporary API did not become ready")
                profile = client.put(
                    "/api/v1/profile",
                    headers={"If-Match": '"0"'},
                    json={
                        "education": "bachelor",
                        "major": "计算机",
                        "academic_year": "大三",
                        "graduation_date": "2027-06-30",
                        "preferred_cities": ["上海"],
                        "weekly_minutes": 600,
                        "timezone": "Asia/Shanghai",
                        "confirmed": True,
                    },
                )
                assert profile.status_code == 200, profile.text
                job = client.post(
                    "/api/v1/jobs/import", json={"content": "岗位：Python 练习\n要求：熟悉 Python"}
                ).json()
                created = client.post(
                    "/api/v1/plans", json={"job_ids": [job["id"]], "start_date": "2026-10-20"}
                )
                assert created.status_code == 201, created.text
                plan = created.json()
                removed_id = plan["tasks"][-1]["id"]
                future = (
                    date.fromisoformat(plan["tasks"][-1]["date"]) + timedelta(days=7)
                ).isoformat()
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch()
                    try:
                        page = browser.new_page(viewport={"width": 1440, "height": 1000})
                        page.on("pageerror", lambda error: errors.append(str(error)))
                        page.goto(base + "/app/learn")
                        row = page.locator(".collection > details.panel").first
                        row.get_by_role("button", name="修改资料", exact=True).click()
                        editor = page.get_by_role("region", name="编辑学习计划", exact=True)
                        editor.get_by_label("计划名称", exact=True).fill("统一管理的学习计划")
                        editor.locator(".plan-editor-task").first.get_by_label(
                            "任务名称", exact=True
                        ).fill("修改后的学习任务")
                        editor.locator(".plan-editor-task").last.get_by_role(
                            "button", name="删除任务", exact=True
                        ).click()
                        editor.get_by_role("button", name="新增任务", exact=True).click()
                        new = editor.locator(".plan-editor-task").last
                        new.get_by_label("任务名称", exact=True).fill("新增记录练习")
                        new.get_by_label("关联技能", exact=True).select_option("python")
                        new.get_by_label("安排日期", exact=True).fill(future)
                        new.get_by_label("学习资源（每行一项）", exact=True).fill("resource-python")
                        new.get_by_label("完成标准", exact=True).fill("完成练习并记录真实过程")
                        editor.get_by_role("button", name="预览修改", exact=True).click()
                        expect(
                            editor.get_by_role("region", name="计划修改预览", exact=True)
                        ).to_be_visible()
                        assert client.get(f"/api/v1/plans/{plan['id']}").json()["version"] == 1
                        editor.get_by_role("button", name="返回修改", exact=True).click()
                        expect(editor.get_by_label("计划名称", exact=True)).to_be_enabled()
                        editor.get_by_role("button", name="预览修改", exact=True).click()
                        editor.get_by_role("button", name="确认保存计划修改", exact=True).click()
                        expect(page.locator(".plan-editor")).to_have_count(0)
                        page.reload()
                        expect(row.locator(":scope > summary")).to_have_text("统一管理的学习计划")
                        current = client.get(f"/api/v1/plans/{plan['id']}").json()
                        assert current["status"] == "draft" and removed_id not in {
                            task["id"] for task in current["tasks"]
                        }
                        assert any(task["title"] == "新增记录练习" for task in current["tasks"])
                        row.get_by_role("button", name="查看历史", exact=True).click()
                        expect(row.get_by_text("资料历史", exact=True)).to_be_visible()
                        assert len(client.get(f"/api/v1/plans/{plan['id']}/history").json()) == 2
                        published = client.post(
                            f"/api/v1/plans/{plan['id']}/publish",
                            headers={"If-Match": f'"{current["version"]}"'},
                            json={"confirmed": True},
                        )
                        assert published.status_code == 200, published.text
                        current = published.json()
                        first_id = current["tasks"][0]["id"]
                        started = client.post(
                            f"/api/v1/plans/{plan['id']}/tasks/{first_id}",
                            headers={"If-Match": f'"{current["version"]}"'},
                            json={"action": "start"},
                        )
                        assert started.status_code == 200, started.text
                        page.reload()
                        row.get_by_role("button", name="修改资料", exact=True).click()
                        expect(
                            editor.locator(".plan-editor-task").first.get_by_label(
                                "任务名称", exact=True
                            )
                        ).to_be_disabled()
                        editor.get_by_role("button", name="取消修改", exact=True).click()
                        row.get_by_role("button", name="删除资料", exact=True).click()
                        row.get_by_role("button", name="取消删除", exact=True).click()
                        assert client.get(f"/api/v1/plans/{plan['id']}").status_code == 200
                        row.get_by_role("button", name="删除资料", exact=True).click()
                        row.get_by_role("button", name="确认删除资料", exact=True).click()
                        expect(page.locator(".collection > details.panel")).to_have_count(0)
                        assert client.get(f"/api/v1/plans/{plan['id']}").status_code == 404
                        offer = {"name": "测试方案", "ratings": {"growth": 3}, "confirmed": True}
                        comparison = client.post(
                            "/api/v1/offer-comparisons",
                            json={
                                "offers": [offer, {**offer, "name": "测试方案二"}],
                                "weights": {"growth": 100},
                                "confirmed": True,
                            },
                        )
                        assert comparison.status_code == 201, comparison.text
                        page.goto(base + "/app/profile#all-information")
                        manager = page.locator(".information-browser")
                        manager.locator(".list-tools").get_by_label(
                            "资料类型", exact=True
                        ).select_option("offer_comparison")
                        record = manager.locator(":scope > details.panel").last
                        expect(record.locator(":scope > summary")).to_contain_text("Offer")
                        record.locator(":scope > summary").click()
                        record.get_by_role("button", name="删除资料", exact=True).click()
                        expect(
                            record.get_by_role("region", name="删除预览", exact=True)
                        ).to_contain_text("可随时恢复")
                        record.get_by_role("button", name="确认移入历史", exact=True).click()
                        expect(manager).to_contain_text("没有匹配的资料")
                        manager.get_by_label("显示历史记录", exact=True).check()
                        record.locator(":scope > summary").click()
                        record.get_by_role("button", name="恢复记录", exact=True).click()
                        page.wait_for_load_state("networkidle")
                        previous = page.url
                        page.goto(base + "/app/settings")
                        page.locator(".settings-language select").select_option("en-US")
                        page.goto(previous)
                        page.wait_for_load_state("networkidle")
                        manager.locator(".list-tools").get_by_label(
                            "Information type", exact=True
                        ).select_option("offer_comparison")
                        expect(record.locator(":scope > summary")).to_contain_text("Offer")
                        if record.get_attribute("open") is None:
                            record.locator(":scope > summary").click()
                        expect(
                            record.get_by_role("button", name="Edit information", exact=True)
                        ).to_be_visible()
                        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                    finally:
                        browser.close()
                assert not errors, errors
                print(
                    "Business browser passed: plan task CRUD, preview/reject/confirm, "
                    "history/refresh, protection, delete/cancel, archive/restore, desktop English"
                )
        finally:
            server.should_exit = True
            api.join(timeout=10)
            engine.dispose()
            if api.is_alive():
                raise RuntimeError("Temporary API did not stop")


if __name__ == "__main__":
    run()
