"""Disposable bilingual collaboration, sources and progress checks with real leased tasks."""

import socket
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import uvicorn
from alembic import command
from alembic.config import Config
from browser_identity import connect_browser
from browser_v4_locale import MISMARKED
from fastapi.testclient import TestClient
from playwright.sync_api import expect, sync_playwright
from sqlalchemy.orm import Session

from careerpilot import growth, notifications
from careerpilot.api import create_app
from careerpilot.cli import provision
from careerpilot.domain import Principal
from careerpilot.persistence import User, make_engine
from careerpilot.runtime import run_once
from careerpilot.seed import seed


def main():
    with tempfile.TemporaryDirectory(prefix="careerpilot-community-browser-") as scratch:
        url = "sqlite:///" + (Path(scratch) / "browser.db").as_posix()
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
            with (
                TestClient(create_app(url, local_only=False)) as client,
                sync_playwright() as playwright,
            ):
                browser = playwright.chromium.launch()
                try:
                    for width in (1440, 375):
                        _, token = provision(engine, f"Synthetic community locale {width}")
                        records = prepare(client, token, width, engine)
                        check_width(browser, port, token, width, records, engine, client)
                finally:
                    browser.close()
            print(
                "Chromium community locale: sharing/preview invalidation/public revalidation/"
                "source comparison/reports/notices/real review worker/originals/"
                "snapshots/keyboard/late responses at 1440/375 passed"
            )
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            engine.dispose()


def prepare(client, token, width, engine):
    headers = {"Authorization": "Bearer " + token}
    bob_id, bob_token = provision(engine, f"Synthetic community peer {width}")

    def post(path, body, *, version=None, key=None):
        extra = {"If-Match": f'"{version}"'} if version is not None else {}
        if key:
            extra["Idempotency-Key"] = key
        response = client.post("/api/v1" + path, headers={**headers, **extra}, json=body)
        assert response.is_success, response.text
        return response.json()

    response = client.put(
        "/api/v1/profile",
        headers={**headers, "If-Match": '"0"'},
        json={
            "education": "bachelor",
            "major": "计算机",
            "academic_year": "大三",
            "graduation_date": "2027-06-30",
            "preferred_cities": ["上海"],
            "weekly_minutes": 600,
            "timezone": "America/New_York",
            "confirmed": True,
        },
    )
    assert response.is_success, response.text
    job = post("/jobs/import", {"content": "岗位：本人岗位\n要求：Python\n要求：SQL"})
    plan = post("/plans", {"job_ids": [job["id"]], "start_date": "2026-10-25"})
    plan = post(f"/plans/{plan['id']}/publish", {"confirmed": True}, version=plan["version"])
    owner_id = client.get("/api/v1/workspaces", headers=headers).json()["user_id"]
    with Session(engine) as db, db.begin():
        user = db.get(User, owner_id)
        asset = growth.save(
            db,
            Principal(user.id, user.tenant_id),
            "project_asset",
            {
                "title": f"本人作品 <b>{width}</b>",
                "content": "明确同意的原文 <img src=x onerror=alert(1)>",
                "status": "active",
                "private_note": "PRIVATE-NOTE-NEVER-SHARE",
            },
            "browser",
        )
    return {
        "asset": asset,
        "bob_id": bob_id,
        "bob_token": bob_token,
        "plan": plan,
        "post": post,
        "headers": headers,
    }


def connect(page, base, token):
    page.goto(base + "#collaboration")
    page.locator("#ui-locale").select_option("en-US")
    connect_browser(page, token)
    expect(page.locator("#space-create")).to_be_visible()


def open_space(page, key):
    button = page.locator(f'[data-space-view="{key}"]')
    details = button.locator("xpath=ancestor::details").first
    if not details.evaluate("el=>el.open"):
        details.locator("summary").first.click()
    button.click()


def originals(page):
    assert page.evaluate(MISMARKED) == [], page.evaluate(MISMARKED)
    assert page.locator("#view b,#view img").count() == 0
    assert page.evaluate("document.documentElement.scrollWidth<=innerWidth+1"), page.evaluate(
        "document.documentElement.scrollWidth"
    )


