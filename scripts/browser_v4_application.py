"""Chromium approval/history/manual review and Offer decisions, disconnected simulator only."""

import socket
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

import uvicorn
from alembic import command
from alembic.config import Config
from browser_identity import connect_browser
from fastapi.testclient import TestClient
from playwright.sync_api import expect, sync_playwright

from careerpilot import application_lab
from careerpilot.api import create_app
from careerpilot.cli import provision
from careerpilot.persistence import make_engine
from careerpilot.seed import seed


def ok(response, status=200):
    assert response.status_code == status, response.status_code
    return response.json()


def connect(page, base, token):
    page.goto(base + "#local-tools")
    connect_browser(page, token)
    expect(page.locator("#tool-application")).to_be_visible()


def main():
    with tempfile.TemporaryDirectory(prefix="careerpilot-application-browser-") as directory:
        url = "sqlite:///" + (Path(directory) / "application.db").as_posix()
        config = Config("alembic.ini")
        config.attributes["database_url"] = url
        command.upgrade(config, "head")
        engine = make_engine(url)
        seed(engine)
        _, token = provision(engine, "Synthetic application student")
        headers = {"Authorization": "Bearer " + token}
        with TestClient(create_app(url, local_only=False)) as client:
            facts = [
                ok(
                    client.post(
                        "/api/v1/resume-facts",
                        headers=headers,
                        json={
                            "kind": kind,
                            "source_type": "manual",
                            "text": text,
                            "confirmed": True,
                        },
                    ),
                    201,
                )
                for kind, text in (
                    ("name", "合成学生"),
                    ("contact", "synthetic@example.test"),
                    ("experience", "完成 2 个本地练习"),
                )
            ]
            resume = ok(
                client.post(
                    "/api/v1/resumes",
                    headers={**headers, "Idempotency-Key": "browser-resume"},
                    json={
                        "title": "演练简历",
                        "fact_ids": [fact["id"] for fact in facts],
                        "confirmed": True,
                    },
                ),
                201,
            )
            applications = []
            for index in range(4):
                job = ok(
                    client.post(
                        "/api/v1/jobs/import",
                        headers=headers,
                        json={"content": f"岗位：本地演练岗位 {index}\n要求：Python"},
                    ),
                    201,
                )
                applications.append(
                    ok(
                        client.post(
                            "/api/v1/applications",
                            headers={
                                **headers,
                                "Idempotency-Key": "browser-application-" + str(index),
                            },
                            json={
                                "job_id": job["id"],
                                "resume_version_id": resume["id"],
                                "confirmed": True,
                            },
                        ),
                        201,
                    )
                )
            unknown_app = applications[-1]
            preview = ok(
                client.post(
                    "/api/v1/application-lab/preview",
                    headers=headers,
                    json={"application_id": unknown_app["id"], "confirmed": True},
                )
            )
            approval = ok(
                client.post(
                    "/api/v1/application-lab/approve",
                    headers=headers,
                    json={
                        "application_id": unknown_app["id"],
                        "preview_hash": preview["preview_hash"],
                        "confirmed": True,
                    },
                )
            )
            with patch.object(application_lab, "simulator", side_effect=TimeoutError("synthetic")):
                uncertain = ok(
                    client.post(
                        "/api/v1/application-lab/submit",
                        headers=headers,
                        json={
                            "application_id": unknown_app["id"],
                            "approval_id": approval["approval_id"],
                            "confirmed": True,
                        },
                    )
                )
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
                    base = f"http://127.0.0.1:{port}/app/"
                    for index, width in enumerate((1440, 375)):
                        page = browser.new_page(viewport={"width": width, "height": 900})
                        errors = []
                        page.on("pageerror", lambda error: errors.append(str(error)))
                        connect(page, base, token)
                        page.locator(
                            '#tool-application select[name="application_id"]'
                        ).select_option(applications[index]["id"])
                        page.locator("#tool-application button").click()
                        expect(page.locator("#application-preview")).to_contain_text(
                            f"本地演练岗位 {index}"
                        )
                        page.locator("#application-approve").click()
                        page.locator("#application-cancel").click()
                        expect(page.locator("#tool-result")).to_contain_text("审批已撤销")
                        page.locator(
                            '#tool-application select[name="application_id"]'
                        ).select_option(applications[index]["id"])
                        page.locator("#tool-application button").click()
                        page.locator("#application-approve").click()
                        page.locator("#application-submit").click()
                        expect(page.locator("#tool-result")).to_contain_text("本地模拟已完成")
                        assert page.evaluate(
                            "async () => (await api('/applications'))"
                            ".every(item=>item.status==='draft')"
                        )
                        page.locator('#tool-offers input[name="nameA"]').fill(f"成长方案 {width}")
                        page.locator('#tool-offers input[name="nameB"]').fill(f"待核对方案 {width}")
                        page.locator('#tool-offers input[name="A-growth"]').fill("4")
                        page.locator('#tool-offers textarea[name="fact-A-compensation"]').fill(
                            "10000 元/月（合成样例）"
                        )
                        page.locator("#tool-offers button").click()
                        expect(page.locator("#tool-result")).to_contain_text(f"成长方案 {width}")
                        comparison = page.locator("[data-offer-comparison]").filter(
                            has_text=f"成长方案 {width}"
                        )
                        comparison.locator(":scope > summary").click()
                        expect(comparison).to_contain_text("10000 元/月（合成样例）")
                        expect(comparison).to_contain_text("未独立核实")
                        comparison.locator('select[name="offer_index"]').select_option("0")
                        comparison.locator('textarea[name="note"]').fill(
                            "本人已核对未知项，保留选择理由"
                        )
                        comparison.locator('input[type="checkbox"]').check()
                        comparison.locator("button").click()
                        expect(page.locator("#tool-result")).to_contain_text("本人选择已记录")
                        page.reload()
                        connect(page, base, token)
                        comparison = page.locator("[data-offer-comparison]").filter(
                            has_text=f"成长方案 {width}"
                        )
                        comparison.locator(":scope > summary").click()
                        expect(comparison).to_contain_text("本人已核对未知项")
                        assert page.locator("[data-simulation]").count() == index + 2
                        if index == 1:
                            unknown = page.locator('[data-simulation="' + uncertain["id"] + '"]')
                            unknown.locator(":scope > summary").click()
                            unknown.locator("textarea").fill("本人核对后仍没有模拟回执")
                            unknown.locator('input[type="checkbox"]').check()
                            unknown.locator("button").click()
                            expect(page.locator("#tool-result")).to_contain_text(
                                "未知结果未被改写为成功"
                            )
                            expect(unknown.locator(":scope > summary")).to_contain_text(
                                "本人已记录核对"
                            )
                        assert not errors, errors
                        page.close()
                    page = browser.new_page()
                    connect(page, base, token)
                    page.locator('#tool-application select[name="application_id"]').select_option(
                        applications[2]["id"]
                    )
                    page.locator("#tool-application button").click()
                    page.locator("#application-approve").click()
                    calls = []

                    def drop_response(route):
                        response = route.fetch()
                        assert response.status == 200
                        calls.append(1)
                        route.abort()

                    page.route("**/api/v1/application-lab/submit", drop_response)
                    page.locator("#application-submit").click()
                    expect(page.locator("#application-preview")).to_contain_text("本次结果未确认")
                    assert page.locator("#application-submit").count() == 0
                    assert (
                        page.evaluate(
                            "async id => (await api('/application-lab/runs'))"
                            ".filter(item=>item.preview.application_id===id).length",
                            applications[2]["id"],
                        )
                        == 1
                    )
                    assert calls == [1]
                    page.close()
                finally:
                    browser.close()
            print("Chromium application: approval/history, unknown review and Offer choice passed")
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            engine.dispose()


if __name__ == "__main__":
    main()
