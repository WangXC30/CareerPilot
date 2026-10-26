"""Private, attributed references; external text never becomes capability evidence."""

import ipaddress
import re
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from careerpilot import catalogue, growth
from careerpilot.application import audit
from careerpilot.domain import DomainError, Principal
from careerpilot.growth_models import GrowthRecord, GrowthRevision
from careerpilot.safety import payload_hash, scan_upload

NOTICE = "检索本地登记索引与本人导入记录，不是实时全网搜索；来源陈述不证明技能或项目已验收。"
RESTRICTED = {"xiaohongshu": "小红书", "douyin": "抖音"}
EXTRA_INDEX = [
    (
        "python-community",
        "python",
        "Python 官方社区讨论入口",
        "community",
        "https://discuss.python.org/",
    ),
    (
        "fastapi-code",
        "fastapi",
        "FastAPI 公开代码项目",
        "project",
        "https://github.com/fastapi/fastapi",
    ),
    (
        "python-code",
        "python",
        "CPython 公开代码项目",
        "project",
        "https://github.com/python/cpython",
    ),
]


def reference_url(value):
    if not value:
        return ""
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower()
        if (
            parsed.scheme != "https"
            or parsed.username
            or parsed.password
            or parsed.port not in {None, 443}
            or "." not in host
            or not re.fullmatch(r"[a-z0-9.-]+", host)
            or host.endswith(".")
            or any(c.isspace() or ord(c) < 32 for c in value)
        ):
            raise ValueError()
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise ValueError()
    except ValueError:
        raise DomainError(
            "INVALID_REFERENCE_URL", "请提供不含账号、凭据的 HTTPS 来源链接", 422
        ) from None
    # Sharing tokens/tracking queries are unnecessary for a reference and are never retained.
    return urlunsplit(("https", host, parsed.path or "/", "", ""))


def platform(url):
    host = urlsplit(url).hostname or ""
    for key, domains in {
        "xiaohongshu": ("xiaohongshu.com", "xhslink.com"),
        "douyin": ("douyin.com", "iesdouyin.com"),
        "github": ("github.com",),
    }.items():
        if any(host == domain or host.endswith("." + domain) for domain in domains):
            return key
    return "other"


def listing(db, principal):
    now = growth.utc_now()
    return [
        growth.data(row)
        for row in db.scalars(
            growth.owner_query(principal, "source_document").order_by(
                GrowthRecord.created_at.desc()
            )
        )
        if growth.data(row)["expires_at"] > now
    ]


def owned(db, principal, source_id):
    row = growth.owned(db, principal, source_id, "source_document")
    if growth.data(row)["expires_at"] <= growth.utc_now():
        raise DomainError("SOURCE_EXPIRED", "来源保留期已到，请删除或重新导入", 410)
    return row


def store(db, principal, payload, trace):
    fingerprint = payload_hash(
        {k: v for k, v in payload.items() if k not in {"imported_at", "retrieved_at"}}
    )
    for existing in listing(db, principal):
        if existing["content_hash"] == fingerprint:
            return existing
    return growth.save(
        db,
        principal,
        "source_document",
        {
            **payload,
            "content_hash": fingerprint,
            "expires_at": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
            "trust": "untrusted_reference",
            "capability_evidence": False,
        },
        trace,
    )


def import_document(db, principal, args, trace):
    growth.authorize_import(db, principal)
    args = {**args, "url": reference_url(args["url"])}
    scan_upload(growth.dump(args).encode(), "source-reference.json")
    if not args["url"] and not args["text"]:
        raise DomainError("EMPTY_SOURCE", "请提供来源链接或有权处理的摘录", 422)
    if args["authorization_scope"] == "reference_only" and (args["text"] or args["claims"]):
        raise DomainError("SCOPE_MISMATCH", "仅保存链接时不能同时保存摘录或观点", 422)
    if args["published_on"] and date.fromisoformat(args["published_on"]) > date.today():
        raise DomainError("INVALID_SOURCE_DATE", "来源发布日期不能在未来", 422)
    for claim in args["claims"]:
        if claim["end"] > len(args["text"]) or claim["start"] >= claim["end"]:
            raise DomainError("INVALID_QUOTE", "观点引用需指向本次摘录中的原文范围", 422)
        claim["quote"] = args["text"][claim["start"] : claim["end"]]
    return store(
        db,
        principal,
        {
            **args,
            "platform": platform(args["url"]),
            "access_mode": "user_import",
            "rights_basis": "user_confirmed_local_processing",
            "imported_at": growth.utc_now(),
            "retrieved_at": None,
            "source_type": "user_declared_" + args["declared_type"],
            "verification": "本人提交与标注，尚未独立读取或核实原文",
        },
        trace,
    )


def delete_document(db, principal, source_id, expected, trace):
    growth.lock_owner(db, principal)
    row = growth.owned(db, principal, source_id, "source_document")
    if row.version != expected:
        raise DomainError("VERSION_CONFLICT", "来源版本已更新", 412)
    db.execute(delete(GrowthRevision).where(GrowthRevision.record_id == row.id))
    db.delete(row)
    audit(db, principal, source_id, "source.deleted", expected, trace)


