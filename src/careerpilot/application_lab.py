"""A disconnected application simulator and explicit, uncertainty-aware offer comparison."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from careerpilot import applications, growth, resumes, tools
from careerpilot.application import audit
from careerpilot.domain import DomainError, Principal
from careerpilot.growth_models import GrowthRecord
from careerpilot.platform_models import Approval

SPEC = tools.ToolSpec(
    "application.local_simulation",
    2,
    {"type": "object"},
    frozenset({"student", "operator", "admin"}),
)


def preview(db, principal, application_id):
    row = growth.owned(db, principal, application_id, "job_application")
    application = growth.data(row)
    if application["status"] in applications.TERMINAL:
        raise DomainError("APPLICATION_CLOSED", "申请已结束，请核对真实求职记录", 409)
    applications.active_job(db, principal, application)
    version = resumes.owned_version(db, principal, application["resume_version_id"])
    resumes.valid_facts(db, principal, [f["id"] for f in version["facts"]])
    return {
        "application_id": row.id,
        "application_version": row.version,
        "resume_version_id": version["id"],
        "material_hash": version["content_hash"],
        "job_id": application["job"]["id"],
        "job_title": application["job"]["title"],
        "company": application["job"]["company"],
        "job_version": application["job"]["version"],
        "job_content_hash": application["job"]["content_hash"],
        "channel": "local_simulator",
        "notice": "断网本地申请模拟器；不会向雇主发送材料，也不会将真实申请标记为已投递。",
    }


def check_attempts(db, principal, value):
    for row in db.scalars(growth.owner_query(principal, "application_simulation")):
        item = growth.data(row)
        if item["preview"]["application_id"] != value["application_id"]:
            continue
        if item["status"] in {"in_progress", "unknown_needs_manual_review"}:
            raise DomainError("RESULT_UNCERTAIN", "已有演练执行中或结果未知，请先人工核对", 409)
        if item["preview"].get("application_version") == value["application_version"]:
            raise DomainError("ALREADY_ATTEMPTED", "此申请版本已经演练，不能重复执行", 409)


def approve(db, principal, application_id, digest, trace):
    growth.lock_owner(db, principal)
    value = preview(db, principal, application_id)
    if growth.payload_hash(value) != digest:
        raise DomainError("PREVIEW_CHANGED", "材料或岗位已变化，请重新预览", 412)
    check_attempts(db, principal, value)
    ticket = tools.issue_approval(db, principal, SPEC.name, application_id, value)
    return {"approval_id": ticket, "preview": value}


def revoke_approval(db, principal, key, trace):
    growth.lock_owner(db, principal)
    row = db.get(Approval, key)
    if row is None or (row.user_id, row.tenant_id, row.action) != (
        principal.user_id,
        principal.tenant_id,
        SPEC.name,
    ):
        raise DomainError("NOT_FOUND", "审批不存在", 404)
    if row.consumed:
        raise DomainError("APPROVAL_CLOSED", "审批已执行或撤销，请查看演练历史", 409)
    row.consumed = 1
    audit(db, principal, row.target, "application_simulation.approval_revoked", 0, trace)
    return {"status": "revoked"}


def simulator(payload):
    return {
        "kind": "simulated_receipt",
        "accepted": True,
        "material_hash": payload["material_hash"],
    }


def submit(db, principal, application_id, approval_id, trace):
    growth.lock_owner(db, principal)
    value = preview(db, principal, application_id)
    tools.authorize_tool(db, principal, SPEC, value, target=application_id, approval_id=approval_id)
    check_attempts(db, principal, value)
    run = growth.save(
        db,
        principal,
        "application_simulation",
        {"preview": value, "status": "in_progress", "receipt": None, "retry_allowed": False},
        trace,
    )
    # Commit consumption before calling any connector; an uncertain result is never blindly retried.
    db.commit()
    row = growth.owned(db, principal, run["id"], "application_simulation")
    try:
        receipt = simulator(value)
        state = "simulated"
    except Exception:
        receipt, state = None, "unknown_needs_manual_review"
    growth.lock_owner(db, principal)
    db.refresh(row)
    if growth.data(row)["status"] != "in_progress":
        return growth.data(row)
    result = growth.save(
        db,
        principal,
        row.kind,
        {"preview": value, "status": state, "receipt": receipt, "retry_allowed": False},
        trace,
        row,
        row.version,
    )
    return result


def review_unknown(db, principal, key, note, expected, trace):
    growth.lock_owner(db, principal)
    row = growth.owned(db, principal, key, "application_simulation")
    value = growth.data(row)
    if value["status"] != "unknown_needs_manual_review":
        raise DomainError("REVIEW_UNAVAILABLE", "只有未知演练结果需要人工核对", 409)
    growth.scan_upload(note.encode(), "simulation-review.txt")
    return growth.save(
        db,
        principal,
        row.kind,
        {
            "preview": value["preview"],
            "status": "reviewed_unknown",
            "receipt": None,
            "retry_allowed": False,
            "manual_review": {"note": note, "at": growth.utc_now(), "basis": "owner_review"},
        },
        trace,
        row,
        expected,
    )


def recover_uncertain(engine):
    """Mark abandoned local submission attempts uncertain without retrying them."""
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    count = 0
    with Session(engine) as db:
        rows = list(
            db.scalars(
                select(GrowthRecord).where(
                    GrowthRecord.kind == "application_simulation",
                    GrowthRecord.created_at < cutoff,
                )
            )
        )
        for row in rows:
            payload = growth.data(row)
            if payload["status"] != "in_progress":
                continue
            principal = Principal(row.user_id, row.tenant_id)
            growth.lock_owner(db, principal)
            db.refresh(row)
            payload = growth.data(row)
            if payload["status"] != "in_progress":
                continue
            growth.save(
                db,
                principal,
                row.kind,
                {
                    "preview": payload["preview"],
                    "status": "unknown_needs_manual_review",
                    "receipt": None,
                    "retry_allowed": False,
                },
                "recovery",
                row,
                row.version,
            )
            count += 1
        db.commit()
    return count


def compare_offers(db, principal, body, trace):
    growth.lock_owner(db, principal)
    weights = body["weights"]
    if sum(weights.values()) != 100:
        raise DomainError("INVALID_WEIGHTS", "比较权重总和须为 100", 422)
    rows = []
    for offer in body["offers"]:
        growth.scan_upload(offer["name"].encode(), "offer.txt")
        growth.scan_upload(offer.get("source_basis", "").encode(), "offer-source.txt")
        for fact in offer.get("facts", {}).values():
            if fact:
                growth.scan_upload(fact.encode(), "offer-fact.txt")
        missing = [key for key in weights if offer["ratings"].get(key) is None]
        lower = sum(weights[k] * (offer["ratings"].get(k) or 0) / 5 for k in weights)
        upper = lower + sum(weights[k] for k in missing)
        rows.append(
            {
                **offer,
                "facts_status": "owner_provided_unverified",
                "ratings_basis": "owner_subjective",
                "score_lower": round(lower, 2),
                "score_upper": round(upper, 2),
                "unknown_dimensions": missing,
            }
        )
    return growth.save(
        db,
        principal,
        "offer_comparison",
        {
            "weights": weights,
            "offers": rows,
            "notice": "评分由本人提供，区间保留未知项；不构成薪酬预测或自动接受 Offer。",
            "decision": None,
        },
        trace,
    )


def decide_offer(db, principal, key, offer_index, note, expected, trace):
    growth.lock_owner(db, principal)
    row = growth.owned(db, principal, key, "offer_comparison")
    value = growth.data(row)
    if offer_index is not None and not 0 <= offer_index < len(value["offers"]):
        raise DomainError("INVALID_OFFER", "所选方案不存在", 422)
    growth.scan_upload(note.encode(), "offer-decision.txt")
    return growth.save(
        db,
        principal,
        row.kind,
        {
            "weights": value["weights"],
            "offers": value["offers"],
            "notice": value["notice"],
            "decision": {
                "offer_index": offer_index,
                "name": value["offers"][offer_index]["name"] if offer_index is not None else None,
                "note": note,
                "at": growth.utc_now(),
                "basis": "owner_review",
                "external_acceptance": False,
            },
        },
        trace,
        row,
        expected,
    )
