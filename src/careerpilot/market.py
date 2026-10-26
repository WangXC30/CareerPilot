"""V1 saved filters, provenance-preserving merge and explicit job lifecycle."""

import json
import re

from sqlalchemy import and_, or_

from careerpilot import growth, job_policy
from careerpilot.domain import DomainError


def search(db, principal, filters, limit=20, cursor=None, descending=False):
    policy = job_policy.preferences(db, principal)
    query = growth.owner_query(principal, "job")
    record = growth.GrowthRecord
    if cursor:
        cursor_row = growth.owned(db, principal, cursor, "job")
        condition = (
            or_(
                record.created_at < cursor_row.created_at,
                and_(record.created_at == cursor_row.created_at, record.id < cursor),
            )
            if descending
            else or_(
                record.created_at > cursor_row.created_at,
                and_(record.created_at == cursor_row.created_at, record.id > cursor),
            )
        )
        query = query.where(condition)
    query = (
        query.order_by(record.created_at.desc(), record.id.desc())
        if descending
        else query.order_by(record.created_at, record.id)
    )
    result = []
    for row in db.scalars(query):
        item = growth.data(row)
        if item.get("status") == "merged" or job_policy.blocked(item, policy):
            continue
        if not matches_filter(item, filters):
            continue
        result.append(item)
        if len(result) >= limit:
            break
    return result


def matches_filter(item, filters):
    """Shared local-library semantics for interactive searches and subscriptions."""
    if filters.get("q", "").casefold() not in item["content"].casefold():
        return False
    fields = item["fields"]
    if any(
        str(value).casefold() not in (fields[key]["value"] or "").casefold()
        for key, value in filters.items()
        if key in fields and value not in {None, ""}
    ):
        return False
    if filters.get("status") and item["status"] != filters["status"]:
        return False
    if filters.get("salary_min") is not None:
        raw = fields["salary"]["value"] or ""
        amount = re.search(r"(\d+(?:\.\d+)?)\s*([kK万]?)", raw)
        unit = (
            "day"
            if "/天" in raw or "日" in raw
            else "month"
            if "/月" in raw or "月" in raw
            else "unknown"
        )
        if not amount or unit != filters.get("salary_unit", "month"):
            return False
        value = float(amount[1]) * ({"k": 1000, "K": 1000, "万": 10000}.get(amount[2], 1))
        if value < filters["salary_min"]:
            return False
    if filters.get("deadline_before"):
        deadline = fields["deadline"]["value"]
        if not deadline or deadline > filters["deadline_before"]:
            return False
    return True


def merge(db, principal, target_id, source_id, version, trace):
    if target_id == source_id:
        raise DomainError("INVALID_MERGE", "不能合并同一岗位", 422)
    growth.lock_owner(db, principal)
    target = growth.owned(db, principal, target_id, "job")
    source = growth.owned(db, principal, source_id, "job")
    a, b = json.loads(target.payload_json), json.loads(source.payload_json)
    if "merged" in {a["status"], b["status"]}:
        raise DomainError("INVALID_MERGE", "岗位已合并")
    a["sources"] += [s for s in b["sources"] if s not in a["sources"]]
    a.setdefault("merged_records", []).append(
        {
            "id": source.id,
            "version": source.version,
            "content": b["content"],
            "fields": b["fields"],
            "sources": b["sources"],
        }
    )
    result = growth.save(db, principal, "job", a, trace, target, version)
    growth.save(
        db,
        principal,
        "job",
        {**b, "status": "merged", "merged_into": target.id},
        trace,
        source,
        source.version,
    )
    return result


def lifecycle(db, principal, job_id, status, reason, version, trace):
    growth.lock_owner(db, principal)
    row = growth.owned(db, principal, job_id, "job")
    payload = json.loads(row.payload_json)
    if payload["status"] == "merged":
        raise DomainError("INVALID_STATE", "请操作合并后的岗位")
    payload.update(status=status, status_reason=reason, status_basis="user_confirmed")
    return growth.save(db, principal, "job", payload, trace, row, version)
