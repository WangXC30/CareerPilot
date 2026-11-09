"""Real Chromium specialist review checks; providers and data are synthetic only."""

import asyncio
import copy
import json
import socket
import threading
import time
from pathlib import Path
from tempfile import TemporaryDirectory

import uvicorn
from alembic import command
from alembic.config import Config
from browser_identity import connect_browser
from browser_v4_locale import CONTRAST, MISMARKED
from playwright.sync_api import expect, sync_playwright
from prepare_expert_review import cases
from sqlalchemy import select
from sqlalchemy.orm import Session

from careerpilot.api import create_app
from careerpilot.cli import provision
from careerpilot.domain import Principal
from careerpilot.expert_comparison import compare
from careerpilot.expert_protocol import digest
from careerpilot.gateway import Gateway, ProviderReply
from careerpilot.persistence import User, make_engine
from careerpilot.platform_models import ModelUsage
from careerpilot.seed import seed


class Simulated:
    model_id, external = "simulated-specialist-browser.v1", False

    async def generate(self, envelope, max_tokens):
        value = json.loads(json.loads(envelope)["untrusted_data"])
        return ProviderReply(
            json.dumps(
                {
                    "action": value["allowed_actions"][0],
                    "citations": value["tool_observations"]["source_versions"],
                }
            ),
            40,
            10,
        )


def main():
    with TemporaryDirectory(prefix="careerpilot-expert-browser-") as directory:
        url = "sqlite:///" + (Path(directory) / "browser.db").as_posix()
        config = Config("alembic.ini")
        config.attributes["database_url"] = url
        command.upgrade(config, "head")
        engine = make_engine(url)
        seed(engine)
        uid, token = provision(engine, "Synthetic expert observer")
        with Session(engine) as db:
            principal = Principal(uid, db.get(User, uid).tenant_id)
        suite = [cases()[i] for i in (0, 4, 8, 12)]
        suite[0].sources[0].id = "evidence-" + "x" * 31
        suite[0].required_sources = [suite[0].sources[0].id]
        suite[0].sources[0].text += ' private-source-canary <img src=x onerror="alert(1)">'
        gates = [
            Gateway(engine, Simulated(), daily_tokens=1000000, task_tokens=1000000)
            for _ in range(2)
        ]
        report = asyncio.run(compare(suite, digest(suite), principal, *gates, repeats=2))
        stopped = asyncio.run(
            compare(suite, digest(suite), principal, *gates, repeats=2, budget_micros=1)
        )
        report["private_reason"] = "private-reason-canary"
        with Session(engine) as db:
            usage_count = len(list(db.scalars(select(ModelUsage))))
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
                    for locale in ("zh-CN", "en-US"):
                        for width in (1440, 375):
                            check(browser, port, token, locale, width, suite, report, stopped)
                finally:
                    browser.close()
            with Session(engine) as db:
                assert len(list(db.scalars(select(ModelUsage)))) == usage_count
            print(
                "Chromium specialist review: steps/sources/manual overlay/withdrawal/"
                "late responses/"
                "no provider calls/privacy/locale/keyboard/1440/375 passed"
            )
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            engine.dispose()


def hold(page):
    page.evaluate("""() => {
      window.holdExpert=true;window.expertHeld=false;
      if(window.expertHook)return;window.expertHook=true;
      const original=api;window.api=async(path,...args)=>{
        const value=await original(path,...args);
        if(path==='/agent-experiments/expert-preview'&&window.holdExpert){
          window.holdExpert=false;window.expertHeld=true;
          await new Promise(resolve=>window.releaseExpert=resolve);
        }return value;
      };
    }""")


