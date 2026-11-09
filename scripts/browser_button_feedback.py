"""Check every available empty-workspace button and shared failure/pending feedback."""

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

ROUTES = (
    "dashboard",
    "explore",
    "market",
    "skills",
    "learning",
    "portfolio",
    "recruiting",
    "settings",
    "sources",
    "progress",
    "directions",
    "local-tools",
    "collaboration",
)


def ready(page, base, route):
    page.goto(f"{base}/app/?button-check={time.monotonic_ns()}#{route}")
    page.wait_for_load_state("networkidle")
    page.locator("#view details").evaluate_all("nodes=>nodes.forEach(n=>n.open=true)")


def check_buttons(page, base):
    checked = 0
    disabled = 0
    for route in ROUTES:
        ready(page, base, route)
        buttons = page.locator("#view button").evaluate_all(
            "nodes=>nodes.map((n,i)=>({index:i,label:n.textContent,disabled:n.disabled}))"
        )
        for item in buttons:
            ready(page, base, route)
            button = page.locator("#view button").nth(item["index"])
            if not button.is_visible():
                continue
            if button.is_disabled():
                disabled += 1
                continue
            # Focus alone on the clicked button is insufficient. Count native validation,
            # changed contents/values, a pending state, a request or a browser action.
            signals = []
            listeners = {}
            for name in ("request", "download", "filechooser", "popup"):
                listeners[name] = lambda value, name=name: signals.append(name)
                page.on(name, listeners[name])
            page.evaluate("""() => {
                window.buttonInvalid=false;
                document.addEventListener('invalid',()=>window.buttonInvalid=true,true);
                window.buttonBefore=document.querySelector('#view').innerHTML;
                window.buttonValues=[...document.querySelectorAll('#view input,#view textarea')]
                    .map(n=>n.value).join('|');
            }""")
            button.click()
            page.wait_for_timeout(100)
            feedback = page.evaluate("""() => window.buttonInvalid ||
                document.querySelector('#notice').textContent.trim() ||
                !document.querySelector('#request-status').hidden ||
                window.buttonBefore!==document.querySelector('#view').innerHTML ||
                window.buttonValues!==[...document.querySelectorAll('#view input,#view textarea')]
                    .map(n=>n.value).join('|')""")
            assert feedback or signals, (route, item["label"], "button had no feedback")
            for name, listener in listeners.items():
                page.remove_listener(name, listener)
            checked += 1
    return checked, disabled


