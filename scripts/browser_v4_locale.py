"""Temporary Chromium checks for the bilingual app shell, server error locale and lang marking."""

import re
import socket
import tempfile
import threading
import time
from pathlib import Path

import uvicorn
from alembic import command
from alembic.config import Config
from browser_identity import connect_browser
from playwright.sync_api import expect, sync_playwright

from careerpilot.api import create_app
from careerpilot.cli import provision
from careerpilot.persistence import make_engine
from careerpilot.runtime import run_once
from careerpilot.seed import seed

CJK = re.compile(r"[\u4e00-\u9fff]")
PAGES = [
    "dashboard",
    "explore",
    "market",
    "skills",
    "learning",
    "sources",
    "progress",
    "local-tools",
    "directions",
    "collaboration",
    "portfolio",
    "recruiting",
    "settings",
]
# Shell regions that must not contain hard-coded Chinese in English mode. The language
# selector itself is intentionally bilingual and excluded.
SHELL_TEXT = """() => {
  const parts = [];
  const take = node => { if (node) parts.push(node.innerText || ''); };
  take(document.querySelector('#sidebar'));
  take(document.querySelector('#connect'));
  take(document.querySelector('.page-heading'));
  take(document.querySelector('.page-footer'));
  take(document.querySelector('#locale-scope'));
  for (const option of document.querySelectorAll('#preview-state option')) parts.push(option.text);
  for (const node of document.querySelectorAll('[aria-label]')) {
    if (!node.closest('#view')) parts.push(node.getAttribute('aria-label'));
  }
  parts.push(document.title);
  return parts.join('\\n');
}"""
# Fixed Chinese UI text must sit under lang="zh-CN". Owner text has no declared
# language and explicitly resets inheritance; it is never translated with UI resources.
MISMARKED = """() => {
  const walker = document.createTreeWalker(document.querySelector('#view'), NodeFilter.SHOW_TEXT);
  const bad = [];
  while (walker.nextNode()) {
    const node = walker.currentNode;
    if (node.parentElement.closest('[data-original-content], textarea, option[lang=""]')) continue;
    if (!/[\\u4e00-\\u9fff]/.test(node.textContent)) continue;
    const owner = node.parentElement.closest('[lang]');
    if (!owner || !owner.lang.startsWith('zh')) bad.push(node.textContent.trim().slice(0, 40));
  }
  return bad;
}"""


# Solid CSS backgrounds only; this is not a screen-reader or full WCAG audit.
CONTRAST = """() => {
  const rgba = value => (value.match(/[\\d.]+/g)||[]).map(Number);
  const blend = (top, bottom) => top.slice(0,3).map((v,i)=>v*(top[3]??1)+bottom[i]*(1-(top[3]??1)));
  const luminance = color => color.slice(0,3).map(v=>{
    const s=v/255;return s<=0.04045?s/12.92:((s+0.055)/1.055)**2.4;
  }).reduce((sum,v,i)=>sum+v*[0.2126,0.7152,0.0722][i],0);
  const walker=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT), bad=[];
  while(walker.nextNode()){
    const node=walker.currentNode,el=node.parentElement;
    if(!node.textContent.trim()||!el||el.closest('script,style,svg,option,[aria-hidden="true"],[inert],:disabled'))continue;
    const range=document.createRange();range.selectNodeContents(node);
    if(!range.getBoundingClientRect().width||!el.checkVisibility({checkOpacity:true,checkVisibilityCSS:true}))continue;
    const ancestors=[];for(let p=el;p;p=p.parentElement)ancestors.push(p);
    let bg=[255,255,255];
    for(const p of ancestors.reverse())bg=blend(rgba(getComputedStyle(p).backgroundColor),bg);
    const style=getComputedStyle(el),fg=blend(rgba(style.color),bg),a=luminance(fg),b=luminance(bg);
    const ratio=(Math.max(a,b)+0.05)/(Math.min(a,b)+0.05),size=parseFloat(style.fontSize);
    const needed=size>=24||(size>=18.667&&Number(style.fontWeight)>=700)?3:4.5;
    if(ratio+0.01<needed)bad.push({text:node.textContent.trim().slice(0,45),ratio:ratio.toFixed(2),needed});
  }
  return bad;
}"""


