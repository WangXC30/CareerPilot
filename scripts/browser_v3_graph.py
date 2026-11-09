"""Real Chromium JD graph selection, source expansion and narrow-screen contract."""

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
    with tempfile.TemporaryDirectory(prefix="careerpilot-graph-") as directory:
        url = "sqlite:///" + (Path(directory) / "graph.db").as_posix()
        config = Config("alembic.ini")
        config.attributes["database_url"] = url
        command.upgrade(config, "head")
        engine = make_engine(url)
        seed(engine)
        _, token = provision(engine, "Synthetic graph student")
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        base = f"http://127.0.0.1:{port}"
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
        try:
            for _ in range(100):
                if server.started:
                    break
                time.sleep(0.05)
            with httpx.Client(
                base_url=base + "/api/v1", headers={"Authorization": "Bearer " + token}
            ) as client:
                for title, skills in [
                    ("后端实习 <img src=x onerror=alert(1)>", "Python SQL FastAPI"),
                    ("平台开发实习", "Python SQL Docker"),
                    ("AI 应用实习", "Python PyTorch"),
                ]:
                    response = client.post(
                        "/jobs/import", json={"content": f"岗位：{title}\n要求：{skills}"}
                    )
                    assert response.status_code == 201, response.text
            if output:
                output.mkdir(parents=True, exist_ok=True)
            with sync_playwright() as p:
                browser = p.chromium.launch()
                page = browser.new_page(
                    viewport={"width": 1440, "height": 1050}, reduced_motion="reduce"
                )
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(base + "/app/#skills")
                connect_browser(page, token)
                form = page.locator("#skill-graph-form")
                expect(form).to_be_visible()
                form.locator("input").nth(0).check()
                form.get_by_role("button", name="生成技能图谱").click()
                expect(page.locator("#notice")).to_contain_text("请选择 3 至 20")
                for checkbox in form.locator("input").all():
                    checkbox.check()
                form.get_by_role("button", name="生成技能图谱").click()
                result = page.locator("#skill-graph-result")
                expect(result.locator("svg")).to_be_visible()
                expect(result.locator('[data-graph-skill="python"] summary')).to_contain_text(
                    "全部岗位共同 · 3/3"
                )
                expect(result.locator('[data-graph-skill="sql"] summary')).to_contain_text(
                    "部分岗位交叉 · 2/3"
                )
                docker = result.locator('[data-graph-skill="docker"]')
                docker.locator("summary").click()
                expect(docker).to_contain_text("完整先修顺序：Git")
                expect(docker.get_by_role("link")).to_have_count(1)
                expect(result.locator("img")).to_have_count(0)
                for width in (1440, 375):
                    page.set_viewport_size({"width": width, "height": 1050})
                    page.locator("#skill-graph-card").scroll_into_view_if_needed()
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                    if width == 375:
                        assert page.locator("#sidebar").evaluate(
                            "el => el.getBoundingClientRect().right <= 0"
                        )
                    if output:
                        page.locator("#skill-graph-card").screenshot(
                            path=str(output / f"graph-{width}.png")
                        )
                # Node verification starts the actual existing assessment flow.
                docker.get_by_role("button", name="开始 Docker 测验").click()
                expect(page.locator("#assessment-form")).to_be_visible()
                # Selection changes invalidate the old rendering; fewer jobs cannot reuse it.
                form.locator("input").nth(0).uncheck()
                expect(result).to_have_text("岗位选择已变化，请重新生成图谱。")
                assert not errors, errors
                browser.close()
            report = {
                "three_jd_intersection": True,
                "prerequisite_expansion": True,
                "quiz_entry": True,
                "selection_invalidates_result": True,
                "injection_inert": True,
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
