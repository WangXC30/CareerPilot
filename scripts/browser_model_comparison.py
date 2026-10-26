"""Disposable model-observation UI checks. All fixture provider calls are simulated."""

import asyncio
import copy
import json
import socket
import tempfile
import threading
import time
from pathlib import Path

import uvicorn
from alembic import command
from alembic.config import Config
from browser_v4_locale import CONTRAST, MISMARKED
from playwright.sync_api import expect, sync_playwright
from sqlalchemy import select
from sqlalchemy.orm import Session

from careerpilot.api import create_app
from careerpilot.cli import provision
from careerpilot.domain import Principal
from careerpilot.gateway import Gateway, ProviderReply
from careerpilot.model_comparison import Dataset, compare
from careerpilot.persistence import User, make_engine
from careerpilot.platform_models import ModelUsage
from careerpilot.seed import seed


class Simulated:
    model_id, external = "simulated-browser.v1", False

    async def generate(self, envelope, max_tokens):
        return ProviderReply('{"risk":true}', 10, 4)


def main():
    with tempfile.TemporaryDirectory(prefix="careerpilot-model-browser-") as scratch:
        url = "sqlite:///" + (Path(scratch) / "browser.db").as_posix()
        config = Config("alembic.ini")
        config.attributes["database_url"] = url
        command.upgrade(config, "head")
        engine = make_engine(url)
        seed(engine)
        uid, token = provision(engine, "Synthetic model observer")
        with Session(engine) as db:
            principal = Principal(uid, db.get(User, uid).tenant_id)
        dataset = Dataset.model_validate(
            {
                "version": "browser-only.v1",
                "source": "synthetic",
                "rights_confirmed": True,
                "examples": [
                    {
                        "id": "case1",
                        "group": "positive",
                        "text": "本人授权原文，不应出现在结果里",
                        "expected": True,
                    }
                ],
            }
        )
        gates = [
            Gateway(engine, Simulated(), daily_tokens=100000, task_tokens=100000) for _ in range(2)
        ]
        report = asyncio.run(compare(dataset, principal, *gates, repeats=2))
        report["rows"][0]["steps"][0]["private_reason"] = "private-reason-canary"
        with Session(engine) as db:
            usage_count = len(list(db.scalars(select(ModelUsage))))
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
                    for locale in ("zh-CN", "en-US"):
                        for width in (1440, 375):
                            check_case(browser, port, token, locale, width, dataset, report)
                finally:
                    browser.close()
            with Session(engine) as db:
                assert len(list(db.scalars(select(ModelUsage)))) == usage_count
            print(
                "Chromium model comparison: transient rescore/manual correction/withdrawal/"
                "mismatch/step projection/"
                "no provider calls/clear/late result/keyboard/locale at 1440/375 zh/en passed"
            )
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            engine.dispose()


def hold_model_response(page):
    page.evaluate("""() => {
      window.holdModel=true;window.modelHeld=false;
      if(window.modelApiInstalled)return;window.modelApiInstalled=true;
      const original=api;
      window.api=async(path,...args)=>{
        const value=await original(path,...args);
        if(path==='/agent-experiments/model-preview'&&window.holdModel){
          window.holdModel=false;window.modelHeld=true;
          await new Promise(resolve=>window.releaseModel=resolve);
        }return value;
      };
    }""")


