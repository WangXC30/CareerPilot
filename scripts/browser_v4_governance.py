"""Public alias review/diff/rollback and private frozen replay in real Chromium."""

import json
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
from sqlalchemy.orm import Session

from careerpilot.api import create_app
from careerpilot.cli import provision
from careerpilot.persistence import User, make_engine
from careerpilot.platform_models import RoleGrant
from careerpilot.seed import seed


def connect(page, base, token, route):
    page.goto(base + "#" + route)
    page.locator("#connect").click()
    page.locator("#token").fill(token)
    page.locator("#account-form button[type=submit]").click()
    expect(
        page.locator("#matching-history" if route == "directions" else "#public-proposal-form")
    ).to_be_attached()


def review_publish(page):
    form = page.locator(".public-review-form").first
    form.locator('[name="note"]').fill("独立核对公共别名与本地语义回归")
    form.get_by_role("button", name="确认审核通过").click()
    page.get_by_role("button", name="确认发布已审核变更").click()
    expect(page.locator("#notice")).to_contain_text("公共版本已发布")


def main():
    with tempfile.TemporaryDirectory(prefix="careerpilot-governance-browser-") as directory:
        url = "sqlite:///" + (Path(directory) / "governance.db").as_posix()
        config = Config("alembic.ini")
        config.attributes["database_url"] = url
        command.upgrade(config, "head")
        engine = make_engine(url)
        seed(engine)
        _, token = provision(engine, "Synthetic matching owner")
        operator_id, operator_token = provision(engine, "Synthetic public operator")
        admin_id, admin_token = provision(engine, "Synthetic public admin")
        _, stranger_token = provision(engine, "Synthetic private stranger")
        with Session(engine) as db, db.begin():
            for user_id, role in ((operator_id, "operator"), (admin_id, "admin")):
                user = db.get(User, user_id)
                db.get(RoleGrant, (user.id, user.tenant_id)).role = role
        headers = {"Authorization": "Bearer " + token}
        with TestClient(create_app(url)) as client:
            imported = client.post(
                "/api/v1/jobs/import",
                headers=headers,
                json={"content": "岗位：合成词库样本\n要求：PyLocal"},
            )
            assert imported.status_code == 201
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
                    errors = []
                    base = f"http://127.0.0.1:{port}/app/"
                    owner = browser.new_page(viewport={"width": 1440, "height": 1000})
                    owner.on("pageerror", lambda e: errors.append(str(e)))
                    connect(owner, base, token, "directions")
                    save = owner.locator("[data-v4-snapshot]")
                    save.locator("..").locator(":scope > summary").click()
                    save.click()
                    expect(owner.locator("#v4-result")).to_contain_text("快照已保存")
                    old_id = owner.evaluate("async ()=>(await api('/matching-snapshots'))[0].id")
                    staff = browser.new_page(viewport={"width": 1440, "height": 1000})
                    staff.on("pageerror", lambda e: errors.append(str(e)))
                    connect(staff, base, operator_token, "admin")
                    staff.locator("#public-catalogue-operations details").first.locator(
                        "summary"
                    ).click()
                    form = staff.locator("#public-proposal-form")
                    form.locator('[name="target"]').select_option("python")
                    payload = json.loads(form.locator('[name="payload"]').input_value())
                    payload["aliases"].append("PyLocal")
                    form.locator('[name="payload"]').fill(json.dumps(payload, ensure_ascii=False))
                    form.locator('[name="note"]').fill("本人维护的合成别名增量")
                    form.locator('[type="checkbox"]').check()
                    form.locator("button").click()
                    proposal = staff.locator("[data-public-proposal]").first
                    expect(proposal).to_contain_text("待独立审核")
                    proposal.locator(".public-field-diff > summary").click()
                    expect(proposal.locator(".public-field-diff")).to_contain_text("PyLocal")
                    assert staff.locator(".public-review-form").count() == 0
                    connect(staff, base, admin_token, "admin")
                    review_publish(staff)
                    owner.reload()
                    connect(owner, base, token, "directions")
                    old = owner.locator(f'[data-matching-snapshot="{old_id}"]')
                    old.locator(":scope > summary").click()
                    old.locator("button").click()
                    expect(owner.locator("#v4-result")).to_contain_text("与历史结果一致")
                    expect(owner.locator("#v4-result")).to_contain_text("历史技能：未识别")
                    owner.locator("[data-v4-snapshot]").locator("..").locator(
                        ":scope > summary"
                    ).click()
                    owner.locator("[data-v4-snapshot]").click()
                    expect(owner.locator("#v4-result")).to_contain_text("快照已保存")
                    new_id = owner.evaluate(
                        "async ()=>(await api('/matching-snapshots'))"
                        ".find(s=>s.catalogue_versions.python===2).id"
                    )
                    connect(staff, base, operator_token, "admin")
                    staff.locator("#public-catalogue-operations details").filter(
                        has=staff.locator("#public-rollback-form")
                    ).locator(":scope > summary").click()
                    rollback = staff.locator("#public-rollback-form")
                    rollback.locator('[name="entry_id"]').select_option("python")
                    rollback.locator('[name="target_version"]').fill("1")
                    rollback.locator('[name="note"]').fill("回滚本地合成别名")
                    rollback.locator("button").click()
                    expect(staff.locator("[data-public-proposal]").first).to_contain_text(
                        "待独立审核"
                    )
                    connect(staff, base, admin_token, "admin")
                    review_publish(staff)
                    for width in (1440, 375):
                        owner.set_viewport_size({"width": width, "height": 1000})
                        owner.reload()
                        connect(owner, base, token, "directions")
                        saved = owner.locator(f'[data-matching-snapshot="{new_id}"]')
                        saved.locator(":scope > summary").click()
                        saved.locator("button").click()
                        expect(owner.locator("#v4-result")).to_contain_text("与历史结果一致")
                        expect(owner.locator("#v4-result")).to_contain_text("python（PyLocal）")
                        expect(owner.locator("#v4-result")).to_contain_text("python v2")
                        assert owner.evaluate(
                            "document.documentElement.scrollWidth <= innerWidth + 1"
                        )
                    stranger = browser.new_page()
                    connect(stranger, base, stranger_token, "directions")
                    assert stranger.locator("[data-matching-snapshot]").count() == 0
                    assert not errors, errors
                finally:
                    browser.close()
            print(
                "Chromium governance: alias diff/review/publish/rollback, "
                "private frozen replay and 375px passed"
            )
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            engine.dispose()


if __name__ == "__main__":
    main()
