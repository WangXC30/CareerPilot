"""Explicit, revocable peer review of a frozen appeal, never an automatic penalty."""

import json
import time

from sqlalchemy import select

from careerpilot import collaboration, growth
from careerpilot.application import audit
from careerpilot.domain import DomainError, Principal
from careerpilot.persistence import User

NOTICE = "人工复核仅记录所见依据与后续建议，不认定作弊、不修改成绩或能力。"


def valid(db, row):
    value = growth.data(row)
    space = db.get(growth.GrowthRecord, value["workspace_id"])
    exam = db.get(growth.GrowthRecord, value["exam_id"])
    if not space or not exam:
        return False
    workspace = json.loads(space.payload_json)
    return (
        value["status"] != "revoked"
        and value["expires_at"] > int(time.time())
        and space.user_id == row.user_id
        and space.tenant_id == row.tenant_id
        and space.kind == "workspace"
        and workspace["status"] == "active"
        and space.version == value["workspace_version"]
        and value["reviewer_id"] in workspace["members"]
        and exam.kind == "adaptive_exam"
        and exam.user_id == row.user_id
        and exam.tenant_id == row.tenant_id
        and exam.version == value["exam_version"]
    )


def create(db, principal, exam_id, workspace_id, reviewer_id, trace):
    growth.lock_owner(db, principal)
    space = collaboration.workspace(db, principal, workspace_id, admin=True)
    reviewer = db.get(User, reviewer_id)
    if (
        not reviewer
        or reviewer_id == principal.user_id
        or reviewer_id not in collaboration.payload(space)["members"]
    ):
        raise DomainError("REVIEWER_UNAVAILABLE", "请选择本人空间中已接受邀请的另一位成员", 422)
    exam = growth.data(growth.owned(db, principal, exam_id, "adaptive_exam"))
    if exam["status"] != "completed" or not exam.get("appeal"):
        raise DomainError("APPEAL_REQUIRED", "完成练习并先保存本人复核申请，再授权接收人", 409)
    existing = [
        row
        for row in db.scalars(growth.owner_query(principal, "practice_review"))
        if growth.data(row)["exam_id"] == exam_id and valid(db, row)
    ]
    if existing:
        raise DomainError("REVIEW_ALREADY_SHARED", "已有有效复核授权，请查看结果或先撤销", 409)
    return growth.save(
        db,
        principal,
        "practice_review",
        {
            "exam_id": exam_id,
            "exam_version": exam["version"],
            "workspace_id": space.id,
            "workspace_version": space.version,
            "reviewer_id": reviewer_id,
            "reviewer_tenant_id": reviewer.tenant_id,
            "expires_at": int(time.time()) + 7 * 86400,
            "status": "pending",
            "decision": None,
            "snapshot": {
                "bank_version": exam["bank_version"],
                "skills": exam["skills"],
                "answers": exam["answers"],
                "review_flags": exam["review_flags"],
                "appeal": exam["appeal"],
                "summary": exam["summary"],
            },
            "notice": NOTICE + "成员变化、练习变更、七天到期或本人撤销都会终止此授权。",
        },
        trace,
    )


def visible(db, principal, key):
    row = db.get(growth.GrowthRecord, key)
    if row is None or row.kind != "practice_review":
        raise DomainError("NOT_FOUND", "复核授权不存在", 404)
    value = growth.data(row)
    is_owner = row.user_id == principal.user_id and row.tenant_id == principal.tenant_id
    is_reviewer = (
        value["reviewer_id"] == principal.user_id
        and value["reviewer_tenant_id"] == principal.tenant_id
    )
    if not is_owner and (not is_reviewer or not valid(db, row)):
        raise DomainError("NOT_FOUND", "复核授权不存在或已失效", 404)
    return row


def view(db, principal, row):
    value = growth.data(visible(db, principal, row.id))
    return {
        **value,
        "access_active": valid(db, row),
        "is_owner": row.user_id == principal.user_id and row.tenant_id == principal.tenant_id,
        "can_review": principal.user_id == value["reviewer_id"] and value["status"] == "pending",
    }


def listing(db, principal):
    result = []
    for row in db.scalars(
        select(growth.GrowthRecord).where(growth.GrowthRecord.kind == "practice_review")
    ):
        value = growth.data(row)
        own = row.user_id == principal.user_id and row.tenant_id == principal.tenant_id
        assigned = (
            value["reviewer_id"] == principal.user_id
            and value["reviewer_tenant_id"] == principal.tenant_id
        )
        if own or (assigned and valid(db, row)):
            result.append(view(db, principal, row))
    return sorted(result, key=lambda value: (value["created_at"], value["id"]), reverse=True)[:100]


def decide(db, principal, key, conclusion, note, expected, trace):
    row = visible(db, principal, key)
    owner = Principal(row.user_id, row.tenant_id)
    growth.lock_owner(db, owner)
    db.expire_all()
    row = visible(db, principal, key)
    value = json.loads(row.payload_json)
    if principal.user_id != value["reviewer_id"] or not valid(db, row):
        raise DomainError("NOT_FOUND", "复核授权不存在或已失效", 404)
    if value["status"] != "pending":
        raise DomainError("REVIEW_FINISHED", "已保存复核结果，不能重复提交", 409)
    if not note.strip():
        raise DomainError("REVIEW_NOTE_REQUIRED", "请填写实际核对依据与局限", 422)
    growth.scan_upload(note.encode(), "practice-review.txt")
    result = growth.save(
        db,
        owner,
        row.kind,
        {
            **value,
            "status": "reviewed",
            "decision": {
                "conclusion": conclusion,
                "note": note,
                "reviewed_by": principal.user_id,
                "reviewed_at": growth.utc_now(),
                "grade_changed": False,
                "penalty": None,
            },
        },
        trace,
        row,
        expected,
    )
    audit(db, principal, key, "practice_review.human_decision", result["version"], trace)
    return result


def revoke(db, principal, key, expected, trace):
    growth.lock_owner(db, principal)
    row = growth.owned(db, principal, key, "practice_review")
    result = growth.save(
        db,
        principal,
        row.kind,
        {**json.loads(row.payload_json), "status": "revoked"},
        trace,
        row,
        expected,
    )
    return result
