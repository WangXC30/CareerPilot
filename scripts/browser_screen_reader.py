"""Real Orca/AT-SPI/Chromium checks in an isolated X11 session; no retained evidence."""

import os
import pty
import socket
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import uvicorn
from alembic import command
from alembic.config import Config
from playwright.sync_api import expect, sync_playwright

from careerpilot.api import create_app
from careerpilot.cli import provision
from careerpilot.persistence import make_engine
from careerpilot.seed import seed


def spoken_since(path, offset, *expected):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        text = path.read_text(errors="replace")[offset:]
        spoken = "\n".join(line for line in text.splitlines() if "SPEECH OUTPUT:" in line)
        if all(part in spoken for part in expected):
            return
        time.sleep(0.1)
    raise AssertionError(f"Orca did not announce {expected!r}; observed: {spoken[-1200:]!r}")


def checkpoint(path):
    time.sleep(0.3)
    return len(path.read_text(errors="replace"))


def key(value):
    subprocess.run(["xdotool", "key", "--clearmodifiers", value], check=True)


def capture_reader(descriptor, path):
    # A terminal makes Orca's own debug stream line-buffered. A regular debug file
    # can hold the most recent utterance in its Python buffer until the next event.
    with path.open("wb", buffering=0) as output:
        while True:
            try:
                data = os.read(descriptor, 65536)
            except OSError:
                break
            if not data:
                break
            output.write(data)


