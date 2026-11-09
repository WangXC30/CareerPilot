"""React growth/career journeys against temporary SQLite and a real Worker."""

import json
import os
import socket
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import uvicorn
from alembic import command
from alembic.config import Config
from playwright.sync_api import expect, sync_playwright
from sqlalchemy import select
from sqlalchemy.orm import Session

from careerpilot import chat_model, growth, local_workspace
from careerpilot.accounts import process_deletion
from careerpilot.api import create_app
from careerpilot.gateway import Gateway, ProviderReply
from careerpilot.persistence import make_engine
from careerpilot.runtime import run_once
from careerpilot.seed import seed
from careerpilot.workflows import process_cleanup

CONFIRM_REPLY_WAITING = threading.Event()
CONFIRM_REPLY_RELEASE = threading.Event()


class BrowserChatProvider:
    """Deterministic transport; real API, graph, consent and tools still execute."""

    external, model_id = False, "browser-chat-fixture"

    async def generate(self, envelope, max_tokens):
        data = json.loads(json.loads(envelope)["untrusted_data"])
        message = data["message"]
        if "查看评测测试" in message:
            value = (
                {"action": "read_context", "arguments": {"section": "project_assessments"}}
                if not data.get("tool_results")
                else {"action": "answer", "arguments": {}}
            )
            return ProviderReply(
                json.dumps(
                    {**value, "answer": "查看实际评测状态。", "source_ids": []}, ensure_ascii=False
                ),
                30,
                20,
            )
        if "面试安排测试" in message:
            results = data.get("tool_results") or []
            if not results:
                value = {"action": "read_context", "arguments": {"section": "applications"}}
            elif results[-1]["name"] == "read_context":
                app = results[-1]["result"]["items"][0]
                value = {
                    "action": "record_interview",
                    "arguments": {
                        "application_id": app["id"],
                        "version": app["version"],
                        "date": "2030-12-06",
                        "time": None,
                        "timezone": "Asia/Shanghai",
                        "preparation_note": "复习 Python",
                    },
                }
            else:
                value = {"action": "answer", "arguments": {}}
            return ProviderReply(
                json.dumps(
                    {**value, "answer": "处理本人面试安排。", "source_ids": []}, ensure_ascii=False
                ),
                30,
                20,
            )
        if "复盘回流测试" in message:
            results = data.get("tool_results") or []
            if isinstance(results, dict):
                results = results.get("items", [])
            history = data.get("tool_history") or []
            if any(
                item.get("name") == "plan_interview_review" and item.get("status") == "success"
                for item in history
            ):
                value = {"action": "answer", "arguments": {}}
            elif not results:
                value = {
                    "action": "save_interview_reflection",
                    "arguments": {
                        "version": 0,
                        "status": "confirmed",
                        "summary": "Python 迭代器没答好",
                        "decisions": [{"skill_id": "python", "minutes": 30, "note": "复习迭代器"}],
                    },
                }
            elif results[-1]["name"] == "save_interview_reflection":
                value = {"action": "read_context", "arguments": {"section": "plans"}}
            elif results[-1]["name"] == "read_context":
                review = next(
                    item["result"]
                    for item in results
                    if item["name"] == "save_interview_reflection"
                )
                plan = next(
                    item for item in results[-1]["result"]["items"] if item["status"] == "active"
                )
                value = {
                    "action": "plan_interview_review",
                    "arguments": {
                        "review_id": review["id"],
                        "review_version": review["version"],
                        "plan_id": plan["id"],
                        "plan_version": plan["version"],
                    },
                }
            else:
                value = {"action": "answer", "arguments": {}}
            return ProviderReply(
                json.dumps(
                    {**value, "answer": "处理本人面试复盘。", "source_ids": []}, ensure_ascii=False
                ),
                30,
                20,
            )
        if "岗位完整流程：" in message:
            results = data.get("tool_results") or []
            if not results:
                value = {
                    "action": "import_job",
                    "arguments": {"content": message.split("岗位完整流程：", 1)[1]},
                }
            elif results[-1]["name"] in {"import_job", "diagnose"}:
                job_id = next(
                    item["result"]["id"] for item in results if item["name"] == "import_job"
                )
                value = {
                    "action": "diagnose" if results[-1]["name"] == "import_job" else "plan",
                    "arguments": {
                        "job_ids": [job_id],
                        **({"weekly_minutes": 180} if results[-1]["name"] == "diagnose" else {}),
                    },
                }
            else:
                value = {"action": "answer", "arguments": {}}
            return ProviderReply(
                json.dumps(
                    {**value, "answer": "处理岗位学习流程。", "source_ids": []}, ensure_ascii=False
                ),
                30,
                20,
            )
        if "确认反馈测试" in message:
            tools = data.get("tool_results") or []
            if not tools:
                value = {"action": "read_context", "arguments": {"section": "jobs"}}
            elif tools[-1]["name"] == "read_context":
                jobs = tools[-1]["result"]["items"]
                value = {"action": "plan", "arguments": {"job_ids": [jobs[0]["id"]]}}
            else:
                CONFIRM_REPLY_WAITING.set()
                if not CONFIRM_REPLY_RELEASE.wait(timeout=30):
                    raise RuntimeError("Confirmation feedback was not observed in time")
                value = {"action": "plan", "arguments": tools[-1]["arguments"]}
            return ProviderReply(
                json.dumps(
                    {**value, "answer": "核对并保存计划草稿。", "source_ids": []},
                    ensure_ascii=False,
                ),
                30,
                20,
            )
        action, arguments, answer = "answer", {}, "你好！可以聊目标，也可以一起安排学习。"
        if data.get("tool_results"):
            tool = data["tool_results"][-1]
            output = tool["result"]
            if "创建聊天简历" in message and tool["name"] == "read_context":
                return ProviderReply(
                    json.dumps(
                        {
                            "action": "create_resume",
                            "answer": "预览求职简历。",
                            "arguments": {
                                "title": "聊天创建的简历",
                                "fact_ids": [
                                    f["id"]
                                    for f in output["items"]
                                    if f["source_status"] == "current"
                                ],
                                "job_id": None,
                            },
                            "source_ids": [],
                        },
                        ensure_ascii=False,
                    ),
                    30,
                    20,
                )
            answer = {
                "progress": "已读取当前技能证据和学习计划。",
                "resources": f"找到 {len(output.get('results', []))} 项资源。",
                "diagnose": f"已分析 {len(output.get('skills', []))} 项技能要求。",
                "remember": "已按你的决定处理此次偏好保存请求。",
            }.get(tool["name"], "已完成此次处理。")
        elif "创建聊天简历" in message:
            action, arguments = "read_context", {"section": "resume_facts"}
        elif "补充到资料" in message:
            action, arguments = "add_profile_note", {"text": message.split("：", 1)[-1]}
        elif "保存档案草稿" in message:
            action, arguments = "save_profile_draft", {"fields": {"major": "计算机"}}
        elif "修改档案" in message:
            action, arguments = (
                "update_profile",
                {
                    "version": data["context"]["profile"][0]["version"],
                    "fields": {"major": "软件工程"},
                },
            )
        elif "添加项目经历" in message:
            action, arguments = (
                "add_experience",
                {
                    "kind": "project",
                    "title": "聊天合成项目",
                    "action": "使用 Python 编写数据清洗脚本。",
                    "start_date": {"value": "2025", "precision": "year"},
                },
            )
        elif "进展" in message:
            action = "progress"
        elif "资源" in message:
            action, arguments = "resources", {"query": "python"}
        elif "差距" in message:
            action, arguments = "diagnose", {"job_ids": data["context"]["linked_jobs"]}
        elif "记住" in message:
            action, arguments = "remember", {"text": "我喜欢简短回答。"}
        elif "上一条" in message:
            answer = next(m["content"] for m in reversed(data["history"]) if m["role"] == "user")
        value = {"action": action, "arguments": arguments, "answer": answer, "source_ids": []}
        return ProviderReply(json.dumps(value, ensure_ascii=False), 30, 20)


def switch_language(page, language):
    previous = page.url
    selected = page.locator('[role="tab"][aria-selected="true"]')
    index = (
        selected.evaluate("el => [...el.parentElement.querySelectorAll('[role=tab]')].indexOf(el)")
        if selected.count()
        else None
    )
    searches = page.locator('input[type="search"]').evaluate_all("els => els.map(el => el.value)")
    page.goto(previous.split("/app/")[0] + "/app/settings")
    page.locator(".settings-language select").select_option(language)
    page.goto(previous)
    if index is not None:
        page.get_by_role("tab").nth(index).click()
    for i, value in enumerate(searches):
        if value and page.locator('input[type="search"]').count() > i:
            page.locator('input[type="search"]').nth(i).fill(value)


def assert_today_layout(page):
    """Keep the page and composer inside the viewport, including short windows."""
    expect(page.locator(".topbar")).to_have_count(0)
    expect(page.locator(".chat-workspace")).to_be_visible()
    expect(page.locator(".assistant-composer")).to_be_visible()
    assert page.evaluate(
        """() => {
            const doc = document.documentElement;
            const chat = document.querySelector('.chat-workspace').getBoundingClientRect();
            const composer = document.querySelector('.assistant-composer').getBoundingClientRect();
            return doc.scrollHeight <= innerHeight + 1 && doc.scrollWidth <= innerWidth
                && chat.height > 0 && composer.top >= chat.top
                && composer.bottom <= chat.bottom + 1;
        }"""
    )


def assert_system_review_dates(browser, base):
    """Use the device's date across timezone, midnight and month boundaries."""
    context = browser.new_context(timezone_id="America/Los_Angeles")
    try:
        page = context.new_page()
        page.clock.install(time=datetime(2026, 11, 1, 6, 59, 45, tzinfo=timezone.utc))
        page.goto(base + "/app/learn")
        page.get_by_role("tab", name="投入与复盘", exact=True).click()
        start = page.get_by_label("开始日期", exact=True)
        end = page.get_by_label("结束日期", exact=True)
        expect(start).to_have_value("2026-10-01")
        expect(end).to_have_value("2026-10-31")
        page.clock.fast_forward(31_000)
        expect(start).to_have_value("2026-11-01")
        expect(end).to_have_value("2026-11-01")
        page.get_by_text("记录阶段反思", exact=True).click()
        expect(page.locator(".reflection > .review-period")).to_have_text(
            "记录期间：2026-11-01 — 2026-11-01"
        )
        start.fill("2026-10-01")
        end.fill("2026-10-31")
        page.get_by_role("button", name="查看复盘", exact=True).click()
        # This local day includes the fall DST transition and lasts 25 hours.
        page.clock.fast_forward(90_000_000)
        expect(end).to_have_value("2026-10-31")
        expect(page.locator(".reflection > .review-period")).to_have_text(
            "记录期间：2026-10-01 — 2026-10-31"
        )
        page.get_by_role("button", name="使用系统日期", exact=True).click()
        expect(start).to_have_value("2026-11-01")
        expect(end).to_have_value("2026-11-02")
        page.clock.set_system_time(datetime(2026, 11, 3, 12, tzinfo=timezone.utc))
        page.evaluate("window.dispatchEvent(new Event('focus'))")
        expect(end).to_have_value("2026-11-03")
        expect(page.locator(".reflection > .review-period")).to_have_text(
            "记录期间：2026-11-01 — 2026-11-03"
        )
        page.set_viewport_size({"width": 1280, "height": 812})
        switch_language(page, "en-US")
        expect(page.get_by_role("button", name="Use system date", exact=True)).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    finally:
        context.close()


