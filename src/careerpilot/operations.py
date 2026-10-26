"""Public catalogue governance and privacy-preserving support, without an owner bypass."""

import json
from collections import Counter
from datetime import datetime
from uuid import NAMESPACE_URL, uuid5

from jsonschema import Draft202012Validator
from sqlalchemy import func, select, update

from careerpilot import catalogue, growth, matching, reporting, source_library
from careerpilot.application import audit
from careerpilot.domain import DomainError, Principal
from careerpilot.persistence import AuditEvent, affected, new_id, utc_now
from careerpilot.platform_models import BackgroundTask, ModelUsage, RequestMetric, SourceRecord
from careerpilot.safety import scan_upload
from careerpilot.v1_models import CatalogueEntry

TEXT = {"type": "string", "minLength": 1, "maxLength": 2000}
URL = {"type": "string", "maxLength": 2000}
LIST = {
    "type": "array",
    "items": {"type": "string", "minLength": 1, "maxLength": 100},
    "maxItems": 30,
    "uniqueItems": True,
}
SCHEMAS = {
    "skill": (
        {
            "name": TEXT,
            "aliases": LIST,
            "requires": LIST,
            "minutes": {"type": "integer", "minimum": 1, "maximum": 10080},
            "industry": TEXT,
            "source_url": URL,
            "reviewed_at": TEXT,
            "scope": {"const": "foundation"},
            "dependency_basis": TEXT,
        },
        [
            "name",
            "aliases",
            "requires",
            "minutes",
            "industry",
            "source_url",
            "reviewed_at",
            "scope",
            "dependency_basis",
        ],
    ),
    "resource": (
        {
            "title": TEXT,
            "skill_id": TEXT,
            "url": URL,
            "type": {
                "enum": [
                    "documentation",
                    "course",
                    "video",
                    "open_source",
                    "exercise",
                    "article",
                    "community",
                ]
            },
            "language": TEXT,
            "source": {"enum": ["official_documentation", "community", "editorial_reviewed"]},
            "updated_at_source": {"type": ["string", "null"]},
            "reviewed_at": TEXT,
            "status": {"enum": ["available", "unavailable", "unverified"]},
            "alternative": TEXT,
        },
        [
            "title",
            "skill_id",
            "url",
            "type",
            "language",
            "source",
            "updated_at_source",
            "reviewed_at",
            "status",
            "alternative",
        ],
    ),
    "career": (
        {
            "title": TEXT,
            "family": TEXT,
            "skills": LIST,
            "duties": LIST,
            "typical_tasks": LIST,
            "entry_requirements": TEXT,
            "source_url": URL,
            "reviewed_at": TEXT,
            "trust": TEXT,
            "region": TEXT,
        },
        [
            "title",
            "family",
            "skills",
            "duties",
            "typical_tasks",
            "entry_requirements",
            "source_url",
            "reviewed_at",
            "trust",
            "region",
        ],
    ),
    "template": (
        {
            "title": TEXT,
            "skill_id": TEXT,
            "minutes": {"type": "integer", "minimum": 1, "maximum": 10080},
            "definition_of_done": TEXT,
            "verification": {"enum": ["practice_not_verification", "objective_quiz"]},
        },
        ["title", "skill_id", "minutes", "definition_of_done", "verification"],
    ),
    "job_category": (
        {"title": TEXT, "description": TEXT, "skill_ids": LIST},
        ["title", "description", "skill_ids"],
    ),
}


SCHEMAS["matching_policy"] = (
    {
        "weights": {
            "type": "object",
            "properties": {
                key: {"type": "integer", "minimum": 0, "maximum": 100}
                for key in (
                    "required_skills",
                    "project_evidence",
                    "preferences",
                    "preferred_skills",
                    "freshness",
                )
            },
            "required": [
                "required_skills",
                "project_evidence",
                "preferences",
                "preferred_skills",
                "freshness",
            ],
            "additionalProperties": False,
        },
        "rollout_percent": {"type": "integer", "minimum": 0, "maximum": 100},
        "basis": TEXT,
    },
    ["weights", "rollout_percent", "basis"],
)


def require_role(principal, publish=False):
    allowed = {"admin"} if publish else {"operator", "admin"}
    if principal.role not in allowed:
        raise DomainError("FORBIDDEN", "发布和回滚需管理员；审核管理需运营权限", 403)


