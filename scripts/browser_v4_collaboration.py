"""Two accounts exercise explicit invitation, field sharing, feedback and revocation."""

import socket
import tempfile
import threading
import time
from pathlib import Path

import uvicorn
from alembic import command
from alembic.config import Config
from playwright.sync_api import expect, sync_playwright
from sqlalchemy.orm import Session

from careerpilot import growth
from careerpilot.api import create_app
from careerpilot.cli import provision
from careerpilot.domain import Principal
from careerpilot.persistence import User, make_engine
from careerpilot.seed import seed


def connect(page, base, token):
    page.goto(base + "#collaboration")
    page.locator("#connect").click()
    page.locator("#token").fill(token)
    page.locator("#account-form button[type=submit]").click()
    expect(page.locator("#space-create")).to_be_visible()


def main():
    with tempfile.TemporaryDirectory(prefix="careerpilot-collab-browser-") as directory:
        url = "sqlite:///" + (Path(directory) / "collaboration.db").as_posix()
        config = Config("alembic.ini")
        config.attributes["database_url"] = url
        command.upgrade(config, "head")
        engine = make_engine(url)
        seed(engine)
        alice_id, alice_token = provision(engine, "Synthetic Alice")
        bob_id, bob_token = provision(engine, "Synthetic Bob")
        with Session(engine) as db:
            user = db.get(User, alice_id)
            asset = growth.save(
                db,
                Principal(user.id, user.tenant_id),
                "project_asset",
                {
                    "title": "授权作品",
                    "content": "只分享此段合成作品正文",
                    "status": "active",
                    "private_note": "PRIVATE-NOTE-NEVER-SHARE",
                },
                "browser",
            )
            db.commit()
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
                    alice = browser.new_page(viewport={"width": 1440, "height": 1000})
                    bob = browser.new_page(viewport={"width": 375, "height": 900})
                    errors = []
                    alice.on("pageerror", lambda error: errors.append(str(error)))
                    bob.on("pageerror", lambda error: errors.append(str(error)))
                    base = f"http://127.0.0.1:{port}/app/"
                    connect(alice, base, alice_token)
                    connect(bob, base, bob_token)
                    assert bob.locator("[data-space-view]").count() == 0
                    alice.locator("#space-create input").fill("协作实验室")
                    with alice.expect_response(
                        lambda response: (
                            response.url.endswith("/workspaces")
                            and response.request.method == "POST"
                        )
                    ) as created:
                        alice.locator("#space-create button").click()
                    space = created.value.json()
                    alice.get_by_text("协作实验室", exact=True).click()
                    alice.locator(".space-invite input").fill(bob_id)
                    with alice.expect_response(
                        lambda response: response.url.endswith("/invitations")
                    ) as invitation_response:
                        alice.locator(".space-invite button").click()
                    invitation = invitation_response.value.json()
                    expect(alice.locator("#space-ticket")).to_contain_text(invitation["ticket"])
                    bob.locator('#space-accept input[name="invitation_id"]').fill(invitation["id"])
                    bob.locator('#space-accept input[name="ticket"]').fill(invitation["ticket"])
                    bob.locator("#space-accept button").click()
                    expect(bob.get_by_text("协作实验室", exact=True)).to_be_visible()
                    alice.locator('#share-preview-form select[name="source"]').select_option(
                        "project_asset:" + asset["id"]
                    )
                    alice.locator('#share-preview-form input[value="title"]').check()
                    alice.locator("#share-preview-form button").click()
                    expect(alice.locator("#share-preview pre")).to_contain_text("授权作品")
                    assert "只分享此段" not in alice.locator("#share-preview pre").inner_text()
                    alice.locator('#share-preview-form input[value="content"]').check()
                    assert alice.locator("#share-confirm-form").count() == 0
                    alice.locator("#share-preview-form button").click()
                    expect(alice.locator("#share-preview pre")).to_contain_text("只分享此段")
                    assert "PRIVATE-NOTE" not in alice.locator("#share-preview pre").inner_text()
                    alice.locator('#share-confirm-form select[name="workspace_id"]').select_option(
                        space["id"]
                    )
                    alice.locator('#share-confirm-form input[name="public"]').check()
                    with alice.expect_response(
                        lambda response: (
                            response.url.endswith("/object-shares")
                            and response.request.method == "POST"
                        )
                    ) as shared_response:
                        alice.locator("#share-confirm-form button").click()
                    shared = shared_response.value.json()
                    expect(alice.locator("#space-ticket")).to_contain_text(shared["ticket"])
                    bob.get_by_text("协作实验室", exact=True).click()
                    bob.locator("[data-space-view]").click()
                    bob.locator("#space-shares > details > summary").click()
                    expect(bob.locator("#space-shares pre")).to_contain_text("只分享此段")
                    assert "PRIVATE-NOTE" not in bob.locator("#space-shares").inner_text()
                    bob.locator(".share-comment textarea").fill("建议补充验证方法")
                    bob.locator(".share-comment button").click()
                    expect(bob.locator("#notice")).to_contain_text("反馈已保存")
                    alice.get_by_text("协作实验室", exact=True).click()
                    alice.locator("[data-space-view]").click()
                    alice.locator("#space-shares > details > summary").click()
                    expect(alice.locator(".share-feedback p")).to_contain_text("验证方法")
                    alice.get_by_text("隐藏这条反馈", exact=True).click()
                    alice.locator(".share-moderation input").fill("双方已确认处理")
                    alice.locator(".share-moderation button").click()
                    expect(alice.locator("#notice")).to_contain_text("反馈已隐藏")
                    assert alice.locator(".share-feedback").count() == 0
                    # The owner terminates membership; a refresh also clears stale rendered content.
                    alice.locator('[data-space-remove][data-member="' + bob_id + '"]').click()
                    expect(alice.locator("#notice")).to_contain_text("已撤销")
                    bob.locator("[data-space-view]").click()
                    expect(bob.locator("#notice .error")).to_contain_text("空间不存在")
                    assert bob.locator("#space-shares").inner_text() == ""
                    assert not errors, errors
                    alice.close()
                    bob.close()
                finally:
                    browser.close()
            print(
                "Chromium collaboration: two accounts, selected fields, feedback/hide/revoke passed"
            )
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            engine.dispose()


if __name__ == "__main__":
    main()