def main():
    with tempfile.TemporaryDirectory(prefix="careerpilot-locale-browser-") as directory:
        url = "sqlite:///" + (Path(directory) / "browser.db").as_posix()
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
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                try:
                    for width in (1440, 375):
                        _uid, token = provision(engine, f"Synthetic locale {width}")
                        check_width(browser, port, token, width, engine)
                finally:
                    browser.close()
            print(
                "Chromium locale: resource parity/shell/server explanations/original text/"
                "profile history/confirmations/stale responses/materials/goals/JD/"
                "learning/assessment/pace/feedback/graph/real assistant/late responses/"
                "language boundaries/keyboard/persistence at 1440/375 passed"
            )
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            engine.dispose()


def check_width(browser, port, token, width, engine):
    context = browser.new_context(viewport={"width": width, "height": 900})
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"http://127.0.0.1:{port}/app/#dashboard")
    connect_browser(page, token)
    page.locator("#ui-locale").select_option("en-US")
    expect(page.locator("html")).to_have_attribute("lang", "en-US")
    parity = page.evaluate(
        """() => {
          const zh = Object.keys(localeResources['zh-CN']).sort();
          const en = Object.keys(localeResources['en-US']).sort();
          const used = [...document.querySelectorAll('[data-i18n]')].map(n => n.dataset.i18n)
            .concat([...document.querySelectorAll('[data-i18n-aria]')]
              .map(n => n.dataset.i18nAria));
          return {
            onlyZh: zh.filter(k => !en.includes(k)),
            onlyEn: en.filter(k => !zh.includes(k)),
            missing: used.filter(k => !(k in localeResources['en-US'])),
          };
        }"""
    )
    assert parity == {"onlyZh": [], "onlyEn": [], "missing": []}, parity
    expect(page.locator("#title")).to_have_text("Growth dashboard")
    expect(page.locator("#view")).to_have_attribute("lang", "en-US")
    shell = page.evaluate(SHELL_TEXT)
    assert not CJK.search(shell), shell
    assert not CJK.search(page.locator("#view").inner_text())
    page.locator("#page-options summary").click()
    for state in ("loading", "empty", "error"):
        page.locator("#preview-state").select_option(state)
        assert not CJK.search(page.locator("#view").inner_text()), state
        assert not CJK.search(page.locator("#notice").inner_text()), state
        assert not CJK.search(page.evaluate(SHELL_TEXT)), state
    page.locator("#preview-state").select_option("default")

    # Reset focus after exercising footer controls, then check the first tab stop.
    page.reload()
    page.keyboard.press("Tab")
    expect(page.locator(".skip")).to_be_focused()
    expect(page.locator(".skip")).to_have_text("Skip to main content")
    if width == 375:
        menu = page.get_by_role("button", name="Open navigation")
        menu.focus()
        page.keyboard.press("Enter")
        expect(menu).to_have_attribute("aria-expanded", "true")
        page.keyboard.press("Escape")
        expect(menu).to_have_attribute("aria-expanded", "false")
        expect(menu).to_be_focused()

    # The local product has no credential UI; its data notice remains accessible.
    expect(page.locator("#account-dialog, #connect, #login-form")).to_have_count(0)
    notice_link = page.locator(".page-footer").get_by_role(
        "link", name="Local use and data notice (new window)"
    )
    expect(notice_link).to_have_attribute("href", "local-use.html#english")
    notice_link.focus()
    with page.expect_popup() as opened:
        page.keyboard.press("Enter")
    notice = opened.value
    expect(notice).to_have_url(re.compile(r"/app/local-use\.html#english$"))
    expect(notice.get_by_role("heading", name="Personal local use", exact=True)).to_be_visible()
    expect(notice.get_by_role("heading", name="Personal local use", exact=True)).to_be_in_viewport()
    expect(notice.locator("#english")).to_have_attribute("lang", "en-US")
    expect(notice.locator("html")).to_have_attribute("lang", "zh-CN")
    assert notice.evaluate("window.opener === null")
    assert not notice.evaluate(
        "document.documentElement.scrollWidth > document.documentElement.clientWidth"
    )
    assert notice.evaluate(CONTRAST) == []
    notice.close()
    check_growth_forms(page, width)
    check_learning_forms(page, width, engine)

    # Every page: shell stays English; Chinese business text is marked zh-CN.
    for name in PAGES:
        page.evaluate(f"location.hash={name!r}")
        expect(page.locator("#title")).not_to_have_text("")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(150)
        shell = page.evaluate(SHELL_TEXT)
        assert not CJK.search(shell), (name, shell)
        assert page.evaluate(MISMARKED) == [], (name, page.evaluate(MISMARKED))
        assert page.evaluate(CONTRAST) == [], (name, page.evaluate(CONTRAST))
        if name != "dashboard":
            expect(page.locator("#title")).to_be_focused()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), name

    # Persistence and switching back to Chinese.
    page.reload()
    expect(page.locator("html")).to_have_attribute("lang", "en-US")
    expect(page.locator("#ui-locale")).to_have_value("en-US")
    page.locator("#ui-locale").select_option("zh-CN")
    expect(page.locator("html")).to_have_attribute("lang", "zh-CN")
    page.evaluate("location.hash='dashboard'")
    expect(page.locator("#title")).to_have_text("成长工作台")
    assert not errors, errors
    context.close()


