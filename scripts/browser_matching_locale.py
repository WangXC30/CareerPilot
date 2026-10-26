"""Disposable bilingual matching/discovery checks with real API and leased tasks."""

import socket
import tempfile
import threading
import time
from pathlib import Path

import uvicorn
from alembic import command
from alembic.config import Config
from browser_v4_locale import MISMARKED
from fastapi.testclient import TestClient
from playwright.sync_api import expect, sync_playwright

from careerpilot.api import create_app
from careerpilot.cli import provision
from careerpilot.persistence import make_engine
from careerpilot.runtime import run_once
from careerpilot.seed import seed


def main():
    with tempfile.TemporaryDirectory(prefix="careerpilot-matching-browser-") as scratch:
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
            with TestClient(create_app(url)) as client, sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                try:
                    for width in (1440, 375):
                        _, token = provision(engine, f"Synthetic matching locale {width}")
                        records = prepare(client, token, width)
                        check_width(browser, port, token, width, records, engine)
                finally:
                    browser.close()
            print(
                "Chromium matching locale: eligibility/scores/originals/favorites/feedback/"
                "comparison/preferences/subscriptions/digests/timezones/keyboard/"
                "late recommendation responses/pending-input preservation at 1440/375 passed"
            )
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            engine.dispose()


def prepare(client, token, width):
    headers = {"Authorization": "Bearer " + token}
    response = client.put(
        "/api/v1/profile",
        headers={**headers, "If-Match": '"0"'},
        json={
            "education": "bachelor",
            "major": "本人原文 <b>计算机</b>",
            "academic_year": "大三",
            "graduation_date": "2027-06-30",
            "preferred_cities": ["上海"],
            "weekly_minutes": 600,
            "timezone": "America/Los_Angeles",
            "confirmed": True,
        },
    )
    assert response.status_code == 200
    headers = {**headers, "If-Match": '"0"'}
    assert (
        client.put(
            "/api/v1/matching-constraints",
            headers=headers,
            json={"available_months": 4, "confirmed": True},
        ).status_code
        == 200
    )
    jd = (
        f"岗位：本人岗位 <b>{width}</b>\n公司：本人企业 {width}\n地点：上海\n学历：本科及以上\n"
        "2027届\n实习时长：3个月\n工作类型：实习\n工作许可：不限\n资格：不限\n要求：Python"
    )
    records = []
    for text in (jd, jd.replace("上海", "北京"), "岗位：未知本人原文\n要求：Python"):
        response = client.post("/api/v1/jobs/import", headers=headers, json={"content": text})
        assert response.status_code == 201
        record = response.json()
        response = client.post(
            f"/api/v1/jobs/{record['id']}/status",
            headers={**headers, "If-Match": '"1"'},
            json={"status": "verified", "reason": "本人本地核对", "confirmed": True},
        )
        assert response.status_code == 200
        records.append(response.json())
    return records


