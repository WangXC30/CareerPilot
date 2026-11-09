"""V1 use cases. Confirmations, ownership and revisions are enforced without an LLM."""

import hashlib
import ipaddress
import json
import re
import time
from datetime import date, datetime, timezone
from difflib import SequenceMatcher
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import select, update

from careerpilot.application import audit, goal_data, owned_goal
from careerpilot.domain import DomainError
from careerpilot.gateway import reserve
from careerpilot.growth_models import GrowthRecord, GrowthRevision
from careerpilot.persistence import CareerGoal, User, affected, utc_now
from careerpilot.platform_models import SourceRecord
from careerpilot.safety import payload_hash, require_source, scan_upload


def dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def owner_query(principal, kind):
    return select(GrowthRecord).where(
        GrowthRecord.user_id == principal.user_id,
        GrowthRecord.tenant_id == principal.tenant_id,
        GrowthRecord.kind == kind,
    )


def owned(db, principal, record_id, kind):
    row = db.scalar(owner_query(principal, kind).where(GrowthRecord.id == record_id))
    if row is None:
        raise DomainError("NOT_FOUND", "资源不存在", 404)
    return row


def data(row):
    return {
        "id": row.id,
        "version": row.version,
        "created_at": row.created_at,
        **json.loads(row.payload_json),
    }


def save(db, principal, kind, payload, trace, row=None, version=0, record_id=None):
    if row is None:
        if version != 0:
            raise DomainError("VERSION_CONFLICT", "资源版本已改变，请刷新", 412)
        row = GrowthRecord(
            user_id=principal.user_id,
            tenant_id=principal.tenant_id,
            kind=kind,
            payload_json=dump(payload),
        )
        if record_id:
            row.id = record_id
        db.add(row)
        db.flush()
    else:
        result = db.execute(
            update(GrowthRecord)
            .where(
                GrowthRecord.id == row.id,
                GrowthRecord.user_id == principal.user_id,
                GrowthRecord.tenant_id == principal.tenant_id,
                GrowthRecord.version == version,
            )
            .values(payload_json=dump(payload), version=version + 1)
        )
        if affected(result) != 1:
            raise DomainError("VERSION_CONFLICT", "资源版本已改变，请刷新", 412)
        db.refresh(row)
    db.add(
        GrowthRevision(
            record_id=row.id,
            version=row.version,
            user_id=principal.user_id,
            tenant_id=principal.tenant_id,
            payload_json=row.payload_json,
        )
    )
    audit(db, principal, row.id, f"{kind}.saved", row.version, trace)
    return data(row)


def history(db, principal, record_id, kind):
    owned(db, principal, record_id, kind)
    return [
        {"version": r.version, "created_at": r.created_at, "payload": json.loads(r.payload_json)}
        for r in db.scalars(
            select(GrowthRevision)
            .where(
                GrowthRevision.record_id == record_id,
                GrowthRevision.user_id == principal.user_id,
                GrowthRevision.tenant_id == principal.tenant_id,
            )
            .order_by(GrowthRevision.version.desc())
        )
    ]


def profile(db, principal):
    return db.scalar(owner_query(principal, "profile"))


def profile_impact(db, principal, payload):
    row = profile(db, principal)
    before = json.loads(row.payload_json) if row else {}
    changes = {
        key: {"before": before.get(key), "after": value}
        for key, value in payload.items()
        if before.get(key) != value
    }
    return {
        "base_version": row.version if row else 0,
        "changes": changes,
        "needs_plan_review": any(k in changes for k in ("weekly_minutes", "preferred_cities")),
        "message": "保存后保留历史；现有计划需重新评估，不会自动覆盖。",
    }


def save_profile(db, principal, payload, version, trace):
    lock_owner(db, principal)
    return save(
        db,
        principal,
        "profile",
        payload,
        trace,
        profile(db, principal),
        version,
    )


def restore_profile(db, principal, target_version, version, trace):
    lock_owner(db, principal)
    row = profile(db, principal)
    if row is None:
        raise DomainError("NOT_FOUND", "档案不存在", 404)
    revision = db.get(GrowthRevision, (row.id, target_version))
    if revision is None:
        raise DomainError("NOT_FOUND", "历史版本不存在", 404)
    result = save(db, principal, "profile", json.loads(revision.payload_json), trace, row, version)
    audit(db, principal, row.id, f"profile.restored_from_{target_version}", row.version, trace)
    return result


