"""Authorized local media indexing with bounded extraction and deletion of all revisions."""

import base64
import binascii
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from careerpilot import growth, source_library
from careerpilot.domain import DomainError
from careerpilot.gateway import reserve
from careerpilot.growth_models import GrowthRecord, GrowthRevision
from careerpilot.safety import scan_upload


def extract(body):
    try:
        raw = base64.b64decode(body["content_base64"], validate=True)
    except (ValueError, binascii.Error):
        raise DomainError("INVALID_MEDIA", "媒体编码无效", 422) from None
    if not 0 < len(raw) <= 8 * 1024 * 1024:
        raise DomainError("MEDIA_TOO_LARGE", "媒体最大 8 MiB", 422)
    kind = body["kind"]
    if kind == "pdf" and not raw.startswith(b"%PDF-"):
        raise DomainError("INVALID_MEDIA", "文件不是 PDF", 422)
    if kind == "image" and not (
        raw.startswith(b"\x89PNG\r\n\x1a\n") or raw.startswith(b"\xff\xd8\xff")
    ):
        raise DomainError("INVALID_MEDIA", "仅支持 PNG 或 JPEG", 422)
    if kind == "video" and not (raw[4:8] == b"ftyp" or raw.startswith(b"\x1a\x45\xdf\xa3")):
        raise DomainError("INVALID_MEDIA", "仅支持 MP4 或 WebM", 422)
    if sys.platform == "win32":
        raise DomainError("EXTRACTOR_UNAVAILABLE", "请使用带本地提取工具的 Compose 服务", 503)
    with tempfile.TemporaryDirectory(prefix="careerpilot-media-") as directory:
        path = Path(directory) / "upload.bin"
        path.write_bytes(raw)
        try:
            result = subprocess.run(
                [sys.executable, "-m", "careerpilot.media_extract", kind, str(path)],
                capture_output=True,
                timeout=25,
                check=True,
                env={
                    "PATH": os.environ.get("PATH", ""),
                    "LANG": "C.UTF-8",
                    "OMP_THREAD_LIMIT": "1",
                    "PYTHONDONTWRITEBYTECODE": "1",
                },
            )
            value = json.loads(result.stdout)
        except (subprocess.SubprocessError, ValueError):
            raise DomainError(
                "EXTRACTION_UNAVAILABLE",
                "提取失败、文件受保护或无可读文字；可改为手动提供授权摘录。",
                422,
            ) from None
    for segment in value["segments"]:
        scan_upload(segment["text"].encode(), "media-excerpt.txt")
    return value


def store(db, principal, body, trace):
    if body.get("rights_confirmed") is not True:
        raise DomainError("RIGHTS_REQUIRED", "请确认文件处理授权", 422)
    source_url = source_library.reference_url(body["source_url"])
    scan_upload(body["title"].encode(), "media-title.txt")
    scan_upload(body["license_basis"].encode(), "license.txt")
    growth.lock_owner(db, principal)
    if len(listing(db, principal)) >= 50:
        raise DomainError("MEDIA_LIMIT", "最多保存 50 份有效媒体索引，请先删除旧索引", 422)
    reserve(db, principal, f"media-extract:{time.strftime('%Y-%m-%d', time.gmtime())}", 1, 30)
    db.commit()
    result = extract(body)
    growth.lock_owner(db, principal)
    if len(listing(db, principal)) >= 50:
        raise DomainError("MEDIA_LIMIT", "最多保存 50 份有效媒体索引，请先删除旧索引", 422)
    return growth.save(
        db,
        principal,
        "media_index",
        {
            "title": body["title"],
            "kind": body["kind"],
            "source_url": source_url,
            "license_basis": body["license_basis"],
            "expires_at": int(time.time()) + body["retention_days"] * 86400,
            "segments": result["segments"],
            "method": result["method"],
            "visibility": "private",
            "notice": (
                "仅保存授权文字索引，原文件处理后删除。"
                "视频仅提取首帧文字，不转写音轨；OCR 可能错误。"
            ),
        },
        trace,
    )


def listing(db, principal, query=""):
    return [
        growth.data(row)
        for row in db.scalars(growth.owner_query(principal, "media_index"))
        if growth.data(row)["expires_at"] > int(time.time())
        and query.strip().casefold()
        in (
            growth.data(row).get("title", "")
            + " "
            + " ".join(s["text"] for s in growth.data(row)["segments"])
        ).casefold()
    ]


def remove(db, principal, key):
    growth.lock_owner(db, principal)
    row = growth.owned(db, principal, key, "media_index")
    db.execute(delete(GrowthRevision).where(GrowthRevision.record_id == row.id))
    db.delete(row)
    return {"status": "deleted"}


def purge_expired(engine):
    with Session(engine) as db, db.begin():
        for row in list(
            db.scalars(
                select(GrowthRecord).where(GrowthRecord.kind.in_({"media_index", "voice_record"}))
            )
        ):
            if growth.data(row)["expires_at"] <= int(time.time()):
                db.execute(delete(GrowthRevision).where(GrowthRevision.record_id == row.id))
                db.delete(row)
