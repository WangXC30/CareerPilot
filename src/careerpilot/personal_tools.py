"""Private recording retention, human-reviewed transcripts and local calendar exchange."""

import base64
import binascii
import json
import time
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import delete

from careerpilot import growth, reporting
from careerpilot.domain import DomainError
from careerpilot.growth_models import GrowthRevision


def voice_view(row):
    return {k: v for k, v in growth.data(row).items() if k != "audio_base64"}


def recording(db, principal, body, trace):
    # Consent is required by the input schema before this function can run.
    growth.owned(db, principal, body["interview_id"], "interview_session")
    try:
        audio = base64.b64decode(body["audio_base64"], validate=True)
    except (ValueError, binascii.Error):
        raise DomainError("INVALID_AUDIO", "音频编码无效", 422) from None
    if not 0 < len(audio) <= 2 * 1024 * 1024 or not (
        audio.startswith(b"\x1a\x45\xdf\xa3")
        or (audio.startswith(b"RIFF") and audio[8:12] == b"WAVE")
    ):
        raise DomainError("INVALID_AUDIO", "仅接受 2 MiB 内 WAV/WebM 录音", 422)
    result = growth.save(
        db,
        principal,
        "voice_record",
        {
            "interview_id": body["interview_id"],
            "audio_base64": body["audio_base64"],
            "mime_type": "audio/wav" if audio.startswith(b"RIFF") else "audio/webm",
            "expires_at": int(time.time()) + body["retention_days"] * 86400,
            "transcript": None,
            "transcription_method": None,
            "audio_deleted": False,
            "notice": "录音仅本地保存；可另行授权离线英文草稿，最终转写须本人核对。",
        },
        trace,
    )
    return {k: v for k, v in result.items() if k != "audio_base64"}


def transcript(db, principal, key, text, trace):
    growth.lock_owner(db, principal)
    row = growth.owned(db, principal, key, "voice_record")
    value = json.loads(row.payload_json)
    if value["expires_at"] <= int(time.time()):
        raise DomainError("NOT_FOUND", "录音已到期", 404)
    growth.scan_upload(text.encode(), "transcript.txt")
    result = growth.save(
        db,
        principal,
        row.kind,
        {**value, "transcript": text, "transcription_method": "human_reviewed"},
        trace,
        row,
        row.version,
    )
    return {k: v for k, v in result.items() if k != "audio_base64"}


def remove_audio(db, principal, key, trace):
    growth.lock_owner(db, principal)
    row = growth.owned(db, principal, key, "voice_record")
    value = json.loads(row.payload_json)
    # Remove prior revisions, including raw audio; never keep a hidden historical copy.
    db.execute(delete(GrowthRevision).where(GrowthRevision.record_id == row.id))
    result = growth.save(
        db,
        principal,
        row.kind,
        {**value, "audio_base64": "", "audio_deleted": True, "transcript_draft": None},
        trace,
        row,
        row.version,
    )
    return {k: v for k, v in result.items() if k != "audio_base64"}


def remove_recording(db, principal, key):
    growth.lock_owner(db, principal)
    row = growth.owned(db, principal, key, "voice_record")
    db.execute(delete(GrowthRevision).where(GrowthRevision.record_id == row.id))
    db.delete(row)
    return {"status": "deleted"}


def calendar_events(db, principal):
    result = []
    for row in db.scalars(growth.owner_query(principal, "milestone")):
        value = growth.data(row)
        result.append(
            {
                "id": row.id,
                "version": row.version,
                "title": value["title"],
                "date": value["target_date"],
                "kind": "milestone",
            }
        )
    for row in db.scalars(growth.owner_query(principal, "job")):
        value = growth.data(row)
        deadline = value.get("fields", {}).get("deadline", {}).get("value")
        if deadline and value.get("status") in {"unverified", "verified"}:
            result.append(
                {
                    "id": row.id,
                    "version": row.version,
                    "title": value["fields"]["title"]["value"] or "待核对岗位",
                    "date": deadline,
                    "kind": "job_deadline",
                }
            )
    for row in db.scalars(growth.owner_query(principal, "calendar_event")):
        value = growth.data(row)
        if value["status"] == "active":
            result.append(value)
    return sorted(result, key=lambda e: (e["date"], e["id"]))


def calendar_candidate(body):
    growth.scan_upload(body["title"].encode(), "calendar-title.txt")
    growth.scan_upload(body.get("preparation_note", "").encode(), "calendar-preparation.txt")
    start = None
    try:
        zone = ZoneInfo(body["timezone"])
    except (ZoneInfoNotFoundError, ValueError):
        raise DomainError("INVALID_TIMEZONE", "请选择有效时区", 422) from None
    if body["time"]:
        local = datetime.fromisoformat(body["date"] + "T" + body["time"])
        candidate = local.replace(tzinfo=zone)
        if candidate.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) != local:
            raise DomainError("INVALID_LOCAL_TIME", "此时区没有该时间，请核对夏令时", 422)
        if candidate.utcoffset() != local.replace(tzinfo=zone, fold=1).utcoffset():
            raise DomainError("AMBIGUOUS_LOCAL_TIME", "此时间出现两次，请换算为 UTC 后填写", 422)
        start = candidate.astimezone(timezone.utc).isoformat()
    return {**body, "starts_at": start, "status": "active", "basis": "owner_provided"}


