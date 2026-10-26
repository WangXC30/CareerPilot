"""Real Chromium recording with a synthetic microphone, disposable storage only."""

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

ALL_STOPPED = "() => captureStreams.every(s=>s.getTracks().every(t=>t.readyState==='ended'))"


def main():
    with tempfile.TemporaryDirectory(prefix="careerpilot-voice-browser-") as directory:
        url = "sqlite:///" + (Path(directory) / "voice.db").as_posix()
        config = Config("alembic.ini")
        config.attributes["database_url"] = url
        command.upgrade(config, "head")
        engine = make_engine(url)
        seed(engine)
        key, token = provision(engine, "Voice synthetic student")
        with Session(engine) as db:
            user = db.get(User, key)
            growth.save(
                db,
                Principal(user.id, user.tenant_id),
                "interview_session",
                {"status": "pending", "questions": [], "facts": [], "answers": []},
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
                browser = playwright.chromium.launch(
                    args=["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"]
                )
                try:
                    page = browser.new_page(viewport={"width": 375, "height": 900})
                    errors = []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.add_init_script("""(() => {
                      window.mediaCalls=0; window.captureStreams=[];
                      const media=navigator.mediaDevices;
                      const original=media.getUserMedia.bind(media);
                      navigator.mediaDevices.getUserMedia=async options=>{
                        window.mediaCalls++;
                        if(window.denyMicrophone) {
                          throw new DOMException('Denied','NotAllowedError');
                        }
                        const stream=await original(options); window.captureStreams.push(stream);
                        if(window.deferMicrophone) await new Promise(r=>window.releaseMicrophone=r);
                        return stream;
                      };
                    })();""")
                    base = f"http://127.0.0.1:{port}/app/"
                    page.goto(base + "#local-tools")
                    page.locator("#connect").click()
                    page.locator("#token").fill(token)
                    page.locator("#account-form button[type=submit]").click()
                    expect(page.locator("#voice-start")).to_be_visible()
                    page.locator("#voice-start").click()
                    expect(page.locator("#notice")).to_contain_text("请先同意")
                    assert page.evaluate("mediaCalls") == 0
                    page.locator("#voice-consent").check()
                    page.locator("#voice-start").click()
                    expect(page.locator("#voice-stop")).to_be_enabled()
                    expect(page.locator("#voice-start")).to_be_disabled()
                    page.wait_for_function("() => voiceCapture.chunks.length > 0")
                    page.locator("#voice-stop").click()
                    expect(page.locator("#tool-result")).to_contain_text("录音已保存")
                    assert page.evaluate(ALL_STOPPED)
                    page.locator("details").filter(has=page.locator(".voice-transcript")).locator(
                        "summary"
                    ).click()
                    page.locator("[data-voice-play]").click()
                    page.wait_for_function("() => document.querySelector('audio')?.readyState >= 1")
                    page.locator(".voice-transcript textarea").fill("本人核对的本地模拟录音")
                    page.locator(".voice-transcript button").click()
                    expect(page.locator("#tool-result")).to_contain_text("转写已保存")
                    page.locator("details").filter(has=page.locator(".voice-transcript")).locator(
                        "summary"
                    ).click()
                    page.locator("[data-voice-audio-delete]").click()
                    expect(page.get_by_text("音频已清除", exact=False)).to_be_visible()
                    assert page.locator("[data-voice-play]").count() == 0
                    assert page.locator(".voice-transcript textarea").input_value() == (
                        "本人核对的本地模拟录音"
                    )
                    page.locator("#voice-consent").check()
                    page.locator("#voice-start").click()
                    expect(page.locator("#voice-stop")).to_be_enabled()
                    page.locator("#voice-consent").uncheck()
                    expect(page.locator("#voice-start")).to_be_enabled()
                    assert page.evaluate(ALL_STOPPED)
                    assert page.locator(".voice-transcript").count() == 1
                    # Navigating away releases the actual synthetic MediaStream tracks.
                    page.locator("#voice-consent").check()
                    page.locator("#voice-start").click()
                    expect(page.locator("#voice-stop")).to_be_enabled()
                    page.goto(base + "#directions")
                    expect(page.get_by_role("heading", name="按方向组织资料")).to_be_visible()
                    page.wait_for_function(ALL_STOPPED)
                    page.goto(base + "#local-tools")
                    expect(page.get_by_role("heading", name="授权资料索引")).to_be_visible()
                    expect(page.locator("#voice-start")).to_be_visible()
                    assert page.locator(".voice-transcript").count() == 1
                    # A late permission response cannot reactivate an abandoned capture.
                    page.evaluate("window.deferMicrophone=true")
                    page.locator("#voice-consent").check()
                    page.locator("#voice-start").click()
                    page.wait_for_function("() => typeof releaseMicrophone === 'function'")
                    page.goto(base + "#directions")
                    expect(page.get_by_role("heading", name="按方向组织资料")).to_be_visible()
                    page.evaluate("releaseMicrophone()")
                    page.wait_for_function(ALL_STOPPED)
                    page.goto(base + "#local-tools")
                    expect(page.get_by_role("heading", name="授权资料索引")).to_be_visible()
                    expect(page.locator("#voice-start")).to_be_visible()
                    page.evaluate("window.deferMicrophone=false;window.denyMicrophone=true")
                    page.locator("#voice-consent").check()
                    page.locator("#voice-start").click()
                    expect(page.locator("#notice")).to_contain_text("Denied")
                    expect(page.locator("#voice-start")).to_be_enabled()
                    assert page.locator(".voice-transcript").count() == 1
                    assert not errors, errors
                    page.close()
                finally:
                    browser.close()
            print(
                "Chromium voice: consent, recording, playback, transcript, purge and races passed"
            )
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            engine.dispose()


if __name__ == "__main__":
    main()