def check_shared_feedback(page, base):
    page.route(
        "**/api/v1/local-workspace",
        lambda route: route.fulfill(status=503, json={"message": "合成工作区暂不可用"}),
    )
    ready(page, base, "dashboard")
    expect(page.locator("[data-initialize]")).to_be_visible()
    page.unroute("**/api/v1/local-workspace")
    page.locator("[data-initialize]").click()
    expect(page.locator("#assistant-form")).to_be_visible()
    for route, marker in (
        ("dashboard", "#assistant-form"),
        ("explore", "#goal-form"),
        ("learning", "#plan-form"),
        ("market", "#job-form"),
    ):
        ready(page, base, route)
        page.locator("#page-options summary").click()
        page.locator("#preview-state").select_option("error")
        page.locator(f"[data-route={route}]").click()
        expect(page.locator(marker)).to_be_visible()

    ready(page, base, "settings")
    page.route(
        "**/api/v1/consents/external_model",
        lambda route: route.fulfill(
            status=502, content_type="text/html", body="<h1>Unavailable</h1>"
        ),
    )
    page.locator("#save-consent").click()
    expect(page.locator("#notice")).to_contain_text("请求失败")
    expect(page.locator("#save-consent")).to_be_enabled()
    page.unroute("**/api/v1/consents/external_model")
    page.route("**/api/v1/consents/external_model", lambda route: route.abort())
    page.locator("#save-consent").click()
    expect(page.locator("#notice")).to_contain_text("无法连接本地服务")
    expect(page.locator("#request-status")).to_be_hidden()
    page.unroute("**/api/v1/consents/external_model")

    ready(page, base, "skills")
    pending = []
    page.route("**/api/v1/assessments", lambda route: pending.append(route))
    quiz = page.locator("[data-quiz]").first
    quiz.click()
    expect(quiz).to_be_disabled()
    expect(quiz).to_have_attribute("aria-busy", "true")
    expect(page.locator("#request-status")).to_contain_text("正在处理")
    page.mouse.click(1, 1)
    quiz.evaluate("button=>button.click()")
    quiz.dispatch_event("click")
    assert len(pending) == 1
    pending[0].fulfill(response=pending[0].fetch())
    expect(page.locator("#assessment-form")).to_be_visible()
    expect(page.locator("#request-status")).to_be_hidden()
    page.unroute("**/api/v1/assessments")

    ready(page, base, "market")
    expect(page.locator("#next-jobs")).to_be_disabled()
    page.locator("#job-search button[type=submit]").click()
    expect(page.locator("#job-search-status")).to_contain_text("没有符合条件的岗位")

    # An aborted preview must not re-disable controls after the owner clears it.
    ready(page, base, "local-tools")
    held = []
    page.route("**/api/v1/agent-experiments/model-preview", lambda route: held.append(route))
    page.evaluate("""() => {
        const form=document.querySelector('#model-comparison-form');
        window.buttonAbort=new AbortController();
        form.addEventListener('submit',async event=>{
            event.preventDefault();event.stopImmediatePropagation();
            event.submitter.disabled=true;
            try{await api('/agent-experiments/model-preview',{
                method:'POST',body:'{}',signal:window.buttonAbort.signal
            });}catch(error){if(error.name!=='AbortError')throw error;}
        },true);
    }""")
    form = page.locator("#model-comparison-form")
    form.locator("[name=dataset]").fill("{}")
    form.locator("[name=report]").fill("{}")
    form.locator("[type=checkbox]").check()
    form.locator("button:not([type])").click()
    expect(form.locator("button:not([type])")).to_be_disabled()
    page.evaluate("window.buttonAbort.abort()")
    form.locator("#clear-model-comparison").click()
    expect(form.locator("button:not([type])")).to_be_enabled()
    expect(page.locator("#notice")).to_contain_text("临时内容已清空")
    for route in held:
        route.abort()
    page.unroute("**/api/v1/agent-experiments/model-preview")


def check_pagination(page, base):
    with httpx.Client(base_url=base, trust_env=False) as client:
        for index in range(21):
            title = f"二十岗位 {index}" if index < 20 else "额外岗位"
            response = client.post(
                "/api/v1/jobs/import",
                json={"content": f"岗位：{title}\n地点：上海\n要求：Python\n编号：{index}"},
            )
            assert response.status_code in (200, 201), response.status_code
    ready(page, base, "market")
    expect(page.locator("#job-list .list-row")).to_have_count(20)
    page.locator("#next-jobs").click()
    expect(page.locator("#job-list .list-row")).to_have_count(1)
    expect(page.locator("#next-jobs")).to_be_disabled()
    expect(page.locator("#job-search-status")).to_contain_text("没有更多岗位")
    search = page.locator("#job-search")
    search.locator("[name=q]").fill("二十岗位")
    search.locator("button[type=submit]").click()
    expect(page.locator("#job-list .list-row")).to_have_count(20)
    page.locator("#next-jobs").click()
    expect(page.locator("#job-list .list-row")).to_have_count(20)
    expect(page.locator("#next-jobs")).to_be_disabled()
    expect(page.locator("#notice")).to_contain_text("没有更多岗位")
    search.locator("[name=q]").fill("")
    search.locator("button[type=submit]").click()
    expect(page.locator("#next-jobs")).to_be_enabled()
    search.locator("[name=q]").fill("额外岗位")
    page.locator("#next-jobs").click()
    expect(page.locator("#job-list .list-row")).to_have_count(1)
    expect(page.locator("#job-list")).to_contain_text("额外岗位")
    expect(page.locator("#next-jobs")).to_be_disabled()