def assert_information_reading(page, base):
    """Read full prose and source provenance across the managed information kinds."""
    experience = {
        "title": "资料阅读测试经历",
        "organization": "示例机构",
        "role": "开发者",
        "start_date": {"value": "2025", "precision": "year"},
        "end_date": {"value": None, "precision": "unknown"},
        "action": "独立实现了资料检索功能。",
        "result": "完成可运行的原型。",
    }
    body = "正文保留_原始字符与空行。\n\n" + "完整资料内容。" * 100
    fixtures = [
        ("profile", {"education": "bachelor", "major": "计算机", "weekly_minutes": 0}),
        ("material", {"filename": "阅读材料.md", "content": body, "status": "confirmed"}),
        (
            "memory",
            {
                "structured_experience": True,
                "experience": experience,
                "text": "\n".join(
                    [experience["title"], experience["action"], experience["result"]]
                ),
                "category": "self_report",
                "skill_status": "unverified",
            },
        ),
        ("resume_fact", {"text": "本人完成了资料系统。", "source_status": "needs_reconfirmation"}),
        (
            "source_document",
            {
                "text": "来源摘录。",
                "url": "https://example.com/reference",
                "verification": "本人提交，尚未独立核实",
            },
        ),
        (
            "job",
            {
                "content": "岗位原文_保留。",
                "status": "unverified",
                "fields": {
                    "company": {
                        "value": "示例公司",
                        "state": "extracted",
                        "evidence": {"quote": "示例公司"},
                    },
                    "salary_min": {"value": None, "state": "unknown"},
                },
            },
        ),
        (
            "practice_project",
            {"note": "项目补充。", "template": {"instructions": "实现数据处理原型。"}},
        ),
        ("resume", {"version_count": 2}),
        (
            "job_application",
            {"status": "draft", "job": {"title": "后端开发", "company": "示例公司"}},
        ),
        ("application_draft", {"kind": "cover_letter", "text": "求职信完整正文。"}),
        (
            "plan",
            {
                "status": "published",
                "tasks": [{"title": "练习数据处理", "minutes": 30, "status": "pending"}],
            },
        ),
        ("study_log", {"date": "2026-10-27", "minutes": 30, "note": "阅读并完成练习。"}),
        (
            "quarter_review",
            {
                "reflection": "本阶段完成了资料整理。",
                "report": {"period": {"start": "2026-10-01", "end": "2026-10-27"}},
            },
        ),
    ]
    rows = [
        {
            "id": f"reading-{kind}",
            "record_kind": kind,
            "version": 2,
            "created_at": "2026-10-27",
            "title": f"阅读测试 {kind}",
            **value,
        }
        for kind, value in fixtures
    ]
    route_pattern = "**/api/v1/information?*"
    page.route(route_pattern, lambda route: route.fulfill(json=rows))
    try:
        page.goto(base + "/app/profile#all-information")
        manager = page.locator(".information-browser")
        panels = manager.locator(":scope > details.panel").filter(
            has=page.locator(".information-detail")
        )
        expect(panels).to_have_count(13)
        for panel in panels.all():
            panel.locator(":scope > summary").click()
        articles = manager.locator(".information-detail")
        expect(articles).to_have_count(13)
        assert articles.locator(":scope > dl").count() == 0
        assert all(
            not node.is_visible() for node in articles.get_by_text("未填写", exact=True).all()
        )
        material = articles.nth(1)
        expect(material.locator(".information-prose")).to_have_text(body)
        assert (
            material.locator(".information-prose").evaluate("el => getComputedStyle(el).whiteSpace")
            == "pre-wrap"
        )
        memory = articles.nth(2)
        expect(memory.locator(".information-prose")).to_have_count(2)
        assert memory.inner_text().count(experience["action"]) == 1
        expect(memory).to_contain_text("2025 – 未知")
        expect(articles.nth(3)).to_contain_text("需要重新确认")
        expect(articles.nth(5).locator(".information-context")).to_contain_text("示例公司")
        expect(articles.nth(5).locator(".information-context")).not_to_contain_text("最低薪资")
        expect(articles.nth(6)).to_contain_text("实现数据处理原型。")
        expect(articles.nth(10)).to_contain_text("练习数据处理")
        expect(articles.nth(12)).to_contain_text("2026-10-01 – 2026-10-27")
        expect(articles.nth(0)).to_contain_text("0 分钟")
        supporting = material.locator(".information-supporting")
        assert not supporting.evaluate("el => el.open")
        supporting.locator("summary").click()
        expect(supporting).to_contain_text("版本")
        supporting.locator("summary").click()
        switch_language(page, "en-US")
        page.wait_for_load_state("networkidle")
        page.set_viewport_size({"width": 1280, "height": 800})
        for panel in panels.all():
            if not panel.evaluate("el => el.open"):
                panel.locator(":scope > summary").click()
        expect(articles.nth(1)).to_contain_text("Confirmed")
        expect(articles.nth(3)).to_contain_text("Needs confirmation")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        switch_language(page, "zh-CN")
        page.set_viewport_size({"width": 1440, "height": 1000})
        print(
            "Information reading passed: 13 kinds, full prose, no repeated experience, "
            "source details, desktop English"
        )
    finally:
        page.unroute(route_pattern)


def assert_information_management(page, client, base):
    assert_information_reading(page, base)
    page.set_viewport_size({"width": 1440, "height": 1000})
    page.goto(base + "/app/profile#all-information")
    switch_language(page, "zh-CN")
    page.wait_for_load_state("networkidle")
    manager = page.locator(".information-browser")
    expect(manager).to_be_visible()
    adding = manager.locator(":scope > details.panel").first
    adding.locator(":scope > summary").click()
    adding.get_by_label("内容", exact=False).fill("浏览器资料维护：新增内容")
    adding.get_by_label("我确认所选资料和此次操作", exact=True).check()
    adding.get_by_role("button", name="确认保存", exact=True).click()
    expect(adding.locator('[role="status"]')).to_contain_text("已保存")
    search = manager.get_by_label("搜索全部资料", exact=True)
    search.fill("浏览器资料维护")
    row = manager.locator(":scope > details.panel").last
    expect(row.locator(":scope > summary")).to_contain_text("新增内容")
    row.locator(":scope > summary").click()
    row.get_by_role("button", name="修改资料", exact=True).click()
    row.get_by_label("内容", exact=False).fill("浏览器资料维护：修改完成")
    row.get_by_label("我确认所选资料和此次操作", exact=True).check()
    row.get_by_role("button", name="保存修改", exact=True).click()
    expect(row.locator(":scope > summary")).to_contain_text("修改完成")
    row.locator(":scope > summary").click()
    row.get_by_role("button", name="查看历史", exact=True).click()
    expect(row).to_contain_text("浏览器资料维护：新增内容")
    row.get_by_role("button", name="删除资料", exact=True).click()
    expect(row.get_by_role("region", name="删除预览", exact=True)).to_be_visible()
    row.get_by_role("button", name="取消删除", exact=True).click()
    assert len(client.get("/api/v1/information?q=浏览器资料维护").json()) == 1
    page.reload()
    search.fill("浏览器资料维护")
    expect(row.locator(":scope > summary")).to_contain_text("修改完成")
    switch_language(page, "en-US")
    page.wait_for_load_state("networkidle")
    page.set_viewport_size({"width": 1280, "height": 812})
    expect(row.locator(":scope > summary")).to_contain_text("修改完成")
    row.locator(":scope > summary").click()
    row.get_by_role("button", name="Delete information", exact=True).click()
    expect(row.get_by_role("region", name="Deletion preview", exact=True)).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    row.get_by_role("button", name="Confirm deletion", exact=True).click()
    expect(manager).to_contain_text("No matching information.")
    assert client.get("/api/v1/information?q=浏览器资料维护").json() == []
    switch_language(page, "zh-CN")
    page.set_viewport_size({"width": 1440, "height": 1000})
    material = client.post(
        "/api/v1/materials?filename=browser-maintenance.md", content="浏览器材料原文".encode()
    ).json()
    page.reload()
    search.fill("browser-maintenance.md")
    expect(row.locator(":scope > summary")).to_contain_text("browser-maintenance.md")
    row.locator(":scope > summary").click()
    row.get_by_role("button", name="修改资料", exact=True).click()
    row.get_by_label("原文", exact=False).fill("浏览器材料修订")
    row.get_by_label("我确认所选资料和此次操作", exact=True).check()
    row.get_by_role("button", name="保存修改", exact=True).click()
    page.wait_for_load_state("networkidle")
    expect(row).not_to_have_attribute("open", "")
    row.locator(":scope > summary").click()
    row.get_by_text("确认材料陈述", exact=True).click()
    row.get_by_role("button", name="读取并预览", exact=True).click()
    row.get_by_label("选择此陈述", exact=True).check()
    row.get_by_label("我确认所选陈述及来源；自述不代表能力已验证。", exact=True).check()
    row.get_by_role("button", name="确认所选陈述", exact=True).click()
    page.wait_for_load_state("networkidle")
    expect(row).not_to_have_attribute("open", "")
    original = client.get(f"/api/v1/materials/{material['id']}").json()
    assert original["content"] == "浏览器材料修订" and original["status"] == "confirmed"
    print(
        "Information browser passed: add/search/edit/history/cancel/delete, "
        "refresh, desktop English, material revision/confirmation"
    )


def assert_confirmation_feedback(page, client, base):
    """Expose the saved result while the final model response is deliberately blocked."""
    CONFIRM_REPLY_WAITING.clear()
    CONFIRM_REPLY_RELEASE.clear()
    before = len(client.get("/api/v1/plans").json())
    try:
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.goto(base + "/app/today")
        switch_language(page, "zh-CN")
        assistant = page.locator(".assistant")
        assistant.get_by_role("button", name="新建对话", exact=True).click()
        assistant.get_by_label("消息", exact=True).fill("确认反馈测试：创建计划草稿")
        assistant.get_by_role("button", name="发送", exact=True).click()
        current = assistant.locator(".run").last
        expect(current).to_have_attribute("data-status", "waiting_confirmation")
        route = "**/api/v1/workflows/runs/*/resume"
        page.route(route, lambda request: request.abort())
        current.get_by_role("button", name="确认继续", exact=True).click()
        expect(current.locator(".confirmation-feedback")).to_have_attribute("role", "alert")
        expect(current).to_have_attribute("data-status", "waiting_confirmation")
        assert len(client.get("/api/v1/plans").json()) == before
        page.unroute(route)
        current.get_by_role("button", name="确认继续", exact=True).click()
        expect(current.locator(".confirmation-feedback")).to_contain_text("已确认并保存为草稿")
        assert CONFIRM_REPLY_WAITING.wait(timeout=5)
        expect(current).to_have_attribute("data-status", "running")
        expect(current.get_by_role("button", name="确认继续", exact=True)).to_have_count(0)
        expect(current.locator(".plan-save-status")).to_have_text("已确认并保存为草稿 · 尚未发布")
        assert len(client.get("/api/v1/plans").json()) == before + 1
        page.reload()
        page.set_viewport_size({"width": 1280, "height": 812})
        switch_language(page, "en-US")
        expect(current.locator(".confirmation-feedback")).to_contain_text(
            "Confirmed and saved as a draft"
        )
        expect(current.locator(".plan-save-status")).to_have_text(
            "Confirmed and saved as a draft · Not published"
        )
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        CONFIRM_REPLY_RELEASE.set()
        expect(current).to_have_attribute("data-status", "completed")
        expect(
            current.get_by_role("button", name="Confirm and continue", exact=True)
        ).to_have_count(0)
        assert len(client.get("/api/v1/plans").json()) == before + 1
        if before == 0:
            saved = client.get("/api/v1/plans").json()[0]
            response = client.post(
                f"/api/v1/plans/{saved['id']}/publish",
                headers={"If-Match": f'"{saved["version"]}"'},
                json={"confirmed": True},
            )
            assert response.status_code == 200, response.text
            page.reload()
            expect(current.locator(".plan-save-status")).to_have_text(
                "Plan published. You can start working on it"
            )
            expect(current.locator(".confirmation-feedback")).not_to_contain_text("as a draft")
        switch_language(page, "zh-CN")
        page.set_viewport_size({"width": 1440, "height": 1000})
        print(
            "Confirmation feedback passed: submit retry, immediate save, refresh, "
            "locale, no duplicates"
        )
    finally:
        CONFIRM_REPLY_RELEASE.set()