def check_width(browser, port, token, width, records, engine, client):
    context = browser.new_context(
        viewport={"width": width, "height": 1000}, timezone_id="Pacific/Auckland"
    )
    peer_context = browser.new_context(viewport={"width": width, "height": 1000})
    page, peer = context.new_page(), peer_context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    peer.on("pageerror", lambda error: errors.append(str(error)))
    base = f"http://127.0.0.1:{port}/app/"

    def get(path):
        response = client.get("/api/v1" + path, headers=records["headers"])
        assert response.is_success, response.text
        return response.json()

    records["get"] = get
    try:
        connect(page, base, token)
        connect(peer, base, records["bob_token"])
        create = page.locator("#space-create")
        create.get_by_label("Space name", exact=True).fill("本人空间 <b>原文</b>")
        with page.expect_response(
            lambda r: r.url.endswith("/workspaces") and r.request.method == "POST"
        ) as created:
            create.get_by_role("button", name="Create private collaboration space").click()
        space = created.value.json()
        open_space(page, space["id"])
        expect(page.locator("#space-shares h3")).to_be_focused()
        invite = page.locator(f'.space-invite[data-space="{space["id"]}"]')
        invite.get_by_label("Recipient account ID", exact=True).fill(records["bob_id"])
        with page.expect_response("**/workspaces/*/invitations") as invited:
            invite.get_by_role("button", name="Generate invitation for this recipient").click()
        invitation = invited.value.json()
        expect(page.locator("#space-ticket")).to_contain_text("Valid for 24 hours and shown once")
        originals(page)
        acceptance = peer.locator("#space-accept")
        acceptance.get_by_label("Invitation ID", exact=True).fill(invitation["id"])
        acceptance.get_by_label("Invitation token", exact=True).fill(invitation["ticket"])
        acceptance.get_by_role("button", name="Confirm joining space").click()
        expect(peer.locator(f'[data-space-view="{space["id"]}"]')).to_be_attached()
        preview_form = page.locator("#share-preview-form")
        preview_form.get_by_label("Project, artifact or plan", exact=True).select_option(
            "project_asset:" + records["asset"]["id"]
        )
        preview_form.locator('[name="fields"][value="title"]').check()
        pending = []
        pattern = "**/api/v1/object-shares/preview"
        page.route(pattern, lambda route: pending.append(route))
        with page.expect_request(pattern):
            preview_form.get_by_role("button", name="Preview selected content").click()
        preview_form.locator('[name="fields"][value="content"]').check()
        old = pending.pop()
        old.fulfill(response=old.fetch())
        page.unroute(pattern)
        expect(page.locator("#share-confirm-form")).to_have_count(0)
        preview_form.get_by_role("button", name="Preview selected content").click()
        preview = page.locator("#share-preview")
        expect(preview.get_by_role("heading")).to_be_focused()
        expect(preview).to_contain_text(records["asset"]["content"])
        expect(preview).not_to_contain_text("PRIVATE-NOTE")
        confirm = preview.locator("#share-confirm-form")
        confirm.get_by_label("Collaboration space", exact=True).select_option(space["id"])
        confirm.locator('[name="public"]').check()
        with page.expect_response(
            lambda r: r.url.endswith("/object-shares") and r.request.method == "POST"
        ) as shared:
            confirm.get_by_role(
                "button", name="Confirm selected fields and recipients, then share"
            ).click()
        share = shared.value.json()
        expect(page.locator("#space-ticket")).to_contain_text(share["ticket"])
        public = context.new_page()
        public.on("pageerror", lambda error: errors.append(str(error)))
        public.goto(base + "share.html#" + share["id"] + ":" + share["ticket"])
        expect(public.locator("#shared-content")).to_contain_text(records["asset"]["content"])
        expect(public.locator("#share-title")).to_be_focused()
        expect(public.locator("#share-status")).to_contain_text("not independently certified")
        assert not public.url.split("share.html")[1]
        assert not public.evaluate(
            "ticket=>Object.values(localStorage).some(v=>v.includes(ticket))", share["ticket"]
        )
        assert public.locator("img").count() == 0
        assert public.evaluate("document.documentElement.scrollWidth<=innerWidth+1")
        open_space(peer, space["id"])
        peer.locator("#space-shares > details > summary").click()
        feedback = peer.locator(".share-comment")
        feedback.get_by_label("Feedback", exact=True).fill("本人反馈 <b>保留</b>")
        feedback.get_by_role("button", name="Submit feedback").click()
        expect(peer.locator("#notice")).to_contain_text("Feedback saved")
        second = records["post"]("/workspaces", {"name": "空的另一个空间", "confirmed": True})
        page.locator("#ui-locale").select_option("zh-CN")
        page.locator("#ui-locale").select_option("en-US")
        pending = []
        pattern = "**/api/v1/object-shares?workspace_id=" + space["id"]
        page.route(pattern, lambda route: pending.append(route))
        with page.expect_request(pattern):
            open_space(page, space["id"])
        open_space(page, second["id"])
        expect(page.locator("#space-shares")).to_contain_text("No active shares")
        old = pending.pop()
        old.fulfill(response=old.fetch())
        page.unroute(pattern)
        expect(page.locator("#space-shares")).not_to_contain_text(records["asset"]["content"])
        open_space(page, space["id"])
        page.locator("#space-shares > details > summary").click()
        expect(page.locator("#space-shares")).to_contain_text("本人反馈 <b>保留</b>")
        page.get_by_text("Hide this feedback", exact=True).click()
        moderation = page.locator(".share-moderation")
        moderation.get_by_label("Reason for hiding", exact=True).fill("本人处理原因")
        moderation.get_by_role("button", name="Confirm hiding this feedback").click()
        expect(page.locator("#notice")).to_have_text("Feedback hidden.")
        page.locator(f'[data-share-revoke="{share["id"]}"]').click()
        expect(page.locator("#notice")).to_have_text("Access revoked.")
        public.locator("#share-locale").select_option("zh-CN")
        expect(public.locator("#shared-content")).to_have_text("")
        expect(public.locator("#share-status")).to_contain_text("撤销")
        public.locator("#share-locale").select_option("en-US")
        expect(public.locator("#share-status")).to_contain_text("was revoked")
        public.close()
        open_space(page, space["id"])
        page.locator(
            f'[data-space-remove="{space["id"]}"][data-member="{records["bob_id"]}"]'
        ).click()
        expect(page.locator("#notice")).to_have_text("Access revoked.")
        open_space(peer, space["id"])
        expect(peer.locator("#space-shares")).to_have_text("")
        originals(page)
        check_sources(page, records)
        check_progress(page, records, engine)
        assert not errors, errors
    finally:
        peer_context.close()
        context.close()


