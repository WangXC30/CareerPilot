"""Chromium recruiting journey, actual downloads and a separate reminder Worker."""

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
from browser_identity import connect_browser
from playwright.sync_api import expect, sync_playwright
from sqlalchemy.orm import Session

from careerpilot import growth
from careerpilot.api import create_app
from careerpilot.cli import provision
from careerpilot.persistence import make_engine
from careerpilot.seed import seed

try:
    from .temporary_artifacts import temporary_artifacts
except ImportError:
    from temporary_artifacts import temporary_artifacts


def run(output=None):
    with tempfile.TemporaryDirectory(prefix="careerpilot-hiring-") as directory:
        url = f"sqlite:///{(Path(directory) / 'hiring.db').as_posix()}"
        config = Config("alembic.ini")
        config.attributes["database_url"] = url
        command.upgrade(config, "head")
        engine = make_engine(url)
        seed(engine)
        _, token = provision(engine, "Synthetic hiring student")
        headers = {"Authorization": "Bearer " + token}
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        server = uvicorn.Server(
            uvicorn.Config(
                create_app(url, local_only=False),
                host="127.0.0.1",
                port=port,
                log_level="error",
                access_log=False,
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
            base = f"http://127.0.0.1:{port}"
            with httpx.Client(
                base_url=base, headers=headers, trust_env=False, timeout=45
            ) as client:
                for _ in range(150):
                    try:
                        if client.get("/health/ready").status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(0.05)
                else:
                    raise RuntimeError("Hiring API not ready")
                response = client.post(
                    "/api/v1/jobs/import",
                    json={
                        "content": (
                            "岗位：后端实习生\n公司：合成测试企业\n要求：Python\n"
                            "要求：SQL\n截止日期：2029-12-31"
                        ),
                        "source_url": "https://example.com/jobs/synthetic",
                    },
                )
                assert response.status_code == 201, response.text
                job_id = response.json()["id"]
                if output:
                    output.mkdir(parents=True, exist_ok=True)
                with sync_playwright() as p:
                    browser = p.chromium.launch()
                    page = browser.new_page(viewport={"width": 1440, "height": 1050})
                    errors = []
                    page.on("pageerror", lambda e: errors.append(str(e)))
                    page.goto(base + "/app/#recruiting")
                    connect_browser(page, token)
                    pass
                    expect(page.locator("#fact-form")).to_be_attached()
                    page.get_by_text("导入文本简历并逐项确认", exact=True).click()
                    importer = page.locator("#resume-import-form")
                    importer.locator('[name="resume_file"]').set_input_files(
                        {
                            "name": "synthetic-resume.txt",
                            "mimeType": "text/plain",
                            "buffer": "姓名：合成学生\nstudent@example.com".encode(),
                        }
                    )
                    importer.get_by_role("button", name="预览简历候选", exact=True).click()
                    confirmation = page.locator("#resume-import-confirm")
                    expect(confirmation.locator("fieldset")).to_have_count(2)
                    confirmation.locator('textarea[name="text"]').first.fill("合成学生")
                    confirmation.locator('input[type="checkbox"]').last.check()
                    confirmation.get_by_role("button", name="确认导入所选事实", exact=True).click()
                    expect(page.locator("#fact-library")).to_contain_text("student@example.com")
                    for kind, text in [
                        ("project", "使用 Python 编写课程中的数据清洗练习。"),
                    ]:
                        form = page.locator("#fact-form")
                        page.get_by_text("添加或重新确认事实", exact=True).click()
                        form.locator('[name="kind"]').select_option(kind)
                        form.locator('[name="text"]').fill(text)
                        form.locator('[name="confirmed"]').check()
                        form.get_by_role("button", name="确认保存事实", exact=True).click()
                        expect(page.locator("#fact-library")).to_contain_text(text)
                    form = page.locator("#resume-form")
                    form.locator('[name="job_id"]').select_option(job_id)
                    for box in form.locator('[name="fact_ids"]').all():
                        box.check()
                    form.locator('[name="confirmed"]').check()
                    form.get_by_role("button", name="保存简历版本", exact=True).click()
                    expect(form.locator('[name="resume_id"] option')).to_have_count(2)
                    root = client.get("/api/v1/resumes").json()[0]
                    v1 = client.get(f"/api/v1/resumes/{root['id']}/versions").json()[0]
                    form.locator('[name="resume_id"]').select_option(root["id"])
                    page.get_by_role("button", name="查看版本 1", exact=True).click()
                    expect(page.locator("#resume-inspection")).to_contain_text("sql")
                    for fmt, label in [("docx", "导出 Word"), ("pdf", "导出 PDF")]:
                        with page.expect_download() as info:
                            page.get_by_role("button", name=label, exact=True).click()
                        download = info.value
                        assert download.failure() is None
                        if output:
                            download.save_as(output / f"sample-short.{fmt}")
                    draft = page.locator("#material-draft")
                    draft.locator('[name="kind"]').select_option("email")
                    draft.locator('input[type="checkbox"]').check()
                    draft.get_by_role("button", name="生成材料草稿").click()
                    expect(page.get_by_text("邮件 · 简洁", exact=True)).to_be_visible()
                    form.locator('[name="resume_id"]').select_option(root["id"])
                    expect(
                        page.get_by_role("button", name="查看版本 1", exact=True)
                    ).to_be_visible()
                    page.get_by_role("button", name="按所选 JD 重排并另存", exact=True).click()
                    expect(form.locator('[name="resume_id"] option').last).to_contain_text(
                        "2 个版本"
                    )
                    form.locator('[name="resume_id"]').select_option(root["id"])
                    expect(
                        page.get_by_role("button", name="查看版本 2", exact=True)
                    ).to_be_visible()
                    page.locator(f'[data-restore-resume="{v1["id"]}"]').click()
                    expect(form.locator('[name="resume_id"] option').last).to_contain_text(
                        "3 个版本"
                    )
                    form.locator('[name="resume_id"]').select_option(root["id"])
                    page.get_by_role("button", name="查看版本 1", exact=True).click()
                    apply = page.locator("#application-form")
                    apply.locator('input[type="checkbox"]').check()
                    apply.get_by_role("button", name="建立申请", exact=True).click()
                    card = page.locator(".application-card")
                    expect(card).to_contain_text("草稿")
                    for target in ("prepared", "awaiting_user_submission"):
                        card.get_by_text("推进申请状态", exact=True).click()
                        f = card.locator(".application-transition")
                        f.locator('[name="target"]').select_option(target)
                        for box in f.locator('input[type="checkbox"]').all():
                            box.check()
                        f.get_by_role("button", name="确认更新状态", exact=True).click()
                        expect(card.locator(".tag")).to_have_text(
                            "已准备" if target == "prepared" else "待本人提交"
                        )
                    rec = card.locator(".application-receipt")
                    rec.locator('input[type="checkbox"]').check()
                    rec.get_by_role("button", name="确认提交结果", exact=True).click()
                    expect(card).to_contain_text("提交结果待核实")
                    rec.locator('[name="receipt_text"]').fill(
                        "投递成功 合成测试企业 后端实习生 编号：BROWSER-123"
                    )
                    rec.get_by_role("button", name="保存回执材料", exact=True).click()
                    expect(rec.locator('[name="material_id"]')).not_to_have_value("")
                    rec.locator('[name="receipt_code"]').fill("BROWSER-123")
                    rec.locator('[name="submitted_at"]').fill(
                        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M")
                    )
                    rec.locator('input[type="checkbox"]').check()
                    rec.get_by_role("button", name="确认提交结果", exact=True).click()
                    expect(card.locator(".tag")).to_have_text("已提交 · 本人回执确认")
                    a = client.get("/api/v1/applications").json()[0]
                    assert a["resume_version_id"] == v1["id"]
                    card.get_by_text("添加站内提醒", exact=True).click()
                    f = card.locator(".application-reminder")
                    f.locator('[name="kind"]').select_option("interview")
                    f.locator('[name="local_time"]').fill(
                        datetime.fromtimestamp(time.time() + 3600, timezone.utc).strftime(
                            "%Y-%m-%dT%H:%M"
                        )
                    )
                    f.locator('[name="timezone"]').fill("UTC")
                    f.locator('[name="quiet_start"]').fill("0")
                    f.locator('[name="quiet_end"]').fill("0")
                    f.locator('input[type="checkbox"]').check()
                    f.get_by_role("button", name="保存提醒", exact=True).click()
                    expect(page.locator("#hiring-notices")).to_contain_text("已开启")
                    r = client.get("/api/v1/application-reminders").json()[0]
                    # Advance only the disposable test schedule to exercise the independent Worker.
                    with Session(engine) as db, db.begin():
                        row = db.get(growth.GrowthRecord, r["id"])
                        body = json.loads(row.payload_json)
                        body.update(due_at=int(time.time()) + 2, next_run_at=int(time.time()) + 2)
                        row.payload_json = growth.dump(body)
                    for _ in range(100):
                        notices = client.get("/api/v1/application-notifications").json()
                        if notices:
                            break
                        assert worker.poll() is None
                        time.sleep(0.1)
                    assert len(notices) == 1
                    page.locator('nav [data-page="explore"]').click()
                    page.locator('nav [data-page="recruiting"]').click()
                    expect(page.locator("#hiring-notices")).to_contain_text("已送达")
                    page.get_by_role("button", name="标为已读", exact=True).click()
                    expect(page.locator("#hiring-notices")).to_contain_text("已读")
                    card.get_by_role("button", name="查看事件历史").click()
                    expect(card.locator(".app-history")).to_contain_text("事件历史")
                    if output:
                        page.locator("#application-board").scroll_into_view_if_needed()
                        page.screenshot(path=output / "hiring-desktop.png", animations="disabled")
                    page.set_viewport_size({"width": 375, "height": 860})
                    sidebar_width = page.locator("#sidebar").evaluate("n=>n.offsetWidth")
                    expect(page.locator("#sidebar")).to_have_css(
                        "transform",
                        f"matrix(1, 0, 0, 1, -{sidebar_width}, 0)",
                    )
                    page.locator("#application-board").scroll_into_view_if_needed()
                    assert page.evaluate("document.documentElement.scrollWidth<=window.innerWidth")
                    if output:
                        page.screenshot(path=output / "hiring-mobile.png", animations="disabled")
                    assert errors == [], errors
                    assert len(client.get("/api/v1/application-notifications").json()) == 1
                    browser.close()
                # Exercise automatic pagination with a multiline user statement.
                long_text = "\n".join(
                    f"课程练习 {n}：使用 Python 读取公开样例，核对字段并记录处理步骤。"
                    for n in range(1, 43)
                )
                f = client.post(
                    "/api/v1/resume-facts",
                    json={
                        "kind": "project",
                        "source_type": "manual",
                        "text": long_text,
                        "confirmed": True,
                    },
                )
                assert f.status_code == 201
                long_v = client.post(
                    "/api/v1/resumes",
                    headers={"Idempotency-Key": "long-sample"},
                    json={
                        "title": "多页测试",
                        "fact_ids": [
                            next(f["id"] for f in v1["facts"] if f["kind"] == "name"),
                            next(f["id"] for f in v1["facts"] if f["kind"] == "contact"),
                            f.json()["id"],
                        ],
                        "confirmed": True,
                    },
                )
                assert long_v.status_code == 201
                for fmt in ("docx", "pdf"):
                    result = client.post(
                        f"/api/v1/resume-versions/{long_v.json()['id']}/export/{fmt}"
                    )
                    assert result.status_code == 200, result.text
                    if output:
                        (output / f"sample-long.{fmt}").write_bytes(result.content)
                result = {
                    "journey": (
                        "facts-resume-tailor-restore-drafts-downloads-"
                        "application-pending-receipt-reminder"
                    ),
                    "fixed_original_version": True,
                    "actual_pdf_docx_downloads": True,
                    "separate_worker_delivered_once": True,
                    "mobile_width": 375,
                    "js_errors": errors,
                    "external_submission_or_mail": False,
                }
                if output:
                    (output / "browser-report.json").write_text(
                        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
                    )
                print(json.dumps(result, ensure_ascii=False))
        finally:
            worker.terminate()
            _, stderr = worker.communicate(timeout=10)
            server.should_exit = True
            thread.join(timeout=5)
            engine.dispose()
            if worker.returncode not in {0, -15}:
                raise RuntimeError(stderr)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    with temporary_artifacts(args.output) as args.output:
        run(args.output)