def assert_followup(page, client, base):
    job = client.post(
        "/api/v1/jobs/import", json={"content": "岗位：后端实习生\n公司：合成公司\n要求：Python"}
    ).json()
    facts = []
    for kind, value in (("name", "合成同学"), ("contact", "student@example.com")):
        response = client.post(
            "/api/v1/resume-facts",
            json={"kind": kind, "source_type": "manual", "text": value, "confirmed": True},
        )
        assert response.status_code == 201, response.text
        facts.append(response.json()["id"])
    response = client.post(
        "/api/v1/resumes",
        headers={"Idempotency-Key": "followup-resume"},
        json={"title": "测试简历", "fact_ids": facts, "job_id": job["id"], "confirmed": True},
    )
    assert response.status_code == 201, response.text
    version = response.json()
    response = client.post(
        "/api/v1/applications",
        headers={"Idempotency-Key": "followup-application"},
        json={"job_id": job["id"], "resume_version_id": version["id"], "confirmed": True},
    )
    assert response.status_code == 201, response.text
    app = response.json()
    for target in ("prepared", "awaiting_user_submission"):
        body = {"target": target, "confirmed": True}
        if target == "prepared":
            body["checklist"] = {
                key: True
                for key in ("facts", "contact", "requirements", "privacy", "source_reviewed")
            }
        response = client.post(
            f"/api/v1/applications/{app['id']}/transition",
            headers={"If-Match": f'"{app["version"]}"'},
            json=body,
        )
        assert response.status_code == 200, response.text
        app = response.json()
    response = client.post(
        "/api/v1/materials?filename=receipt.txt", content="投递成功 合成公司 TEST-123".encode()
    )
    assert response.status_code == 201, response.text
    proof = response.json()
    response = client.post(
        f"/api/v1/applications/{app['id']}/receipt",
        headers={"If-Match": f'"{app["version"]}"'},
        json={
            "material_id": proof["id"],
            "material_version": proof["version"],
            "receipt_code": "TEST-123",
            "submitted_at": datetime.now(timezone.utc).isoformat(),
            "confirmed": True,
        },
    )
    assert response.status_code == 200, response.text
    response = client.post(
        "/api/v1/plans",
        json={"job_ids": [job["id"]], "start_date": "2026-10-28", "weekly_minutes": 180},
    )
    assert response.status_code == 201, response.text
    plan = response.json()
    response = client.post(
        f"/api/v1/plans/{plan['id']}/publish",
        headers={"If-Match": f'"{plan["version"]}"'},
        json={"confirmed": True},
    )
    assert response.status_code == 200, response.text
    page.reload()
    page.get_by_label("消息", exact=True).fill("面试安排测试：2030年12月6日，时间未定，复习 Python")
    page.get_by_role("button", name="发送", exact=True).click()
    current = page.locator(".run").last
    expect(current).to_have_attribute("data-status", "waiting_confirmation")
    expect(current).to_contain_text("2030-12-06")
    expect(current).to_contain_text("时间待定")
    current.get_by_role("button", name="确认继续").click()
    expect(current).to_have_attribute("data-status", "completed")
    current.get_by_role("link", name="已保存 · 查看求职").click()
    expect(page.get_by_role("tab", name="面试", exact=True)).to_have_attribute(
        "aria-selected", "true"
    )
    expect(page.get_by_role("region", name="面试安排")).to_contain_text("2030-12-06")
    page.get_by_role("link", name="AI 助手").click()
    page.get_by_label("消息", exact=True).fill("复盘回流测试：Python 迭代器没答好，复习30分钟")
    page.get_by_role("button", name="发送", exact=True).click()
    current = page.locator(".run").filter(has_text="复盘回流测试").last
    expect(current).to_have_attribute("data-status", "waiting_confirmation")
    expect(current).to_contain_text("本人面试复盘")
    current.get_by_role("button", name="确认继续").click()
    expect(current).to_contain_text("生成计划调整预览")
    current.get_by_role("button", name="确认继续").click()
    expect(current).to_have_attribute("data-status", "completed")
    expect(current).to_contain_text("计划调整预览已保存，尚未应用")
    current.get_by_role("link", name="已保存 · 查看复盘").click()
    expect(page.get_by_role("tab", name="面试", exact=True)).to_have_attribute(
        "aria-selected", "true"
    )
    expect(page.get_by_text("面试复盘", exact=True).locator("..")).to_have_attribute("open", "")
    expect(
        page.locator(".collection > details.panel[open]")
        .filter(has_text="Python 迭代器没答好")
        .first
    ).to_be_visible()
    page.get_by_role("link", name="AI 助手").click()
    from careerpilot import projects

    response = client.post(
        "/api/v1/projects",
        headers={"Idempotency-Key": "followup-project"},
        json={"job_id": job["id"], "template_id": projects.TEMPLATE["id"], "confirmed": True},
    )
    assert response.status_code == 201, response.text
    project = response.json()
    response = client.post(
        f"/api/v1/projects/{project['id']}/submissions",
        headers={"If-Match": f'"{project["version"]}"', "Idempotency-Key": "followup-submission"},
        json={"code": "def solve(records):\n    return {}\n"},
    )
    assert response.status_code == 201, response.text
    page.get_by_label("消息", exact=True).fill("查看评测测试")
    page.get_by_role("button", name="发送", exact=True).click()
    assessment = page.locator(".run").last
    expect(assessment).to_have_attribute("data-status", "completed")
    expect(assessment).to_contain_text("等待检查")
    expect(assessment).not_to_contain_text("项目评测通过")
    page.goto(base + "/app/career")
    page.get_by_role("tab", name="面试", exact=True).click()
    appointments = page.get_by_role("region", name="面试安排")
    expect(appointments).to_contain_text("2030-12-06")
    expect(appointments).to_contain_text("复习 Python")
    appointments.get_by_text("修改面试安排", exact=True).click()
    appointment_form = (
        appointments.get_by_text("修改面试安排", exact=True).locator("..").locator("form")
    )
    expect(appointment_form.get_by_role("textbox", name="准备事项")).to_have_value("复习 Python")
    appointment_form.get_by_role("textbox", name="准备事项").fill("复习 Python 和 SQL")
    appointment_form.get_by_label("我确认所选资料和此次操作").check()
    appointment_form.get_by_role("button", name="确认保存").click()
    expect(appointments).to_contain_text("复习 Python 和 SQL")
    page.get_by_text("面试复盘", exact=True).click()
    review = (
        page.locator(".collection > details.panel").filter(has_text="Python 迭代器没答好").first
    )
    review.locator("summary").first.click()
    review.get_by_role("button", name="修改资料").click()
    edit = review.locator("form").filter(has=page.get_by_role("button", name="保存修改")).last
    edit.get_by_label("摘要", exact=False).fill("更正：迭代器和生成器需要复习")
    edit.get_by_label("我确认所选资料和此次操作").check()
    edit.get_by_role("button", name="保存修改").click()
    expect(page.locator(".feedback")).to_have_text("已保存")
    assert (
        client.get("/api/v1/interview-reviews").json()[0]["summary"]
        == "更正：迭代器和生成器需要复习"
    )


def assert_asset_loading(page, client, base):
    facts = []
    for kind, text in (("name", "合成同学"), ("contact", "student@example.com")):
        response = client.post(
            "/api/v1/resume-facts",
            json={"kind": kind, "source_type": "manual", "text": text, "confirmed": True},
        )
        assert response.status_code == 201, response.text
        facts.append(response.json()["id"])
    for index in range(3):
        response = client.post(
            "/api/v1/resumes",
            headers={"Idempotency-Key": f"loading-resume-{index}"},
            json={"title": f"合成简历 {index}", "fact_ids": facts, "confirmed": True},
        )
        assert response.status_code == 201, response.text

    paths = []
    page.on("request", lambda request: paths.append(urlsplit(request.url).path))
    with page.expect_response(
        lambda response: response.url.endswith("/api/v1/resume-version-options")
    ):
        page.goto(base + "/app/")
    expect(page.locator(".chat-workspace")).to_be_visible()
    page.wait_for_load_state("networkidle")
    initial = list(paths)
    assert initial.count("/api/v1/resume-version-options") == 1
    assert not [path for path in initial if "/resumes/" in path and path.endswith("/versions")]
    assert not [
        path
        for path in initial
        if any(
            f"/assets/{name}-" in path
            for name in ("career", "learning", "workspace", "project", "assistant-markdown")
        )
    ], "A deferred feature loaded on the empty assistant screen"

    page.goto(base + "/app/career")
    expect(page.get_by_role("heading", name="求职", exact=True)).to_be_visible()
    assert any("/assets/career-" in path for path in paths)
    page.goto(base + "/app/learn")
    expect(page.get_by_role("heading", name="学习", exact=True)).to_be_visible()
    assert any("/assets/learning-" in path for path in paths)
    print("Asset loading passed: deferred pages and one batched version request for three resumes")


