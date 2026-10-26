"""Real Chromium popup/callback and timeline UI, using a simulated OAuth provider."""

import os
import socket
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlsplit

import uvicorn
from alembic import command
from alembic.config import Config
from browser_v4_locale import MISMARKED
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from playwright.sync_api import expect, sync_playwright
from profile_history_contract import Provider
from study_contract import checked as ok

from careerpilot import github_binding
from careerpilot.api import create_app
from careerpilot.cli import provision
from careerpilot.persistence import make_engine
from careerpilot.seed import seed

PROFILE = {
    "education": "bachelor",
    "major": "计算机",
    "academic_year": "大三",
    "graduation_date": "2027-06-30",
    "preferred_cities": ["上海"],
    "weekly_minutes": 600,
    "confirmed": True,
}


def version(headers, value):
    return {**headers, "If-Match": f'"{value}"'}


def run(locale="zh-CN"):
    with tempfile.TemporaryDirectory(prefix="careerpilot-profile-") as directory:
        url = "sqlite:///" + (Path(directory) / "profile.db").as_posix()
        config = Config("alembic.ini")
        config.attributes["database_url"] = url
        command.upgrade(config, "head")
        engine = make_engine(url)
        seed(engine)
        _, token = provision(engine, "Timeline student")
        headers = {"Authorization": "Bearer " + token}
        with TestClient(create_app(url)) as client:
            ok(client.put("/api/v1/profile", headers=version(headers, 0), json=PROFILE))
            ok(
                client.put(
                    "/api/v1/profile",
                    headers=version(headers, 1),
                    json={**PROFILE, "weekly_minutes": 300},
                )
            )
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        fake = Provider()
        env = dict(
            zip(
                ("CAREERPILOT_" + key for key in github_binding.CONFIG),
                (
                    "browser-client",
                    "synthetic-client-secret",
                    base + "/app/github-callback.html",
                    Fernet.generate_key().decode(),
                ),
                strict=True,
            )
        )
        with patch.dict(os.environ, env), patch.object(github_binding, "remote", fake):
            server = uvicorn.Server(
                uvicorn.Config(
                    create_app(url),
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
                with sync_playwright() as p:
                    browser = p.chromium.launch()
                    context = browser.new_context(
                        viewport={"width": 1440, "height": 1050}, reduced_motion="reduce"
                    )
                    errors = []
                    page = context.new_page()
                    page.on("pageerror", lambda error: errors.append(str(error)))

                    def authorize(route):
                        query = parse_qs(urlsplit(route.request.url).query, keep_blank_values=True)
                        assert query["scope"] == [""]
                        route.fulfill(
                            status=302,
                            headers={
                                "Location": base
                                + "/app/github-callback.html?"
                                + urlencode(
                                    {"state": query["state"][0], "code": "browser-synthetic"}
                                )
                            },
                        )

                    context.route("https://github.com/login/oauth/authorize?*", authorize)
                    page.goto(base + "/app/#settings")
                    if locale == "en-US":
                        page.locator("#ui-locale").select_option(locale)
                    page.locator("#connect").click()
                    page.locator("#token").fill(token)
                    page.locator("#account-form button[type=submit]").click()
                    expect(page.locator("#github-start")).to_be_enabled()
                    page.locator("#timeline-more").click()
                    expect(page.locator("#timeline-events")).to_contain_text("weekly_minutes")
                    if locale == "en-US":
                        finishing = []
                        page.route(
                            "**/api/v1/github-binding/finish", lambda route: finishing.append(route)
                        )
                        with page.expect_request("**/api/v1/github-binding/finish"):
                            page.locator("#github-start").click()
                        expect(page.locator("#github-start")).to_be_disabled()
                        finish = finishing.pop()
                        finish.fulfill(response=finish.fetch())
                        page.unroute("**/api/v1/github-binding/finish")
                    else:
                        page.locator("#github-start").click()
                    expect(page.locator("#github-binding")).to_contain_text(
                        (
                            "Connected：public-student"
                            if locale == "en-US"
                            else "已绑定：public-student"
                        )
                    )
                    page.locator("#github-sync").click()
                    expect(page.locator("#github-binding")).to_contain_text("demo")
                    expect(page.locator("#github-binding")).not_to_contain_text("private-secret")
                    for width in (1440, 375):
                        page.set_viewport_size({"width": width, "height": 1050})
                        page.locator("#github-binding").scroll_into_view_if_needed()
                        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                    page.locator("#github-binding summary").click()
                    page.locator("#github-unbind").click()
                    expect(page.locator("#github-start")).to_be_enabled()
                    expect(page.locator("#github-binding")).not_to_contain_text("demo")
                    assert fake.calls[-1][1] == "DELETE"
                    if locale == "en-US":
                        assert page.evaluate(MISMARKED) == [], page.evaluate(MISMARKED)
                        expect(page.locator("#account-sessions")).to_contain_text("Unknown device")
                        callback = context.new_page()
                        callback.goto(base + "/app/github-callback.html?code=synthetic-removed")
                        expect(callback.locator("html")).to_have_attribute("lang", "en-US")
                        expect(callback.locator("#result")).to_contain_text(
                            "original window is unavailable"
                        )
                        assert "code=" not in callback.url
                        callback.close()
                        pending = []
                        page.route(
                            "**/api/v1/account/sessions/*", lambda route: pending.append(route)
                        )
                        with page.expect_request("**/api/v1/account/sessions/*"):
                            page.locator('[data-current-session="true"]').click()
                        page.evaluate("location.hash='portfolio'")
                        expect(page.locator("#project-recommend")).to_be_attached()
                        request = pending.pop()
                        request.fulfill(response=request.fetch())
                        page.unroute("**/api/v1/account/sessions/*")
                    else:
                        page.locator('[data-current-session="true"]').click()
                    expect(page.locator("#connect")).to_have_text(
                        "Connect my account" if locale == "en-US" else "连接我的账号"
                    )
                    if locale == "en-US":
                        expect(page.locator("#project-recommend")).to_have_count(0)
                    revoked = page.request.get(base + "/api/v1/account/export", headers=headers)
                    assert revoked.status == 401
                    assert not errors, errors
                    browser.close()
                print(
                    "Chromium: timeline, OAuth popup/PKCE callback, public-only sync, "
                    f"unbind, session revocation, 375px passed ({locale}); provider simulated"
                )
            finally:
                server.should_exit = True
                thread.join(timeout=5)
                engine.dispose()


if __name__ == "__main__":
    run()
    run("en-US")
