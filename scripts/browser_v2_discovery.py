"""V2 discovery journey through Chromium, HTTP and a separate Worker process."""

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import httpx
import uvicorn
from alembic import command
from alembic.config import Config
from playwright.sync_api import expect, sync_playwright
from sqlalchemy.orm import Session

from careerpilot import growth
from careerpilot.api import create_app
from careerpilot.cli import provision
from careerpilot.persistence import make_engine
from careerpilot.seed import seed

JD = (
    "岗位：后端实习甲\n公司：企业甲\n地点：上海\n学历：本科及以上\n2027届\n"
    "实习时长：3个月\n工作类型：实习\n工作许可：不限\n资格：不限\n要求：Python\n薪资：10000元/月"
)


try:
    from .temporary_artifacts import temporary_artifacts
except ImportError:
    from temporary_artifacts import temporary_artifacts


def run(output=None):
    with tempfile.TemporaryDirectory(prefix="careerpilot-v2-discovery-") as directory:
        url = f"sqlite:///{(Path(directory) / 'discovery.db').as_posix()}"
        config = Config("alembic.ini")
        config.attributes["database_url"] = url
        command.upgrade(config, "head")
        engine = make_engine(url)
        seed(engine)
        _, token = provision(engine, "Synthetic discovery student")
        headers = {"Authorization": "Bearer " + token}
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
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
            base = f"http://127.0.0.1:{port}"
            with httpx.Client(
                base_url=base, headers=headers, trust_env=False, timeout=10
            ) as client:
                for _ in range(150):
                    try:
                        if client.get("/health/ready").status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(0.05)
                else:
                    raise RuntimeError("Discovery API not ready")
                a = client.post(
                    "/api/v1/jobs/import",
                    json={"content": JD, "source_url": "https://example.com/jobs/a"},
                )
                b = client.post(
                    "/api/v1/jobs/import",
                    json={"content": JD.replace("实习甲", "实习乙").replace("企业甲", "企业乙")},
                )
                c = client.post(
                    "/api/v1/jobs/import",
                    json={"content": "岗位：前端探索\n公司：企业丙\n要求：JavaScript"},
                )
                assert [r.status_code for r in (a, b, c)] == [201, 201, 201]
                a_id, c_id = a.json()["id"], c.json()["id"]
                if output:
                    output.mkdir(parents=True, exist_ok=True)
                with sync_playwright() as p:
                    browser = p.chromium.launch()
                    page = browser.new_page(viewport={"width": 1440, "height": 1050})
                    errors = []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.goto(base + "/app/#market")
                    page.get_by_role("button", name="连接我的账号", exact=True).click()
                    page.locator("#token").fill(token)
                    page.locator("#account-form").get_by_role(
                        "button", name="连接", exact=True
                    ).click()
                    expect(page.locator("#job-preferences-form")).to_be_visible()
                    card = page.locator(f'#job-matches [data-match-job="{a_id}"]')
                    card.get_by_role("button", name="收藏岗位", exact=True).click()
                    expect(page.locator("#saved-jobs")).to_contain_text("后端实习甲")
                    page.locator(
                        f'#job-matches [data-match-job="{a_id}"] input[name=compare_jobs]'
                    ).check()
                    page.locator(
                        f'#job-matches [data-match-job="{c_id}"] input[name=compare_jobs]'
                    ).check()
                    page.get_by_role("button", name="比较所选岗位", exact=True).click()
                    expect(page.locator("#job-comparison table")).to_contain_text("未知")
                    expect(page.locator("#job-comparison table")).to_contain_text("前端探索")
                    if output:
                        page.locator("#job-comparison").screenshot(
                            path=str(output / "comparison-desktop.png")
                        )
                    card.locator(".job-controls > details > summary").click()
                    card.locator(".job-feedback-form [name=label]").select_option("like")
                    card.get_by_role("button", name="确认记录反馈", exact=True).click()
                    expect(page.locator("#notice")).to_contain_text("反馈已记录")
                    expect(
                        page.locator("#job-recommendations .match-card").first
                    ).to_have_attribute("data-match-job", a_id)
                    card.locator(".job-controls > details > summary").click()
                    card.get_by_role("button", name="撤销反馈", exact=True).click()
                    expect(page.locator("#notice")).to_contain_text("反馈已撤销")
                    page.locator("#job-preferences-form [name=blocked_companies]").fill("企业甲")
                    page.get_by_role("button", name="确认保存岗位偏好", exact=True).click()
                    expect(page.locator(f'#job-matches [data-match-job="{a_id}"]')).to_have_count(0)
                    expect(page.locator("#saved-jobs")).to_contain_text("后端实习甲")
                    page.locator("#job-preferences-form [name=blocked_companies]").fill("")
                    page.get_by_role("button", name="确认保存岗位偏好", exact=True).click()
                    expect(card).to_be_visible()
                    page.locator("#recommendation-mode-form [name=mode]").select_option("entry")
                    page.get_by_role("button", name="查看此模式", exact=True).click()
                    expect(page.locator("#job-recommendations .match-card")).to_have_count(3)
                    page.locator(
                        '#job-preferences-form [name=enabled_modes][value="entry"]'
                    ).uncheck()
                    page.get_by_role("button", name="确认保存岗位偏好", exact=True).click()
                    expect(page.locator("#job-recommendations")).to_contain_text("此模式已关闭")
                    form = page.locator("#subscription-form")
                    for key, value in {
                        "name": "每周上海后端",
                        "q": "后端",
                        "location": "上海",
                        "quiet_start": "0",
                        "quiet_end": "0",
                    }.items():
                        form.locator(f"[name={key}]").fill(value)
                    form.get_by_role("button", name="确认创建订阅", exact=True).click()
                    expect(page.locator(".subscription-card")).to_have_count(1)
                    page.get_by_role("button", name="立即检查岗位库", exact=True).click()
                    expect(page.locator("#notice")).to_contain_text("岗位库检查完成", timeout=20000)
                    expect(page.locator("#job-notifications .notification-card")).to_have_count(1)
                    expect(page.locator("#job-notifications .evidence-row")).to_have_count(2)
                    page.get_by_role("button", name="标为已读", exact=True).click()
                    expect(page.locator("#job-notifications .tag")).to_have_text("已读")
                    page.get_by_role("button", name="立即检查岗位库", exact=True).click()
                    expect(page.locator("#notice")).to_contain_text("0 项变化", timeout=20000)
                    expect(page.locator("#job-notifications .notification-card")).to_have_count(1)
                    changed = client.post(
                        "/api/v1/jobs/import",
                        json={
                            "content": JD.replace("10000元/月", "15000元/月"),
                            "source_url": "https://example.com/jobs/a",
                        },
                    )
                    assert changed.status_code == 201 and changed.json()["id"] == a_id
                    page.get_by_role("button", name="立即检查岗位库", exact=True).click()
                    expect(page.locator("#notice")).to_contain_text("1 项变化", timeout=20000)
                    expect(page.locator("#job-notifications .notification-card")).to_have_count(2)
                    page.get_by_role("button", name="暂停订阅", exact=True).click()
                    expect(page.locator(".subscription-card .tag")).to_have_text("已暂停")
                    page.get_by_role("button", name="恢复订阅", exact=True).click()
                    expect(page.locator(".subscription-card .tag")).to_have_text("启用")
                    page.get_by_role("button", name="取消订阅", exact=True).click()
                    expect(page.locator(".subscription-card .tag")).to_have_text("已取消")
                    page.get_by_role("button", name="恢复订阅", exact=True).click()
                    expect(page.locator(".subscription-card .tag")).to_have_text("启用")
                    page.get_by_role("button", name="编辑配置", exact=True).click()
                    form.locator("[name=name]").fill("每周上海后端（已编辑）")
                    form.get_by_role("button", name="确认保存订阅配置", exact=True).click()
                    expect(page.locator(".subscription-card h3")).to_have_text(
                        "每周上海后端（已编辑）"
                    )
                    sub = client.get("/api/v1/job-subscriptions").json()[0]
                    runs_before = len(
                        client.get(f"/api/v1/job-subscriptions/{sub['id']}/runs").json()
                    )
                    # Advance only the due-time of this disposable test subscription.
                    with Session(engine) as db, db.begin():
                        row = db.get(growth.GrowthRecord, sub["id"])
                        row.payload_json = growth.dump(
                            {**json.loads(row.payload_json), "next_run_at": int(time.time())}
                        )
                    for _ in range(100):
                        runs = client.get(f"/api/v1/job-subscriptions/{sub['id']}/runs").json()
                        if len(runs) > runs_before and runs[0]["task"]["status"] == "success":
                            break
                        time.sleep(0.1)
                    else:
                        raise AssertionError("Separate worker did not schedule a due subscription")
                    assert len(client.get("/api/v1/job-notifications").json()) == 2
                    page.get_by_role("button", name="刷新订阅与摘要", exact=True).click()
                    expect(page.locator(".subscription-card h3")).to_have_text(
                        "每周上海后端（已编辑）"
                    )
                    page.get_by_role("button", name="查看运行记录", exact=True).click()
                    expect(page.locator(".subscription-runs")).to_contain_text("检查完成")
                    if output:
                        page.locator("#job-subscriptions").screenshot(
                            path=str(output / "subscriptions-desktop.png")
                        )
                    page.set_viewport_size({"width": 375, "height": 850})
                    expect(page.locator("#sidebar")).to_have_css(
                        "transform", "matrix(1, 0, 0, 1, -240, 0)"
                    )
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                    if output:
                        page.locator("#job-subscriptions").screenshot(
                            path=str(output / "subscriptions-mobile.png")
                        )
                        page.locator("#job-notifications").screenshot(
                            path=str(output / "notifications-mobile.png")
                        )
                    assert not errors, errors
                    assert worker.poll() is None
                    browser.close()
            return {
                "browser": "Chromium",
                "scope": "V2 CP-133/191 local library only",
                "favorites_compare_blocks_undo": True,
                "feedback_undo_modes": True,
                "separate_worker": True,
                "manual_and_scheduled_checks": True,
                "incremental_notifications_and_dedup": True,
                "pause_cancel_restore_edit": True,
                "mobile_width": 375,
                "console_errors": 0,
                "full_v2_exit": False,
            }
        finally:
            worker.terminate()
            _, stderr = worker.communicate(timeout=15)
            server.should_exit = True
            thread.join(timeout=15)
            engine.dispose()
            if thread.is_alive() or worker.returncode != 0 or "Traceback" in stderr:
                raise RuntimeError("Discovery acceptance processes did not stop cleanly")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    with temporary_artifacts(args.output) as args.output:
        result = run(args.output)
        if args.output:
            (args.output / "browser-report.json").write_text(
                json.dumps(result, indent=2), encoding="utf-8"
            )
        print(json.dumps(result))