def run(
    *,
    information_only=False,
    confirmation_only=False,
    navigation_only=False,
    journey_only=False,
    markdown_failure_only=False,
    asset_loading_only=False,
    followup_only=False,
    learning_only=False,
):
    root = Path(__file__).resolve().parents[1]
    if not os.environ.get("CAREERPILOT_WEB_ROOT"):
        os.environ["CAREERPILOT_WEB_ROOT"] = str(root / "frontend" / "dist")
    with tempfile.TemporaryDirectory(prefix="careerpilot-react-browser-") as directory:
        url = f"sqlite:///{(Path(directory) / 'business.db').as_posix()}"
        config = Config("alembic.ini")
        config.attributes["database_url"] = url
        command.upgrade(config, "head")
        engine = make_engine(url)
        seed(engine)
        original_gateway = chat_model.gateway
        chat_model.gateway = lambda engine: Gateway(
            engine,
            BrowserChatProvider(),
            daily_tokens=2_000_000,
            task_tokens=300_000,
            instruction=chat_model.INSTRUCTION,
        )
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        server = uvicorn.Server(
            uvicorn.Config(
                create_app(url),
                host="127.0.0.1",
                port=port,
                log_level="error",
                access_log=False,
            )
        )
        api = threading.Thread(target=server.run, daemon=True)
        stopped = threading.Event()
        errors = []

        def work():
            while not stopped.wait(0.1):
                try:
                    process_deletion(engine)
                    run_once(engine)
                    process_cleanup(engine)
                except Exception as exc:
                    errors.append(type(exc).__name__)

        worker = threading.Thread(target=work, daemon=True)
        base = f"http://127.0.0.1:{port}"
        api.start()
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
                    raise RuntimeError("React API did not become ready")
                assert (
                    client.get("/app/profile", headers={"Accept": "text/html"}).status_code == 200
                )
                assert (
                    client.get(
                        "/app/assets/missing.js", headers={"Accept": "text/html"}
                    ).status_code
                    == 404
                )
                assert (
                    client.get("/api/v1/missing", headers={"Accept": "text/html"}).status_code
                    == 404
                )
                assert (
                    client.get(
                        "/api/v1/local-workspace", headers={"Origin": "https://untrusted.invalid"}
                    ).status_code
                    == 403
                )
                assert (
                    client.get("/api/v1/local-workspace", headers={"Origin": base}).status_code
                    == 200
                )
                granted = client.put("/api/v1/consents/external_model", json={"granted": True})
                assert granted.status_code == 200, granted.text
                principal = local_workspace.principal(engine)
                with Session(engine) as db, db.begin():
                    for read in (False, True):
                        growth.save(
                            db,
                            principal,
                            "personal_notice",
                            {
                                "kind": "daily_learning",
                                "read": read,
                                "channel": "in_app",
                                "content": {
                                    "title": "已读测试提醒" if read else "今日学习提醒",
                                    "tasks": [{"title": "复习 Python 基础"}],
                                },
                            },
                            "browser-notice",
                        )
                worker.start()
                with sync_playwright() as p:
                    browser = p.chromium.launch()
                    page = browser.new_page(viewport={"width": 1440, "height": 1000})
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    if asset_loading_only:
                        assert_asset_loading(page, client, base)
                        browser.close()
                        assert not errors, errors
                        return
                    failed_markdown = []
                    if markdown_failure_only:

                        def block_markdown(route):
                            failed_markdown.append(True)
                            route.abort()

                        page.route("**/assistant-markdown-*.js", block_markdown)
                    page.goto(base + "/app/")
                    if navigation_only:

                        def fail_jobs(route):
                            route.fulfill(status=503, json={"detail": "temporary failure"})

                        page.route("**/api/v1/jobs", fail_jobs)
                        page.reload()
                        warning = page.get_by_role("alert").filter(has_text="部分关联资料加载失败")
                        expect(warning).to_be_visible()
                        page.unroute("**/api/v1/jobs", fail_jobs)
                        warning.get_by_role("button", name="重试").click()
                        expect(warning).to_have_count(0)
                    if learning_only:
                        plan = {
                            "id": "plan-test",
                            "title": "测试计划",
                            "status": "active",
                            "version": 1,
                            "start_date": "2000-01-01",
                            "validation": {"budget_minutes": 120},
                            "tasks": [
                                {
                                    "id": "task-a",
                                    "title": "基础任务",
                                    "status": "pending",
                                    "date": "2000-01-01",
                                    "minutes": 30,
                                    "requires": [],
                                },
                                {
                                    "id": "task-b",
                                    "title": "后续任务",
                                    "status": "pending",
                                    "date": "2000-01-02",
                                    "minutes": 30,
                                    "requires": ["task-a"],
                                },
                                {
                                    "id": "task-c",
                                    "title": "独立任务",
                                    "status": "pending",
                                    "date": "2000-01-02",
                                    "minutes": 30,
                                    "requires": [],
                                },
                            ],
                        }
                        reviews = {
                            "today": "2026-10-28",
                            "items": [
                                {
                                    "skill_id": "python",
                                    "name": "Python",
                                    "conclusion": "failed",
                                    "due_date": "2000-01-01",
                                    "due": True,
                                    "risk": "overdue",
                                    "wrong_questions": [],
                                }
                            ],
                            "notice": "复习规则",
                        }
                        assessment = {
                            "id": "assessment-test",
                            "version": 1,
                            "skill_id": "python",
                            "questions": [
                                {"prompt": "测试问题", "choices": ["选项一", "选项二", "选项三"]},
                                {
                                    "prompt": "第二个测验问题",
                                    "choices": ["甲选项", "乙选项", "丙选项"],
                                },
                            ],
                        }
                        project = {
                            "id": "project-test",
                            "version": 1,
                            "template": {
                                "title": "测试练习项目",
                                "instructions": "练习说明",
                                "scope_notice": "有限评测范围",
                                "milestones": [],
                            },
                            "progress": [
                                {
                                    "id": "design",
                                    "title": "梳理输入",
                                    "status": "pending",
                                    "minutes": 30,
                                    "verification": "self_report",
                                },
                                {
                                    "id": "implementation",
                                    "title": "实现与测试",
                                    "status": "unverified",
                                    "minutes": 60,
                                    "verification": "sandbox_tests",
                                },
                            ],
                            "assets": [],
                            "submissions": [],
                        }
                        page.route(
                            "**/api/v1/plans",
                            lambda route: (
                                route.fulfill(json=[plan])
                                if route.request.method == "GET"
                                else route.continue_()
                            ),
                        )
                        page.route(
                            "**/api/v1/study-reviews", lambda route: route.fulfill(json=reviews)
                        )
                        page.route(
                            "**/api/v1/assessments",
                            lambda route: (
                                route.fulfill(json=assessment)
                                if route.request.method == "POST"
                                else route.continue_()
                            ),
                        )
                        page.route(
                            "**/api/v1/assessments/assessment-test/submit",
                            lambda route: route.fulfill(
                                json={
                                    "status": "completed",
                                    "result": {
                                        "passed": True,
                                        "score": 2,
                                        "total": 2,
                                        "feedback": [
                                            {
                                                "correct": True,
                                                "expected_choice": 1,
                                                "explanation": "解析一",
                                            },
                                            {
                                                "correct": True,
                                                "expected_choice": 0,
                                                "explanation": "解析二",
                                            },
                                        ],
                                    },
                                }
                            ),
                        )
                        page.route(
                            "**/api/v1/interview-knowledge",
                            lambda route: route.fulfill(
                                json={
                                    "items": [
                                        {
                                            "id": "card-a",
                                            "topic": "Python",
                                            "question": "第一张卡片",
                                            "answer": "卡片答案一",
                                            "source_url": "https://example.com/a",
                                        },
                                        {
                                            "id": "card-b",
                                            "topic": "Python",
                                            "question": "第二张卡片",
                                            "answer": "卡片答案二",
                                            "source_url": "https://example.com/b",
                                        },
                                    ]
                                }
                            ),
                        )
                        practice_items = {
                            "items": [
                                {
                                    "id": "practice-a",
                                    "topic": "Python",
                                    "question": "第一道练习题",
                                    "choices": ["错误选项", "正确选项", "选项三", "选项四"],
                                    "language": "zh",
                                },
                                {
                                    "id": "practice-b",
                                    "topic": "SQL",
                                    "question": "第二道练习题",
                                    "choices": ["一", "二", "三", "四"],
                                    "language": "zh",
                                },
                            ]
                            + [
                                {
                                    "id": f"practice-extra-{n}",
                                    "topic": "Git",
                                    "question": f"额外题目 {n}",
                                    "choices": ["一", "二", "三", "四"],
                                    "language": "zh",
                                }
                                for n in range(20)
                            ]
                        }
                        page.route(
                            "**/api/v1/interview-practice",
                            lambda route: route.fulfill(json=practice_items),
                        )
                        page.route(
                            "**/api/v1/interview-practice/*/answer",
                            lambda route: route.fulfill(
                                json={
                                    "correct": route.request.post_data_json["choice"] == 1,
                                    "correct_choice": 1,
                                    "explanation": "练习解析",
                                    "source_url": "https://example.com",
                                }
                            ),
                        )
                        page.route(
                            "**/api/v1/projects",
                            lambda route: (
                                route.fulfill(json=[project])
                                if route.request.method == "GET"
                                else route.continue_()
                            ),
                        )
                        page.route(
                            "**/api/v1/projects/project-test",
                            lambda route: route.fulfill(json=project),
                        )
                        page.goto(base + "/app/learn")
                        expect(page.get_by_role("region", name="学习概览")).to_contain_text(
                            "基础任务"
                        )
                        blocked = page.locator('[data-task-id="task-b"]')
                        expect(blocked).to_contain_text("先完成先修任务：基础任务")
                        first = page.locator('[data-task-id="task-a"]')
                        other = page.locator('[data-task-id="task-c"]')
                        first.get_by_role("checkbox").check()
                        first.get_by_label("记录这项任务的学习成果").fill("暂存的学习成果")
                        other.get_by_role("checkbox").check()
                        first.get_by_role("checkbox").check()
                        expect(first.get_by_label("记录这项任务的学习成果")).to_have_value(
                            "暂存的学习成果"
                        )
                        page.reload()
                        first.get_by_role("checkbox").check()
                        expect(first.get_by_label("记录这项任务的学习成果")).to_have_value(
                            "暂存的学习成果"
                        )
                        page.get_by_role("tab", name="测验与复习").click()
                        page.get_by_role("button", name="刷题", exact=True).click()
                        practice = page.locator(".interview-practice")
                        expect(practice.get_by_role("heading", name="题库")).to_be_visible()
                        practice.get_by_role("button", name="下一页").click()
                        expect(practice.get_by_role("button", name="额外题目 19")).to_be_visible()
                        practice.get_by_role("button", name="上一页").click()
                        practice.get_by_label("搜索题目").fill("第二道")
                        expect(practice.get_by_role("button", name="第二道练习题")).to_be_visible()
                        expect(practice.get_by_role("button", name="第一道练习题")).to_have_count(0)
                        practice.get_by_label("搜索题目").clear()
                        practice.get_by_label("练习领域").select_option("SQL")
                        expect(practice.get_by_role("button", name="第二道练习题")).to_be_visible()
                        expect(practice.get_by_role("button", name="第一道练习题")).to_have_count(0)
                        practice.get_by_label("练习领域").select_option("")
                        practice.get_by_role("button", name="第一道练习题").click()
                        practice.get_by_label("错误选项").check()
                        practice.get_by_role("button", name="检查答案").click()
                        expect(practice.get_by_text("再想一想")).to_be_visible()
                        practice.get_by_role("button", name="下一题").click()
                        practice.get_by_role("button", name="上一题").click()
                        expect(practice.get_by_text("再想一想")).to_be_visible()
                        page.get_by_role("button", name="复习", exact=True).click()
                        cards = page.locator(".interview-knowledge")
                        expect(cards.get_by_text("第一张卡片")).to_be_visible()
                        expect(cards.get_by_text("卡片答案一")).not_to_be_visible()
                        cards.get_by_text("查看答案").click()
                        expect(cards.get_by_text("卡片答案一")).to_be_visible()
                        cards.get_by_role("button", name="下一题").click()
                        expect(cards.get_by_text("第二张卡片")).to_be_visible()
                        expect(cards.get_by_text("卡片答案二")).not_to_be_visible()
                        page.get_by_role("button", name="刷题", exact=True).click()
                        expect(practice.get_by_text("再想一想")).to_be_visible()
                        practice.get_by_role("button", name="题目列表").click()
                        expect(practice.get_by_role("button", name="本次错题")).to_contain_text("1")
                        expect(practice.get_by_text("待重做")).to_be_visible()
                        practice.get_by_role("button", name="本次错题").click()
                        practice.get_by_role("button", name="第一道练习题").click()
                        practice.get_by_role("button", name="再做一次").click()
                        practice.get_by_label("正确选项").check()
                        practice.get_by_role("button", name="检查答案").click()
                        expect(practice.get_by_text("回答正确")).to_be_visible()
                        practice.get_by_role("button", name="题目列表").click()
                        expect(practice.get_by_role("button", name="全部题目")).to_contain_text("0")
                        expect(practice.get_by_text("已答对")).to_be_visible()
                        page.get_by_role("button", name="复习", exact=True).click()
                        page.get_by_text("复习安排", exact=True).click()
                        expect(page.locator(".review-queue")).to_contain_text("已逾期")
                        expect(page.locator(".review-queue")).not_to_contain_text("assessment id")
                        page.get_by_role("button", name="开始复测").click()
                        page.get_by_label("选项二").check()
                        page.locator(".assessment-workspace").get_by_role(
                            "button", name="下一题"
                        ).click()
                        expect(page.get_by_text("第二个测验问题")).to_be_visible()
                        page.get_by_role("button", name="提交测验").click()
                        expect(page.get_by_role("alert")).to_contain_text("请回答全部题目")
                        page.get_by_label("甲选项").check()
                        page.locator(".assessment-workspace").get_by_role(
                            "button", name="上一题"
                        ).click()
                        expect(page.get_by_label("选项二")).to_be_checked()
                        page.get_by_role("tab", name="项目", exact=True).click()
                        expect(page.get_by_role("link", name="从目标岗位创建")).to_have_attribute(
                            "href", "/app/goals#jobs"
                        )
                        project_panel = page.locator('details[data-record-id="project-test"]')
                        project_panel.locator(":scope > summary").click()
                        expect(project_panel).to_contain_text("有限评测范围")
                        milestone_summary = (
                            project_panel.locator(".panel-content > details.panel > summary")
                            .filter(has_text="实现与测试")
                            .first
                        )
                        milestone_summary.click()
                        milestone = milestone_summary.locator("..")
                        expect(milestone).to_contain_text("由实际代码评测结果决定")
                        expect(milestone.locator("form")).to_have_count(0)
                        expect(
                            project_panel.get_by_text("客观代码评测", exact=True)
                        ).to_be_visible()
                        expect(
                            page.get_by_role("heading", name="学习资料", exact=True)
                        ).to_have_count(0)
                        expect(page.get_by_text("检索官方学习资料", exact=True)).to_have_count(0)
                        page.get_by_role("tab", name="测验与复习").click()
                        expect(page.get_by_label("选项二")).to_be_checked()
                        page.reload()
                        page.get_by_role("tab", name="测验与复习").click()
                        expect(page.get_by_label("选项二")).to_be_checked()
                        page.locator(".assessment-workspace").get_by_role(
                            "button", name="下一题"
                        ).click()
                        expect(page.get_by_label("甲选项")).to_be_checked()
                        page.get_by_role("button", name="提交测验").click()
                        expect(page.get_by_text("测验通过", exact=False)).to_be_visible()
                        page.goto(base + "/app/profile#history")
                        expect(page.get_by_text("添加本人引用与摘录", exact=True)).to_be_visible()
                        page.get_by_text("添加本人引用与摘录", exact=True).click()
                        expect(
                            page.locator("details")
                            .filter(has=page.get_by_text("添加本人引用与摘录", exact=True))
                            .locator("form")
                        ).to_be_visible()
                        browser.close()
                        assert not errors, errors
                        print("Learning overview, drafts, review queue and project page passed")
                        return
                    if followup_only:
                        assert_followup(page, client, base)
                        browser.close()
                        assert not errors, errors
                        print("Local interview appointment and review-to-plan browser flow passed")
                        return
                    if journey_only or markdown_failure_only:
                        text = "岗位：后端实习生\n公司：合成公司\n要求：Python 和 SQL"
                        page.get_by_label("消息", exact=True).fill(
                            "我每周可学习180分钟。岗位完整流程：" + text
                        )
                        page.get_by_role("button", name="发送", exact=True).click()
                        current = page.locator(".run").last
                        expect(current).to_have_attribute("data-status", "waiting_confirmation")
                        expect(current.locator(".preview-text")).to_have_text(text)
                        current.get_by_role("button", name="确认继续").click()
                        expect(current).to_contain_text("生成以下学习计划草案")
                        current.get_by_role("button", name="确认继续").click()
                        expect(current).to_have_attribute("data-status", "completed")
                        expect(current).to_contain_text("岗位原文已保存")
                        expect(current).to_contain_text("已确认并保存为草稿")
                        if markdown_failure_only:
                            assert failed_markdown, "Markdown chunk was not requested"
                            expect(current.locator(".assistant-markdown")).not_to_be_empty()
                        current.get_by_role("link", name="查看岗位").click()
                        expect(page.get_by_role("tab", name="岗位", exact=True)).to_have_attribute(
                            "aria-selected", "true"
                        )
                        expect(
                            page.locator(".collection > details.panel[open]")
                            .filter(has_text="后端")
                            .first
                        ).to_be_visible()
                        expect(
                            page.locator(".collection > details.panel[open] > summary:focus").first
                        ).to_be_visible()
                        page.get_by_role("link", name="AI 助手").click()
                        current = page.locator(".run").last
                        current.get_by_role("link", name="打开完整计划并核对发布").click()
                        expect(page.get_by_role("tab", name="计划与任务")).to_have_attribute(
                            "aria-selected", "true"
                        )
                        expect(
                            page.get_by_role("heading", name="学习计划", exact=True)
                        ).to_be_visible()
                        publish = page.locator("form").filter(
                            has=page.get_by_role("button", name="确认发布计划")
                        )
                        publish.get_by_label("我确认所选资料和此次操作").check()
                        publish.get_by_role("button", name="确认发布计划").click()
                        expect(
                            page.locator('.task-check input[type="checkbox"]').first
                        ).to_be_enabled()
                        task = page.locator(".task-item").first
                        expect(task).to_be_visible()
                        expect(task.locator(".task-meta")).to_be_visible()
                        filters = page.locator(".collection-filter").first
                        filters.locator("summary").click()
                        expect(filters.get_by_label("显示历史记录")).to_be_visible()
                        filters.locator("summary").click()
                        page.get_by_role("navigation", name="主导航").get_by_role(
                            "link", name="AI 助手"
                        ).click()
                        expect(page.locator(".run").last).to_contain_text("岗位原文已保存")
                        page.get_by_label("消息", exact=True).fill(
                            "添加项目经历：2025年用 Python 编写数据清洗脚本。"
                        )
                        page.get_by_role("button", name="发送", exact=True).click()
                        experience_run = page.locator(".run").last
                        expect(experience_run).to_have_attribute(
                            "data-status", "waiting_confirmation"
                        )
                        experience_run.get_by_role("button", name="确认继续").click()
                        expect(experience_run).to_have_attribute("data-status", "completed")
                        experience_run.get_by_role("link", name="已保存 · 查看我的资料").click()
                        experience = (
                            page.locator(".collection > details.panel")
                            .filter(has=page.get_by_text("聊天合成项目", exact=True))
                            .first
                        )
                        expect(experience).to_have_attribute("open", "")
                        fact_form = experience.locator("form").filter(
                            has=page.get_by_role("button", name="引用为求职事实")
                        )
                        fact_form.get_by_label("我确认所选资料和此次操作").check()
                        fact_form.get_by_role("button", name="引用为求职事实").click()
                        expect(page.locator(".feedback")).to_have_text("已保存")
                        assert (
                            client.get("/api/v1/resume-facts").json()[0]["source_status"]
                            == "current"
                        )
                        page.get_by_role("link", name="AI 助手").click()
                        page.get_by_label("消息", exact=True).fill("创建聊天简历")
                        page.get_by_role("button", name="发送", exact=True).click()
                        resume_run = page.locator(".run").filter(has_text="创建聊天简历").last
                        expect(resume_run).to_have_attribute("data-status", "waiting_confirmation")
                        resume_run.get_by_role("button", name="确认继续").click()
                        expect(resume_run).to_have_attribute("data-status", "completed")
                        resume_run.get_by_role("link", name="已保存 · 查看简历").click()
                        expect(page.get_by_role("tab", name="简历与材料")).to_have_attribute(
                            "aria-selected", "true"
                        )
                        expect(
                            page.locator(".collection > details.panel[open]")
                            .filter(has_text="聊天创建的简历")
                            .first
                        ).to_be_visible()
                        page.get_by_role("tabpanel").locator("summary").get_by_text(
                            "创建简历", exact=True
                        ).click()
                        form = (
                            page.get_by_role("tabpanel")
                            .locator("summary")
                            .get_by_text("创建简历", exact=True)
                            .locator("..")
                            .locator("form")
                            .first
                        )
                        form.get_by_label("名称", exact=False).fill("岗位学习简历")
                        form.get_by_role("combobox", name="岗位", exact=True).select_option(index=1)
                        form.get_by_role("checkbox", name="聊天合成项目").check()
                        form.get_by_label("我确认所选资料和此次操作").check()
                        form.get_by_role("button", name="确认保存").click()
                        expect(page.get_by_text("岗位学习简历", exact=True).first).to_be_visible()
                        resume = next(
                            item
                            for item in client.get("/api/v1/resumes").json()
                            if item["title"] == "岗位学习简历"
                        )
                        job = client.get("/api/v1/jobs").json()[0]
                        response = client.post(
                            "/api/v1/applications",
                            headers={"Idempotency-Key": "overview-app"},
                            json={
                                "job_id": job["id"],
                                "resume_version_id": resume["current_version_id"],
                                "confirmed": True,
                            },
                        )
                        assert response.status_code == 201, response.text
                        application = response.json()
                        page.goto(
                            base + "/app/career?record=" + application["id"] + "#applications"
                        )
                        expect(page.locator(".collection").first).to_contain_text(
                            application["job"]["title"]
                        )
                        expect(page.get_by_role("tab", name="申请记录")).to_have_attribute(
                            "aria-selected", "true"
                        )
                        expect(
                            page.locator(".collection > details.panel[open]")
                            .filter(has_text=application["job"]["title"])
                            .first
                        ).to_be_visible()
                        assert client.get("/api/v1/profile").json()["profile"] is None
                        browser.close()
                        if markdown_failure_only:
                            unexpected = [
                                error
                                for error in errors
                                if "dynamically imported module" not in error
                            ]
                            assert not unexpected, unexpected
                            print("Markdown load failure retained reply and confirmation flow")
                        else:
                            assert not errors, errors
                            print("Chat-to-resume desktop flow passed (project is a self-report)")
                        return
                    if navigation_only:
                        main_nav = page.get_by_role("navigation", name="主导航")
                        expect(main_nav.get_by_role("link")).to_have_count(4)
                        expect(main_nav.get_by_role("link", name="设置")).to_be_visible()
                        expect(page.locator(".sidebar .chat-history")).to_be_visible()
                        width_before = page.locator(".chat-stage").bounding_box()["width"]
                        page.get_by_role("button", name="收起侧边栏").click()
                        expect(page.locator(".sidebar")).to_be_hidden()
                        assert page.locator(".chat-stage").bounding_box()["width"] > width_before
                        page.get_by_role("button", name="展开侧边栏").click()
                        expect(page.locator(".sidebar .chat-history")).to_be_visible()
                        expect(page.get_by_role("heading", level=1)).to_have_text("AI 助手")
                        page.get_by_label("消息", exact=True).fill("未发送的测试草稿")
                        main_nav.get_by_role("link", name="学习", exact=True).click()
                        main_nav.get_by_role("link", name="AI 助手").click()
                        expect(page.get_by_label("消息", exact=True)).to_have_value(
                            "未发送的测试草稿"
                        )
                        page.reload()
                        expect(page.get_by_label("消息", exact=True)).to_have_value(
                            "未发送的测试草稿"
                        )
                        page.get_by_label("消息", exact=True).fill("")
                        draft_chats = []
                        for i in range(2):
                            response = client.post(
                                "/api/v1/workflows/conversations",
                                headers={"Idempotency-Key": f"draft-switch-{i}"},
                                json={"title": f"草稿隔离 {i}"},
                            )
                            assert response.status_code == 201
                            draft_chats.append(response.json()["id"])
                        page.reload()
                        page.locator(
                            f'.conversation-item[data-conversation-id="{draft_chats[0]}"]'
                        ).click()
                        page.get_by_label("消息", exact=True).fill("第一会话草稿")
                        page.locator(
                            f'.conversation-item[data-conversation-id="{draft_chats[1]}"]'
                        ).click()
                        page.get_by_label("消息", exact=True).fill("第二会话草稿")
                        page.locator(
                            f'.conversation-item[data-conversation-id="{draft_chats[0]}"]'
                        ).click()
                        expect(page.get_by_label("消息", exact=True)).to_have_value("第一会话草稿")
                        page.reload()
                        expect(page.get_by_label("消息", exact=True)).to_have_value("第一会话草稿")
                        for chat_id in draft_chats:
                            page.once("dialog", lambda dialog: dialog.accept())
                            page.locator(
                                f'.conversation-row:has([data-conversation-id="{chat_id}"]) '
                                ".conversation-delete"
                            ).click()
                        expect(page.get_by_label("消息", exact=True)).to_have_value("")
                        guide = page.get_by_role("region", name="资料引导")
                        guide.get_by_role("button", name="学习提升").click()
                        expect(guide).to_contain_text("每周能投入多少时间")
                        page.reload()
                        expect(guide).to_contain_text("每周能投入多少时间")
                        guide.get_by_role("button", name="暂时跳过").click()
                        expect(guide).to_contain_text("引导已暂缓")
                        guide.get_by_role("button", name="继续引导").click()
                        expect(guide).to_contain_text("每周能投入多少时间")
                        page.get_by_label("消息", exact=True).fill("我每周有三个小时")
                        page.get_by_role("button", name="发送", exact=True).click()
                        expect(guide).to_contain_text("已收到你的对话")
                        page.reload()
                        expect(guide).to_contain_text("已收到你的对话")
                        expect(page.locator(".run").last).to_have_attribute(
                            "data-status", "completed"
                        )
                        page.get_by_label("消息", exact=True).fill("保存档案草稿：我学计算机")
                        page.get_by_role("button", name="发送", exact=True).click()
                        current = page.locator(".run").last
                        expect(current).to_have_attribute("data-status", "waiting_confirmation")
                        expect(current).to_contain_text("私人档案草稿")
                        current.get_by_role("button", name="确认继续").click()
                        expect(current).to_contain_text("草稿已保存")
                        assert (
                            client.get("/api/v1/drafts/profile/personal").json()["values"]["major"]
                            == "计算机"
                        )
                        assert client.get("/api/v1/profile").json()["profile"] is None
                        main_nav.get_by_role("link", name="学习", exact=True).click()
                        main_nav.get_by_role("link", name="AI 助手").click()
                        expect(page.locator(".chat-stage")).to_be_visible()
                        main_nav.get_by_role("link", name="学习", exact=True).click()
                        expect(
                            page.get_by_role("heading", name="学习计划", exact=True)
                        ).to_be_visible()
                        expect(page.get_by_role("region", name="当前进展")).to_have_count(0)
                        plan_tab = page.get_by_role("tab", name="计划与任务")
                        plan_tab.focus()
                        page.keyboard.press("ArrowRight")
                        expect(page.get_by_role("tab", name="测验与复习")).to_be_focused()
                        expect(page.get_by_role("tab", name="测验与复习")).to_have_attribute(
                            "aria-selected", "true"
                        )
                        page.keyboard.press("Home")
                        expect(plan_tab).to_be_focused()
                        expect(
                            main_nav.get_by_role("link", name="学习", exact=True)
                        ).to_have_attribute("aria-current", "page")
                        main_nav.get_by_role("link", name="求职", exact=True).click()
                        page.reload()
                        expect(
                            page.get_by_role("navigation", name="主导航").get_by_role(
                                "link", name="求职", exact=True
                            )
                        ).to_have_attribute("aria-current", "page")
                        page.get_by_role("navigation", name="主导航").get_by_role(
                            "link", name="设置"
                        ).click()
                        expect(page.get_by_role("link", name="目标与岗位")).to_be_visible()
                        expect(page.get_by_role("link", name="我的资料")).to_be_visible()
                        expect(page.get_by_label("语言")).to_be_visible()
                        settings_group = page.locator(".settings-group").first
                        expect(settings_group).not_to_have_attribute("open", "")
                        settings_group.locator(":scope > summary").click()
                        expect(settings_group.get_by_text("助手记忆", exact=True)).to_be_visible()
                        settings_group.locator(":scope > summary").click()
                        data_group = page.locator(".settings-group").last
                        data_group.locator(":scope > summary").click()
                        expect(data_group.get_by_text("清空本地资料", exact=True)).to_be_visible()
                        expect(page.get_by_text("下一步 · 学习行动")).to_have_count(0)
                        expect(page.get_by_text("提醒与复盘")).to_have_count(0)
                        page.get_by_role("navigation", name="主导航").get_by_role(
                            "link", name="AI 助手"
                        ).click()
                        expect(page.locator(".chat-stage")).to_be_visible()
                        page.get_by_role("navigation", name="主导航").get_by_role(
                            "link", name="设置"
                        ).click()
                        page.goto(base + "/app/learn#reflection")
                        expect(page.get_by_role("tab", name="投入与复盘")).to_have_attribute(
                            "aria-selected", "true"
                        )
                        expect(page.get_by_text("提醒", exact=True).first).to_be_visible()
                        page.goto(base + "/app/career#applications")
                        expect(page.get_by_role("tab", name="申请记录")).to_have_attribute(
                            "aria-selected", "true"
                        )
                        page.goto(base + "/app/settings")
                        page.get_by_role("link", name="我的资料").click()
                        expect(page.get_by_role("heading", level=1)).to_have_text("我的资料")
                        page.locator(".brand").click()
                        expect(page.locator(".sidebar .chat-history")).to_be_visible()
                        for route in ("today", "goals", "learn", "career", "profile", "settings"):
                            page.goto(base + "/app/" + route)
                            expect(page.get_by_role("heading", level=1)).to_have_count(1)
                        page.goto(base + "/app/profile#materials")
                        expect(page.get_by_role("tab", name="材料与经历")).to_have_attribute(
                            "aria-selected", "true"
                        )
                        page.evaluate("location.hash = 'facts'")
                        expect(page.get_by_role("tab", name="求职事实")).to_have_attribute(
                            "aria-selected", "true"
                        )
                        page.goto(base + "/app/settings")
                        switch_language(page, "en-US")
                        page.goto(base + "/app/career")
                        expect(
                            page.get_by_role("navigation", name="Main navigation").get_by_role(
                                "link", name="Career", exact=True
                            )
                        ).to_have_attribute("aria-current", "page")
                        for width, height in ((1100, 700), (1440, 900)):
                            page.set_viewport_size({"width": width, "height": height})
                            assert page.evaluate(
                                "document.documentElement.scrollWidth <= innerWidth"
                            )
                        browser.close()
                        assert not errors, errors
                        print("Navigation, old routes, refresh and desktop locales passed")
                        return
                    if confirmation_only:
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
                        assert response.status_code == 200, response.text
                        response = client.post(
                            "/api/v1/jobs/import",
                            json={
                                "content": "岗位：后端实习生\n公司：合成公司\n要求：Python 和 SQL"
                            },
                        )
                        assert response.status_code == 201, response.text
                        assert_confirmation_feedback(page, client, base)
                        browser.close()
                        assert not errors, errors
                        return
                    if information_only:
                        assert_information_management(page, client, base)
                        browser.close()
                        assert not errors, errors
                        return
                    expect(page.get_by_role("heading", level=1)).to_have_text("AI 助手")
                    expect(
                        page.get_by_role("navigation", name="主导航").get_by_role("link")
                    ).to_have_count(4)
                    expect(page.locator('input[type="password"]')).to_have_count(0)
                    expect(page.locator(".chat-welcome")).to_be_visible()
                    expect(page.locator(".chat-history")).to_be_visible()
                    for width, height in [(1440, 900), (1366, 768), (1920, 1080), (1280, 720)]:
                        page.set_viewport_size({"width": width, "height": height})
                        assert_today_layout(page)
                    page.set_viewport_size({"width": 1440, "height": 1000})
                    page.goto(base + "/app/learn#reflection")
                    support = page.locator('[role="tabpanel"]')
                    support.get_by_text("提醒", exact=True).click()
                    expect(support.locator(".notice-row")).to_have_count(1)
                    expect(support.locator(".notice-content strong")).to_have_text("今日学习提醒")
                    expect(support.locator(".notice-content p")).to_contain_text("复习 Python 基础")
                    expect(support.locator(".notice-detail > .details").first).not_to_be_visible()
                    expect(support.get_by_label("开始日期", exact=True)).to_be_visible()
                    review = support.locator(".progress-review")
                    expect(review).to_contain_text("这段时间还没有学习时长或任务完成记录")
                    expect(review.locator("dl")).to_have_count(0)
                    expect(review).not_to_contain_text("rule_version")
                    day = support.get_by_label("结束日期", exact=True).input_value()
                    logged = client.post(
                        "/api/v1/study-logs",
                        headers={"Idempotency-Key": "browser-review-study-log"},
                        json={"day": day, "minutes": 90, "confirmed": True},
                    )
                    assert logged.status_code == 201, logged.text
                    support.get_by_role("button", name="查看复盘", exact=True).click()
                    expect(review).to_contain_text("1 小时 30 分钟的学习")
                    support.get_by_text("记录阶段反思", exact=True).click()
                    reflection = support.locator(".reflection")
                    reflection.get_by_role("textbox").fill(
                        "学会了拆分学习任务，下次先安排最重要的一项。"
                    )
                    reflection.get_by_label("确认保存这段反思", exact=True).check()
                    reflection.get_by_role("button", name="保存反思", exact=True).click()
                    expect(reflection.locator('form [role="status"]')).to_have_text("反思已保存")
                    expect(reflection.locator(".reflection-note")).to_contain_text(
                        "学会了拆分学习任务"
                    )
                    expect(reflection.locator(".reflection-note dl")).to_have_count(0)
                    assert len(client.get("/api/v1/quarter-reviews").json()) == 1
                    page.reload()
                    support.get_by_text("提醒", exact=True).click()
                    support.get_by_text("记录阶段反思", exact=True).click()
                    expect(support.locator(".reflection-note")).to_contain_text(
                        "学会了拆分学习任务"
                    )
                    support.get_by_text("记录阶段反思", exact=True).click()
                    support.get_by_role("button", name="标记已读", exact=True).click()
                    expect(support.locator(".notice-row")).to_have_count(0)
                    support.get_by_role("button", name="全部提醒", exact=True).click()
                    expect(support.locator(".notice-row")).to_have_count(2)
                    page.get_by_role("link", name="AI 助手").click()
                    page.get_by_role("button", name="找到今天的重点", exact=True).click()
                    expect(page.get_by_label("消息", exact=True)).to_have_value(
                        "查看当前进展，帮我找到今天可以完成的一步。"
                    )
                    assert client.get("/api/v1/workflows/conversations").json() == []
                    page.get_by_role("button", name="新建对话", exact=True).click()
                    expect(page.get_by_label("消息", exact=True)).to_have_value("")
                    assert page.locator(".assistant").bounding_box()["width"] > 1000
                    page.get_by_role("link", name="设置", exact=True).click()
                    page.get_by_role("link", name="我的资料", exact=True).click()
                    profile = page.locator("form").filter(
                        has=page.get_by_role("button", name="保存私人草稿")
                    )
                    profile.get_by_label("专业", exact=False).fill("计算机")
                    profile.get_by_role("button", name="保存私人草稿").click()
                    expect(
                        profile.get_by_role("status").filter(has_text="草稿已保存")
                    ).to_be_visible()
                    page.reload()
                    page.get_by_role("button", name="恢复编辑", exact=True).click()
                    profile = page.locator("form").filter(
                        has=page.get_by_role("button", name="保存私人草稿")
                    )
                    expect(profile.get_by_label("专业", exact=False)).to_have_value("计算机")
                    profile.get_by_label("学历", exact=False).select_option("bachelor")
                    profile.get_by_label("年级", exact=False).fill("大三")
                    profile.get_by_label("毕业日期", exact=False).fill("2027-06-30")
                    profile.get_by_role("button", name="下一步", exact=True).click()
                    cities = profile.get_by_role("group", name="意向城市")
                    cities.get_by_role("button", name="添加一项").click()
                    cities.get_by_role("textbox").fill("上海")
                    profile.get_by_label("每周学习分钟数", exact=False).fill("600")
                    profile.get_by_role("button", name="下一步", exact=True).click()
                    profile.get_by_label("我确认所选资料和此次操作").check()
                    profile.get_by_role("button", name="确认保存", exact=True).click()
                    expect(page.locator(".feedback")).to_have_text("已保存")
                    page.get_by_role("link", name="设置", exact=True).click()
                    page.get_by_role("link", name="目标与岗位", exact=True).click()
                    page.get_by_role("tab", name="岗位", exact=True).click()
                    page.get_by_text("导入岗位原文", exact=True).click()
                    job_form = (
                        page.get_by_text("导入岗位原文", exact=True).locator("..").locator("form")
                    )
                    job_form.get_by_label("原文", exact=False).fill(
                        "岗位：后端实习生\n公司：合成公司\n地点：上海\n要求：Python 和 SQL"
                    )
                    job_form.get_by_role("button", name="确认保存").click()
                    expect(page.get_by_text("后端实习生", exact=True).first).to_be_visible()
                    page.get_by_role("navigation").get_by_role(
                        "link", name="AI 助手", exact=True
                    ).click()
                    assistant = page.locator(".assistant form")
                    page.route("**/api/v1/workflows/runs/*/events*", lambda route: route.abort())
                    assistant.get_by_label("消息", exact=True).fill("你好")
                    assistant.get_by_label("消息", exact=True).press("Enter")
                    expect(page.locator(".assistant .assistant-reply").last).to_contain_text(
                        "一起安排学习", timeout=15000
                    )
                    page.reload()
                    expect(page.locator(".assistant .assistant-reply").last).to_contain_text(
                        "一起安排学习", timeout=15000
                    )
                    assistant.get_by_label("消息", exact=False).fill("查看当前进展")
                    with page.expect_response(
                        lambda response: (
                            response.request.method == "POST"
                            and response.url.endswith("/api/v1/workflows/runs")
                        )
                    ) as accepted:
                        assistant.get_by_role("button", name="发送").click()
                    assert accepted.value.status == 202, accepted.value.text()
                    expect(page.locator(".assistant .run")).to_have_count(2, timeout=15000)
                    expect(page.locator(".assistant .run").last).to_have_attribute(
                        "data-status", "completed", timeout=15000
                    )
                    expect(page.locator(".assistant .assistant-reply").last).to_contain_text(
                        "当前技能证据", timeout=15000
                    )
                    page.unroute("**/api/v1/workflows/runs/*/events*")
                    expect(page.locator(".assistant .run")).to_have_count(2)
                    expect(page.get_by_text("查看实际结果与依据", exact=True)).to_have_count(0)
                    result = page.locator(".assistant .assistant-reply").last.locator(
                        ".assistant-result"
                    )
                    expect(result).to_contain_text("暂无技能进展记录")
                    expect(result).to_contain_text("还没有已发布的学习计划")
                    for metadata in ("foundation", "state_rule_version", "memory_ids", "as_of"):
                        expect(result).not_to_contain_text(metadata)
                    assistant.get_by_label("消息", exact=True).fill("给我 Python 学习资源")
                    assistant.get_by_role("button", name="发送", exact=True).click()
                    expect(page.locator(".assistant .assistant-reply").last).to_contain_text(
                        "项资源", timeout=15000
                    )
                    expect(result.get_by_role("link").first).to_be_visible()
                    expect(result).not_to_contain_text("curated_official_index")
                    expect(result).not_to_contain_text("reviewed_at")
                    assistant.locator("details > summary").click()
                    assistant.get_by_role("checkbox", name="后端实习生").check()
                    assistant.get_by_label("消息", exact=True).fill("分析岗位差距")
                    assistant.get_by_role("button", name="发送", exact=True).click()
                    expect(page.locator(".assistant .assistant-reply").last).to_contain_text(
                        "项技能要求", timeout=15000
                    )
                    evidence = result.locator(".assistant-result-sources").first
                    evidence.locator("summary").click()
                    expect(evidence).to_contain_text("后端实习生")
                    expect(evidence.locator("blockquote")).to_be_visible()
                    expect(result).not_to_contain_text("catalogue_versions")
                    expect(result).not_to_contain_text("estimated_minutes")
                    expect(page.locator(".assistant .run")).to_have_count(4)
                    assistant.get_by_label("消息", exact=True).fill("请重复我上一条问题")
                    assistant.get_by_role("button", name="发送", exact=True).click()
                    expect(page.locator(".assistant .assistant-reply").last).to_contain_text(
                        "分析岗位差距"
                    )
                    assistant.get_by_label("消息", exact=True).fill("请记住我喜欢简短回答")
                    assistant.get_by_role("button", name="发送", exact=True).click()
                    expect(page.locator(".assistant .run").last).to_have_attribute(
                        "data-status", "waiting_confirmation"
                    )
                    expect(page.locator(".assistant .notice")).to_contain_text("我喜欢简短回答")
                    page.reload()
                    page.locator(".assistant").get_by_role(
                        "button", name="拒绝", exact=True
                    ).click()
                    expect(page.locator(".assistant .run").last).to_have_attribute(
                        "data-status", "completed"
                    )
                    assert client.get("/api/v1/workflows/preferences").json() == []
                    selected_chat = page.locator(
                        ".conversation-item[aria-current='true']"
                    ).get_attribute("data-conversation-id")
                    extra = client.post(
                        "/api/v1/workflows/conversations",
                        json={"title": "桌面长标题边界测试" * 8},
                        headers={"Idempotency-Key": "browser-sidebar-long-title"},
                    )
                    assert extra.status_code == 201
                    extra_id = extra.json()["id"]
                    page.reload()
                    expect(page.locator(".conversation-item")).to_have_count(2)
                    search = page.get_by_role("searchbox", name="搜索对话", exact=True)
                    search.fill("没有这个标题")
                    expect(page.get_by_text("未找到匹配的对话。", exact=True)).to_be_visible()
                    search.fill("桌面长标题")
                    expect(page.locator(".conversation-item")).to_have_count(1)
                    search.fill("")
                    page.get_by_role("button", name="新建对话", exact=True).click()
                    expect(page.locator(".assistant .run")).to_have_count(0)
                    expect(page.locator(".chat-welcome")).to_be_visible()
                    page.locator(f'.conversation-item[data-conversation-id="{extra_id}"]').click()
                    page.reload()
                    expect(
                        page.locator(f'.conversation-item[data-conversation-id="{extra_id}"]')
                    ).to_have_attribute("aria-current", "true")
                    expect(page.locator(".assistant .run")).to_have_count(0)
                    page.locator(
                        f'.conversation-item[data-conversation-id="{selected_chat}"]'
                    ).click()
                    expect(page.locator(".assistant .run")).to_have_count(6)
                    page.reload()
                    expect(page.locator(".assistant .run")).to_have_count(6)
                    expect(page.locator(".assistant .user-message")).to_have_text(
                        [
                            "你好",
                            "查看当前进展",
                            "给我 Python 学习资源",
                            "分析岗位差距",
                            "请重复我上一条问题",
                            "请记住我喜欢简短回答",
                        ]
                    )
                    expect(page.locator(".assistant .assistant-reply").last).to_contain_text(
                        "按你的决定"
                    )
                    page.set_viewport_size({"width": 1280, "height": 900})
                    page.get_by_role("button", name="收起侧边栏", exact=True).click()
                    expect(page.locator(".chat-history")).to_be_hidden()
                    page.get_by_role("button", name="展开侧边栏", exact=True).click()
                    expect(page.locator(".chat-history")).to_be_visible()
                    page.keyboard.press("Escape")
                    expect(page.locator(".chat-history")).to_be_hidden()
                    expect(
                        page.get_by_role("button", name="展开侧边栏", exact=True)
                    ).to_be_focused()
                    page.get_by_role("button", name="展开侧边栏", exact=True).click()
                    page.locator(f'.conversation-item[data-conversation-id="{extra_id}"]').click()
                    expect(page.locator(".chat-history")).to_be_visible()
                    page.get_by_role("button", name="收起侧边栏", exact=True).click()
                    expect(page.locator(".chat-heading p")).to_have_text("桌面长标题边界测试" * 8)
                    assert_today_layout(page)
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                    switch_language(page, "en-US")
                    assert_today_layout(page)
                    page.goto(base + "/app/learn#reflection")
                    expect(page.locator(".progress-review")).to_contain_text("1 h 30 min")
                    expect(page.locator(".progress-review h3")).to_have_text(
                        ["Learning", "Skills", "Job search"]
                    )
                    page.get_by_role("link", name="AI assistant").click()
                    expect(
                        page.get_by_role("heading", name="Growth assistant", exact=True)
                    ).to_be_visible()
                    page.get_by_role("searchbox", name="Search conversations", exact=True).fill(
                        "no-such-title"
                    )
                    expect(
                        page.get_by_text("No matching conversations.", exact=True)
                    ).to_be_visible()
                    page.keyboard.press("Escape")
                    page.get_by_role("button", name="New conversation", exact=True).click()
                    page.get_by_role("button", name="Find today’s focus", exact=True).click()
                    expect(page.get_by_label("Message", exact=True)).to_have_value(
                        "Review my progress and help me find one step I can take today."
                    )
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                    switch_language(page, "zh-CN")
                    page.set_viewport_size({"width": 1440, "height": 1000})
                    # The same real graph writes personal information only after approval.
                    count = len(client.get("/api/v1/memories").json())
                    assistant.get_by_label("消息", exact=True).fill(
                        "补充到资料：我叫聊天合成同学，正在学习 Python。"
                    )
                    assistant.get_by_role("button", name="发送", exact=True).click()
                    expect(page.locator(".assistant .run").last).to_have_attribute(
                        "data-status", "waiting_confirmation"
                    )
                    expect(
                        page.locator(".assistant .notice .personal-info-result")
                    ).to_contain_text("我叫聊天合成同学")
                    assert len(client.get("/api/v1/memories").json()) == count
                    page.reload()
                    page.locator(".assistant").get_by_role(
                        "button", name="确认继续", exact=True
                    ).click()
                    expect(page.locator(".assistant .run").last).to_have_attribute(
                        "data-status", "completed"
                    )
                    expect(
                        page.locator(".assistant .run").last.get_by_role(
                            "link", name="已保存 · 查看我的资料", exact=True
                        )
                    ).to_be_visible()
                    assert len(client.get("/api/v1/memories").json()) == count + 1
                    assistant.get_by_label("消息", exact=True).fill(
                        "补充到资料：这条拒绝保存的内容不应写入。"
                    )
                    assistant.get_by_role("button", name="发送", exact=True).click()
                    expect(page.locator(".assistant .run").last).to_have_attribute(
                        "data-status", "waiting_confirmation"
                    )
                    page.locator(".assistant").get_by_role(
                        "button", name="拒绝", exact=True
                    ).click()
                    expect(page.locator(".assistant .run").last).to_have_attribute(
                        "data-status", "completed"
                    )
                    assert len(client.get("/api/v1/memories").json()) == count + 1
                    assistant.get_by_label("消息", exact=True).fill(
                        "修改档案：把我的专业更新为软件工程。"
                    )
                    assistant.get_by_role("button", name="发送", exact=True).click()
                    expect(page.locator(".assistant .run").last).to_have_attribute(
                        "data-status", "waiting_confirmation"
                    )
                    expect(
                        page.locator(".assistant .notice .personal-info-result")
                    ).to_contain_text("软件工程")
                    assert client.get("/api/v1/profile").json()["major"] == "计算机"
                    page.locator(".assistant").get_by_role(
                        "button", name="确认继续", exact=True
                    ).click()
                    expect(page.locator(".assistant .run").last).to_have_attribute(
                        "data-status", "completed"
                    )
                    assert client.get("/api/v1/profile").json()["major"] == "软件工程"
                    assistant.get_by_label("消息", exact=True).fill(
                        "添加项目经历：2025年用 Python 编写数据清洗脚本。"
                    )
                    assistant.get_by_role("button", name="发送", exact=True).click()
                    expect(page.locator(".assistant .run").last).to_have_attribute(
                        "data-status", "waiting_confirmation"
                    )
                    page.set_viewport_size({"width": 1280, "height": 900})
                    page.get_by_role("button", name="收起侧边栏", exact=True).click()
                    expect(
                        page.locator(".assistant .notice .personal-info-result")
                    ).to_contain_text("聊天合成项目")
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                    switch_language(page, "en-US")
                    expect(page.locator(".assistant .notice")).to_contain_text(
                        "Add this experience to My information"
                    )
                    expect(
                        page.locator(".assistant .notice .personal-info-result")
                    ).to_contain_text("Not independently verified")
                    page.locator(".assistant").get_by_role(
                        "button", name="Confirm and continue", exact=True
                    ).click()
                    expect(page.locator(".assistant .run").last).to_have_attribute(
                        "data-status", "completed"
                    )
                    assert len(client.get("/api/v1/experiences").json()) == 1
                    switch_language(page, "zh-CN")
                    page.set_viewport_size({"width": 1440, "height": 1000})
                    page.locator(".assistant .run").last.get_by_role(
                        "link", name="已保存 · 查看我的资料", exact=True
                    ).click()
                    expect(
                        page.get_by_role("tab", name="材料与经历", exact=True)
                    ).to_have_attribute("aria-selected", "true")
                    note = (
                        page.locator(".collection > details.panel")
                        .filter(
                            has=page.get_by_text("我叫聊天合成同学，正在学习 Python。", exact=True)
                        )
                        .last
                    )
                    if note.get_attribute("open") is None:
                        note.locator("summary").first.click()
                    expect(
                        note.locator("article").get_by_text(
                            "我叫聊天合成同学，正在学习 Python。", exact=True
                        )
                    ).to_be_visible()
                    expect(note.locator("form")).not_to_be_visible()
                    note.get_by_text("编辑文字资料", exact=True).click()
                    expect(note.get_by_label("内容", exact=False)).to_have_value(
                        "我叫聊天合成同学，正在学习 Python。"
                    )
                    note.get_by_text("编辑文字资料", exact=True).click()
                    assert client.get("/api/v1/resume-facts").json() == []
                    page.get_by_role("navigation").get_by_role(
                        "link", name="学习", exact=True
                    ).click()
                    page.get_by_text("生成学习计划", exact=True).click()
                    plan_form = (
                        page.get_by_text("生成学习计划", exact=True)
                        .locator("..")
                        .locator("form")
                        .first
                    )
                    plan_form.get_by_role("checkbox", name="后端实习生").check()
                    plan_form.get_by_label("开始日期", exact=False).fill("2026-10-27")
                    plan_form.get_by_role("button", name="确认保存").click()
                    expect(page.get_by_text("确认发布计划", exact=True)).to_be_visible()
                    checks = page.locator('.task-check input[type="checkbox"]')
                    expect(checks.first).to_be_disabled()
                    publish = page.locator("form").filter(
                        has=page.get_by_role("button", name="确认发布计划")
                    )
                    publish.get_by_label("我确认所选资料和此次操作").check()
                    publish.get_by_role("button", name="确认发布计划").click()
                    expect(checks.first).to_be_enabled()
                    tasks = page.locator(".task-item")
                    first = tasks.first
                    expect(first.locator(".panel-content")).not_to_be_visible()
                    expect(checks.nth(1)).to_be_disabled()
                    original = client.get("/api/v1/plans").json()[0]
                    task_path = f"/api/v1/plans/{original['id']}/tasks/{original['tasks'][0]['id']}"
                    checks.first.check()
                    expect(first.get_by_role("button", name="保存并完成")).to_be_disabled()
                    first.get_by_role("button", name="取消", exact=True).click()
                    expect(checks.first).not_to_be_checked()
                    assert client.get("/api/v1/plans").json()[0]["version"] == original["version"]

                    # A completion failure preserves the actual started state and typed outcome.
                    def fail_complete(route):
                        if route.request.post_data_json.get("action") == "complete":
                            route.fulfill(
                                status=503,
                                content_type="application/json",
                                body=json.dumps({"message": "暂时无法保存成果"}),
                            )
                        else:
                            route.continue_()

                    page.route("**" + task_path, fail_complete)
                    checks.first.check()
                    outcome = first.get_by_label("记录这项任务的学习成果", exact=True)
                    outcome.fill("完成官方基础练习，并核对了运行结果。")
                    first.get_by_role("button", name="保存并完成").click()
                    expect(page.locator('.task-list > [role="status"]')).to_contain_text(
                        "暂时无法保存成果"
                    )
                    expect(outcome).to_have_value("完成官方基础练习，并核对了运行结果。")
                    started = client.get("/api/v1/plans").json()[0]
                    assert started["tasks"][0]["status"] == "running"
                    assert started["version"] == original["version"] + 1
                    expect(page.get_by_role("progressbar")).to_have_attribute("value", "0")
                    page.unroute("**" + task_path, fail_complete)
                    first.get_by_role("button", name="保存并完成").click()
                    expect(first).to_have_class("task-item is-complete")
                    expect(page.get_by_role("progressbar")).to_have_attribute("value", "1")
                    expect(checks.nth(1)).to_be_enabled()
                    saved_plan = client.get("/api/v1/plans").json()[0]
                    assert saved_plan["version"] == original["version"] + 2
                    assert (
                        saved_plan["tasks"][0]["outcome"] == "完成官方基础练习，并核对了运行结果。"
                    )
                    page.reload()
                    expect(checks.first).to_be_checked()
                    expect(checks.first).to_be_disabled()
                    page.set_viewport_size({"width": 1280, "height": 812})
                    switch_language(page, "en-US")
                    expect(page.locator(".plan-progress")).to_contain_text("1 /")
                    expect(checks.nth(1)).to_be_enabled()
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                    switch_language(page, "zh-CN")
                    page.set_viewport_size({"width": 1440, "height": 1000})
                    # Prepare factual material once and reuse it in the React career form.
                    fact = client.post(
                        "/api/v1/resume-facts",
                        json={
                            "kind": "name",
                            "source_type": "manual",
                            "text": "合成同学",
                            "confirmed": True,
                        },
                    ).json()
                    assert fact.get("id"), fact
                    page.get_by_role("navigation").get_by_role(
                        "link", name="AI 助手", exact=True
                    ).click()
                    assistant = page.locator(".assistant")
                    assistant.get_by_role("button", name="新建对话", exact=True).click()
                    assistant.get_by_label("消息", exact=True).fill("创建聊天简历")
                    assistant.get_by_role("button", name="发送", exact=True).click()
                    expect(assistant.locator(".run").last).to_have_attribute(
                        "data-status", "waiting_confirmation"
                    )
                    expect(assistant.locator(".notice .hiring-result")).to_contain_text("合成同学")
                    assert client.get("/api/v1/resumes").json() == []
                    page.reload()
                    page.set_viewport_size({"width": 1280, "height": 812})
                    switch_language(page, "en-US")
                    expect(assistant.locator(".notice")).to_contain_text(
                        "Create a resume from these confirmed facts."
                    )
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                    assistant.get_by_role("button", name="Confirm and continue", exact=True).click()
                    expect(assistant.locator(".run").last).to_have_attribute(
                        "data-status", "completed"
                    )
                    expect(
                        assistant.get_by_role("link", name="Saved · View resume")
                    ).to_be_visible()
                    saved_resumes = client.get("/api/v1/resumes").json()
                    assert len(saved_resumes) == 1 and saved_resumes[0]["title"] == "聊天创建的简历"
                    assistant.get_by_role("link", name="Saved · View resume").click()
                    expect(
                        page.get_by_role("tabpanel").get_by_text("聊天创建的简历", exact=True).first
                    ).to_be_visible()
                    switch_language(page, "zh-CN")
                    page.set_viewport_size({"width": 1440, "height": 1000})
                    # This fixture is written outside the UI. Reload to refresh the
                    # shared reference options before selecting it in the resume form.
                    page.reload()
                    page.goto(base + "/app/career")
                    page.get_by_role("tab", name="简历与材料", exact=True).click()
                    page.get_by_role("tabpanel").locator("summary").get_by_text(
                        "创建简历", exact=True
                    ).click()
                    resume_form = (
                        page.get_by_role("tabpanel")
                        .locator("summary")
                        .get_by_text("创建简历", exact=True)
                        .locator("..")
                        .locator("form")
                        .first
                    )
                    resume_form.get_by_label("名称", exact=False).fill("后端实习简历")
                    resume_form.get_by_role("combobox", name="岗位", exact=True).select_option(
                        index=1
                    )
                    resume_form.get_by_role("checkbox", name="合成同学").check()
                    resume_form.get_by_label("我确认所选资料和此次操作").check()
                    resume_form.get_by_role("button", name="确认保存").click()
                    expect(page.get_by_text("后端实习简历", exact=True).first).to_be_visible()
                    page.get_by_role("tab", name="申请记录", exact=True).click()
                    page.get_by_text("添加申请", exact=True).click()
                    page.get_by_text("准备申请", exact=True).click()
                    application = (
                        page.get_by_text("准备申请", exact=True).locator("..").locator("form")
                    )
                    application.get_by_role("combobox", name="岗位", exact=True).select_option(
                        index=1
                    )
                    application.get_by_role("combobox", name="简历版本", exact=True).select_option(
                        index=1
                    )
                    application.get_by_label("我确认所选资料和此次操作").check()
                    with page.expect_response(
                        lambda r: (
                            r.request.method == "POST" and r.url.endswith("/api/v1/applications")
                        )
                    ) as write:
                        application.get_by_role("button", name="确认保存", exact=True).click()
                    assert write.value.status == 201, write.value.text()
                    expect(page.locator(".feedback")).to_have_text("已保存")
                    assert len(client.get("/api/v1/applications").json()) == 1
                    page.get_by_role("tab", name="面试", exact=True).click()
                    page.get_by_text("开始文字面试", exact=True).click()
                    interview = (
                        page.get_by_text("开始文字面试", exact=True).locator("..").locator("form")
                    )
                    interview.get_by_role("combobox", name="岗位", exact=True).select_option(
                        index=1
                    )
                    interview.get_by_role("combobox", name="简历版本", exact=True).select_option(
                        index=1
                    )
                    interview.get_by_label("我确认所选资料和此次操作").check()
                    with page.expect_response(
                        lambda r: (
                            r.request.method == "POST" and r.url.endswith("/api/v1/interviews")
                        )
                    ) as write:
                        interview.get_by_role("button", name="确认保存", exact=True).click()
                    assert write.value.status == 201, write.value.text()
                    expect(page.locator(".feedback")).to_have_text("已保存")
                    page.locator(".collection > details.panel > summary").first.click()
                    for _ in range(12):
                        sessions = client.get("/api/v1/interviews").json()
                        if sessions and sessions[0]["status"] == "completed":
                            break
                        answer = page.locator(".collection form").first
                        expect(answer.get_by_label("题目", exact=True)).to_have_value(
                            sessions[0]["current_question"]["id"]
                        )
                        answer.get_by_role("checkbox", name="已跳过", exact=True).check()
                        with page.expect_response(
                            lambda r: (
                                r.request.method == "GET" and r.url.endswith("/api/v1/interviews")
                            )
                        ):
                            with page.expect_response(
                                lambda r: r.request.method == "POST" and r.url.endswith("/answers")
                            ) as write:
                                answer.get_by_role("button", name="确认保存", exact=True).click()
                            assert write.value.status == 200, write.value.text()
                        expect(page.locator(".feedback")).to_have_text("已保存")
                        if (
                            page.locator(".collection > details.panel").first.get_attribute("open")
                            is None
                        ):
                            page.locator(".collection > details.panel > summary").first.click()
                    else:
                        raise AssertionError("Interview did not complete")
                    page.get_by_role("button", name="生成面试复盘", exact=True).click()
                    review = page.locator(".collection form").first
                    review.get_by_label("状态", exact=True).select_option("confirmed")
                    review.get_by_label("摘要", exact=False).fill(
                        "本次为合成练习，跳过题目不证明掌握。"
                    )
                    review.get_by_label("我确认所选资料和此次操作").check()
                    review.get_by_role("button", name="确认保存", exact=True).click()
                    expect(page.locator(".feedback")).to_have_text("已保存")
                    # Route refresh, all five sections, desktop layout and locale.
                    for route, heading in [
                        ("today", "AI 助手"),
                        ("goals", "目标与岗位"),
                        ("learn", "学习"),
                        ("career", "求职"),
                        ("profile", "我的资料"),
                    ]:
                        page.goto(base + "/app/" + route)
                        expect(page.get_by_role("heading", level=1)).to_have_text(heading)
                        page.set_viewport_size({"width": 1280, "height": 900})
                        assert page.evaluate(
                            "document.documentElement.scrollWidth <= innerWidth"
                        ), route
                        page.set_viewport_size({"width": 1440, "height": 1000})
                    switch_language(page, "en-US")
                    expect(page.get_by_role("heading", level=1)).to_have_text("My information")
                    page.keyboard.press("Tab")
                    assert page.evaluate("document.activeElement !== document.body")
                    page.goto(base + "/app/#collaboration")
                    expect(
                        page.get_by_text("This feature is deferred.", exact=False)
                    ).to_be_visible()
                    # Delete an inactive chat, then the current chat; cancellation
                    # and confirmation must preserve the other chat and business data.
                    page.set_viewport_size({"width": 1440, "height": 900})
                    page.goto(base + "/app/today")
                    untouched_ids = {
                        item["id"] for item in client.get("/api/v1/workflows/conversations").json()
                    } - {selected_chat, extra_id}
                    page.locator(
                        f'.conversation-item[data-conversation-id="{selected_chat}"]'
                    ).click()
                    expect(page.locator(".assistant .run")).to_have_count(6)
                    page.wait_for_load_state("networkidle")
                    draft = page.get_by_label("Message", exact=True)
                    draft.fill("Keep this draft while deleting another conversation")
                    expect(draft).to_have_value(
                        "Keep this draft while deleting another conversation"
                    )
                    inactive = page.locator(".conversation-row").filter(
                        has=page.locator(f'.conversation-item[data-conversation-id="{extra_id}"]')
                    )
                    page.once("dialog", lambda dialog: dialog.dismiss())
                    inactive.get_by_role("button", name="Delete conversation:").click()
                    expect(
                        page.locator(f'.conversation-item[data-conversation-id="{extra_id}"]')
                    ).to_have_count(1)
                    page.once("dialog", lambda dialog: dialog.accept())
                    with page.expect_response(
                        lambda response: response.request.method == "DELETE"
                    ) as removed:
                        inactive.get_by_role("button", name="Delete conversation:").click()
                    assert removed.value.status == 204
                    expect(
                        page.locator(f'.conversation-item[data-conversation-id="{extra_id}"]')
                    ).to_have_count(0)
                    expect(draft).to_have_value(
                        "Keep this draft while deleting another conversation"
                    )
                    expect(page.locator(".assistant .run")).to_have_count(6)
                    active = page.locator(".conversation-row").filter(
                        has=page.locator(
                            f'.conversation-item[data-conversation-id="{selected_chat}"]'
                        )
                    )
                    page.once("dialog", lambda dialog: dialog.accept())
                    active.get_by_role("button", name="Delete conversation:").click()
                    expect(page.locator(".assistant .run")).to_have_count(0)
                    expect(page.locator(".chat-welcome")).to_be_visible()
                    page.reload()
                    expect(page.locator(".conversation-item")).to_have_count(len(untouched_ids))
                    assert (
                        client.get(
                            f"/api/v1/workflows/conversations/{selected_chat}/runs"
                        ).status_code
                        == 404
                    )
                    assert client.get("/api/v1/plans").json()
                    assert client.get("/api/v1/resumes").json()
                    # The same delete control stays reachable on desktop and in Chinese.
                    desktop = client.post(
                        "/api/v1/workflows/conversations",
                        headers={"Idempotency-Key": "desktop-deletion"},
                        json={"title": "桌面删除测试"},
                    ).json()
                    page.set_viewport_size({"width": 1280, "height": 812})
                    page.reload()
                    switch_language(page, "zh-CN")
                    expect(page.locator(".chat-history")).to_be_visible()
                    desktop_row = page.locator(".conversation-row").filter(
                        has=page.locator(
                            f'.conversation-item[data-conversation-id="{desktop["id"]}"]'
                        )
                    )
                    expect(
                        desktop_row.get_by_role("button", name="删除对话：桌面删除测试")
                    ).to_be_visible()
                    page.once("dialog", lambda dialog: dialog.accept())
                    desktop_row.get_by_role("button", name="删除对话：桌面删除测试").click()
                    expect(page.locator(".conversation-item")).to_have_count(len(untouched_ids))
                    page.keyboard.press("Escape")
                    assert_today_layout(page)
                    for _ in range(100):
                        with Session(engine) as db:
                            pending = db.scalar(
                                select(growth.GrowthRecord.id).where(
                                    growth.GrowthRecord.kind == "conversation_cleanup"
                                )
                            )
                        if pending is None:
                            break
                        time.sleep(0.05)
                    assert pending is None, "Conversation storage cleanup did not finish"
                    assert {
                        item["id"] for item in client.get("/api/v1/workflows/conversations").json()
                    } == untouched_ids
                    assert_confirmation_feedback(page, client, base)
                    assert_information_management(page, client, base)
                    assert_system_review_dates(browser, base)
                    browser.close()
                assert not errors, errors
                assert fact.get("id")
                print(
                    "React browser passed: growth/career, drafts, graph/SSE, "
                    "five routes, 1280/1366/1440/1920px, locale, keyboard, "
                    "chat sidebar/search/long titles and local origins"
                )
        finally:
            stopped.set()
            if worker.is_alive():
                worker.join(timeout=10)
            server.should_exit = True
            api.join(timeout=10)
            engine.dispose()
            chat_model.gateway = original_gateway
            if api.is_alive() or worker.is_alive():
                raise RuntimeError("Temporary services did not stop")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--information-only", action="store_true")
    parser.add_argument("--confirmation-only", action="store_true")
    parser.add_argument("--navigation-only", action="store_true")
    parser.add_argument("--journey-only", action="store_true")
    parser.add_argument("--markdown-failure-only", action="store_true")
    parser.add_argument("--asset-loading-only", action="store_true")
    parser.add_argument("--followup-only", action="store_true")
    parser.add_argument("--learning-only", action="store_true")
    args = parser.parse_args()
    run(
        information_only=args.information_only,
        confirmation_only=args.confirmation_only,
        navigation_only=args.navigation_only,
        journey_only=args.journey_only,
        markdown_failure_only=args.markdown_failure_only,
        asset_loading_only=args.asset_loading_only,
        followup_only=args.followup_only,
        learning_only=args.learning_only,
    )
