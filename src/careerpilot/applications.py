"""User-controlled applications with immutable resume bindings and receipt provenance."""

import json
import re
from datetime import date, datetime, timezone

from careerpilot import growth, resumes
from careerpilot.domain import DomainError
from careerpilot.persistence import IdempotencyRecord
from careerpilot.safety import payload_hash, scan_upload
from careerpilot.states import validate_transition

TERMINAL = {"offer", "rejected", "withdrawn", "no_response"}


def payload(item):
    return {k: v for k, v in item.items() if k not in {"id", "version", "created_at"}}


def create(db, principal, job_id, version_id, key, trace):
    growth.lock_owner(db, principal)
    digest = payload_hash({"action": "application.create", "job": job_id, "resume": version_id})
    previous = db.get(IdempotencyRecord, (principal.user_id, principal.tenant_id, key))
    if previous:
        if previous.request_hash != digest:
            raise DomainError("IDEMPOTENCY_CONFLICT", "幂等键已用于不同申请", 409)
        return growth.data(
            growth.owned(db, principal, json.loads(previous.response_json)["id"], "job_application")
        )
    job = resumes.job_context(db, principal, job_id)
    version = resumes.owned_version(db, principal, version_id)
    resumes.valid_version_facts(db, principal, version)
    root = growth.data(growth.owned(db, principal, version["resume_id"], "resume"))
    if version["job"] and version["job"]["id"] != job_id:
        raise DomainError("JOB_MISMATCH", "简历定制岗位与申请岗位不同", 422)
    if version["job"] and version["job"]["content_hash"] != job["content_hash"]:
        raise DomainError("JOB_CHANGED", "岗位原文已更新，请重新核对定制简历", 409)
    for row in db.scalars(growth.owner_query(principal, "job_application")):
        old = growth.data(row)
        if old["job"]["id"] == job_id and old["status"] not in TERMINAL:
            raise DomainError("DUPLICATE_APPLICATION", "此岗位已有进行中的申请", 409)
    result = growth.save(
        db,
        principal,
        "job_application",
        {
            "job": job,
            "resume_version_id": version_id,
            "resume_content_hash": version["content_hash"],
            "resume_title": root["title"],
            "resume_sequence": version["sequence"],
            "status": "draft",
            "verification": "not_submitted",
            "receipt": None,
            "checklist": None,
            "last_event": {"at": growth.utc_now(), "type": "created", "note": ""},
        },
        trace,
    )
    db.add(
        IdempotencyRecord(
            user_id=principal.user_id,
            tenant_id=principal.tenant_id,
            key=key,
            request_hash=digest,
            response_json=json.dumps({"id": result["id"]}),
        )
    )
    from careerpilot import analytics

    analytics.record(db, principal, "application_recorded", trace, trusted=True)
    return result


def record_external(db, principal, job_id, resume_description, submitted_at, note, key, trace):
    """Record an owner-reported application without fabricating a resume snapshot."""
    growth.lock_owner(db, principal)
    body = {
        "action": "application.external",
        "job": job_id,
        "resume": resume_description,
        "submitted_at": submitted_at,
        "note": note,
    }
    digest = payload_hash(body)
    previous = db.get(IdempotencyRecord, (principal.user_id, principal.tenant_id, key))
    if previous:
        if previous.request_hash != digest:
            raise DomainError("IDEMPOTENCY_CONFLICT", "幂等键已用于不同申请", 409)
        return growth.data(
            growth.owned(db, principal, json.loads(previous.response_json)["id"], "job_application")
        )
    job = resumes.job_context(db, principal, job_id)
    for row in db.scalars(growth.owner_query(principal, "job_application")):
        old = growth.data(row)
        if old["job"]["id"] == job_id and old["status"] not in TERMINAL:
            raise DomainError("DUPLICATE_APPLICATION", "此岗位已有进行中的申请", 409)
    if submitted_at:
        when = datetime.fromisoformat(submitted_at)
        if when.tzinfo is None or when.timestamp() > datetime.now(timezone.utc).timestamp() + 300:
            raise DomainError("INVALID_RECEIPT", "投递时间需包含时区且不能在未来", 422)
    scan_upload((resume_description + "\n" + note).encode(), "application-report.txt")
    result = growth.save(
        db,
        principal,
        "job_application",
        {
            "job": job,
            "resume_version_id": None,
            "resume_content_hash": None,
            "resume_title": resume_description or "外部简历／待补充",
            "resume_sequence": None,
            "resume_basis": "owner_reported_external",
            "status": "reported_submitted",
            "verification": "pending_verification",
            "receipt": None,
            "checklist": None,
            "self_report": {"submitted_at": submitted_at, "note": note, "basis": "owner_reported"},
            "last_event": {"at": growth.utc_now(), "type": "submission_claim", "note": note},
        },
        trace,
    )
    db.add(
        IdempotencyRecord(
            user_id=principal.user_id,
            tenant_id=principal.tenant_id,
            key=key,
            request_hash=digest,
            response_json=json.dumps({"id": result["id"]}),
        )
    )
    from careerpilot import analytics

    analytics.record(db, principal, "application_recorded", trace, trusted=True)
    return result