def check_width(browser, port, token, width, records, engine):
    context = browser.new_context(
        viewport={"width": width, "height": 1000}, timezone_id="Pacific/Auckland"
    )
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        page.goto(f"http://127.0.0.1:{port}/app/#market")
        page.locator("#ui-locale").select_option("en-US")
        page.locator("#connect").click()
        page.locator("#token").fill(token)
        page.locator("#account-form").get_by_role("button", name="Connect", exact=True).click()
        expect(page.locator("#job-recommendations")).to_have_attribute("aria-busy", "false")
        first_id, failed_id, unknown_id = (record["id"] for record in records)
        first = page.locator(f'#job-matches [data-match-job="{first_id}"]')
        expect(first.locator(".tag")).to_have_text("Meets requirements")
        expect(
            page.locator(f'[data-match-group="excluded"] [data-match-job="{failed_id}"]')
        ).to_have_count(1)
        expect(
            page.locator(f'[data-match-group="needs_review"] [data-match-job="{unknown_id}"]')
        ).to_have_count(1)
        first.locator("summary").first.click()
        expect(first).to_contain_text("Confirmed education meets the minimum requirement")
        expect(first).to_contain_text("No current trusted project acceptance evidence")
        expect(first).to_contain_text(records[0]["fields"]["title"]["value"])
        assert first.locator("b").count() == 0
        assert page.evaluate(MISMARKED) == [], page.evaluate(MISMARKED)
        limits = page.locator("#matching-constraints-form")
        limits.get_by_label("Work authorizations held (comma-separated)", exact=True).fill(
            "本人许可"
        )
        limits.get_by_label("Confirm no work authorization", exact=True).check()
        limits.get_by_role("button", name="Confirm and save matching constraints").click()
        expect(page.locator("#notice")).to_contain_text("cannot both be selected")
        limits.get_by_label("Confirm no work authorization", exact=True).uncheck()
        limits.get_by_role("button", name="Confirm and save matching constraints").click()
        expect(page.locator("#notice")).to_contain_text("Constraints confirmed")
        limits.get_by_label("Confirm no required qualification", exact=True).check()
        limits.get_by_role("button", name="Confirm and save matching constraints").click()
        expect(page.locator("#notice")).to_contain_text("Constraints confirmed")
        first.locator("summary").first.click()
        expect(first).to_contain_text("Confirmed none")
        favorite_writes = []

        def hold_favorite(route):
            if route.request.method == "PUT":
                favorite_writes.append(route)
            else:
                route.continue_()

        page.route("**/api/v1/job-preferences", hold_favorite)
        with page.expect_request("**/api/v1/job-preferences"):
            first.get_by_role("button", name="Favorite job", exact=True).click()
        expect(first.get_by_role("button", name="Favorite job", exact=True)).to_be_disabled()
        assert len(favorite_writes) == 1
        write = favorite_writes.pop()
        write.fulfill(response=write.fetch())
        page.unroute("**/api/v1/job-preferences", hold_favorite)
        expect(page.locator("#saved-jobs")).to_contain_text(records[0]["fields"]["title"]["value"])
        first.locator('[name="compare_jobs"]').check()
        page.locator(f'#job-matches [data-match-job="{unknown_id}"] [name="compare_jobs"]').check()
        page.get_by_role("button", name="Compare selected jobs", exact=True).focus()
        page.keyboard.press("Enter")
        comparison = page.locator("#job-comparison")
        expect(comparison.get_by_role("heading")).to_be_focused()
        page.keyboard.press("Tab")
        expect(comparison.get_by_role("region")).to_be_focused()
        expect(comparison.get_by_role("columnheader", name="Condition", exact=True)).to_be_visible()
        expect(comparison).to_contain_text("Unknown")
        feedback = first.locator(".job-feedback-form")
        first.locator("details summary").last.click()
        feedback.get_by_label("Feedback", exact=True).select_option("like")
        marker = f"本人依据 <b>保留 {width}</b>"
        feedback.get_by_label("Basis or reason", exact=True).fill(marker)
        feedback.get_by_role("button", name="Confirm feedback").click()
        expect(page.locator("#notice")).to_contain_text("Feedback recorded")
        first.locator("details summary").last.click()
        first.get_by_role("button", name="View feedback history").click()
        expect(first.locator(".feedback-history")).to_contain_text(marker)
        assert first.locator(".feedback-history b").count() == 0
        first.get_by_role("button", name="Undo feedback", exact=True).click()
        expect(page.locator("#notice")).to_contain_text("Feedback undone")
        page.locator("#save-match-snapshot").click()
        expect(page.locator("#match-snapshot-result")).to_contain_text("Explanation saved")

        preferences = page.locator("#job-preferences-form")
        preferences.get_by_label("Blocked companies (exact names, one per line)").fill(
            records[0]["fields"]["company"]["value"]
        )
        preferences.get_by_role("button", name="Confirm and save job preferences").click()
        expect(first).to_have_count(0)
        preferences.get_by_label("Blocked companies (exact names, one per line)").fill("")
        preferences.get_by_role("button", name="Confirm and save job preferences").click()
        expect(first).to_have_count(1)
        # Hold the initial automatic recommendation, then request a different mode.
        page.evaluate("location.hash='dashboard'")
        expect(page.locator("#assistant-form")).to_be_visible()
        held = []
        page.route("**/api/v1/job-recommendations?*", lambda route: held.append(route))
        page.evaluate("location.hash='market'")
        mode = page.locator("#recommendation-mode-form")
        expect(mode).to_be_visible()
        mode.get_by_label("Recommendation mode", exact=True).select_option("entry")
        with page.expect_request("**/api/v1/job-recommendations?mode=entry"):
            mode.get_by_role("button", name="View this mode").click()
        expect(page.locator("#job-recommendations")).to_have_attribute("aria-busy", "true")
        assert len(held) == 2
        latest = held.pop()
        latest.fulfill(response=latest.fetch())
        expect(page.locator("#job-recommendations h3").first).to_have_text(
            "Recommendation mode: Entry"
        )
        old = held.pop()
        with page.expect_response(old.request.url):
            old.fulfill(status=503, json={"message": "Old request failed"})
        page.evaluate(
            "() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))"
        )
        expect(page.locator("#job-recommendations h3").first).to_have_text(
            "Recommendation mode: Entry"
        )
        expect(page.locator("#notice")).not_to_contain_text("Old request failed")
        page.unroute("**/api/v1/job-recommendations?*")
        preferences.locator('[name="enabled_modes"][value="entry"]').uncheck()
        preferences.get_by_role("button", name="Confirm and save job preferences").click()
        expect(page.locator("#job-recommendations")).to_contain_text("This mode is disabled")

        form = page.locator("#subscription-form")
        form.get_by_label("Subscription name", exact=True).fill(f"本人订阅 <b>{width}</b>")
        form.get_by_label("Keyword", exact=True).fill("本人岗位")
        form.get_by_label("Day of week", exact=True).select_option("6")
        form.get_by_label("Quiet start hour", exact=True).fill("0")
        form.get_by_label("Quiet end hour", exact=True).fill("0")
        form.get_by_role("button", name="Confirm and create subscription").click()
        card = page.locator(".subscription-card")
        expect(card).to_have_count(1)
        expect(card).to_contain_text("Sunday")
        expect(card).to_contain_text("America/Los_Angeles")
        expect(card).to_contain_text("No external recruiting platform search")
        clocks = page.evaluate(
            """() => [
              clockLabel(Date.parse('2026-11-01T08:30:00Z')/1000,'America/Los_Angeles'),
              clockLabel(Date.parse('2026-11-01T09:30:00Z')/1000,'America/Los_Angeles'),
              clockLabel(Date.parse('2026-12-31T23:30:00Z')/1000,'Asia/Shanghai'),
              clockLabel(Date.parse('2026-12-31T23:30:00Z')/1000,'America/Los_Angeles')
            ]"""
        )
        assert "GMT-7" in clocks[0] and "GMT-8" in clocks[1], clocks
        assert "Jan 1, 2027" in clocks[2] and "Dec 31, 2026" in clocks[3], clocks
        # A completed real task must not erase input entered while its response was pending.
        pending = []
        page.route("**/api/v1/job-subscriptions/*/runs", lambda route: pending.append(route))
        with page.expect_response("**/api/v1/job-subscriptions/*/check"):
            card.get_by_role("button", name="Check job library now").click()
        expect(page.locator("#notice")).to_contain_text("Check queued")
        form.get_by_label("Subscription name", exact=True).fill(marker)
        assert run_once(engine)
        assert len(pending) == 1
        run = pending.pop()
        run.fulfill(response=run.fetch())
        expect(page.locator("#notice")).to_contain_text("Your current input is preserved")
        expect(form.get_by_label("Subscription name", exact=True)).to_have_value(marker)
        page.unroute("**/api/v1/job-subscriptions/*/runs")
        page.locator("#refresh-discovery").click()
        notification = page.locator("#job-notifications .notification-card").first
        expect(notification).to_contain_text(
            "Incremental checks cover only your imported local job library"
        )
        notification.get_by_role("button", name="Mark as read").click()
        expect(notification.locator(".tag")).to_have_text("Read")
        card.get_by_role("button", name="View run history").click()
        expect(card.locator(".subscription-runs h3")).to_be_focused()
        expect(card.locator(".subscription-runs")).to_contain_text("Check completed")
        card.get_by_role("button", name="Edit configuration").click()
        expect(form.get_by_label("Subscription name", exact=True)).to_be_focused()
        form.get_by_label("Subscription name", exact=True).fill(f"本人改名 {width}")
        form.get_by_role("button", name="Confirm and save subscription configuration").click()
        expect(card).to_contain_text(f"本人改名 {width}")
        card.get_by_role("button", name="Pause subscription").click()
        expect(card.locator(".tag")).to_have_text("Paused")
        card.get_by_role("button", name="Resume subscription").click()
        expect(card.locator(".tag")).to_have_text("Active")
        card.get_by_role("button", name="Cancel subscription").click()
        expect(card.locator(".tag")).to_have_text("Cancelled")
        card.get_by_role("button", name="Resume subscription").click()
        page.locator("#ui-locale").select_option("zh-CN")
        expect(form.get_by_label("订阅名称", exact=True)).to_be_visible()
        expect(card).to_contain_text(f"本人改名 {width}")
        page.locator("#ui-locale").select_option("en-US")
        expect(form.get_by_label("Subscription name", exact=True)).to_be_visible()
        assert page.evaluate(MISMARKED) == [], page.evaluate(MISMARKED)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), (
            page.evaluate(
                """() => ({width:innerWidth,scroll:document.documentElement.scrollWidth,
              wide:[...document.querySelectorAll('body *')].filter(n => {
                const r=n.getBoundingClientRect(),style=getComputedStyle(n);
                return r.width && (r.right>innerWidth+1 ||
                  (style.overflowX==='visible' && n.scrollWidth>n.clientWidth+1));
              }).slice(0,12).map(n=>({tag:n.tagName,id:n.id,cls:n.className,
                 width:n.clientWidth,scroll:n.scrollWidth}))})"""
            )
        )
        page.evaluate("location.hash='explore'")
        expect(
            page.locator("#career-interests-form").get_by_role(
                "button", name="Confirm career interests"
            )
        ).to_be_visible()
        expect(page.locator("#career-comparison")).to_contain_text("Estimated foundational study")
        assert page.evaluate(MISMARKED) == [], page.evaluate(MISMARKED)
        assert not errors, errors
    finally:
        context.close()


if __name__ == "__main__":
    main()