def purge_expired(engine):
    # The worker repeats this maintenance independently of user visits.
    with Session(engine) as db:
        candidates = [
            (row.id, Principal(row.user_id, row.tenant_id))
            for row in db.scalars(
                select(GrowthRecord).where(GrowthRecord.kind == "source_document")
            )
            if growth.data(row)["expires_at"] <= growth.utc_now()
        ]
    for source_id, principal in candidates:
        with Session(engine) as db, db.begin():
            growth.lock_owner(db, principal)
            row = db.scalar(
                growth.owner_query(principal, "source_document").where(GrowthRecord.id == source_id)
            )
            if row and growth.data(row)["expires_at"] <= growth.utc_now():
                delete_document(db, principal, row.id, row.version, "retention")


def discover(
    db, principal, query="", skill_id=None, job_id=None, kind="all", source_platform="all"
):
    skills = None
    if skill_id:
        catalogue.require_skill(db, skill_id)
        skills = {skill_id}
    if job_id:
        job = growth.data(growth.owned(db, principal, job_id, "job"))
        job_skills = {s["skill_id"] for s in job["skills"]}
        skills = job_skills if skills is None else skills & job_skills
    resources = []
    from careerpilot.learning_resources import entries

    personalized = entries(db, principal)
    blocked_urls = {r["url"] for r in personalized if r["status"] != "available"}
    for entry in personalized:
        if entry["status"] != "available":
            continue
        resources.append(
            {
                "id": entry["id"],
                "title": entry["title"],
                "url": entry["url"],
                "kind": "project" if entry["type"] == "open_source" else entry["type"],
                "skill_ids": [entry["skill_id"]],
                "platform": platform(entry["url"]),
                "source_type": "official_entry"
                if entry["source"] == "official_documentation"
                else "curated_entry",
                "status": entry["status"],
                "alternative": entry["alternative"],
                "access_mode": "curated_index",
                "published_on": entry["updated_at_source"],
                "reviewed_at": entry["reviewed_at"],
                "text": "",
                "verification": "仅登记入口，未实时读取；页面内个人观点不因官方域名被验证",
            }
        )
    for key, skill, title, entry_kind, url in EXTRA_INDEX:
        resources.append(
            {
                "id": key,
                "title": title,
                "url": url,
                "kind": entry_kind,
                "skill_ids": [skill],
                "platform": platform(url),
                "source_type": "curated_entry",
                "access_mode": "curated_index",
                "published_on": None,
                "reviewed_at": "2026-10-21",
                "text": "",
                "verification": "登记入口，未实时核验",
            }
        )
    resources += listing(db, principal)
    resources = [r for r in resources if r["url"] not in blocked_urls]
    terms = query.casefold().split()
    filtered = [
        r
        for r in resources
        if (kind == "all" or r["kind"] == kind)
        and (source_platform == "all" or r["platform"] == source_platform)
        and (skills is None or skills & set(r["skill_ids"]))
        and all(
            t in (r["title"] + " " + r["text"] + " " + " ".join(r["skill_ids"])).casefold()
            for t in terms
        )
    ]
    return {
        "mode": "curated_and_private_index",
        "notice": NOTICE,
        "results": filtered,
        "searched_at": growth.utc_now(),
        "restricted_platforms": {
            key: {
                "direct_search": False,
                "authorized_api": False,
                "notice": name
                + "未配置授权接口或合规搜索索引；仅显示本人导入记录，可手动提供链接/摘录",
            }
            for key, name in RESTRICTED.items()
        },
    }


def compare(db, principal, ids):
    items = [growth.data(owned(db, principal, key)) for key in ids]
    grouped: dict[str, list] = {}
    for item in items:
        for claim in item["claims"]:
            topic = claim["topic"].casefold().strip()
            grouped.setdefault(topic, []).append(
                {
                    **claim,
                    "source_id": item["id"],
                    "url": item["url"],
                    "published_on": item["published_on"],
                    "source_type": item["source_type"],
                }
            )
    groups = []
    for topic, claims in grouped.items():
        values = {re.sub(r"\s+", "", c["value"]).casefold() for c in claims}
        domains = {urlsplit(c["url"]).hostname for c in claims if c["url"]}
        groups.append(
            {
                "topic": topic,
                "claims": claims,
                "status": "conflicting"
                if len(values) > 1
                else "agreement_unverified"
                if len({c["source_id"] for c in claims}) > 1
                else "single_source",
                "distinct_hosts": len(domains),
                "verified": False,
            }
        )
    return {
        "groups": groups,
        "sources": items,
        "notice": (
            "仅比较本人标注的同名观点和值；一致不证明真实，差异不自动裁定谁正确；"
            "未检测到差异不代表无语义冲突。"
        ),
    }