def lock_owner(db, principal):
    # A stable owner lock serializes dedup even across rate-limit minute boundaries.
    # This no-op UPDATE works on SQLite and PostgreSQL and stores no new data.
    db.execute(
        update(User)
        .where(User.id == principal.user_id, User.tenant_id == principal.tenant_id)
        .values(display_name=User.display_name)
    )


def authorize_import(db, principal):
    lock_owner(db, principal)
    source = db.get(SourceRecord, "user_import")
    now = int(time.time())
    require_source(source, "import", now)
    reserve(db, principal, f"source:user_import:{now // 60}", 1, source.rate_per_minute)


def import_material(db, principal, filename, content, trace):
    authorize_import(db, principal)
    text = scan_upload(content, filename)
    if not text.strip():
        raise DomainError("EMPTY_MATERIAL", "材料不能为空", 422)
    # Conservative line extraction; no claim becomes a fact before confirmation.
    candidates = [
        {"text": m.group().strip(), "start": m.start(), "end": m.end()}
        for m in re.finditer(r"[^\r\n]+", text)
        if m.group().strip()
    ][:100]
    return save(
        db,
        principal,
        "material",
        {
            "filename": filename,
            "content": text,
            "sha256": hashlib.sha256(content).hexdigest(),
            "parser_version": "lines-v1",
            "status": "pending_confirmation",
            "candidates": candidates,
        },
        trace,
    )


def confirm_material(db, principal, material_id, claims, version, trace):
    row = owned(db, principal, material_id, "material")
    payload = json.loads(row.payload_json)
    if payload["status"] != "pending_confirmation":
        raise DomainError("ALREADY_CONFIRMED", "材料已经确认，请编辑对应记忆")
    for claim in claims:
        start, end = claim["start"], claim["end"]
        if not 0 <= start < end <= len(payload["content"]):
            raise DomainError("INVALID_SPAN", "来源范围必须位于原文内", 422)
    payload["status"] = "confirmed"
    payload["confirmed_at"] = utc_now()
    material = save(db, principal, "material", payload, trace, row, version)
    memories = []
    for claim in claims:
        memories.append(
            save(
                db,
                principal,
                "memory",
                {
                    **claim,
                    "source_id": material_id,
                    "source_version": version,
                    "source_quote": payload["content"][claim["start"] : claim["end"]],
                    "trust": "user_confirmed",
                    "skill_status": "unverified",
                    "deleted": False,
                },
                trace,
            )
        )
    return {"material": material, "memories": memories}


def update_memory(db, principal, record_id, text, category, version, trace, deleted=False):
    lock_owner(db, principal)
    row = owned(db, principal, record_id, "memory")
    payload = json.loads(row.payload_json)
    if payload.get("structured_experience"):
        raise DomainError("VALIDATION_ERROR", "请在结构化经历表单中修改此记录", 422)
    payload.update(text=text, category=category, deleted=deleted, skill_status="unverified")
    return save(db, principal, "memory", payload, trace, row, version)


def source_url(value):
    """Validate display-only attribution URLs; this function never fetches them."""
    if value is None:
        return None
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        if (
            parsed.scheme != "https"
            or not host
            or parsed.username
            or parsed.password
            or parsed.port not in {None, 443}
            or "." not in host
            or host.lower().endswith((".localhost", ".local", ".internal"))
        ):
            raise ValueError
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None and not address.is_global:
            raise ValueError
    except ValueError:
        raise DomainError("UNSAFE_URL", "来源链接必须为公开 HTTPS 地址", 422) from None
    return urlunsplit(("https", parsed.netloc.lower(), parsed.path, parsed.query, ""))


def span_field(text, pattern):
    matches = list(re.finditer(pattern, text, re.IGNORECASE | re.MULTILINE))
    if len(matches) != 1:
        return {"value": None, "state": "unknown", "evidence": None}
    match = matches[0]
    group = 1 if match.lastindex else 0
    start, end = match.span(group)
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    if start == end:
        return {"value": None, "state": "unknown", "evidence": None}
    return {
        "value": text[start:end],
        "state": "extracted",
        "evidence": {"start": start, "end": end, "quote": text[start:end]},
    }


SKILLS = {
    "python": r"python",
    "sql": r"sql",
    "pytorch": r"(?:pytorch|torch)",
    "javascript": r"(?:javascript|js)",
    "git": r"git",
    "docker": r"docker",
    "fastapi": r"fastapi",
    "react": r"react",
}


