"""Owner-declared written and algorithm practice; no capability certification."""

from collections import defaultdict
from datetime import date

from sqlalchemy import delete

from careerpilot import growth
from careerpilot.domain import DomainError
from careerpilot.growth_models import GrowthRevision


def save(db, principal, body, trace, key=None, expected=0):
    growth.lock_owner(db, principal)
    if date.fromisoformat(body["practiced_on"]) > date.today():
        raise DomainError("INVALID_DATE", "练习日期不能在未来", 422)
    growth.scan_upload(growth.dump(body).encode(), "practice.txt")
    row = growth.owned(db, principal, key, "practice_attempt") if key else None
    return growth.save(
        db,
        principal,
        "practice_attempt",
        {
            **body,
            "basis": "owner_declared",
            "capability_effect": "none",
        },
        trace,
        row,
        expected,
    )


def listing(db, principal):
    return sorted(
        [growth.data(r) for r in db.scalars(growth.owner_query(principal, "practice_attempt"))],
        key=lambda r: (r["practiced_on"], r["created_at"]),
        reverse=True,
    )


def summary(db, principal):
    buckets = defaultdict(list)
    for value in listing(db, principal):
        for tag in value["tags"] or ["未分类"]:
            buckets[(value["kind"], value["difficulty"], tag)].append(value)
    cells = []
    for (kind, difficulty, tag), values in sorted(buckets.items()):
        judged = [v for v in values if v["outcome"] in {"passed", "failed"}]
        passed = sum(v["outcome"] == "passed" for v in judged)
        cells.append(
            {
                "kind": kind,
                "difficulty": difficulty,
                "tag": tag,
                "attempts": len(values),
                "judged": len(judged),
                "passed": passed,
                "success_rate": passed / len(judged) if judged else None,
                "review_suggestion": "复做失败题并检查边界条件，记录错误原因"
                if any(v["outcome"] == "failed" for v in values)
                else "安排延迟复做，核对是否仍能独立完成",
            }
        )
    return {
        "cells": cells,
        "notice": (
            "按本人声明的结果计算，通过率分母仅含明确通过/失败的尝试；同题复做也计一次，"
            "多标签可重复归组。未评定不计分母，不更新技能认证。"
        ),
    }


def remove(db, principal, key):
    growth.lock_owner(db, principal)
    row = growth.owned(db, principal, key, "practice_attempt")
    db.execute(delete(GrowthRevision).where(GrowthRevision.record_id == row.id))
    db.delete(row)
    return {"status": "deleted"}
