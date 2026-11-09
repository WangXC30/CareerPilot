"""Real browser + leased Worker acceptance for the V1 local growth journey."""

import argparse
import json
import re
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

from careerpilot.accounts import process_deletion
from careerpilot.api import create_app
from careerpilot.persistence import make_engine
from careerpilot.runtime import run_once
from careerpilot.seed import seed

ROOT = Path(__file__).resolve().parents[1]


try:
    from .temporary_artifacts import temporary_artifacts
except ImportError:
    from temporary_artifacts import temporary_artifacts


def run(output=None):
    with tempfile.TemporaryDirectory(prefix="careerpilot-v1-browser-") as directory:
        url = f"sqlite:///{(Path(directory) / 'acceptance.db').as_posix()}"
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
        server_thread = threading.Thread(target=server.run, daemon=True)
        server_thread.start()
        stopped = threading.Event()
        worker_errors = []

        def work():
            while not stopped.is_set():
                try:
                    process_deletion(engine)
                    run_once(engine)
                except Exception as exc:
                    worker_errors.append(type(exc).__name__)
                stopped.wait(0.15)

        worker = threading.Thread(target=work, daemon=True)
        base = f"http://127.0.0.1:{port}"
        try:
            with httpx.Client(base_url=base, trust_env=False) as client:
                for _ in range(150):
                    try:
                        if client.get("/health/ready").status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(0.05)
                else:
                    raise RuntimeError("V1 server not ready")
            worker.start()
            if output:
                output.mkdir(parents=True, exist_ok=True)
            with sync_playwright() as p:
                browser = p.chromium.launch()
                page = browser.new_page(viewport={"width": 1440, "height": 1050})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(base + "/app/#settings")
                expect(page.locator("#account-dialog")).to_have_count(0)
                profile = page.locator("#profile-form")
                for key, value in {
                    "major": "计算机",
                    "academic_year": "大三",
                    "graduation_date": "2027-06-30",
                    "preferred_cities": "上海",
                    "weekly_minutes": "600",
                }.items():
                    profile.locator(f"[name={key}]").fill(value)
                page.get_by_role("button", name="预览档案变更").click()
                page.get_by_role("button", name="确认保存档案").click()
                expect(page.locator("#notice")).to_contain_text("档案已保存")
                page.locator("#web-consent").check()
                page.get_by_role("button", name="保存公开来源授权").click()
                expect(page.locator("#notice")).to_contain_text("公开来源授权已保存")
                page.evaluate("location.hash='explore'")
                page.locator("#goal-title").fill("后端工程师")
                page.get_by_role("button", name="保存目标草稿").click()
                expect(page.locator("#goals")).to_contain_text("后端工程师")
                page.get_by_role("button", name="预览设为主目标").click()
                page.get_by_role("button", name="确认切换主目标").click()
                expect(page.locator("#goals .tag")).to_contain_text("主目标")
                # E2E-02 uses a bounded real posting excerpt, with source and unknown fields.
                real_case = json.loads(
                    (ROOT / "evaluation/jd-v1.json").read_text(encoding="utf-8")
                )["cases"][-1]
                page.evaluate("location.hash='market'")
                page.locator("#job-form [name=content]").fill(real_case["text"])
                page.locator("#job-form [name=source_url]").fill(real_case["source_url"])
                page.get_by_role("button", name="导入并提取要求").click()
                expect(page.locator("#job-detail")).to_contain_text("Python Backend Engineer")
                expect(page.locator("#job-detail")).to_contain_text("未知")
                page.get_by_role("button", name="原文：Python", exact=True).first.click()
                expect(page.locator("#jd-original mark")).to_have_text("Python")
                page.evaluate("location.hash='skills'")
                page.locator("#gap-form input[name=job_ids]").first.check()
                page.get_by_role("button", name="分析技能差距", exact=True).click()
                expect(page.locator("#gap-result")).to_contain_text("未评估")
                expect(page.locator("#gap-result")).to_contain_text("原文：")
                page.evaluate("location.hash='learning'")
                page.locator("#plan-form input[name=job_ids]").first.check()
                page.get_by_role("button", name="生成计划草案", exact=True).click()
                expect(page.locator("#plan-view")).to_contain_text("计划草案")
                page.get_by_role("button", name="确认发布计划", exact=True).click()
                expect(page.locator("#notice")).to_contain_text("学习计划已发布")
                plan = page.evaluate(
                    "(async()=> (await api('/plans')).find(p=>p.status==='active'))()"
                )
                assert all(v <= 600 for v in plan["validation"]["weekly_minutes"].values())
                # Complete Python course tasks, without receiving verified capability.
                for task in [t for t in plan["tasks"] if t["skill_id"] == "python"]:
                    card = page.locator(f"[data-task-id='{task['id']}']")
                    card.get_by_role("button", name="开始任务", exact=True).click()
                    expect(card).to_contain_text("进行中")
                    card.locator("[name=outcome]").fill("完成列表、异常与字典练习，保留过程记录")
                    card.get_by_role("button", name="记录完成", exact=True).click()
                    expect(card.locator(".tag")).to_contain_text("已完成")
                locked_id = plan["tasks"][-1]["id"]
                page.locator(f"[data-task-id='{locked_id}']").get_by_role(
                    "button", name="锁定", exact=True
                ).click()
                expect(page.locator(f"[data-task-id='{locked_id}'] .tag")).to_contain_text("已锁定")
                before = page.evaluate(
                    "(async()=> (await api('/plans')).find(p=>p.status==='active'))()"
                )
                page.evaluate("location.hash='skills'")
                expect(page.locator("#view")).to_contain_text("证据支持")
                page.get_by_role("button", name="开始 Python 测验", exact=True).click()
                for i in range(3):
                    page.locator(f"#assessment-form input[name=q{i}][value='0']").check()
                page.get_by_role("button", name="提交测验", exact=True).click()
                expect(page.locator("#assessment-view")).to_contain_text("测验未通过")
                page.evaluate("location.hash='learning'")
                failure_preview = (
                    page.locator("#plan-previews > .growth-detail")
                    .filter(has=page.get_by_role("heading", name=re.compile("能力证据变化")))
                    .first
                )
                expect(failure_preview).to_contain_text("新增 1 项")
                failure_preview.get_by_role("button", name="保留当前计划").click()
                expect(page.locator("#notice")).to_contain_text("已保留当前计划")
                assert (
                    page.evaluate(
                        "(async()=> (await api('/plans')).find(p=>p.status==='active'))()"
                    )["tasks"]
                    == before["tasks"]
                )
                page.evaluate("location.hash='skills'")
                page.get_by_role("button", name="开始 Python 测验", exact=True).click()
                for i, value in enumerate([1, 0, 2]):
                    page.locator(f"#assessment-form input[name=q{i}][value='{value}']").check()
                page.get_by_role("button", name="提交测验", exact=True).click()
                expect(page.locator("#assessment-view")).to_contain_text("测验通过")
                if output:
                    page.screenshot(
                        path=str(output / "skills-and-assessment.png"),
                        full_page=True,
                        animations="disabled",
                    )
                page.evaluate("location.hash='learning'")
                evidence_preview = (
                    page.locator("#plan-previews > .growth-detail")
                    .filter(has=page.get_by_role("heading", name=re.compile("能力证据变化")))
                    .first
                )
                evidence_preview.get_by_role("button", name="接受此调整").click()
                expect(page.locator("#notice")).to_contain_text("已接受调整")
                expect(page.locator(f"[data-task-id='{locked_id}'] .tag")).to_contain_text("已锁定")
                page.get_by_role("button", name="查看计划历史").click()
                expect(page.locator("#plan-history-view")).to_contain_text("v1")
                if output:
                    page.screenshot(
                        path=str(output / "learning-plan-desktop.png"),
                        full_page=True,
                        animations="disabled",
                    )
                page.evaluate("location.hash='explore'")
                page.locator("#goal-title").fill("AI 应用工程师")
                page.get_by_role("button", name="保存目标草稿").click()
                expect(page.locator("#goals")).to_contain_text("AI 应用工程师")
                page.get_by_role("button", name="预览设为主目标").click()
                expect(page.locator("#goal-impact")).to_contain_text("锁定与已完成任务会保留")
                page.get_by_role("button", name="取消切换").click()
                page.get_by_role("button", name="预览设为主目标").click()
                page.get_by_role("button", name="确认切换主目标").click()
                expect(page.locator("#goals")).to_contain_text("AI 应用工程师")
                page.evaluate("location.hash='learning'")
                goal_preview = (
                    page.locator("#plan-previews > .growth-detail")
                    .filter(has=page.get_by_role("heading", name=re.compile("职业目标变化")))
                    .first
                )
                goal_preview.get_by_role("button", name="接受此调整").click()
                expect(page.locator("#notice")).to_contain_text("已接受调整")
                expect(page.locator("#plan-view")).to_contain_text("PyTorch")
                page.set_viewport_size({"width": 375, "height": 812})
                assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
                if output:
                    page.screenshot(
                        path=str(output / "learning-plan-mobile.png"),
                        full_page=True,
                        animations="disabled",
                    )
                page.evaluate("location.hash='dashboard'")
                page.locator("#assistant-form [name=message]").fill("帮我复盘当前进展")
                page.get_by_role("button", name="交给成长助手").click()
                expect(page.locator("#assistant-result")).to_contain_text(
                    "已读取当前技能证据", timeout=15000
                )
                expect(page.locator("#assistant-result")).to_contain_text("读取成长进展")
                page.evaluate("location.hash='settings'")
                with page.expect_download() as download:
                    page.get_by_role("button", name="导出我的资料").click()
                assert download.value.suggested_filename == "careerpilot-export.json"
                page.locator("#delete-account-form").locator("..").locator("summary").click()
                page.locator("#delete-account-form [name=confirmation]").fill("CLEAR LOCAL DATA")
                page.get_by_role("button", name="确认清空本地资料").click()
                expect(page.locator("#notice")).to_contain_text("本地资料已清空", timeout=15000)
                assert not errors, errors
                assert not worker_errors, worker_errors
                assert page.evaluate("localStorage.length") == 0
                browser.close()
            return {
                "browser": "Chromium",
                "mode": "single_local_workspace_and_real_worker",
                "e2e": {
                    key: "passed"
                    for key in [
                        "E2E-01",
                        "E2E-02",
                        "E2E-03",
                        "E2E-04",
                        "E2E-05",
                        "E2E-06",
                        "E2E-07",
                    ]
                },
                "e2e05_scope": (
                    "V1 objective assessment failure; V3 code execution remains a later subcase"
                ),
                "real_jd_source": real_case["source_url"],
                "weekly_budget_minutes": 600,
                "locked_task_preserved": True,
                "assistant_tools_executed": True,
                "account_export_deletion": True,
                "mobile_width": 375,
                "console_errors": 0,
                "worker_errors": 0,
            }
        finally:
            stopped.set()
            if worker.is_alive():
                worker.join(timeout=15)
            server.should_exit = True
            server_thread.join(timeout=15)
            engine.dispose()
            if worker.is_alive() or server_thread.is_alive():
                raise RuntimeError("Acceptance processes did not stop")


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