def extract_job(text, skill_patterns=None):
    fields = {
        "title": span_field(text, r"^(?:岗位|职位|job title|title)(?:名称)?[：:][ \t]*([^\r\n]+)"),
        "company": span_field(text, r"^(?:公司|企业|company)[：:][ \t]*([^\r\n]+)"),
        "location": span_field(text, r"^(?:工作地点|地点|城市|location)[：:][ \t]*([^\r\n]+)"),
        "education": span_field(text, r"^(?:学历|学历要求)[：:][ \t]*([^\r\n]+)"),
        "graduation_cohort": span_field(text, r"(20\d{2})届"),
        "duration": span_field(text, r"^(?:实习时长|实习期限)[：:][ \t]*([^\r\n]+)"),
        "salary": span_field(text, r"^(?:薪资|薪酬|工资)[：:][ \t]*([^\r\n]+)"),
        "employment_type": span_field(text, r"^(?:工作类型|岗位类型)[：:][ \t]*([^\r\n]+)"),
        "deadline": span_field(text, r"^截止(?:日期)?[：:][ \t]*(20\d{2}-\d{2}-\d{2})"),
        "published_at": span_field(text, r"^发布日期[：:][ \t]*(20\d{2}-\d{2}-\d{2})"),
        "responsibilities": span_field(text, r"^(?:职责|岗位职责)[：:][ \t]*([^\r\n]+)"),
    }
    skills = []
    # Keep every occurrence, its original spelling and local requirement classification.
    for skill, pattern in (SKILLS if skill_patterns is None else skill_patterns).items():
        pattern = r"(?<![A-Za-z0-9_])" + pattern + r"(?![A-Za-z0-9_])"
        for match in re.finditer(pattern, text, re.IGNORECASE):
            separators = "\n。，,；;"
            left = max(text.rfind(c, 0, match.start()) for c in separators) + 1
            ends = [p for c in separators if (p := text.find(c, match.end())) >= 0]
            context = text[left : min(ends) if ends else len(text)]
            if re.search(r"不要求|无需|不需要|not\s+required|optional", context, re.I):
                category = "mentioned"
            elif re.search(r"优先|加分|preferred|nice.to.have", context, re.I):
                category = "preferred"
            elif re.search(
                r"必须|要求|必备|熟悉|掌握|required|must|knowledge|familiarity|experience",
                context,
                re.I,
            ):
                category = "required"
            else:
                category = "mentioned"
            skills.append(
                {
                    "skill_id": skill,
                    "requirement": category,
                    "evidence": {
                        "start": match.start(),
                        "end": match.end(),
                        "quote": match.group(),
                    },
                }
            )
    return {
        "fields": fields,
        "skills": skills,
        "extractor_version": "jd-labels-v1",
        "review_status": "unreviewed",
        "trust": "untrusted_data",
    }


def import_job(db, principal, text, url, trace):
    authorize_import(db, principal)
    payload = job_payload(text, url)
    url = payload["sources"][0]["source_url"]
    # Exact bytes only: line breaks change evidence and must remain distinct versions.
    digest = hashlib.sha256(text.encode()).hexdigest()
    source = {"source_url": url, "retrieved_at": utc_now(), "access_mode": "user_import"}
    rows = list(db.scalars(owner_query(principal, "job").order_by(GrowthRecord.created_at)))
    target = None
    candidates = []
    for row in rows:
        payload = json.loads(row.payload_json)
        if payload["status"] == "merged":
            continue
        if payload["content_hash"] == digest:
            if not any(s["source_url"] == url for s in payload["sources"]):
                payload["sources"].append(source)
                result = save(db, principal, "job", payload, trace, row, row.version)
            else:
                result = data(row)
            return {**result, "deduplicated": True, "possible_duplicates": []}
        if url and any(s["source_url"] == url for s in payload["sources"]):
            target = row
        elif SequenceMatcher(None, payload["content"][:5000], text[:5000]).ratio() >= 0.85:
            candidates.append({"id": row.id, "reason": "similar_text_requires_review"})
    payload = job_payload(text, url)
    if target:
        previous = json.loads(target.payload_json)
        payload["sources"] = previous["sources"] + [source]
    # authorize_import holds the owner row lock until commit, serializing
    # concurrent imports without making mutable aggregate IDs depend on content.
    result = save(
        db,
        principal,
        "job",
        payload,
        trace,
        target,
        target.version if target else 0,
    )
    return {**result, "deduplicated": False, "possible_duplicates": candidates}