def public_data(row):
    return {
        "id": row.id,
        "version": row.version,
        "kind": row.kind,
        "updated_at": row.updated_at,
        **json.loads(row.payload_json),
    }


def serialize_catalogue(db):
    db.execute(
        update(CatalogueEntry)
        .where(CatalogueEntry.id == "python")
        .values(version=CatalogueEntry.version)
    )


def validate(db, entry_id, kind, payload):
    if kind not in SCHEMAS:
        raise DomainError("INVALID_CATALOGUE_KIND", "不支持的公共词库类型", 422)
    props, required = SCHEMAS[kind]
    if not Draft202012Validator(
        {"type": "object", "properties": props, "required": required, "additionalProperties": False}
    ).is_valid(payload):
        raise DomainError("INVALID_PUBLIC_PAYLOAD", "公共数据字段或取值不符合此类型规范", 422)
    scan_upload(growth.dump(payload).encode(), "public-catalogue.json")
    if "reviewed_at" in payload:
        try:
            if datetime.fromisoformat(payload["reviewed_at"]).tzinfo is None:
                raise ValueError()
        except ValueError:
            raise DomainError("INVALID_PUBLIC_DATE", "核对时间需为带时区的 ISO 时间", 422) from None
    for key in ("url", "source_url"):
        if key in payload:
            canonical = source_library.reference_url(payload[key])
            if not canonical or canonical != payload[key]:
                raise DomainError(
                    "INVALID_PUBLIC_URL", "公共来源需规范 HTTPS 链接且不含查询参数", 422
                )
    if kind == "matching_policy" and (
        entry_id != "matching-policy" or sum(payload["weights"].values()) != 100
    ):
        raise DomainError(
            "INVALID_MATCHING_POLICY", "权重总和须为 100，且使用固定 matching-policy 条目", 422
        )
    graph = catalogue.skill_map(db)
    if kind == "skill":
        aliases = {a.casefold() for a in payload["aliases"] + [entry_id, payload["name"]]}
        if any(
            aliases & {a.casefold() for a in entry["aliases"] + [key, entry["name"]]}
            for key, entry in graph.items()
            if key != entry_id
        ):
            raise DomainError("AMBIGUOUS_SKILL_ALIAS", "技能别名不能与其他技能重复", 422)
        graph[entry_id] = {**payload, "id": entry_id}
        catalogue.topological(graph)
        matching.validate_alias_regression(graph, entry_id)
    else:
        skills = payload.get(
            "skills",
            payload.get("skill_ids", [payload["skill_id"]] if "skill_id" in payload else []),
        )
        for skill in skills:
            catalogue.require_skill(db, skill)


def proposal(db, principal, args, trace):
    require_role(principal)
    serialize_catalogue(db)
    validate(db, args["entry_id"], args["entry_kind"], args["payload"])
    current = db.get(CatalogueEntry, args["entry_id"])
    if (current.version if current else 0) != args["base_version"] or (
        current and current.kind != args["entry_kind"]
    ):
        raise DomainError("VERSION_CONFLICT", "公共数据版本或类型已变更", 412)
    scan_upload(args["note"].encode(), "public-change-note.txt")
    before = json.loads(current.payload_json) if current else {}
    row = CatalogueEntry(
        id=new_id(),
        kind="ops_proposal",
        payload_json=growth.dump(
            {
                **args,
                "status": "submitted",
                "submitted_by": principal.user_id,
                "reviewer_id": None,
                "review_note": "",
                "public_only_confirmed": True,
                "base_payload": before if current else None,
                "changes": {
                    key: {"before": before.get(key), "after": args["payload"].get(key)}
                    for key in sorted(set(args["payload"]) | set(before))
                    if before.get(key) != args["payload"].get(key)
                },
            }
        ),
    )
    db.add(row)
    db.flush()
    audit(db, principal, row.id, "catalogue.proposed", row.version, trace)
    return public_data(row)


def owned_proposal(db, proposal_id):
    row = db.get(CatalogueEntry, proposal_id)
    if row is None or row.kind != "ops_proposal":
        raise DomainError("NOT_FOUND", "公共变更单不存在", 404)
    return row