def check_growth_actions(page, base):
    with httpx.Client(base_url=base, trust_env=False) as client:

        def post(path, payload=None, version=None):
            headers = {"Idempotency-Key": f"button-{time.monotonic_ns()}"}
            if version is not None:
                headers["If-Match"] = f'"{version}"'
            response = client.post("/api/v1" + path, json=payload, headers=headers)
            assert response.is_success, (path, response.status_code)
            return response.json()

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
                "timezone": "Asia/Shanghai",
                "confirmed": True,
            },
        )
        assert response.is_success, response.status_code
        goal = post("/career-goals", {"title": "后端工程师"})
        preview = post(f"/career-goals/{goal['id']}/preview")
        post(f"/goal-previews/{preview['id']}/accept", {"confirmed": True}, preview["version"])
        plan = post("/plans", {"job_ids": [], "start_date": date.today().isoformat()})
        post(f"/plans/{plan['id']}/publish", {"confirmed": True}, plan["version"])

    ready(page, base, "learning")
    task = page.locator(".task-card").first
    task.locator('[data-task-action="start"]').click()
    expect(task.locator(".tag")).to_contain_text("进行中")
    task.locator('[data-task-action="pause"]').click()
    expect(task.locator(".tag")).to_contain_text("已暂停")
    task.locator('[data-task-action="lock"]').click()
    expect(task.locator(".tag")).to_contain_text("已锁定")
    task.locator('[data-task-action="unlock"]').click()
    expect(task.locator(".tag")).not_to_contain_text("已锁定")
    task.locator('[data-task-action="skip"]').click()
    expect(task.locator(".tag")).to_contain_text("已跳过")
    pending = page.locator(".task-card").filter(has=page.locator(".split-task-form")).first
    pending.locator("details").last.evaluate("n=>n.open=true")
    pending.locator(".split-task-form [name=minutes]").fill("1")
    count = page.locator(".task-card").count()
    pending.locator(".split-task-form button").click()
    expect(page.locator(".task-card")).to_have_count(count + 1)
    pending = page.locator(".task-card").filter(has=page.locator(".defer-task-form")).last
    pending.locator("details").last.evaluate("n=>n.open=true")
    task_id = pending.locator(".defer-task-form").get_attribute("data-task")
    original_day = page.evaluate(
        "async id=>(await api('/plans')).find(p=>p.status==='active')"
        ".tasks.find(t=>t.id===id).date",
        task_id,
    )
    day = (date.fromisoformat(original_day) + timedelta(days=7)).isoformat()
    pending.locator(".defer-task-form [name=date]").fill(day)
    pending.locator(".defer-task-form button").click()
    expect(page.locator("#notice .error")).to_have_count(0)
    expect(page.locator("#notice")).to_contain_text("任务已更新")
    page.locator("#request-replan").click()
    expect(page.locator("#plan-previews [data-adjustment]")).not_to_have_count(0)
    page.locator("#plan-history").click()
    page.locator("[data-restore-plan]").first.click()
    expect(page.locator("#notice")).to_contain_text("已恢复")

    ready(page, base, "market")
    page.locator("#job-search [name=filter_name]").fill("按钮过滤器")
    page.locator("#save-job-filter").click()
    expect(page.locator("#saved-filters [data-use-filter]")).to_have_count(1)
    expect(page.locator("#notice")).to_contain_text("筛选已保存")
    page.locator("[data-use-filter]").click()
    expect(page.locator("#notice")).to_contain_text("已找到")
    page.locator("#job-list [data-job]").first.click()
    status = page.locator(".job-status-form")
    status.locator("[name=status]").select_option("verified")
    status.locator("[name=reason]").fill("合成岗位核对")
    status.locator("button").click()
    expect(page.locator("#notice")).to_contain_text("岗位状态已更新")
    page.locator("#job-list [data-job]").first.click()
    page.locator(".merge-job-form").locator("..").evaluate("n=>n.open=true")
    page.locator(".merge-job-form button").click()
    expect(page.locator("#notice")).to_contain_text("已合并")

    ready(page, base, "settings")
    page.locator("#material-form [name=content]").fill("熟悉 Python，完成合成课程练习")
    page.locator("#material-form button").click()
    expect(page.locator("#confirm-material-form")).to_be_visible()
    ready(page, base, "settings")
    page.locator("[data-material]").first.click()
    page.locator("#confirm-material-form [name=selected]").first.check()
    page.locator("#confirm-material-form button").click()
    expect(page.locator("#notice")).to_contain_text("已写入记忆")
    ready(page, base, "skills")
    page.locator(".memory-form details").first.evaluate("n=>n.open=true")
    page.locator("[data-delete-memory]").first.click()
    expect(page.locator("#notice")).to_contain_text("已删除")
    expect(page.locator(".memory-form")).to_have_count(0)
    with httpx.Client(base_url=base, trust_env=False) as client:
        exam = client.post("/api/v1/assessments", json={"skill_id": "python"}).json()
        result = client.post(
            f"/api/v1/assessments/{exam['id']}/submit",
            headers={"If-Match": f'"{exam["version"]}"'},
            json={"answers": [0] * len(exam["questions"])},
        )
        assert result.is_success, result.status_code
    ready(page, base, "skills")
    page.locator("[data-revoke-evidence]").first.click()
    expect(page.locator("#notice")).to_contain_text("已撤销")
    ready(page, base, "learning")
    page.locator("#resource-search [name=q]").fill("Python")
    page.locator("#resource-search button").click()
    expect(page.locator("[data-fetch-source]").first).to_be_visible()
    page.locator("[data-fetch-source]").first.click()
    expect(page.locator("#notice .error")).to_contain_text("授权")


