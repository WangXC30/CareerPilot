"""Career-direction views preserve shared facts; translations require explicit review."""

import re

from careerpilot import growth, resumes
from careerpilot.domain import DomainError


def validate_selection(db, principal, body):
    facts = resumes.valid_facts(db, principal, body["fact_ids"])
    projects = [
        growth.data(growth.owned(db, principal, key, "practice_project"))
        for key in body["project_ids"]
    ]
    return {"facts": facts, "projects": projects}


def save_direction(db, principal, body, trace, key=None, expected=0):
    growth.lock_owner(db, principal)
    validate_selection(db, principal, body)
    growth.scan_upload(body["title"].encode(), "direction.txt")
    row = growth.owned(db, principal, key, "direction_view") if key else None
    return growth.save(db, principal, "direction_view", body, trace, row, expected)


def direction_detail(db, principal, key):
    row = growth.owned(db, principal, key, "direction_view")
    value = growth.data(row)
    # Resolve facts at read time. A withdrawn fact never remains usable through a view.
    facts = [resumes.fact(db, principal, key) for key in value["fact_ids"]]
    return {
        **value,
        "facts": facts,
        "projects": [
            growth.data(growth.owned(db, principal, key, "practice_project"))
            for key in value["project_ids"]
        ],
        "history": growth.history(db, principal, key, "direction_view"),
    }


def restore_direction(db, principal, key, target, expected, trace):
    row = growth.owned(db, principal, key, "direction_view")
    historical = next(
        (r for r in growth.history(db, principal, key, row.kind) if r["version"] == target), None
    )
    if historical is None:
        raise DomainError("NOT_FOUND", "历史版本不存在", 404)
    return save_direction(db, principal, historical["payload"], trace, key, expected)


def translate(db, principal, body, trace):
    version = resumes.owned_version(db, principal, body["resume_version_id"])
    resumes.valid_facts(db, principal, [f["id"] for f in version["facts"]])
    mappings = {item["fact_id"]: item["text"] for item in body["mappings"]}
    if len(mappings) != len(body["mappings"]) or set(mappings) != {
        f["id"] for f in version["facts"]
    }:
        raise DomainError("INCOMPLETE_TRANSLATION", "请逐条映射全部事实且不要重复", 422)
    for fact in version["facts"]:
        translated = mappings[fact["id"]]
        growth.scan_upload(translated.encode(), "translation.txt")
        if sorted(re.findall(r"\d+(?:[.,]\d+)?", fact["text"])) != sorted(
            re.findall(r"\d+(?:[.,]\d+)?", translated)
        ):
            raise DomainError("TRANSLATION_FACT_MISMATCH", "数字与日期不一致，请核对翻译", 422)
    return growth.save(
        db,
        principal,
        "resume_translation",
        {
            "resume_version_id": version["id"],
            "locale": body["locale"],
            "mappings": body["mappings"],
            "source_facts": version["facts"],
            "review": "user_reviewed_translation",
            "visibility": "private",
            "notice": (
                "数字一致性已检查；语义、专有名词与学历对应关系由本人逐条复核，不构成自动认证。"
            ),
        },
        trace,
    )


def translation_detail(db, principal, key):
    value = growth.data(growth.owned(db, principal, key, "resume_translation"))
    sources = [resumes.fact(db, principal, f["id"]) for f in value["source_facts"]]
    return {
        **value,
        "sources_current": all(f["source_status"] == "current" for f in sources),
        "brand_advice": [
            "公开前移除电话、住址与不必要的个人身份信息。",
            "只陈述有来源的成果，不将自述称为独立认证。",
            "作品使用独立分享链接，逐项选择字段并设置到期时间。",
        ],
    }


def matching_snapshot(db, principal, job_id, trace):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from careerpilot import matching

    growth.lock_owner(db, principal)
    job = growth.data(growth.owned(db, principal, job_id, "job"))
    context = matching.context(db, principal)
    today = datetime.now(ZoneInfo(context["profile"].get("timezone", "Asia/Shanghai"))).date()
    return growth.save(
        db,
        principal,
        "matching_snapshot",
        {
            "job": job,
            "context": context,
            "as_of_date": today.isoformat(),
            "catalogue_snapshot": context["catalogue"],
            "result": matching.evaluate(job, context, today),
            "rule_version": matching.RULE_VERSION,
        },
        trace,
    )


def replay_matching(db, principal, key):
    from datetime import date

    from careerpilot import matching

    item = growth.data(growth.owned(db, principal, key, "matching_snapshot"))
    result = matching.evaluate(
        item["job"],
        {**item["context"], "catalogue": item["catalogue_snapshot"]},
        date.fromisoformat(item["as_of_date"]),
        rule_version=item["rule_version"],
    )
    return {
        "result": result,
        "identical": result == item["result"],
        "rule_version": item["rule_version"],
        "weight_version": item["context"].get("policy", {}).get("version", "matching.weights.v1"),
        "catalogue_versions": {k: v["version"] for k, v in item["catalogue_snapshot"].items()},
        "as_of_date": item["as_of_date"],
        "notice": "使用冻结的岗位、用户证据、词库、日期与权重；不代表当前匹配。",
    }