def save_proposal(db, row, payload, expected):
    result = db.execute(
        update(CatalogueEntry)
        .where(CatalogueEntry.id == row.id, CatalogueEntry.version == expected)
        .values(payload_json=growth.dump(payload), version=expected + 1, updated_at=utc_now())
    )
    if affected(result) != 1:
        raise DomainError("VERSION_CONFLICT", "公共变更单已更新", 412)
    db.refresh(row)
    return public_data(row)


def review(db, principal, proposal_id, expected, accepted, note, trace):
    require_role(principal)
    serialize_catalogue(db)
    row = owned_proposal(db, proposal_id)
    item = json.loads(row.payload_json)
    if item["submitted_by"] == principal.user_id:
        raise DomainError("INDEPENDENT_REVIEW_REQUIRED", "请由另一位运营或管理员审核", 403)
    if item["status"] != "submitted":
        raise DomainError("INVALID_TRANSITION", "此变更单已审核", 409)
    scan_upload(note.encode(), "public-review-note.txt")
    result = save_proposal(
        db,
        row,
        {
            **item,
            "status": "reviewed" if accepted else "rejected",
            "reviewer_id": principal.user_id,
            "review_note": note,
        },
        expected,
    )
    audit(db, principal, row.id, "catalogue.reviewed", row.version, trace)
    return result


def revision_id(entry_id, version):
    return str(uuid5(NAMESPACE_URL, f"catalogue-revision:{entry_id}:{version}"))


def remember_revision(db, row):
    key = revision_id(row.id, row.version)
    if db.get(CatalogueEntry, key) is None:
        db.add(
            CatalogueEntry(
                id=key,
                kind="ops_revision",
                payload_json=growth.dump(
                    {
                        "entry_id": row.id,
                        "entry_kind": row.kind,
                        "entry_version": row.version,
                        "payload": json.loads(row.payload_json),
                        "recorded_at": utc_now(),
                    }
                ),
            )
        )
        db.flush()


def publish(db, principal, proposal_id, expected, trace):
    require_role(principal, True)
    serialize_catalogue(db)
    row = owned_proposal(db, proposal_id)
    item = json.loads(row.payload_json)
    if item["status"] != "reviewed":
        raise DomainError("REVIEW_REQUIRED", "公共变更需要独立审核后才能发布", 409)
    current = db.get(CatalogueEntry, item["entry_id"])
    if (current.version if current else 0) != item["base_version"]:
        raise DomainError("VERSION_CONFLICT", "公共数据已改变，请基于新版本重新提交", 412)
    validate(db, item["entry_id"], item["entry_kind"], item["payload"])
    if current:
        remember_revision(db, current)
        current.payload_json = growth.dump(item["payload"])
        current.version += 1
        current.updated_at = utc_now()
    else:
        current = CatalogueEntry(
            id=item["entry_id"], kind=item["entry_kind"], payload_json=growth.dump(item["payload"])
        )
        db.add(current)
        db.flush()
    remember_revision(db, current)
    result = save_proposal(
        db, row, {**item, "status": "published", "published_version": current.version}, expected
    )
    audit(db, principal, row.id, "catalogue.published", current.version, trace)
    return result


def rollback_proposal(db, principal, entry_id, target_version, expected, note, trace):
    require_role(principal)
    snapshot = db.get(CatalogueEntry, revision_id(entry_id, target_version))
    if snapshot is None or snapshot.kind != "ops_revision":
        raise DomainError("NOT_FOUND", "公共历史版本不存在", 404)
    item = json.loads(snapshot.payload_json)
    return proposal(
        db,
        principal,
        {
            "entry_id": entry_id,
            "entry_kind": item["entry_kind"],
            "base_version": expected,
            "payload": item["payload"],
            "note": note,
            "rollback_from_version": target_version,
        },
        trace,
    )


def create_ticket(db, principal, args, trace):
    growth.lock_owner(db, principal)
    scan_upload(growth.dump(args).encode(), "support-ticket.json")
    if args["resource_id"]:
        row = db.get(CatalogueEntry, args["resource_id"])
        if row is None or row.kind != "resource":
            raise DomainError("NOT_FOUND", "公共资源不存在", 404)
    if args["job_id"]:
        growth.owned(db, principal, args["job_id"], "job")
    return growth.save(
        db, principal, "support_ticket", {**args, "status": "open", "responses": []}, trace
    )