def check_growth_forms(page, width):
    """Exercise real bilingual confirmations, original text and stale-response safety."""
    page.evaluate("location.hash='settings'")
    expect(page.locator("#export-account")).to_have_text("Export my data")
    profile = page.locator("#profile-form")
    marker = f"本人原文 <b>计算机 {width}</b>"
    year = 2027 if width == 1440 else 2028
    for key, value in {
        "major": marker,
        "academic_year": "大三",
        "graduation_date": f"{year}-06-30",
        "preferred_cities": "上海,北京",
        "weekly_minutes": "600",
        "expected_monthly_salary": "12000",
        "timezone": "America/Los_Angeles",
    }.items():
        profile.locator(f"[name={key}]").fill(value)
    profile.get_by_role("button", name="Preview profile changes").focus()
    page.keyboard.press("Enter")
    preview = page.locator("#profile-preview")
    expect(preview.get_by_role("heading", name="Confirm profile changes")).to_be_focused()
    expect(preview.get_by_role("columnheader", name="Before", exact=True)).to_be_visible()
    expect(preview.get_by_role("columnheader", name="After", exact=True)).to_be_visible()
    expect(preview).to_contain_text(f"Jun 30, {year}")
    expect(preview).to_contain_text(marker)
    assert preview.locator("b").count() == 0  # Owner markup is text, not executable HTML.
    assert preview.locator("[data-original-content]").first.get_attribute("lang") == ""
    page.keyboard.press("Tab")
    expect(preview.get_by_role("region", name="Confirm profile changes")).to_be_focused()
    page.keyboard.press("Tab")
    expect(page.locator(":focus")).to_have_id("confirm-profile")
    page.keyboard.press("Tab")
    expect(preview.get_by_role("button", name="Cancel changes")).to_be_focused()
    page.keyboard.press("Enter")
    expect(profile.get_by_role("button", name="Preview profile changes")).to_be_focused()
    expect(preview.locator("#confirm-profile")).to_have_count(0)

    # A response for the old input cannot restore a save button after an edit.
    pending = []
    page.route("**/api/v1/profile/preview", lambda route: pending.append(route))
    profile.get_by_role("button", name="Preview profile changes").click()
    profile.get_by_label("Major", exact=True).fill(marker + "已修改")
    assert len(pending) == 1
    with page.expect_response("**/api/v1/profile/preview"):
        pending.pop().continue_()
    expect(page.locator("#notice")).to_contain_text("Preview it again before saving")
    expect(preview.locator("#confirm-profile")).to_have_count(0)
    page.unroute("**/api/v1/profile/preview")
    profile.get_by_label("Major", exact=True).fill(marker)
    profile.get_by_role("button", name="Preview profile changes").click()
    expect(preview.get_by_role("heading")).to_be_focused()
    page.keyboard.press("Tab")
    page.keyboard.press("Tab")
    page.keyboard.press("Enter")
    expect(page.locator("#notice")).to_contain_text("Profile saved")
    expect(profile.get_by_label("Major", exact=True)).to_have_value(marker)
    expect(page.locator("#export-account")).to_be_visible()
    page.locator("#profile-history summary").click()
    expect(page.locator("#profile-history")).to_contain_text("600 minutes per week")
    saved_version = re.search(r"v(\d+)", page.locator("#profile-history summary").inner_text())[1]
    profile.get_by_label("Weekly study budget (minutes)", exact=True).fill("300")
    profile.get_by_role("button", name="Preview profile changes").click()
    preview.get_by_role("button", name="Confirm and save profile").click()
    expect(page.locator("#notice")).to_contain_text("Profile saved")
    expect(page.locator("#export-account")).to_be_visible()
    page.locator("#profile-history summary").click()
    page.locator(f'[data-restore-profile="{saved_version}"]').click()
    expect(page.locator("#notice")).to_contain_text("Previous profile restored as a new version")
    expect(profile.get_by_label("Weekly study budget (minutes)", exact=True)).to_have_value("600")
    profile.get_by_role("button", name="Preview profile changes").click()
    expect(preview).to_contain_text("No fields changed.")
    preview.get_by_role("button", name="Cancel changes").click()
    page.locator("#model-consent").check()
    page.get_by_role("button", name="Save consent choice", exact=True).click()
    expect(page.locator("#notice")).to_have_text("Consent saved.")
    page.locator("#model-consent").uncheck()
    page.get_by_role("button", name="Save consent choice", exact=True).click()
    expect(page.locator("#notice")).to_have_text("Consent revoked.")

    # Language switches preserve stored profile values and discard pending confirmations.
    profile.get_by_label("Major", exact=True).fill(marker + "临时编辑")
    profile.get_by_role("button", name="Preview profile changes").click()
    expect(preview.locator("#confirm-profile")).to_be_visible()
    page.locator("#ui-locale").select_option("zh-CN")
    expect(profile.get_by_label("专业", exact=True)).to_have_value(marker)
    expect(preview.locator("#confirm-profile")).to_have_count(0)
    page.locator("#ui-locale").select_option("en-US")
    expect(profile.get_by_label("Major", exact=True)).to_have_value(marker)
    expect(page.locator("#export-account")).to_be_visible()

    material = page.locator("#material-form")
    material.get_by_role("button", name="Parse and preview materials").click()
    expect(page.locator("#notice")).to_have_text("Choose a file or paste materials.")
    content = f"本人完成 Python 和 SQL 课程 {width}"
    material.get_by_label("Or paste materials", exact=True).fill(content)
    material.get_by_role("button", name="Parse and preview materials").click()
    expect(page.locator("#material-preview h3")).to_be_focused()
    claims = page.locator("#confirm-material-form")
    expect(claims.get_by_label("Statement 1", exact=True)).to_have_value(content)
    claims.get_by_role("button").click()
    expect(page.locator("#notice")).to_have_text("Select at least one statement.")
    claims.get_by_label("Keep item 1", exact=True).check()
    claims.get_by_role("button").click()
    expect(page.locator("#notice")).to_contain_text("Skills remain unverified")
    page.evaluate("location.hash='skills'")
    memory = page.locator(".memory-form").filter(has_text=content)
    expect(memory.get_by_label("Memory content", exact=True)).to_have_value(content)
    expect(memory.get_by_label("Statement type", exact=True)).to_have_value("self_report")
    expect(memory.locator("..")).to_have_attribute("lang", "en-US")

    page.evaluate("location.hash='explore'")
    goal_title = f"本人目标 <b>后端 {width}</b>"
    page.locator("#goal-title").fill(goal_title)
    page.get_by_role("button", name="Save goal draft", exact=True).click()
    goal = page.locator("#goals .list-row").filter(has_text=goal_title)
    expect(goal).to_have_count(1)
    goal.get_by_role("button", name="Preview as primary goal").click()
    impact = page.locator("#goal-impact")
    expect(impact.get_by_role("heading")).to_be_focused()
    expect(impact).to_contain_text("Confirmation switches the primary goal")
    impact.get_by_role("button", name="Cancel switch").click()
    expect(goal.get_by_role("button")).to_be_focused()
    goal.get_by_role("button").click()
    expect(impact.get_by_role("heading")).to_be_focused()
    page.keyboard.press("Tab")
    expect(impact.get_by_role("button", name="Confirm primary goal switch")).to_be_focused()
    page.keyboard.press("Enter")
    expect(goal.locator(".tag")).to_contain_text("Primary goal")

    page.evaluate("location.hash='market'")
    job = page.locator("#job-form")
    jd = f"岗位：后端工程师\n公司：本人样本 {width}\n地点：上海\n要求：Python 和 SQL"
    job.get_by_label("Original job description", exact=True).fill(jd)
    job.get_by_role("button", name="Import and extract requirements").click()
    original = page.locator("#jd-original")
    expect(original).to_have_text(jd)
    expect(original).to_have_attribute("lang", "")
    expect(page.locator("#job-detail")).to_have_attribute("lang", "en-US")
    expect(page.locator("#job-detail")).to_contain_text("Unknown")
    expect(page.locator("#job-detail").get_by_label("Status", exact=True)).to_have_value(
        "unverified"
    )
    expect(page.locator("#job-search").get_by_label("Salary unit", exact=True)).to_have_value(
        "month"
    )
    page.locator("#ui-locale").select_option("zh-CN")
    expect(page.locator("#job-form").get_by_label("JD 原文", exact=True)).to_be_visible()
    page.get_by_role("button", name="查看原文与证据", exact=True).last.click()
    expect(page.locator("#job-detail")).to_have_attribute("lang", "zh-CN")
    page.locator("#ui-locale").select_option("en-US")
    expect(page.locator("#job-form").get_by_label("Original job description")).to_be_visible()


