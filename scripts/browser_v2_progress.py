"""Real report-to-review journey plus independent public governance in Chromium."""

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import uvicorn
from alembic import command
from alembic.config import Config
from hiring_contract import create_hiring
from interview_contract import create_practice, verify_practice
from playwright.sync_api import expect, sync_playwright
from sqlalchemy import select
from sqlalchemy.orm import Session

from careerpilot import growth, reporting
from careerpilot.api import create_app
from careerpilot.cli import provision
from careerpilot.persistence import make_engine
from careerpilot.seed import seed

try:
    from .temporary_artifacts import temporary_artifacts
except ImportError:
    from temporary_artifacts import temporary_artifacts


def run(output=None):
    with tempfile.TemporaryDirectory(prefix="careerpilot-progress-") as directory:
        url = "sqlite:///" + (Path(directory) / "progress.db").as_posix()
        config = Config("alembic.ini")
        config.attributes["database_url"] = url
        command.upgrade(config, "head")
        engine = make_engine(url)
        seed(engine)
        _, token = provision(engine, "Synthetic progress student")
        _, operator_token = provision(engine, "Synthetic public editor", role="operator")
        _, admin_token = provision(engine, "Synthetic public reviewer", role="admin")
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        server = uvicorn.Server(
            uvicorn.Config(
                create_app(url), host="127.0.0.1", port=port, log_level="error", access_log=False
            )
        )
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        worker = subprocess.Popen(
            [sys.executable, "-m", "careerpilot.worker"],
            env={**os.environ, "CAREERPILOT_DATABASE_URL": url},
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            for _ in range(100):
                if server.started:
                    break
                time.sleep(0.05)
            with httpx.Client(
                base_url=base + "/api/v1", headers={"Authorization": "Bearer " + token}, timeout=45
            ) as client:
                response = client.put(
                    "/profile",
                    headers={"If-Match": '"0"'},
                    json={
                        "education": "bachelor",
                        "major": "计算机",
                        "academic_year": "大三",
                        "graduation_date": "2027-06-30",
                        "preferred_cities": ["上海"],
                        "weekly_minutes": 300,
                        "timezone": "Asia/Shanghai",
                        "confirmed": True,
                    },
                )
                assert response.status_code == 200, response.text
                j = client.post(
                    "/jobs/import",
                    json={
                        "content": (
                            "岗位：后端实习\n公司：合成企业\n要求：Python\n"
                            "要求：SQL\n截止日期：2029-12-31"
                        )
                    },
                ).json()
                response = client.post(
                    "/plans", json={"job_ids": [j["id"]], "start_date": "2026-10-21"}
                )
                assert response.status_code == 201, response.text
                plan = response.json()
                plan = client.post(
                    f"/plans/{plan['id']}/publish",
                    headers={"If-Match": '"1"'},
                    json={"confirmed": True},
                ).json()
                fixture_client = httpx.Client(base_url=base, headers=client.headers, timeout=45)
                hiring = create_hiring(fixture_client, {}, j["id"], "browser-progress")
                practice = create_practice(
                    fixture_client,
                    {},
                    j["id"],
                    hiring["version"]["id"],
                    plan["id"],
                    "browser-practice",
                )
                verify_practice(fixture_client, {}, practice)
                fixture_client.close()
                a = hiring["application"]
                m = client.post(
                    "/materials?filename=receipt.txt",
                    content="后端实习 投递成功，申请编号 TEST-123。".encode(),
                ).json()
                response = client.post(
                    f"/applications/{a['id']}/receipt",
                    headers={"If-Match": f'"{a["version"]}"'},
                    json={
                        "material_id": m["id"],
                        "material_version": m["version"],
                        "receipt_code": "TEST-123",
                        "submitted_at": datetime.now(timezone.utc).isoformat(),
                        "confirmed": True,
                    },
                )
                assert response.status_code == 200, response.text
                a = response.json()
                response = client.post(
                    f"/applications/{a['id']}/transition",
                    headers={"If-Match": f'"{a["version"]}"'},
                    json={
                        "target": "interviewing",
                        "note": "合成面试通知，由本人补录",
                        "confirmed": True,
                    },
                )
                assert response.status_code == 200, response.text
                before_plan = client.get(f"/plans/{plan['id']}").json()
                with sync_playwright() as p:
                    browser = p.chromium.launch()
                    page = browser.new_page(viewport={"width": 1440, "height": 1050})
                    errors = []
                    page.on("pageerror", lambda e: errors.append(str(e)))

                    def login(page, credential, route):
                        page.goto(base + f"/app/?session={time.monotonic_ns()}#" + route)
                        page.get_by_role("button", name="连接我的账号", exact=True).click()
                        page.locator("#token").fill(credential)
                        page.locator("#account-form button[type=submit]").click()

                    login(page, token, "progress")
                    expect(page.locator("#period-report-result")).to_contain_text("0 分钟")
                    form = page.locator("#study-log-form")
                    form.locator("xpath=ancestor::details/summary").click()
                    form.locator('[name="minutes"]').fill("25")
                    form.locator('[name="note"]').fill(
                        "真实投入由本人记录。<script>window.__reportInjected=1</script>"
                    )
                    form.locator('[type="checkbox"]').check()
                    form.get_by_role("button", name="保存学习时间").click()
                    expect(page.locator("#period-report-result")).to_contain_text("25 分钟")
                    expect(page.locator("#period-report-result")).to_contain_text("1 / 1")
                    assert page.evaluate("window.__reportInjected===undefined")
                    page.locator("#personal-notification-settings summary").click()
                    nf = page.locator("#personal-notification-form")
                    nf.locator('[name="status"]').select_option("active")
                    nf.locator('[name="weekly_report"]').check()
                    nf.locator('[name="quiet_start"]').fill("0")
                    nf.locator('[name="quiet_end"]').fill("0")
                    nf.locator('[type="checkbox"]').last.check()
                    nf.get_by_role("button", name="保存成长通知配置").click()
                    expect(page.locator("#notice")).to_contain_text("配置已保存")
                    # Simulate the due clock boundary; the separate Worker performs delivery.
                    with Session(engine) as db, db.begin():
                        row = db.scalar(
                            select(growth.GrowthRecord).where(
                                growth.GrowthRecord.kind == "notification_settings"
                            )
                        )
                        item = growth.data(row)
                        item["next_runs"] = {"weekly_report": int(time.time()) - 1}
                        row.payload_json = growth.dump(reporting.body(item))
                    for _ in range(100):
                        if any(
                            n["record_kind"] == "personal_notice"
                            for n in client.get("/notification-center").json()["items"]
                        ):
                            break
                        time.sleep(0.1)
                    else:
                        raise AssertionError("Separate Worker did not deliver report notice")
                    page.reload()
                    login(page, token, "progress")
                    expect(page.locator("#period-report-result")).to_contain_text("25 分钟")
                    page.locator("#review-pipeline-create summary").click()
                    wf = page.locator("#review-pipeline-form")
                    wf.locator('[name="query"]').fill("python")
                    wf.locator('[type="checkbox"]').check()
                    wf.get_by_role("button", name="创建连续复盘任务").click()
                    expect(page.locator("[data-review-run]")).to_contain_text(
                        "已完成 3 / 3 步", timeout=20000
                    )
                    assert (
                        client.get(f"/plans/{plan['id']}").json()["version"]
                        == before_plan["version"]
                    )
                    page.locator("[data-review-run]").get_by_role(
                        "button", name="接受此调整", exact=True
                    ).click()
                    expect(page.locator("#notice")).to_contain_text("已接受")
                    assert (
                        client.get(f"/plans/{plan['id']}").json()["version"]
                        == before_plan["version"] + 1
                    )
                    if output:
                        output.mkdir(parents=True, exist_ok=True)
                        page.locator("#period-report-result").scroll_into_view_if_needed()
                        page.screenshot(path=output / "report-desktop.png", animations="disabled")
                    page.set_viewport_size({"width": 375, "height": 860})
                    assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
                    if output:
                        page.screenshot(path=output / "report-mobile.png", animations="disabled")
                    page.set_viewport_size({"width": 1440, "height": 1050})
                    login(page, operator_token, "admin")
                    expect(page.locator("#public-catalogue-operations")).to_be_visible()
                    page.locator("#public-catalogue-operations details").first.locator(
                        "summary"
                    ).click()
                    pf = page.locator("#public-proposal-form")
                    pf.locator('[name="target"]').select_option("resource-python")
                    fields = json.loads(pf.locator('[name="payload"]').input_value())
                    original_title = fields["title"]
                    fields["title"] = "合成审核后发布的 Python 入口"
                    pf.locator('[name="payload"]').fill(json.dumps(fields, ensure_ascii=False))
                    pf.locator('[name="note"]').fill("合成公共资源维护")
                    pf.locator('[type="checkbox"]').check()
                    pf.get_by_role("button", name="提交独立审核").click()
                    expect(page.locator("[data-public-proposal]")).to_contain_text("待独立审核")
                    login(page, admin_token, "admin")
                    review_form = page.locator(".public-review-form").first
                    review_form.locator('[name="note"]').fill("独立核对合成字段")
                    review_form.get_by_role("button", name="确认审核通过").click()
                    publish = page.get_by_role("button", name="确认发布已审核变更")
                    expect(publish).to_be_visible()
                    publish.click()
                    expect(page.locator("#notice")).to_contain_text("公共版本已发布")
                    assert (
                        next(
                            r
                            for r in client.get("/catalogue/resource").json()
                            if r["id"] == "resource-python"
                        )["version"]
                        == 2
                    )
                    login(page, operator_token, "admin")
                    page.locator("#public-catalogue-operations details").filter(
                        has=page.locator("#public-rollback-form")
                    ).locator("summary").first.click()
                    rf = page.locator("#public-rollback-form")
                    rf.locator('[name="entry_id"]').select_option("resource-python")
                    rf.locator('[name="target_version"]').fill("1")
                    rf.locator('[name="note"]').fill("恢复原公共标题")
                    rf.get_by_role("button", name="创建回滚审核单").click()
                    expect(page.locator("[data-public-proposal]").first).to_contain_text(
                        "待独立审核"
                    )
                    login(page, admin_token, "admin")
                    review_form = page.locator(".public-review-form").first
                    review_form.locator('[name="note"]').fill("独立核对回滚版本")
                    review_form.get_by_role("button", name="确认审核通过").click()
                    page.get_by_role("button", name="确认发布已审核变更").click()
                    expect(page.locator("#notice")).to_contain_text("公共版本已发布")
                    restored = next(
                        r
                        for r in client.get("/catalogue/resource").json()
                        if r["id"] == "resource-python"
                    )
                    assert restored["version"] == 3 and restored["title"] == original_title
                    if output:
                        page.locator("#public-catalogue-operations").scroll_into_view_if_needed()
                        page.screenshot(
                            path=output / "operations-desktop.png", animations="disabled"
                        )
                    assert not errors, errors
                    browser.close()
                result = {
                    "study_time_is_explicit": True,
                    "same_cohort_funnel": True,
                    "separate_worker_report_delivery_with_due_clock_fixture": True,
                    "three_step_review_and_explicit_plan_acceptance": True,
                    "independent_public_review_publish_and_rollback": True,
                    "raw_ticket_sharing": False,
                    "mobile_width": 375,
                    "js_errors": errors,
                    "external_mail_or_submission": False,
                    "full_v2_exit": False,
                }
                if output:
                    (output / "browser-report.json").write_text(
                        json.dumps(result, indent=2), encoding="utf-8"
                    )
                print(json.dumps(result))
        finally:
            worker.terminate()
            _, worker_error = worker.communicate(timeout=10)
            server.should_exit = True
            thread.join(timeout=5)
            engine.dispose()
            if worker_error.strip():
                raise AssertionError("Progress Worker emitted an error: " + worker_error)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    with temporary_artifacts(args.output) as args.output:
        run(args.output)
