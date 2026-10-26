"""Private bilingual drafts from individually selected, currently valid facts."""

import re

from careerpilot import directions, growth, resumes
from careerpilot.domain import DomainError


def create(db, principal, translation_id, fact_ids, headline, trace):
    growth.lock_owner(db, principal)
    value = directions.translation_detail(db, principal, translation_id)
    if not value["sources_current"]:
        raise DomainError("SOURCE_UNAVAILABLE", "翻译事实来源已失效，请重新核对", 409)
    if len(set(fact_ids)) != len(fact_ids):
        raise DomainError("DUPLICATE_FACT", "请逐项选择且不要重复", 422)
    sources = {v["id"]: v for v in value["source_facts"]}
    mappings = {v["fact_id"]: v["text"] for v in value["mappings"]}
    items = []
    for key in fact_ids:
        if key not in sources:
            raise DomainError("INVALID_FACT", "所选事实不属于此翻译版本", 422)
        fact = sources[key]
        if fact["kind"] in {"name", "contact"}:
            raise DomainError("PRIVATE_IDENTITY", "此公开草稿不包含姓名或联系方式栏目", 422)
        items.append({"original": fact["text"], "translated": mappings[key]})
    growth.scan_upload(headline.encode(), "profile-headline.txt")
    if re.search(
        r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|(?<!\d)1[3-9]\d{9}(?!\d)", headline + growth.dump(items)
    ):
        raise DomainError("PRIVATE_IDENTITY", "草稿含邮箱或手机号码，请先移除或重新选择事实", 422)
    return growth.save(
        db,
        principal,
        "brand_profile",
        {
            "title": headline,
            "headline": headline,
            "items": items,
            "locale": value["locale"],
            "translation_id": translation_id,
            "translation_version": value["version"],
            "fact_ids": fact_ids,
            "visibility": "private",
            "status": "active",
            "notice": (
                "本人逐项选择的双语事实草稿；尚未公开。请在协作页预览并另行创建有期限、可撤销的分享。"
                "数字检查不证明语义或学历等价。"
            ),
        },
        trace,
    )


def ensure_current(db, principal, value):
    translation = directions.translation_detail(db, principal, value["translation_id"])
    if translation["version"] != value["translation_version"] or not translation["sources_current"]:
        raise DomainError("SOURCE_UNAVAILABLE", "公开草稿来源已失效，请重新选择并分享", 409)
    resumes.valid_facts(db, principal, value["fact_ids"])