def check_mobile_feedback(page, base):
    page.set_viewport_size({"width": 375, "height": 812})
    ready(page, base, "settings")
    page.locator("#ui-locale").select_option("en-US")
    expect(page.locator("#save-consent")).to_have_text("Save consent choice")
    held = []
    page.route("**/api/v1/consents/external_model", lambda route: held.append(route))
    page.locator("#save-consent").click()
    expect(page.locator("#request-status")).to_contain_text("Working")
    expect(page.locator("#request-status")).to_be_in_viewport()
    held.pop().fulfill(status=503, content_type="text/html", body="Unavailable")
    expect(page.locator("#notice")).to_contain_text("The request failed")
    expect(page.locator("#notice")).to_be_in_viewport()
    expect(page.locator("#save-consent")).to_be_enabled()
    page.unroute("**/api/v1/consents/external_model")
    ready(page, base, "local-tools")
    page.locator("#clear-expert-comparison").click()
    expect(page.locator("#notice")).to_contain_text("Temporary content cleared")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


def check_remaining_actions(page, base):
    from tests.test_v4_media import pdf_bytes

    with httpx.Client(base_url=base, trust_env=False) as client:
        response = client.post(
            "/api/v1/resume-facts",
            json={
                "kind": "experience",
                "source_type": "manual",
                "text": "本人完成的合成实践",
                "confirmed": True,
            },
        )
        assert response.is_success, response.status_code
    ready(page, base, "recruiting")
    revoke = page.locator(".revoke-fact").first
    revoke.locator("[name=reason]").fill("本人撤回合成陈述")
    revoke.locator("button").click()
    expect(page.locator("#notice")).to_contain_text("已撤销")

    ready(page, base, "local-tools")
    media = page.locator("#tool-media")
    media.locator("[name=title]").fill("合成按钮 PDF")
    media.locator("[name=license_basis]").fill("本人生成的临时样例")
    media.locator("[name=file]").set_input_files(
        {
            "name": "button.pdf",
            "mimeType": "application/pdf",
            "buffer": pdf_bytes(),
        }
    )
    media.locator("[name=rights]").check()
    media.locator("button").click()
    expect(page.locator("#tool-result")).to_contain_text("索引")
    ready(page, base, "local-tools")
    page.locator("[data-tool-media-delete]").first.click()
    expect(page.locator("[data-tool-media-delete]")).to_have_count(0)
    page.locator("#run-agent-experiment").click()
    expect(page.locator("#tool-result")).to_contain_text("多模型协作保持关闭")
    ready(page, base, "local-tools")
    page.locator("[data-experiment-replay]").first.click()
    expect(page.locator("#tool-result")).to_contain_text("重算一致")
    review = page.locator(".experiment-review").first
    review.locator("[name=note]").fill("本人核对固定合成结果")
    review.locator("button").click()
    expect(page.locator("#tool-result")).to_contain_text("复核已保存")

    ready(page, base, "progress")
    log = page.locator("#study-log-form")
    log.locator("[name=minutes]").fill("15")
    log.locator("[type=checkbox]").check()
    log.locator("button").click()
    expect(page.locator("[data-withdraw-study]")).to_have_count(1)
    page.locator("[data-withdraw-study]").locator("..").locator("..").evaluate("n=>n.open=true")
    page.locator("[data-withdraw-study]").click()
    expect(page.locator("[data-withdraw-study]")).to_have_count(0)
    page.locator("#review-pipeline-create").evaluate("n=>n.open=true")
    pipeline = page.locator("#review-pipeline-form")
    pipeline.locator("[type=checkbox]").check()
    pipeline.locator("button").click()
    expect(page.locator('[data-review-task-action="cancel"]')).to_have_count(1)
    page.locator('[data-review-task-action="cancel"]').click()
    expect(page.locator('[data-review-task-action="cancel"]')).to_have_count(0)

    ready(page, base, "collaboration")
    page.locator("#space-create [name=name]").fill("按钮检查临时空间")
    page.locator("#space-create button").click()
    expect(page.locator("[data-space-close]")).to_have_count(1)
    page.locator("[data-space-close]").locator("..").evaluate("n=>n.open=true")
    page.locator("[data-space-close]").click()
    expect(page.locator("[data-space-close]")).to_have_count(0)


