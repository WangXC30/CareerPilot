"""Private, reversible job preferences and feedback; never changes application status."""

from uuid import NAMESPACE_URL, uuid5

from careerpilot import growth
from careerpilot.domain import DomainError

MODES = {"best": "最匹配", "stretch": "可冲刺", "entry": "入门", "explore": "探索方向"}
DEFAULTS: dict[str, object] = {
    "favorite_ids": [],
    "blocked_companies": [],
    "excluded_keywords": [],
    "enabled_modes": list(MODES),
}
FEEDBACK = {"like": 5, "not_interested": -20, "misclassified": -10, "applied": 0}


def preferences(db, principal):
    row = db.scalar(growth.owner_query(principal, "job_preferences"))
    return growth.data(row) if row else {"version": 0, **DEFAULTS}


def save_preferences(db, principal, payload, version, trace):
    growth.lock_owner(db, principal)
    for key in payload["favorite_ids"]:
        growth.owned(db, principal, key, "job")
    row = db.scalar(growth.owner_query(principal, "job_preferences"))
    return growth.save(db, principal, "job_preferences", payload, trace, row, version)


def blocked(job, policy):
    company = (job["fields"]["company"]["value"] or "").strip().casefold()
    if company and company in {c.casefold() for c in policy["blocked_companies"]}:
        return "企业在我的屏蔽名单中"
    if any(word.casefold() in job["content"].casefold() for word in policy["excluded_keywords"]):
        return "命中我的排除关键词"
    return None


def feedback_id(principal, job_id):
    return str(
        uuid5(
            NAMESPACE_URL,
            f"careerpilot:feedback:{principal.tenant_id}:{principal.user_id}:{job_id}",
        )
    )


def feedback_map(db, principal):
    return {
        item["job_id"]: item
        for row in db.scalars(growth.owner_query(principal, "job_feedback"))
        if (item := growth.data(row))
    }


def save_feedback(db, principal, job_id, label, reason, version, trace, undo=False):
    growth.lock_owner(db, principal)
    growth.owned(db, principal, job_id, "job")
    key = feedback_id(principal, job_id)
    row = db.get(growth.GrowthRecord, key)
    if row:
        row = growth.owned(db, principal, key, "job_feedback")
    if undo and row is None:
        raise DomainError("NOT_FOUND", "没有可撤销的反馈", 404)
    before = growth.data(row) if row else {}
    return growth.save(
        db,
        principal,
        "job_feedback",
        {
            "job_id": job_id,
            "label": before.get("label") if undo else label,
            "reason": before.get("reason", "") if undo else reason,
            "active": not undo,
            "basis": "用户自记；已投递反馈不构成投递凭据，也不修改申请状态",
        },
        trace,
        row,
        version,
        record_id=key,
    )
