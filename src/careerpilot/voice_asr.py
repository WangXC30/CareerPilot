"""Explicit-consent offline drafts, never an assessment or a reviewed transcript."""

import base64
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory

from careerpilot import growth
from careerpilot.domain import DomainError
from careerpilot.gateway import reserve


def decode(audio):
    if not shutil.which("pocketsphinx_continuous") or not shutil.which("ffmpeg"):
        raise DomainError("ASR_UNAVAILABLE", "请使用包含离线英文转写工具的 Compose 服务", 503)
    with TemporaryDirectory(prefix="careerpilot-voice-") as directory:
        path = Path(directory) / "recording.bin"
        path.write_bytes(base64.b64decode(audio, validate=True))
        try:
            result = subprocess.run(
                [sys.executable, "-m", "careerpilot.voice_decode", str(path)],
                check=True,
                capture_output=True,
                timeout=38,
                env={
                    "PATH": os.environ.get("PATH", ""),
                    "LANG": "C.UTF-8",
                    "OMP_THREAD_LIMIT": "1",
                    "PYTHONDONTWRITEBYTECODE": "1",
                },
            )
            return json.loads(result.stdout)["text"]
        except (subprocess.SubprocessError, ValueError, KeyError):
            raise DomainError(
                "ASR_FAILED", "未取得可用语音草稿，可回放并手动填写；未改动已核对文字", 422
            ) from None


def transcribe(db, principal, key, expected, trace):
    growth.lock_owner(db, principal)
    row = growth.owned(db, principal, key, "voice_record")
    value = json.loads(row.payload_json)
    if row.version != expected:
        raise DomainError("VERSION_CONFLICT", "录音记录已改变，请刷新", 412)
    if value["audio_deleted"] or value["expires_at"] <= int(time.time()):
        raise DomainError("NOT_FOUND", "录音已删除或到期", 404)
    if not shutil.which("pocketsphinx_continuous"):
        raise DomainError("ASR_UNAVAILABLE", "离线转写工具不可用", 503)
    reserve(db, principal, f"voice-asr:{time.strftime('%Y-%m-%d', time.gmtime())}", 1, 20)
    # Release the DB lock during CPU work. A deletion or edit must win at publication.
    db.commit()
    text = decode(value["audio_base64"])
    growth.scan_upload(text.encode(), "voice-draft.txt")
    growth.lock_owner(db, principal)
    db.expire_all()
    row = growth.owned(db, principal, key, "voice_record")
    current = json.loads(row.payload_json)
    if row.version != expected:
        raise DomainError("VERSION_CONFLICT", "录音在转写期间已改变，请刷新", 412)
    if current["audio_deleted"] or current["expires_at"] <= int(time.time()):
        raise DomainError("NOT_FOUND", "录音已删除或到期", 404)
    result = growth.save(
        db,
        principal,
        row.kind,
        {
            **current,
            "transcript_draft": text,
            "draft_method": "pocketsphinx-en-us",
            "draft_notice": ("仅离线识别前 30 秒英文，可能误识别；请核对后另存，不据此评分。"),
        },
        trace,
        row,
        expected,
    )
    return {k: v for k, v in result.items() if k != "audio_base64"}