def job_payload(text, url):
    scan_upload(text.encode(), "job.txt")
    if not text.strip():
        raise DomainError("EMPTY_JD", "JD 不能为空", 422)
    payload = {
        "content": text,
        "content_hash": hashlib.sha256(text.encode()).hexdigest(),
        "sources": [
            {"source_url": source_url(url), "retrieved_at": utc_now(), "access_mode": "user_import"}
        ],
        "status": "unverified",
        **extract_job(text),
    }
    deadline = payload["fields"]["deadline"]["value"]
    if deadline:
        try:
            if date.fromisoformat(deadline) < datetime.now(timezone.utc).date():
                payload["status"] = "possibly_expired"
        except ValueError:
            payload["fields"]["deadline"] = {"value": None, "state": "unknown", "evidence": None}
    return payload


def goal_snapshot(db, principal):
    return [
        goal_data(row)
        for row in db.scalars(
            select(CareerGoal)
            .where(
                CareerGoal.user_id == principal.user_id, CareerGoal.tenant_id == principal.tenant_id
            )
            .order_by(CareerGoal.id)
        )
    ]


def preview_goal(db, principal, goal_id, trace):
    from careerpilot import learning

    goal = owned_goal(db, principal, goal_id)
    if goal.status == "archived":
        raise DomainError("INVALID_STATE", "已归档目标不能激活")
    snapshot = goal_snapshot(db, principal)
    plan = learning.active_plan(db, principal)
    current_plan = data(plan) if plan else {"tasks": []}
    return save(
        db,
        principal,
        "goal_preview",
        {
            "goal_id": goal_id,
            "goal_title": goal.title,
            "snapshot_hash": payload_hash({"goals": snapshot}),
            "status": "pending",
            "previous_active": [g for g in snapshot if g["status"] == "active"],
            "plan_impact": {
                "plan_id": plan.id,
                "plan_version": plan.version,
                "protected_tasks": sum(
                    t["locked"] or t["status"] == "completed" for t in current_plan["tasks"]
                ),
                "pending_tasks": sum(
                    t["status"] in {"pending", "paused", "running"} for t in current_plan["tasks"]
                ),
            }
            if plan
            else None,
            "impact": "确认后切换主目标；保留原目标和历史，学习计划与岗位匹配需重新评估。"
            + (
                f" 当前学习计划 v{plan.version}，共 {len(current_plan['tasks'])} 项任务；"
                "锁定与已完成任务会保留，新规划需另行确认。"
                if plan
                else ""
            ),
        },
        trace,
    )


def accept_goal(db, principal, preview_id, version, trace):
    preview = owned(db, principal, preview_id, "goal_preview")
    payload = json.loads(preview.payload_json)
    if payload["status"] != "pending":
        raise DomainError("INVALID_STATE", "预览已处理")
    snapshot = goal_snapshot(db, principal)
    if payload_hash({"goals": snapshot}) != payload["snapshot_hash"]:
        raise DomainError("STALE_PREVIEW", "目标已变化，请重新预览", 412)
    # Consume the preview first; competing accepts cannot execute a second switch.
    save(db, principal, "goal_preview", {**payload, "status": "accepted"}, trace, preview, version)
    # Deactivate first to respect the partial unique index.
    for item in sorted(snapshot, key=lambda g: g["id"] == payload["goal_id"]):
        target_status = "active" if item["id"] == payload["goal_id"] else "draft"
        if item["status"] == "archived" or item["status"] == target_status:
            continue
        result = db.execute(
            update(CareerGoal)
            .where(
                CareerGoal.id == item["id"],
                CareerGoal.user_id == principal.user_id,
                CareerGoal.tenant_id == principal.tenant_id,
                CareerGoal.version == item["version"],
            )
            .values(status=target_status, version=item["version"] + 1)
        )
        if affected(result) != 1:
            raise DomainError("STALE_PREVIEW", "目标已变化，请重新预览", 412)
        audit(db, principal, item["id"], "goal.switched", item["version"] + 1, trace)
    db.expire_all()
    return goal_data(owned_goal(db, principal, payload["goal_id"]))
