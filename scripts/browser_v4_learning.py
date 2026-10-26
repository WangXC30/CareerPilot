"""Actual two-account tutoring and explicitly assigned human review, local only."""

import socket
import tempfile
import threading
import time
from pathlib import Path

import uvicorn
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from playwright.sync_api import expect, sync_playwright

from careerpilot.api import create_app
from careerpilot.cli import provision
from careerpilot.persistence import make_engine
from careerpilot.seed import seed


def checked(response, status=200):
    assert response.status_code == status, response.status_code
    return response.json()


def connect(page, base, token):
    page.goto(base + "#directions")
    page.locator("#connect").click()
    page.locator("#token").fill(token)
    page.locator("#account-form button[type=submit]").click()
    expect(page.locator("#practice-review-list")).to_be_visible()


def main():
    with tempfile.TemporaryDirectory(prefix="careerpilot-learning-browser-") as directory:
        url = "sqlite:///" + (Path(directory) / "learning.db").as_posix()
        config = Config("alembic.ini")
        config.attributes["database_url"] = url
        command.upgrade(config, "head")
        engine = make_engine(url)
        seed(engine)
        _, alice_token = provision(engine, "Synthetic learner")
        bob_id, bob_token = provision(engine, "Synthetic invited reviewer")
        alice = {"Authorization": "Bearer " + alice_token}
        bob = {"Authorization": "Bearer " + bob_token}
        with TestClient(create_app(url)) as client:
            space = checked(
                client.post(
                    "/api/v1/workspaces",
                    headers=alice,
                    json={"name": "练习复核同伴", "confirmed": True},
                ),
                201,
            )
            invite = checked(
                client.post(
                    f"/api/v1/workspaces/{space['id']}/invitations",
                    headers=alice,
                    json={"recipient_id": bob_id, "confirmed": True},
                ),
                201,
            )
            checked(
                client.post(
                    f"/api/v1/workspace-invitations/{invite['id']}/accept",
                    headers=bob,
                    json={"ticket": invite["ticket"], "confirmed": True},
                )
            )
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
                    base = f"http://127.0.0.1:{port}/app/"
                    for width in (1440, 375):
                        owner = browser.new_page(viewport={"width": width, "height": 1000})
                        peer = browser.new_page(viewport={"width": 375, "height": 1000})
                        errors = []
                        for page in (owner, peer):
                            page.on("pageerror", lambda error: errors.append(str(error)))
                        connect(owner, base, alice_token)
                        scenario = owner.locator("#v4-scenario")
                        scenario.locator('input[value="career-backend"]').check()
                        scenario.locator('button[value="routes"]').click()
                        expect(owner.locator(".learning-path")).to_have_count(2)
                        expect(owner.locator("#v4-result")).to_contain_text("金钱成本未知")
                        owner.locator('#v4-exam input[value="python"]').check()
                        owner.locator("#v4-exam button").click()
                        for choice in (1, 0, 0):
                            expect(owner.locator("#v4-answer")).to_be_visible()
                            old_version = owner.evaluate("v4.exam.version")
                            owner.locator(f'#v4-answer input[value="{choice}"]').check()
                            owner.locator("#v4-answer button").click()
                            owner.wait_for_function(
                                "version=>v4.exam.version>version", arg=old_version
                            )
                        expect(owner.locator("#v4-result")).to_contain_text("完成 3 题")
                        exam_id = owner.evaluate("v4.exam.id")
                        wrong = owner.locator("[data-tutor-start]").first
                        wrong.locator("..").locator(":scope > summary").click()
                        wrong.click()
                        expect(owner.locator("#tutor-followup")).to_be_visible()
                        for step in (2, 3):
                            form = owner.locator("#tutor-followup")
                            form.locator('[name="attempt"]').fill(
                                "<img src=x onerror=window.__unsafe=true> 我先区分条件"
                            )
                            form.locator('[name="question"]').fill("哪种前提会改变结论？")
                            form.locator('[name="focus"]').select_option("counterexample")
                            form.locator('button[value="hint"]').click()
                            expect(
                                owner.locator("#v4-result details").last.locator("summary")
                            ).to_contain_text(f"提示 {step}/3")
                        assert owner.evaluate("window.__unsafe !== true")
                        owner.reload()
                        connect(owner, base, alice_token)
                        session_id = owner.evaluate(
                            "async ()=>(await api('/teaching-sessions'))[0].id"
                        )
                        owner.locator(f'[data-tutor-open="{session_id}"]').click()
                        expect(owner.locator("#v4-result")).to_contain_text("哪种前提会改变结论")
                        owner.locator(f'[data-v4-exam="{exam_id}"]').click()
                        owner.locator('#v4-appeal [name="text"]').fill(
                            "熟悉题目，请同伴核对实际计时背景"
                        )
                        owner.locator("#v4-appeal button").click()
                        expect(owner.locator("#practice-review-request")).to_be_visible()
                        owner.locator('#practice-review-request input[type="checkbox"]').check()
                        owner.locator("#practice-review-request button").click()
                        expect(owner.locator("#notice")).to_contain_text("仅本次练习快照已授权")
                        review_id = owner.evaluate(
                            "async id=>(await api('/practice-reviews')).find(r=>r.exam_id===id).id",
                            exam_id,
                        )
                        connect(peer, base, bob_token)
                        peer.locator(f'[data-review-open="{review_id}"]').click()
                        form = peer.locator("#practice-review-decision")
                        expect(form).to_be_visible()
                        form.locator('[name="conclusion"]').select_option("inconclusive")
                        form.locator('[name="note"]').fill(
                            "仅有计时信息不足判断诚信；建议本人说明作答依据"
                        )
                        form.locator('[type="checkbox"]').check()
                        form.locator("button").click()
                        expect(peer.locator("#notice")).to_contain_text("成绩与能力未改变")
                        assert peer.locator("[data-review-revoke]").count() == 0
                        owner.locator("#refresh-practice-reviews").click()
                        owner.locator(f'[data-review-open="{review_id}"]').click()
                        expect(owner.locator("#v4-result")).to_contain_text("现有信息不足以判断")
                        owner.locator(f'[data-review-revoke="{review_id}"]').click()
                        expect(owner.locator("#practice-review-list")).to_contain_text("授权已失效")
                        peer.locator(f'[data-review-open="{review_id}"]').click()
                        expect(peer.locator("#v4-result")).to_contain_text("复核授权已失效")
                        peer.locator("#refresh-practice-reviews").click()
                        assert peer.locator(f'[data-review-open="{review_id}"]').count() == 0
                        assert (
                            owner.evaluate("async ()=>(await api('/skill-evidence')).length") == 0
                        )
                        owner.locator(f'[data-tutor-delete="{session_id}"]').click()
                        expect(owner.locator(f'[data-tutor-open="{session_id}"]')).to_have_count(0)
                        assert owner.evaluate(
                            "document.documentElement.scrollWidth <= innerWidth + 1"
                        )
                        assert not errors, errors
                        owner.close()
                        peer.close()
                finally:
                    browser.close()
            print(
                "Chromium learning: A/B paths, tutoring/history/purge, "
                "explicit peer review and revocation at 1440/375px passed"
            )
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            engine.dispose()


if __name__ == "__main__":
    main()