def main(only_shared=False):
    with tempfile.TemporaryDirectory(prefix="careerpilot-button-feedback-") as directory:
        url = "sqlite:///" + (Path(directory) / "buttons.db").as_posix()
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
            with httpx.Client(trust_env=False) as client:
                for _ in range(100):
                    try:
                        if client.get(base + "/health/ready").status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(0.05)
                else:
                    raise RuntimeError("Button test server did not start")
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                if only_shared:
                    check_shared_feedback(page, base)
                    summary = (
                        "Shared retry/error/pending/cancellation/duplicate-click checks passed"
                    )
                else:
                    checked, disabled = check_buttons(page, base)
                    assert checked >= 60, (checked, "Button coverage unexpectedly low")
                    check_shared_feedback(page, base)
                    check_pagination(page, base)
                    check_growth_actions(page, base)
                    check_remaining_actions(page, base)
                    check_mobile_feedback(page, base)
                    summary = (
                        f"Button feedback passed: {len(ROUTES)} routes, {checked} clicks, "
                        f"{disabled} unavailable controls; retry/error/pending, pagination, "
                        "task actions/history/filter/status/merge/memory/evidence, "
                        "remaining actions and 375px English passed"
                    )
                assert not errors, errors
                browser.close()
                print(summary)
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            if thread.is_alive():
                raise RuntimeError("Button test server did not stop")


if __name__ == "__main__":
    main()