def check(browser, port, token, locale, width, suite, report, stopped):
    context = browser.new_context(viewport={"width": width, "height": 950})
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    zh = locale == "zh-CN"
    try:
        page.goto(f"http://127.0.0.1:{port}/app/#local-tools")
        page.locator("#ui-locale").select_option(locale)
        connect_browser(page, token)
        form = page.locator("#expert-comparison-form")
        expect(form).to_be_attached()
        case_input = form.locator('[name="cases"]')
        report_input = form.locator('[name="report"]')
        case_input.fill(json.dumps({"cases": [case.model_dump() for case in suite]}))
        report_input.fill(json.dumps(report))
        report_input.focus()
        page.keyboard.press("Tab")
        expect(form.locator('[type="checkbox"]')).to_be_focused()
        form.locator('[type="checkbox"]').check()
        submit = form.get_by_role(
            "button", name="核对版本并重算" if zh else "Check versions and rescore"
        )
        submit.click()
        result = page.locator("#expert-comparison-result")
        expect(result.locator("h3").first).to_have_text(
            "重算结果与导入指标一致" if zh else "Rescored metrics match the imported metrics"
        )
        expect(result.locator("h3").first).to_be_focused()
        expect(result.locator("details")).to_have_count(16)
        for private in ("private-source-canary", "private-reason-canary"):
            expect(result).not_to_contain_text(private)
        assert result.locator("img").count() == 0
        original_table = result.locator("table").inner_text()
        original_report = report_input.input_value()
        manual = page.locator("#expert-corrections-form")
        row = manual.locator('[data-repeat="0"][data-case="0"][data-arm="single"]')
        row.locator("summary").click()
        expect(row.locator("[data-expert-step-decision]")).to_have_count(1)
        row.locator("[data-expert-action]").select_option("compare_directions")
        note = row.locator("[data-expert-note]")
        assert note.evaluate("node=>node.required")
        note.fill('private-note-canary <img src=x onerror="alert(1)">')
        manual.locator(":scope > label input").check()
        apply = manual.get_by_role(
            "button", name="核对人工修正参考结果" if zh else "Review human-assisted results"
        )
        row.locator("[data-expert-source]").uncheck()
        apply.click()
        expect(page.locator("#notice")).to_contain_text("至少引用" if zh else "must cite")
        row.locator("[data-expert-source]").check()
        apply.click()
        assisted = page.locator("#expert-correction-result")
        expect(assisted.locator("h3")).to_be_focused()
        expect(assisted).to_contain_text("7 / 8")
        expect(assisted).not_to_contain_text("private-note-canary")
        assert report_input.input_value() == original_report
        assert result.locator("table").inner_text() == original_table
        assert result.locator("img").count() == 0
        hold(page)
        note.fill("旧人工依据")
        apply.click()
        page.wait_for_function("()=>window.expertHeld")
        row.locator("[data-expert-action]").select_option("request_evidence")
        note.fill("当前依据")
        expect(assisted.locator("h3")).to_have_count(0)
        apply.click()
        expect(assisted).to_contain_text("8 / 8")
        page.evaluate("window.releaseExpert()")
        expect(assisted).to_contain_text("8 / 8")
        expect(note).to_have_value("当前依据")
        manual.get_by_role(
            "button", name="撤回全部人工修正" if zh else "Withdraw all manual corrections"
        ).click()
        expect(assisted.locator("h3")).to_have_count(0)
        expect(note).to_have_value("")
        assert not note.evaluate("node=>node.required")
        assert result.locator("table").inner_text() == original_table
        assert page.evaluate(MISMARKED) == []
        assert page.evaluate(CONTRAST) == []
        hold(page)
        submit.click()
        page.wait_for_function("()=>window.expertHeld")
        changed = copy.deepcopy(report)
        changed["summary"]["arms"]["single"]["correct"] = 0
        report_input.fill(json.dumps(changed))
        expect(result.locator("h3")).to_have_count(0)
        submit.click()
        mismatch = "导入指标与重算不一致" if zh else "Imported metrics differ"
        expect(result.locator("h3").first).to_contain_text(mismatch)
        page.evaluate("window.releaseExpert()")
        expect(result.locator("h3").first).to_contain_text(mismatch)
        report_input.fill(json.dumps(stopped))
        submit.click()
        expect(result.locator("details")).to_have_count(16)
        row.locator("summary").click()
        expect(row).to_contain_text("预算预留上限" if zh else "Budget reservation limit")
        expect(row.locator("[data-expert-original]")).to_have_text(
            "未形成有效观察" if zh else "No valid observation"
        )
        stopped_metrics = result.locator("table").inner_text()
        row.locator("[data-expert-action]").select_option("request_evidence")
        note.fill("本人补足未执行观察，仅作人工参考")
        manual.locator(":scope > label input").check()
        apply.click()
        expect(assisted).to_contain_text("1 / 8")
        assert result.locator("table").inner_text() == stopped_metrics
        expect(row.locator("[data-expert-original]")).to_have_text(
            "未形成有效观察" if zh else "No valid observation"
        )
        report_input.fill("{bad")
        submit.click()
        expect(page.locator("#notice")).to_contain_text("有效 JSON" if zh else "valid JSON")
        expect(report_input).to_have_value("{bad")
        form.get_by_role("button", name="清空临时内容" if zh else "Clear temporary content").click()
        expect(case_input).to_have_value("")
        expect(report_input).to_have_value("")
        expect(case_input).to_be_focused()
        case_input.fill(json.dumps([case.model_dump() for case in suite]))
        page.locator("#ui-locale").select_option("en-US" if zh else "zh-CN")
        expect(case_input).to_have_value("")
        case_input.fill("temporary-owner-input")
        page.evaluate("window.dispatchEvent(new PageTransitionEvent('pagehide',{persisted:true}))")
        expect(case_input).to_have_value("")
        assert page.evaluate("document.documentElement.scrollWidth<=innerWidth+1")
        assert page.evaluate("async()=>await api('/agent-experiments')") == []
        assert not errors, errors
    finally:
        context.close()


if __name__ == "__main__":
    main()
