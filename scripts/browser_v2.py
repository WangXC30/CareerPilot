"""Chromium acceptance for the first V2 matching and career-comparison batch."""

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
    with tempfile.TemporaryDirectory(prefix="careerpilot-v2-browser-") as directory:
        url = f"sqlite:///{(Path(directory) / 'browser.db').as_posix()}"
        config = Config("alembic.ini")
        config.attributes["database_url"] = url
        command.upgrade(config, "head")
        engine = make_engine(url)
        seed(engine)
        _, access_token = provision(engine, "V2 synthetic browser student")
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        server = uvicorn.Server(
            uvicorn.Config(
                create_app(url, local_only=False),
                host="127.0.0.1",
                port=port,
                log_level="error",
                access_log=False,
            )
        )
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{port}"
        try:
            with httpx.Client(
                base_url=base, trust_env=False, headers={"Authorization": "Bearer " + access_token}
            ) as client:
                for _ in range(150):
                    try:
                        if client.get("/health/ready").status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(0.05)
                else:
                    raise RuntimeError("V2 server not ready")
                response = client.put(
                    "/api/v1/profile",
                    headers={"If-Match": '"0"'},
                    json={
                        "education": "bachelor",
                        "major": "计算机",
                        "academic_year": "大三",
                        "graduation_date": "2027-06-30",
                        "preferred_cities": ["上海"],
                        "weekly_minutes": 600,
                        "confirmed": True,
                    },
                )
                assert response.status_code == 200, response.status_code
                content = (
                    "岗位：后端实习\n公司：本地验收\n地点：上海\n学历：本科及以上\n2027届\n"
                    "工作类型：实习\n实习时长：3个月\n工作许可：不限\n资格：不限\n要求：Python"
                )
                for text in [
                    content,
                    content.replace("上海", "北京"),
                    "岗位：未知条件\n要求：Python",
                    content + "\n截止日期：2000-01-01",
                ]:
                    response = client.post("/api/v1/jobs/import", json={"content": text})
                    assert response.status_code == 201, response.status_code
                    job = response.json()
                    response = client.post(
                        f"/api/v1/jobs/{job['id']}/status",
                        headers={"If-Match": '"1"'},
                        json={"status": "verified", "reason": "合成验收", "confirmed": True},
                    )
                    assert response.status_code == 200, response.status_code
            if output:
                output.mkdir(parents=True, exist_ok=True)
            with sync_playwright() as p:
                browser = p.chromium.launch()
                page = browser.new_page(viewport={"width": 1440, "height": 1050})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(base + "/app/#market")
                connect_browser(page, access_token)
                expect(page.locator("#matching-constraints-form")).to_be_visible()
                form = page.locator("#matching-constraints-form")
                form.locator("[name=available_months]").fill("4")
                form.get_by_role("button", name="确认保存匹配条件").click()
                expect(page.locator('[data-match-group="eligible"] .match-card')).to_have_count(1)
                expect(page.locator('[data-match-group="needs_review"] .match-card')).to_have_count(
                    1
                )
                expect(page.locator('[data-match-group="excluded"] .match-card')).to_have_count(2)
                card = page.locator('[data-match-group="eligible"] .match-card')
                card.get_by_text("查看条件与评分依据", exact=True).click()
                expect(card).to_contain_text("实习时长 · 符合")
                expect(card).to_contain_text("无当前有效的受信项目验收证据，保持未知")
                page.get_by_role("button", name="重算并保存本次解释").click()
                expect(page.locator("#match-snapshot-result")).to_contain_text("本次解释已保存")
                if output:
                    page.screenshot(path=str(output / "matching-desktop.png"), full_page=True)
                page.set_viewport_size({"width": 375, "height": 850})
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                if output:
                    page.screenshot(path=str(output / "matching-mobile.png"), full_page=True)
                page.goto(base + "/app/#explore")
                # Hash navigation retains this document's in-memory credential.
                expect(page.locator("#career-comparison .card")).to_have_count(4)
                form = page.locator("#career-interests-form")
                form.locator('[value="career-frontend"]').check()
                form.get_by_role("button", name="确认职业兴趣").click()
                expect(page.locator("#career-comparison .card").first).to_contain_text("前端工程师")
                expect(page.locator("#career-comparison .card").first).to_contain_text(
                    "不保证就业或薪资"
                )
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                if output:
                    page.screenshot(
                        path=str(output / "career-comparison-mobile.png"), full_page=True
                    )
                page.set_viewport_size({"width": 1440, "height": 1050})
                if output:
                    page.screenshot(
                        path=str(output / "career-comparison-desktop.png"), full_page=True
                    )
                assert not errors, errors
                browser.close()
            return {
                "browser": "Chromium",
                "scope": "V2 batch 1 CP-121/190, synthetic local JD",
                "three_value_groups": True,
                "saved_constraints_and_snapshot": True,
                "career_interest_and_comparison": True,
                "mobile_width": 375,
                "console_errors": 0,
                "full_v2_exit": False,
            }
        finally:
            server.should_exit = True
            thread.join(timeout=15)
            engine.dispose()
            if thread.is_alive():
                raise RuntimeError("V2 browser server did not stop")


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