def ticket_queue(db, principal):
    require_role(principal)
    return [
        {
            "id": r.id,
            "version": r.version,
            "created_at": r.created_at,
            "category": (s := growth.data(r))["category"],
            "status": s["status"],
            "resource_id": s["resource_id"],
            "has_private_details": True,
        }
        for r in db.scalars(
            select(growth.GrowthRecord).where(
                growth.GrowthRecord.tenant_id == principal.tenant_id,
                growth.GrowthRecord.kind == "support_ticket",
            )
        )
    ]


def respond_ticket(db, principal, record_id, expected, status, response, trace):
    require_role(principal)
    # Explicit support action sees only a queue item, never private report text/JD/attachments.
    scan_upload(response.encode(), "support-response.txt")
    row = db.scalar(
        select(growth.GrowthRecord).where(
            growth.GrowthRecord.id == record_id,
            growth.GrowthRecord.tenant_id == principal.tenant_id,
            growth.GrowthRecord.kind == "support_ticket",
        )
    )
    if row is None:
        raise DomainError("NOT_FOUND", "工单不存在", 404)
    item = growth.data(row)
    owner = Principal(row.user_id, row.tenant_id)
    growth.lock_owner(db, owner)
    db.refresh(row)
    item = growth.data(row)
    if item["status"] == "resolved":
        raise DomainError("INVALID_TRANSITION", "已解决工单不能重复处理", 409)
    result = growth.save(
        db,
        owner,
        "support_ticket",
        {
            **reporting.body(item),
            "status": status,
            "responses": item["responses"]
            + [{"text": response, "at": utc_now(), "status": status}],
        },
        trace,
        row,
        expected,
    )
    audit(db, principal, record_id, "support.responded", result["version"], trace)
    return {"id": record_id, "version": result["version"], "status": status}


def monitor(db, principal):
    require_role(principal)
    usage = list(db.scalars(select(ModelUsage).where(ModelUsage.tenant_id == principal.tenant_id)))
    groups: dict[str, dict] = {}
    for u in usage:
        # Group by declared prompt/workflow version, never return task/user IDs or text.
        key = u.prompt_version
        group = groups.setdefault(
            key,
            {
                "calls": 0,
                "errors": 0,
                "pending": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "estimated_cost_micros": 0,
                "latency_total_ms": 0,
            },
        )
        group["calls"] += 1
        group["pending"] += u.error_class == "pending"
        group["errors"] += u.error_class not in {"", "success", "pending"}
        for field in ("input_tokens", "output_tokens", "estimated_cost_micros"):
            group[field] += getattr(u, field)
        if u.error_class != "pending":
            group["latency_total_ms"] += u.latency_ms
    for group in groups.values():
        finished = group["calls"] - group["pending"]
        elapsed = group.pop("latency_total_ms")
        group["mean_latency_ms"] = elapsed / finished if finished else None
        group["error_rate"] = group["errors"] / finished if finished else None
    denied = db.scalar(
        select(func.count())
        .select_from(AuditEvent)
        .where(AuditEvent.tenant_id == principal.tenant_id, AuditEvent.action == "access.denied")
    )
    failures = db.scalar(
        select(func.count())
        .select_from(BackgroundTask)
        .where(BackgroundTask.tenant_id == principal.tenant_id, BackgroundTask.status == "failed")
    )
    sources = [
        s.id for s in db.scalars(select(SourceRecord).where(SourceRecord.last_error_class != ""))
    ]
    requests = list(
        db.scalars(
            select(RequestMetric)
            .where(RequestMetric.tenant_id == principal.tenant_id)
            .order_by(RequestMetric.created_at.desc())
            .limit(1000)
        )
    )
    return {
        "model_by_workflow": groups,
        "model_quality": "未接入真实质量评测时不报告模型准确率",
        "risk_alerts": {
            "denied_access_count": denied,
            "failed_tasks": failures,
            "degraded_source_ids": sources,
        },
        "request_error_classes": dict(Counter(str(r.status) for r in requests if r.status >= 400)),
        "private_content_visible": False,
        "cost_basis": "网关估算，不等于供应商账单",
    }