def check_learning_forms(page, width, engine):
    page.evaluate("location.hash='explore'")
    milestone = page.locator("#milestone-form")
    outcome_title = f"本人阶段成果 {width}"
    milestone.get_by_label("Stage outcome", exact=True).fill(outcome_title)
    milestone.get_by_label("Target date", exact=True).fill("2027-02-01")
    milestone.get_by_role("button", name="Confirm and add milestone").click()
    expect(page.locator("#notice")).to_have_text("Stage milestone saved.")
    row = page.locator(".list-row").filter(has_text=outcome_title)
    row.get_by_role("button", name="Confirm completion", exact=True).click()
    expect(row.get_by_role("button", name="Mark as pending")).to_be_visible()
    row.get_by_role("button", name="Mark as pending").click()
    expect(row.get_by_role("button", name="Confirm completion", exact=True)).to_be_visible()
    page.evaluate("location.hash='dashboard'")
    assistant = page.locator("#assistant-form")
    expect(assistant.get_by_label("Action", exact=True)).not_to_be_visible()
    assistant.locator("summary").click()
    expect(assistant.get_by_label("Action", exact=True)).to_be_visible()
    assistant.locator('[name="job_ids"]').first.check()
    assistant.get_by_label("Your request", exact=True).fill("Please PLAN my learning")
    assistant.get_by_role("button", name="Ask the growth assistant").focus()
    with page.expect_response("**/api/v1/assistant/runs"):
        page.keyboard.press("Enter")
    assert run_once(engine)
    expect(page.locator("#assistant-result")).to_contain_text("Generated a draft")
    assert not CJK.search(page.locator("#assistant-result").inner_text())
    page.locator("#assistant-result a").click()
    expect(page.locator("#publish-plan")).to_have_text("Confirm and publish plan")
    plan_form = page.locator("#plan-form")
    plan_form.locator('[name="job_ids"]').first.check()
    plan_form.get_by_label("Start date", exact=True).fill("2026-10-24")
    plan_form.get_by_role("button", name="Generate plan draft").focus()
    page.keyboard.press("Enter")
    expect(page.locator("#plan-view h2")).to_be_focused()
    page.keyboard.press("Tab")
    expect(page.locator("#publish-plan")).to_be_focused()
    page.keyboard.press("Enter")
    expect(page.locator("#notice")).to_have_text("Study plan published.")
    first = page.locator(".task-card").first
    first_id = first.get_attribute("data-task-id")
    first.get_by_role("button", name="Lock", exact=True).click()
    expect(first.locator(".tag")).to_contain_text("Locked")
    mode = page.locator("#study-mode-form")
    mode.get_by_label("Study mode", exact=True).select_option("research_busy")
    mode.get_by_role("button", name="Preview pace adjustment").click()
    preview = page.locator("#study-mode-preview")
    expect(preview.get_by_role("heading")).to_be_focused()
    expect(preview).to_contain_text("600 → 300")
    expect(preview.locator("[data-adjustment]")).to_have_attribute("lang", "en-US")
    preview.get_by_role("button", name="Keep current plan").click()
    expect(page.locator("#notice")).to_have_text("Current plan retained.")
    mode.get_by_label("Study mode", exact=True).select_option("research_busy")
    mode.get_by_role("button", name="Preview pace adjustment").click()
    expect(preview.get_by_role("heading")).to_be_focused()
    page.keyboard.press("Tab")  # details summary precedes the decision buttons
    expect(preview.locator("summary")).to_be_focused()
    page.keyboard.press("Tab")
    expect(preview.get_by_role("button", name="Accept adjustment")).to_be_focused()
    page.keyboard.press("Enter")
    expect(page.locator("#notice")).to_have_text("Adjustment accepted; history saved.")
    expect(page.locator(f'[data-task-id="{first_id}"] .tag')).to_contain_text("Locked")
    expect(page.locator("#plan-view")).to_contain_text("Weekly budget：300")
    task = page.locator(".task-card").first
    task_id = task.get_attribute("data-task-id")
    task.get_by_role("button", name="Start task", exact=True).focus()
    page.keyboard.press("Enter")
    task = page.locator(f'[data-task-id="{task_id}"]')
    expect(task.get_by_role("button", name="Pause", exact=True)).to_be_visible()
    outcome = f"本人学习成果 <b>保留 {width}</b>"
    task.get_by_label("Study outcome", exact=True).fill(outcome)
    task.get_by_role("button", name="Record completion").click()
    expect(page.locator("#notice")).to_contain_text("Task updated.")
    expect(page.locator(f'[data-task-id="{task_id}"]')).to_contain_text(outcome)
    assert page.locator(f'[data-task-id="{task_id}"] b').count() == 0
    assert page.evaluate(MISMARKED) == [], page.evaluate(MISMARKED)
    page.locator("#plan-history").click()
    expect(page.locator("#plan-history-view")).to_contain_text("tasks")

    page.evaluate("location.hash='skills'")
    page.get_by_role("button", name="Start Python assessment", exact=True).click()
    exam = page.locator("#assessment-view")
    expect(exam.get_by_role("heading")).to_be_focused()
    expect(exam.locator("fieldset").first).to_have_attribute("lang", "zh-CN")
    for index in range(3):
        exam.locator(f'input[name="q{index}"][value="0"]').check()
    exam.get_by_role("button", name="Submit assessment").click()
    expect(exam).to_contain_text("Assessment not passed.")
    expect(exam.get_by_role("heading")).to_be_focused()
    page.get_by_role("button", name="Start Python assessment", exact=True).click()
    for index, answer in enumerate((1, 0, 2)):
        exam.locator(f'input[name="q{index}"][value="{answer}"]').check()
    exam.get_by_role("button", name="Submit assessment").click()
    expect(exam).to_contain_text("Assessment passed.")
    expect(page.locator("#evidence-ledger")).to_contain_text("Objective assessment")
    assert page.evaluate(MISMARKED) == [], page.evaluate(MISMARKED)
    page.locator("#gap-form input").first.check()
    page.get_by_role("button", name="Analyze skill gaps", exact=True).click()
    expect(page.locator("#gap-result h3")).to_be_focused()
    expect(page.locator("#gap-result")).to_contain_text("Priority")

    # Navigation during the submit-triggered refresh must not inject the old exam
    # into the learning page's empty assessment container.
    page.get_by_role("button", name="Start Python assessment", exact=True).click()
    for index, answer in enumerate((1, 0, 2)):
        exam.locator(f'input[name="q{index}"][value="{answer}"]').check()
    held_skills = []
    page.route("**/api/v1/skills", lambda route: held_skills.append(route))
    with page.expect_response("**/api/v1/assessments/*/submit"):
        exam.get_by_role("button", name="Submit assessment").click()
    expect(page.locator("#view")).to_contain_text("Loading growth records")
    page.evaluate("location.hash='learning'")
    expect(page.locator("#study-resource-feedback")).to_be_visible()
    assert len(held_skills) == 1
    old = held_skills.pop()
    with page.expect_response(old.request.url):
        old.fulfill(response=old.fetch())
    page.unroute("**/api/v1/skills")
    page.evaluate("() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))")
    expect(page.locator("#assessment-view .card")).to_have_count(0)

    page.evaluate("location.hash='market'")
    for index in range(2):
        job = page.locator("#job-form")
        job.get_by_label("Original job description", exact=True).fill(
            f"岗位：本人图谱岗位 {width}-{index}\n公司：本地样例 {index}\n要求：Python 和 SQL"
        )
        job.get_by_role("button", name="Import and extract requirements").click()
        expect(page.locator("#jd-original")).to_contain_text(f"{width}-{index}")
    page.evaluate("location.hash='skills'")
    graph = page.locator("#skill-graph-form")
    expect(graph.locator("input")).to_have_count(3)
    for checkbox in graph.locator("input").all():
        checkbox.check()
    graph.get_by_role("button", name="Generate skill graph").focus()
    page.keyboard.press("Enter")
    result = page.locator("#skill-graph-result")
    expect(result.get_by_role("heading").first).to_be_focused()
    expect(result).to_contain_text("Common to all jobs")
    expect(result.get_by_role("img")).to_have_attribute("aria-label", re.compile("Arrows lead"))
    expect(result.locator(".skill-graph-scroll")).to_have_attribute("tabindex", "0")
    assert page.evaluate(MISMARKED) == [], page.evaluate(MISMARKED)

    page.evaluate("location.hash='learning'")
    resource = page.locator('[data-resource="resource-python"]').first
    resource.locator("summary").click()
    feedback = resource.locator("form")
    feedback.get_by_label("Quality", exact=True).select_option("helpful")
    feedback.get_by_label("Feedback note", exact=True).fill(outcome)
    feedback.get_by_role("button", name="Save my feedback").click()
    expect(page.locator("#notice")).to_contain_text("Feedback saved")
    resource.locator("summary").click()
    expect(feedback.get_by_label("Feedback note", exact=True)).to_have_value(outcome)
    page.locator("#ui-locale").select_option("zh-CN")
    expect(page.locator("#plan-form").get_by_role("button", name="生成计划草案")).to_be_visible()
    expect(page.locator(f'[data-task-id="{task_id}"]')).to_contain_text(outcome)
    page.locator("#ui-locale").select_option("en-US")
    expect(
        page.locator("#plan-form").get_by_role("button", name="Generate plan draft")
    ).to_be_visible()

    # Resolve the latest request before an older response: the latest summary must win.
    page.evaluate("location.hash='dashboard'")
    expect(assistant).to_be_visible()
    assistant.locator("summary").click()
    held = []
    page.route("**/api/v1/assistant/runs/*", lambda route: held.append(route))
    assistant.get_by_label("Action", exact=True).select_option("progress")
    assistant.get_by_label("Your request", exact=True).fill("Read progress")
    with page.expect_response("**/api/v1/assistant/runs"):
        assistant.get_by_role("button").click()
    assert run_once(engine)
    assistant.get_by_label("Action", exact=True).select_option("resources")
    assistant.get_by_label("Your request", exact=True).fill("Find Python resources")
    with page.expect_response("**/api/v1/assistant/runs"):
        assistant.get_by_role("button").click()
    assert run_once(engine)
    expect(page.locator("#assistant-result")).to_contain_text("Queued")
    assert len(held) == 2
    newest = held.pop()
    newest.fulfill(response=newest.fetch())
    expect(page.locator("#assistant-result")).to_contain_text("Found")
    oldest = held.pop()
    with page.expect_response(oldest.request.url):
        oldest.fulfill(response=oldest.fetch())
    expect(page.locator("#assistant-result")).to_contain_text("Found")
    page.unroute("**/api/v1/assistant/runs/*")
    assistant.get_by_label("Action", exact=True).select_option("auto")
    assistant.get_by_label("Your request", exact=True).fill("Send an email")
    with page.expect_response("**/api/v1/assistant/runs"):
        assistant.get_by_role("button").click()
    assert run_once(engine)
    expect(page.locator("#assistant-result")).to_contain_text("not authorized")
    expect(page.locator("#assistant-result a")).to_have_count(0)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")


if __name__ == "__main__":
    main()