def check_sources(page, records):
    page.evaluate("location.hash='sources'")
    get = records["get"]
    expect(page.locator("#sources-search").get_by_label("Platform", exact=True)).to_be_visible()
    expect(page.locator("#source-restrictions")).to_contain_text("were not searched directly")
    source_ids = []
    for i in (1, 2):
        page.locator("#source-import > summary").click()
        form = page.locator("#source-import-form")
        form.get_by_label("Title", exact=True).fill(f"本人来源 <b>{i}</b>")
        form.get_by_label("Original URL", exact=True).fill(
            f"https://example{i}.com/article?tracking=remove"
        )
        form.get_by_label("Authorized processing scope", exact=True).select_option("local_excerpt")
        form.get_by_label("Excerpt, article or video summary", exact=True).fill(
            "本人引用 <b>保留</b>"
        )
        form.get_by_label("Claim topic (optional)", exact=True).fill("本人截止日期")
        form.get_by_label("Claim value entered by you", exact=True).fill(f"2026-11-0{i}")
        form.get_by_label("Original publication date (optional)", exact=True).fill("2025-12-31")
        form.locator('[name="rights"]').check()
        with page.expect_response("**/api/v1/source-documents") as imported:
            form.get_by_role("button", name="Confirm source import").click()
        source_ids.append(imported.value.json()["id"])
        expect(page.locator("#notice")).to_contain_text("Source imported privately")
    for key in source_ids:
        page.locator(f'[data-source-id="{key}"] [name="source_compare"]').check()
    page.get_by_role("button", name="Compare 2–4 selected sources").focus()
    page.keyboard.press("Enter")
    comparison = page.locator("#source-comparison")
    expect(comparison.get_by_role("heading").first).to_be_focused()
    expect(comparison).to_contain_text("Differences found; check them")
    expect(comparison).to_contain_text("different domains do not guarantee independent sources")
    expect(comparison).to_contain_text("Dec 31, 2025")
    expect(comparison).to_contain_text("本人引用 <b>保留</b>")
    canonical = get("/source-documents")
    originals(page)
    pending = []
    pattern = "**/api/v1/source-discovery?*"
    page.route(pattern, lambda route: pending.append(route))
    search = page.locator("#sources-search")
    search.get_by_label("Keywords", exact=True).fill("Python")
    with page.expect_request(pattern):
        search.get_by_role("button", name="Search local reference index").click()
    search.get_by_label("Keywords", exact=True).fill("本人来源")
    with page.expect_request(pattern):
        search.get_by_role("button", name="Search local reference index").click()
    latest = pending.pop()
    latest.fulfill(response=latest.fetch())
    expect(page.locator("#source-results [data-source-id]")).to_have_count(2)
    pending.pop().fulfill(status=503, json={"message": "stale source failure"})
    page.unroute(pattern)
    expect(page.locator("#source-results [data-source-id]")).to_have_count(2)
    expect(page.locator("#notice")).not_to_contain_text("stale source failure")
    page.locator("#ui-locale").select_option("zh-CN")
    page.locator("#ui-locale").select_option("en-US")
    expect(page.locator("#sources-search")).to_be_attached()
    assert get("/source-documents") == canonical
    card = page.locator(f'[data-source-id="{source_ids[0]}"]')
    card.get_by_text("Delete my saved source", exact=True).click()
    card.get_by_role("button", name="Confirm deletion of this source record").click()
    expect(page.locator("#notice")).to_contain_text("source and its revisions were deleted")
    expect(card).to_have_count(0)
    originals(page)
    page.locator("#source-import > summary").click()
    form = page.locator("#source-import-form")
    form.get_by_label("Title", exact=True).fill("已确认保存的来源")
    form.get_by_label("Original URL", exact=True).fill("https://example.com/original")
    form.locator('[name="rights"]').check()
    pending = []
    pattern = "**/api/v1/source-documents"
    page.route(pattern, lambda route: pending.append(route))
    with page.expect_request(pattern):
        form.get_by_role("button", name="Confirm source import").click()
    form.get_by_label("Title", exact=True).fill("本人下一条未保存草稿")
    write = pending.pop()
    write.fulfill(response=write.fetch())
    page.unroute(pattern)
    expect(page.locator("#notice")).to_contain_text("Your current input was retained")
    expect(form.get_by_label("Title", exact=True)).to_have_value("本人下一条未保存草稿")