def active_job(db, principal, item):
    current = resumes.job_context(db, principal, item["job"]["id"])
    deadline = current["deadline"]
    if current["status"] not in {"unverified", "verified"} or (
        deadline and date.fromisoformat(deadline) < datetime.now(timezone.utc).date()
    ):
        raise DomainError("JOB_RECHECK_REQUIRED", "岗位已关闭、过期或状态待核对，请先核对岗位", 409)
    if current["version"] != item["job"]["version"]:
        raise DomainError("JOB_CHANGED", "岗位已更新，请撤回旧草稿并重新建立申请", 409)


def change(db, principal, application_id, target, expected, note, trace, checklist=None):
    growth.lock_owner(db, principal)
    row = growth.owned(db, principal, application_id, "job_application")
    item = growth.data(row)
    if row.version != expected:
        raise DomainError("VERSION_CONFLICT", "申请已更新，请刷新", 412)
    validate_transition("application", item["status"], target)
    if target in {"prepared", "awaiting_user_submission"}:
        active_job(db, principal, item)
        diagnostic = resumes.ats(
            db, principal, resumes.owned_version(db, principal, item["resume_version_id"])
        )
        if not diagnostic["ready_for_preparation"]:
            raise DomainError("RESUME_REVIEW_REQUIRED", "请先处理简历姓名、联系方式或来源问题", 409)
        if target == "prepared" and not checklist:
            raise DomainError("CHECKLIST_REQUIRED", "请确认事实、联系方式、岗位要求及隐私检查", 422)
    scan_upload(note.encode(), "application-note.txt")
    result = payload(item)
    result.update(status=target, last_event={"at": growth.utc_now(), "type": target, "note": note})
    if checklist:
        result["checklist"] = checklist
    return growth.save(db, principal, "job_application", result, trace, row, expected)


def receipt(db, principal, application_id, body, expected, trace):
    growth.lock_owner(db, principal)
    row = growth.owned(db, principal, application_id, "job_application")
    item = growth.data(row)
    if row.version != expected:
        raise DomainError("VERSION_CONFLICT", "申请已更新，请刷新", 412)
    reported = bool(item.get("self_report")) and item["verification"] == "pending_verification"
    if item["status"] != "awaiting_user_submission" and not reported:
        raise DomainError("INVALID_TRANSITION", "请先进入等待本人源站提交状态", 409)
    result = payload(item)
    result["last_event"] = {
        "at": growth.utc_now(),
        "type": "submission_claim",
        "note": body["note"],
    }
    scan_upload(body["note"].encode(), "receipt-note.txt")
    if not body.get("material_id"):
        result["verification"] = "pending_verification"
        return growth.save(db, principal, "job_application", result, trace, row, expected)
    material = growth.data(growth.owned(db, principal, body["material_id"], "material"))
    if material["version"] != body["material_version"]:
        raise DomainError("VERSION_CONFLICT", "回执材料版本已改变", 412)
    text = material["content"]
    positive = re.search(
        r"投递成功|提交成功|已提交|application received|successfully submitted", text, re.I
    )
    negative = re.search(
        r"未提交|未投递|未成功|未完成|尚未|没有提交|提交失败|投递失败|不成功|"
        r"not (?:successfully )?submitted|not received|failed|unsuccessful",
        text,
        re.I,
    )
    context = [item["job"][k] for k in ("title", "company") if item["job"].get(k)]
    when = datetime.fromisoformat(body["submitted_at"])
    if when.timestamp() > datetime.now(timezone.utc).timestamp() + 300:
        raise DomainError("INVALID_RECEIPT", "提交时间不能在未来", 422)
    if (
        not positive
        or negative
        or body["receipt_code"] not in text
        or not context
        or not any(value.casefold() in text.casefold() for value in context)
    ):
        raise DomainError("INVALID_RECEIPT", "回执需包含成功陈述、编号及本岗位或企业信息", 422)
    if not reported:
        validate_transition(
            "application",
            item["status"],
            "submitted",
            {"confirmed": True, "receipt": material["id"]},
        )
    result.update(
        status=(
            item["status"] if reported and item["status"] != "reported_submitted" else "submitted"
        ),
        verification="user_confirmed_receipt",
        receipt={
            "material_id": material["id"],
            "material_version": material["version"],
            "material_hash": payload_hash(text),
            "receipt_code": body["receipt_code"],
            "submitted_at": body["submitted_at"],
            "resume_version_id": item["resume_version_id"],
            "job_id": item["job"]["id"],
            "confirmed_at": growth.utc_now(),
            "basis": "user_confirmed_not_independently_verified",
        },
    )
    return growth.save(db, principal, "job_application", result, trace, row, expected)