def main():
    with tempfile.TemporaryDirectory(prefix="careerpilot-orca-") as directory:
        scratch = Path(directory)
        for name in ("config", "cache", "data", "runtime", "logs", "speech", "prefs"):
            (scratch / name).mkdir(mode=0o700)
        os.environ.update(
            {
                "DISPLAY": ":91",
                "XDG_CONFIG_HOME": str(scratch / "config"),
                "XDG_CACHE_HOME": str(scratch / "cache"),
                "XDG_DATA_HOME": str(scratch / "data"),
                "XDG_RUNTIME_DIR": str(scratch / "runtime"),
                "NO_AT_BRIDGE": "0",
                "ACCESSIBILITY_ENABLED": "1",
                "GTK_A11Y": "always",
                "SPEECHD_ADDRESS": "unix_socket:" + str(scratch / "speech.sock"),
                "ALSA_CONFIG_PATH": str(scratch / "alsa.conf"),
            }
        )
        audio = scratch / "speech.raw"
        (scratch / "alsa.conf").write_text(
            "pcm.null { type null }\n"
            'pcm.audit { type file slave.pcm "null" '
            f'file "{audio}" format "raw" }}\n',
            encoding="utf-8",
        )
        (scratch / "speech" / "speechd.conf").write_text(
            f'LogLevel 4\nLogDir "{scratch / "logs"}"\n'
            'AddModule "espeak-ng" "sd_espeak-ng" "/etc/speech-dispatcher/modules/espeak-ng.conf"\n'
            'DefaultModule espeak-ng\nAudioOutputMethod "alsa"\nAudioALSADevice "audit"\n'
            'DefaultLanguage "en"\n',
            encoding="utf-8",
        )
        url = "sqlite:///" + (scratch / "browser.db").as_posix()
        config = Config("alembic.ini")
        config.attributes["database_url"] = url
        command.upgrade(config, "head")
        engine = make_engine(url)
        seed(engine)
        _, token = provision(engine, "Synthetic screen reader account")
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        server = uvicorn.Server(
            uvicorn.Config(create_app(url), host="127.0.0.1", port=port, log_level="error")
        )
        thread = threading.Thread(target=server.run, daemon=True)
        processes = []
        reader_log = scratch / "orca.log"
        reader_descriptor = None
        slave = None
        reader_thread = None
        try:
            with (scratch / "runtime.log").open("w") as output:
                reader_descriptor, slave = pty.openpty()
                reader_thread = threading.Thread(
                    target=capture_reader, args=(reader_descriptor, reader_log), daemon=True
                )
                reader_thread.start()
                processes.append(
                    subprocess.Popen(
                        ["Xvfb", ":91", "-screen", "0", "1600x1000x24", "-nolisten", "tcp"],
                        stdout=output,
                        stderr=output,
                    )
                )
                time.sleep(0.5)
                processes.append(subprocess.Popen(["openbox"], stdout=output, stderr=output))
                subprocess.run(
                    [
                        "gsettings",
                        "set",
                        "org.gnome.desktop.interface",
                        "toolkit-accessibility",
                        "true",
                    ],
                    stdout=output,
                    stderr=output,
                    check=True,
                )
                processes.append(
                    subprocess.Popen(
                        [
                            "speech-dispatcher",
                            "-s",
                            "-C",
                            str(scratch / "speech"),
                            "-S",
                            str(scratch / "speech.sock"),
                            "-t",
                            "0",
                            "-L",
                            str(scratch / "logs"),
                        ],
                        stdout=output,
                        stderr=output,
                    )
                )
                time.sleep(0.5)
                processes.append(
                    subprocess.Popen(
                        [
                            "/usr/bin/orca",
                            "--debug-file=/dev/stdout",
                            "-u",
                            str(scratch / "prefs"),
                        ],
                        stdout=slave,
                        stderr=output,
                        cwd=scratch,
                    )
                )
                os.close(slave)
                slave = None
                thread.start()
                for _ in range(100):
                    if server.started:
                        break
                    time.sleep(0.05)
                assert server.started
                time.sleep(2)
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(
                        headless=False,
                        args=[
                            "--force-renderer-accessibility",
                            "--disable-dev-shm-usage",
                            "--disable-features=Translate,TranslateUI",
                        ],
                    )
                    try:
                        for locale, labels in (
                            (
                                "en-US",
                                (
                                    "Sign in or register",
                                    "Access token",
                                    "Major",
                                    "Academic year",
                                    "Education",
                                    "Allow external models",
                                    "Consent revoked.",
                                    "Confirm profile changes",
                                    "Confirm and save profile",
                                    "Preview profile changes",
                                ),
                            ),
                            (
                                "zh-CN",
                                (
                                    "登录或注册",
                                    "访问令牌",
                                    "专业",
                                    "年级",
                                    "学历",
                                    "允许外部模型",
                                    "授权已撤销。",
                                    "确认档案变更",
                                    "确认保存档案",
                                    "预览档案变更",
                                ),
                            ),
                        ):
                            audio_offset = audio.stat().st_size if audio.exists() else 0
                            page = browser.new_page(viewport={"width": 1440, "height": 950})
                            page.add_init_script(
                                f"localStorage.setItem('careerpilot-ui-locale', '{locale}')"
                            )
                            page.goto(f"http://127.0.0.1:{port}/app/#settings")
                            page.wait_for_load_state("networkidle")
                            time.sleep(2)
                            window = subprocess.check_output(
                                ["xdotool", "search", "--onlyvisible", "--name", "CareerPilot"],
                                text=True,
                            ).splitlines()[-1]
                            subprocess.run(
                                ["xdotool", "windowactivate", "--sync", window], check=True
                            )
                            offset = checkpoint(reader_log)
                            page.locator("#connect").click()
                            expect(page.get_by_role("dialog", name=labels[0])).to_be_visible()
                            spoken_since(reader_log, offset, labels[0], "heading 2")
                            offset = checkpoint(reader_log)
                            page.locator("#token").focus()
                            spoken_since(reader_log, offset, labels[1], "password")
                            page.locator("#token").fill(token)
                            page.locator("#account-form button[type=submit]").click()
                            expect(page.locator("#profile-form")).to_be_visible()
                            page.wait_for_load_state("networkidle")
                            time.sleep(1)
                            page.bring_to_front()
                            offset = checkpoint(reader_log)
                            page.locator("#profile-form input[name=major]").focus()
                            spoken_since(reader_log, offset, labels[2], "entry", "required")
                            offset = checkpoint(reader_log)
                            key("Tab")
                            expect(
                                page.locator("#profile-form input[name=academic_year]")
                            ).to_be_focused()
                            spoken_since(reader_log, offset, labels[3], "entry", "required")
                            offset = checkpoint(reader_log)
                            page.locator("#profile-form select[name=education]").focus()
                            spoken_since(reader_log, offset, labels[4], "combo box")
                            offset = checkpoint(reader_log)
                            page.locator("#model-consent").focus()
                            spoken_since(reader_log, offset, labels[5], "check box", "not checked")
                            offset = checkpoint(reader_log)
                            key("space")
                            expect(page.locator("#model-consent")).to_be_checked()
                            spoken_since(reader_log, offset, "'checked'")
                            offset = checkpoint(reader_log)
                            key("space")
                            expect(page.locator("#model-consent")).not_to_be_checked()
                            spoken_since(reader_log, offset, "not checked")
                            offset = checkpoint(reader_log)
                            page.locator("#save-consent").focus()
                            spoken_since(
                                reader_log,
                                offset,
                                page.locator("#save-consent").inner_text(),
                                "button",
                            )
                            offset = checkpoint(reader_log)
                            key("Return")
                            spoken_since(reader_log, offset, labels[6])
                            for name, value in {
                                "major": "Computer Science",
                                "academic_year": "Year 3",
                                "graduation_date": "2027-06-30",
                                "preferred_cities": "Shanghai",
                                "weekly_minutes": "600",
                            }.items():
                                page.locator(f"#profile-form [name={name}]").fill(value)
                            offset = checkpoint(reader_log)
                            page.locator("#profile-form button[type=submit]").focus()
                            spoken_since(reader_log, offset, labels[9], "button")
                            deadline = time.monotonic() + 8
                            while not audio.exists() or audio.stat().st_size <= audio_offset:
                                assert time.monotonic() < deadline, "No real synthesized audio"
                                time.sleep(0.1)
                            with audio.open("rb") as stream:
                                stream.seek(audio_offset)
                                assert any(stream.read()), "Synthesized audio is all silence"
                            offset = checkpoint(reader_log)
                            key("Return")
                            spoken_since(reader_log, offset, labels[7], "heading 3")
                            offset = checkpoint(reader_log)
                            page.locator("#confirm-profile").focus()
                            spoken_since(reader_log, offset, labels[8], "button")
                            offset = checkpoint(reader_log)
                            page.locator("#cancel-profile").focus()
                            spoken_since(
                                reader_log,
                                offset,
                                page.locator("#cancel-profile").inner_text(),
                                "button",
                            )
                            offset = checkpoint(reader_log)
                            key("Return")
                            spoken_since(reader_log, offset, labels[9], "button")
                            print(
                                f"Orca {locale}: dialog/password, required fields, native Tab, "
                                "select, checkbox/state, live status, preview heading, "
                                "confirm/cancel focus and real synthesized audio passed",
                                flush=True,
                            )
                            page.close()
                        text = reader_log.read_text(errors="replace")
                        assert processes[-1].poll() is None
                        assert (
                            "SPEECH: Using speech server factory: speechdispatcherfactory" in text
                        )
                        assert "SPEECH DISPATCHER: Speaking" in text
                        assert "SPEECH: Not available" not in text
                        dispatcher_log = (scratch / "logs" / "speech-dispatcher.log").read_text(
                            errors="replace"
                        )
                        assert "Module espeak-ng started successfully" in dispatcher_log
                        print(
                            "Real Orca/AT-SPI/Speech Dispatcher/eSpeak checks passed; "
                            "temporary output removed on exit."
                        )
                    finally:
                        browser.close()
        finally:
            server.should_exit = True
            if thread.is_alive():
                thread.join(timeout=10)
            for process in reversed(processes):
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            if slave is not None:
                os.close(slave)
            if reader_thread:
                reader_thread.join(timeout=2)
            if reader_descriptor is not None:
                os.close(reader_descriptor)
            engine.dispose()


if __name__ == "__main__":
    main()