def check_progress(page, records, engine):
    page.evaluate("location.hash='progress'")
    get = records["get"]
    report = page.locator("#period-report-result")
    expect(report).to_contain_text("Zero denominator; conversion rate unknown")
    today = datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    expect(page.locator('#period-report-form [name="end"]')).to_have_value(today)
    log = page.locator("#study-log-form")
    log.locator("xpath=ancestor::details/summary").click()
    log.get_by_label("Minutes recorded by you", exact=True).fill("25")
    marker = "报表只读取本人记录，不执行岗位搜索、投递或能力认证。"
    log.get_by_label("Study note", exact=True).fill(marker)
    log.locator('[type="checkbox"]').check()
    log.get_by_role("button", name="Save study time").click()
    expect(report).to_contain_text("25 minutes")
    expect(report).to_contain_text("without independent timing")
    assert get("/study-logs")[0]["note"] == marker
    pending = []
    pattern = "**/api/v1/progress-report?*"
    page.route(pattern, lambda route: pending.append(route))
    form = page.locator("#period-report-form")
    form.get_by_label("Start date", exact=True).fill(today)
    with page.expect_request(pattern):
        form.get_by_role("button", name="View period statistics").click()
    form.get_by_label("Start date", exact=True).fill(today[:8] + "01")
    with page.expect_request(pattern):
        form.get_by_role("button", name="View period statistics").click()
    latest = pending.pop()
    latest.fulfill(response=latest.fetch())
    expect(report.get_by_role("heading").first).to_be_focused()
    pending.pop().fulfill(status=503, json={"message": "stale report failure"})
    page.unroute(pattern)
    expect(page.locator("#notice")).not_to_contain_text("stale report failure")
    page.locator("#personal-notification-settings > summary").click()
    settings = page.locator("#personal-notification-form")
    settings.get_by_label("Status", exact=True).select_option("active")
    settings.get_by_label("Time zone", exact=True).fill("America/New_York")
    settings.locator('[name="weekly_report"]').check()
    settings.get_by_label("Quiet start hour", exact=True).fill("0")
    settings.get_by_label("Quiet end hour", exact=True).fill("0")
    settings.locator('[type="checkbox"]').last.check()
    settings.get_by_role("button", name="Save growth notification settings").click()
    expect(page.locator("#notice")).to_contain_text("Email is not configured")
    config = get("/notification-settings")
    assert notifications.schedule_due(engine, now=config["next_runs"]["weekly_report"]) == 1
    page.locator("#ui-locale").select_option("zh-CN")
    page.locator("#ui-locale").select_option("en-US")
    expect(page.locator(".notice-item")).to_contain_text("Previous week growth report")
    canonical_notices = get("/notification-center")["items"]
    page.locator(".notice-item > summary").click()
    page.get_by_role("button", name="Mark this notification as read").click()
    expect(page.get_by_role("button", name="Mark this notification as read")).to_have_count(0)
    assert get("/notification-center")["items"][0]["content"] == canonical_notices[0]["content"]
    originals(page)
    support = page.locator("#support-ticket-form")
    support.locator("xpath=ancestor::details/summary").click()
    support.get_by_label("My private handling record", exact=True).fill("本人处理原文 <b>保留</b>")
    support.locator('[type="checkbox"]').check()
    support.get_by_role("button", name="Save handling request").click()
    expect(page.locator("#notice")).to_contain_text("operations queue sees metadata only")
    assert get("/support-tickets")[0]["details"] == "本人处理原文 <b>保留</b>"
    page.locator("#review-pipeline-create > summary").click()
    pipeline = page.locator("#review-pipeline-form")
    pipeline.get_by_label("Reference keywords", exact=True).fill("python")
    pipeline.locator('[type="checkbox"]').check()
    with page.expect_response(
        lambda r: r.url.endswith("/review-pipelines") and r.request.method == "POST"
    ) as created:
        pipeline.get_by_role("button", name="Create continuous review task").click()
    run = created.value.json()
    panel = page.locator(f'[data-review-run="{run["id"]}"]')
    expect(panel.get_by_role("heading").first).to_be_focused()
    assert run_once(engine)
    panel.get_by_role("button", name="Refresh task status").click()
    expect(panel).to_contain_text("Completed 3 / 3 steps")
    expect(panel).to_contain_text("Three fixed allowed steps")
    assert get(f"/plans/{records['plan']['id']}")["version"] == records["plan"]["version"]
    snapshot = get(f"/review-pipelines/{run['id']}")
    panel.get_by_role("button", name="Keep current plan", exact=True).click()
    page.locator("#ui-locale").select_option("zh-CN")
    page.locator("#ui-locale").select_option("en-US")
    expect(panel).to_contain_text("This preview was rejected")
    assert get(f"/review-pipelines/{run['id']}") == snapshot
    assert get("/skill-evidence") == []
    # A poll already in flight cannot append its result after navigating away.
    pending = []
    pattern = "**/api/v1/review-pipelines/" + run["id"]
    page.route(pattern, lambda route: pending.append(route))
    with page.expect_request(pattern):
        panel.get_by_role("button", name="Refresh task status").click()
    page.evaluate("location.hash='sources'")
    expect(page.locator("#sources-search")).to_be_visible()
    old = pending.pop()
    old.fulfill(response=old.fetch())
    page.unroute(pattern)
    expect(page.locator("[data-review-run]")).to_have_count(0)
    originals(page)


if __name__ == "__main__":
    main()