def save_calendar_event(db, principal, body, trace, key=None, expected=0, record_id=None):
    growth.lock_owner(db, principal)
    row = growth.owned(db, principal, key, "calendar_event") if key else None
    if row and growth.data(row)["status"] != "active":
        raise DomainError("NOT_FOUND", "事件已移除", 404)
    if row:
        existing = growth.data(row)
        body = {
            **{k: v for k, v in existing.items() if k not in {"id", "version", "created_at"}},
            **body,
        }
    return growth.save(
        db,
        principal,
        "calendar_event",
        calendar_candidate(body),
        trace,
        row,
        expected,
        record_id=record_id,
    )


def remove_calendar_event(db, principal, key, expected, trace):
    growth.lock_owner(db, principal)
    row = growth.owned(db, principal, key, "calendar_event")
    return growth.save(
        db,
        principal,
        row.kind,
        {**json.loads(row.payload_json), "status": "removed"},
        trace,
        row,
        expected,
    )


def calendar_sync(db, principal, ids, trace):
    growth.lock_owner(db, principal)
    chosen = [e for e in calendar_events(db, principal) if e["id"] in ids]
    if len(chosen) != len(set(ids)):
        raise DomainError("NOT_FOUND", "选中事件不存在", 404)
    return growth.save(
        db,
        principal,
        "calendar_sync",
        {
            "events": chosen,
            "status": "active",
            "provider": "local_calendar",
            "notice": (
                "本地日历交换，不连接第三方账号。撤销后不能再次导出；"
                "已导入其他软件的副本需在那里删除。"
            ),
        },
        trace,
    )


def ical(db, principal, key):
    growth.lock_owner(db, principal)
    row = growth.owned(db, principal, key, "calendar_sync")
    value = growth.data(row)
    if value["status"] != "active":
        raise DomainError("NOT_FOUND", "日历授权已撤销", 404)
    current = {e["id"]: e for e in calendar_events(db, principal)}
    if any(current.get(e["id"], {}).get("version") != e["version"] for e in value["events"]):
        raise DomainError("CALENDAR_STALE", "事件已变更，请重新选择导出", 409)

    def escape(text):
        return (
            text.replace("\\", "\\\\")
            .replace("\n", "\\n")
            .replace("\r", "")
            .replace(";", "\\;")
            .replace(",", "\\,")
        )

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//CareerPilot//Local calendar//ZH",
        "CALSCALE:GREGORIAN",
    ]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    for event in value["events"]:
        day = date.fromisoformat(event["date"]).strftime("%Y%m%d")
        lines += [
            "BEGIN:VEVENT",
            f"UID:{event['id']}@careerpilot.local",
            f"DTSTAMP:{stamp}",
            (
                "DTSTART:" + datetime.fromisoformat(event["starts_at"]).strftime("%Y%m%dT%H%M%SZ")
                if event.get("starts_at")
                else f"DTSTART;VALUE=DATE:{day}"
            ),
            "CLASS:PRIVATE",
            "TRANSP:TRANSPARENT",
            f"SEQUENCE:{event['version']}",
            "SUMMARY:" + escape(event["title"]),
            "END:VEVENT",
        ]
    # RFC 5545 section 3.1: fold at 75 octets without splitting a UTF-8 character.
    folded = []
    for line in lines + ["END:VCALENDAR", ""]:
        segment, size = "", 0
        for char in line:
            width = len(char.encode("utf-8"))
            if size + width > 75:
                folded.append(segment)
                segment, size = " ", 1
            segment += char
            size += width
        folded.append(segment)
    return "\r\n".join(folded)


def revoke_calendar(db, principal, key, trace):
    growth.lock_owner(db, principal)
    row = growth.owned(db, principal, key, "calendar_sync")
    return growth.save(
        db,
        principal,
        row.kind,
        {**json.loads(row.payload_json), "status": "revoked", "events": []},
        trace,
        row,
        row.version,
    )


def remove_synced_event(db, principal, key, event_id, expected, trace):
    growth.lock_owner(db, principal)
    row = growth.owned(db, principal, key, "calendar_sync")
    value = json.loads(row.payload_json)
    if value["status"] != "active" or event_id not in {e["id"] for e in value["events"]}:
        raise DomainError("NOT_FOUND", "同步事件不存在或已撤销", 404)
    events = [e for e in value["events"] if e["id"] != event_id]
    return growth.save(
        db,
        principal,
        row.kind,
        {**value, "events": events, "status": "active" if events else "revoked"},
        trace,
        row,
        expected,
    )


def quarter_review(db, principal, start, end, reflection, trace):
    growth.lock_owner(db, principal)
    growth.scan_upload(reflection.encode(), "reflection.txt")
    report = reporting.report(db, principal, start, end)
    learning = report["learning"]
    overdue = len(learning["overdue_pending"])
    suggestion = (
        "当前范围有逾期待办，可到学习中心预览调整安排，再决定是否需要改变目标。"
        if overdue
        else "当前范围未发现逾期待办；这不足以判断目标适配性，请结合本人复盘再决定方向。"
    )
    return growth.save(
        db,
        principal,
        "quarter_review",
        {
            "report": report,
            "goal_snapshot": growth.goal_snapshot(db, principal),
            "plan_versions": [
                {"id": r.id, "version": r.version}
                for r in db.scalars(growth.owner_query(principal, "plan"))
            ],
            "reflection": reflection,
            "suggestion": {
                "rule_version": "quarter-review.v1",
                "text": suggestion,
                "basis": {
                    "overdue_pending": overdue,
                    "self_reported_minutes": learning["self_reported_minutes"],
                },
                "automatic_goal_change": False,
            },
            "notice": "复盘不会自动调整目标；请到职业探索预览并确认目标切换，历史计划保留。",
        },
        trace,
    )