def check_case(browser, port, token, locale, width, dataset, report):
    context = browser.new_context(viewport={"width": width, "height": 950})
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    zh = locale == "zh-CN"
    try:
        page.goto(f"http://127.0.0.1:{port}/app/#local-tools")
        page.locator("#ui-locale").select_option(locale)
        page.locator("#connect").click()
        page.locator("#token").fill(token)
        page.locator("#account-form button[type=submit]").click()
        form = page.locator("#model-comparison-form")
        expect(form).to_be_attached()
        form.get_by_label("授权数据集 JSON" if zh else "Authorized dataset JSON").fill(
            dataset.model_dump_json()
        )
        observation = form.get_by_label(
            "冻结观察 JSON（含 rows）" if zh else "Frozen observations JSON (including rows)"
        )
        observation.fill(json.dumps(report))
        form.locator('[type="checkbox"]').check()
        submit = form.get_by_role(
            "button", name="核对版本并重算" if zh else "Check versions and rescore"
        )
        observation.focus()
        page.keyboard.press("Tab")
        expect(form.locator('[type="checkbox"]')).to_be_focused()
        submit.click()
        result = page.locator("#model-comparison-result")
        expect(result.locator("h3").first).to_have_text(
            "重算结果与导入指标一致" if zh else "Rescored metrics match the imported metrics"
        )
        expect(result.locator("h3").first).to_be_focused()
        expect(result.locator("details")).to_have_count(4)
        result.locator("summary").first.click()
        expect(result).not_to_contain_text("private-reason-canary")
        expect(result).not_to_contain_text("本人授权原文")
        assert page.evaluate(MISMARKED) == [], page.evaluate(MISMARKED)
        assert page.evaluate(CONTRAST) == [], page.evaluate(CONTRAST)

        frozen = observation.input_value()
        original_metrics = result.locator("table").inner_text()
        human = page.locator("#model-corrections-form")
        row = human.locator('[data-repeat="0"][data-case="0"][data-arm="single"]')
        row.locator("[data-human-prediction]").select_option("false")
        row.locator("[data-human-note]").fill("本人依据 <img src=x onerror=alert(1)>")
        human.locator('[type="checkbox"]').check()
        apply = human.get_by_role(
            "button", name="核对人工修正参考结果" if zh else "Review human-assisted results"
        )
        apply.click()
        assisted = page.locator("#model-correction-result")
        expect(assisted.locator("h3")).to_be_focused()
        expect(assisted).to_contain_text("1/2")
        expect(assisted).not_to_contain_text("本人依据")
        assert observation.input_value() == frozen
        assert result.locator("table").inner_text() == original_metrics
        assert result.locator("img").count() == 0
        hold_model_response(page)
        row.locator("[data-human-note]").fill("迟到请求的依据")
        apply.click()
        page.wait_for_function("()=>window.modelHeld")
        row.locator("[data-human-prediction]").select_option("true")
        row.locator("[data-human-note]").fill("当前依据，不改原观察")
        expect(assisted.locator("h3")).to_have_count(0)
        apply.click()
        expect(assisted).to_contain_text("2/2")
        page.evaluate("window.releaseModel()")
        expect(assisted).to_contain_text("2/2")
        expect(row.locator("[data-human-note]")).to_have_value("当前依据，不改原观察")
        assert observation.input_value() == frozen
        assert result.locator("table").inner_text() == original_metrics
        assert page.evaluate(MISMARKED) == []
        assert page.evaluate(CONTRAST) == []
        human.get_by_role(
            "button", name="撤回全部人工修正" if zh else "Withdraw all manual corrections"
        ).click()
        expect(assisted.locator("h3")).to_have_count(0)
        expect(row.locator("[data-human-prediction]")).to_have_value("")
        expect(row.locator("[data-human-note]")).to_have_value("")
        assert not row.locator("[data-human-note]").evaluate("node=>node.required")
        assert observation.input_value() == frozen

        # Hold a completed response while a newer set of observations is reviewed.
        hold_model_response(page)
        submit.click()
        page.wait_for_function("()=>window.modelHeld")
        altered = copy.deepcopy(report)
        altered["summary"]["arms"]["single"]["correct"] = 0
        observation.fill(json.dumps(altered))
        expect(result.locator("h3")).to_have_count(0)
        submit.click()
        mismatch = "导入指标与重算不一致" if zh else "Imported metrics differ"
        expect(result.locator("h3").first).to_contain_text(mismatch)
        page.evaluate("window.releaseModel()")
        expect(result.locator("h3").first).to_contain_text(mismatch)
        assert page.evaluate("async()=>await api('/agent-experiments')") == []

        observation.fill("{invalid")
        submit.click()
        expect(page.locator("#notice")).to_contain_text(
            "请输入有效 JSON" if zh else "Enter valid JSON"
        )
        expect(observation).to_have_value("{invalid")
        form.get_by_role("button", name="清空临时内容" if zh else "Clear temporary content").click()
        expect(observation).to_have_value("")
        expect(form.locator('[name="dataset"]')).to_have_value("")
        form.locator('[name="dataset"]').fill(dataset.model_dump_json())
        observation.fill(json.dumps(report))
        page.locator("#ui-locale").select_option("en-US" if zh else "zh-CN")
        expect(page.locator('#model-comparison-form [name="dataset"]')).to_have_value("")
        expect(page.locator('#model-comparison-form [name="report"]')).to_have_value("")
        page.locator('#model-comparison-form [name="dataset"]').fill(dataset.model_dump_json())
        page.evaluate("window.dispatchEvent(new PageTransitionEvent('pagehide',{persisted:true}))")
        expect(page.locator('#model-comparison-form [name="dataset"]')).to_have_value("")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        assert not errors, errors
    finally:
        context.close()


if __name__ == "__main__":
    main()
