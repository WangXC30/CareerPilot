"""Confirmed facts, immutable resume versions and conservative JD-specific ordering."""

import json
import re
from uuid import NAMESPACE_URL, uuid5

from careerpilot import growth
from careerpilot.domain import DomainError
from careerpilot.persistence import IdempotencyRecord
from careerpilot.safety import payload_hash, scan_upload

KINDS = {
    "name": "姓名",
    "contact": "联系方式",
    "summary": "个人简介",
    "education": "教育背景",
    "experience": "实践经历",
    "project": "项目经历",
    "skill": "技能与证书",
}


def has_skill(key, text):
    pattern = growth.SKILLS.get(key, re.escape(key))
    return bool(re.search(r"(?<![A-Za-z0-9_])" + pattern + r"(?![A-Za-z0-9_])", text, re.I))


def import_preview(db, principal, filename, content, trace):
    material = growth.import_material(db, principal, filename, content, trace)
    candidates = []
    section = "experience"
    for candidate in material["candidates"]:
        text = candidate["text"].strip().lstrip("# ")
        headings = {
            "教育背景": "education",
            "教育经历": "education",
            "实践经历": "experience",
            "工作经历": "experience",
            "项目经历": "project",
            "技能": "skill",
            "技能与证书": "skill",
            "个人简介": "summary",
        }
        heading = text in headings
        if heading:
            section = headings[text]
        kind = (
            "name"
            if re.match(r"姓名[：:]", text)
            else "contact"
            if re.search(r"邮箱|电话|手机|email|@", text, re.I)
            else section
        )
        candidates.append({**candidate, "suggested_kind": kind, "section_heading": heading})
    truncated = sum(bool(line.strip()) for line in material["content"].splitlines()) > 100
    return {
        "material": material,
        "candidates": candidates,
        "truncated": truncated,
        "notice": ("本次仅预览前 100 条非空行，请拆分长文件。" if truncated else "")
        + "按行提取与栏目建议，尚未确认；请校正陈述、栏目和来源后逐项选择。",
    }


def confirm_import(db, principal, material_id, claims, expected, trace):
    growth.lock_owner(db, principal)
    kinds = [c["kind"] for c in claims]
    selected = [{k: v for k, v in c.items() if k != "kind"} for c in claims]
    confirmed = growth.confirm_material(db, principal, material_id, selected, expected, trace)
    facts = [
        create_fact(
            db,
            principal,
            {
                "kind": kind,
                "source_type": "memory",
                "source_id": memory["id"],
                "source_version": memory["version"],
            },
            trace,
        )
        for kind, memory in zip(kinds, confirmed["memories"], strict=True)
    ]
    return {"material": confirmed["material"], "facts": facts}


def revoke_id(principal, fact_id):
    return str(
        uuid5(NAMESPACE_URL, f"resume-fact:{principal.tenant_id}:{principal.user_id}:{fact_id}")
    )


def fact_status(db, principal, item):
    if db.get(growth.GrowthRecord, revoke_id(principal, item["id"])):
        return "revoked"
    source = item["source"]
    if source["type"] == "manual":
        return "current"
    if source["type"] == "project":
        from careerpilot import project_evidence

        if source.get("narrative_id"):
            from careerpilot.project_workflow import narrative_source_status

            if (status := narrative_source_status(db, principal, source)) != "current":
                return status

        valid = any(
            e["artifact_id"] == source["id"]
            and e["artifact_hash"] == source["artifact_hash"]
            and e["assessment_id"] == source["assessment_id"]
            and e["assessment_version"] == source["assessment_version"]
            for e in project_evidence.current(db, principal)
        )
        return "current" if valid else "source_withdrawn"
    kind = "memory" if source["type"] == "memory" else "profile"
    row = db.scalar(
        growth.owner_query(principal, kind).where(growth.GrowthRecord.id == source["id"])
    )
    if row is None:
        return "source_deleted"
    current = growth.data(row)
    if (
        current.get("deleted")
        or current.get("superseded_by")
        or current.get("category") == "inference"
    ):
        return "source_withdrawn"
    if current["version"] != source["version"]:
        return "needs_reconfirmation"
    return "current"


def fact(db, principal, fact_id):
    item = growth.data(growth.owned(db, principal, fact_id, "resume_fact"))
    return {**item, "source_status": fact_status(db, principal, item)}


def create_fact(db, principal, body, trace):
    growth.lock_owner(db, principal)
    source = {"type": body["source_type"], "basis": "user_confirmed_statement"}
    text = body.get("text", "")
    if source["type"] == "memory":
        memory = growth.data(growth.owned(db, principal, body["source_id"], "memory"))
        if memory["version"] != body["source_version"]:
            raise DomainError("VERSION_CONFLICT", "记忆已更新，请重新选择", 412)
        if (
            memory.get("deleted")
            or memory.get("superseded_by")
            or memory.get("category") == "inference"
            or memory.get("trust") != "user_confirmed"
        ):
            raise DomainError("FACT_CONFIRMATION_REQUIRED", "只能使用当前已确认的用户陈述", 422)
        text = memory["text"]
        source.update(
            id=memory["id"],
            version=memory["version"],
            material_id=memory.get("source_id"),
            material_version=memory.get("source_version"),
            quote=memory.get("source_quote"),
            category=memory["category"],
        )
    elif source["type"] == "profile":
        row = growth.profile(db, principal)
        if row is None or row.version != body["source_version"]:
            raise DomainError("VERSION_CONFLICT", "档案不存在或版本已改变", 412)
        profile = growth.data(row)
        key = body["profile_field"]
        value = profile[key]
        if key == "education":
            value = {
                "high_school": "高中",
                "associate": "专科",
                "bachelor": "本科",
                "master": "硕士",
                "doctor": "博士",
                "other": "其他",
            }[value]
        label = {"major": "专业", "education": "学历", "graduation_date": "毕业日期"}[key]
        text = f"{label}：{value}"
        source.update(id=row.id, version=row.version, field=key, value=value)
    if not text.strip():
        raise DomainError("EMPTY_FACT", "请填写要确认的事实陈述", 422)
    if any((ord(c) < 32 and c not in "\t\r\n") or 0xD800 <= ord(c) <= 0xDFFF for c in text):
        raise DomainError("INVALID_FACT", "陈述包含不支持的控制字符", 422)
    scan_upload(text.encode(), "resume-fact.txt")
    if body["kind"] == "name" and len(text) > 100:
        raise DomainError("INVALID_NAME", "姓名陈述最多 100 字符", 422)
    return growth.save(
        db,
        principal,
        "resume_fact",
        {
            "kind": body["kind"],
            "text": text,
            "source": source,
            "confirmed_at": growth.utc_now(),
            "verification": "user_confirmed_not_independently_verified",
        },
        trace,
    )


def revoke_fact(db, principal, fact_id, reason, trace):
    growth.lock_owner(db, principal)
    scan_upload(reason.encode(), "fact-revocation.txt")
    growth.owned(db, principal, fact_id, "resume_fact")
    key = revoke_id(principal, fact_id)
    row = db.get(growth.GrowthRecord, key)
    return (
        growth.data(row)
        if row
        else growth.save(
            db,
            principal,
            "resume_fact_revocation",
            {"fact_id": fact_id, "reason": reason},
            trace,
            record_id=key,
        )
    )


def valid_facts(db, principal, fact_ids):
    result = [fact(db, principal, key) for key in fact_ids]
    if any(f["source_status"] != "current" for f in result):
        raise DomainError("STALE_FACT", "陈述来源已变更或撤销，请重新确认后创建新版本", 409)
    if sum(len(f["text"]) for f in result) > 20000:
        raise DomainError("RESUME_TOO_LONG", "单个简历版本最多 20000 字符", 422)
    for kind in ("name", "summary"):
        if sum(f["kind"] == kind for f in result) > 1:
            raise DomainError("DUPLICATE_FIELD", "姓名与个人简介各限一条，请核对选择", 422)
    return [{k: v for k, v in f.items() if k != "source_status"} for f in result]


def owned_version(db, principal, version_id):
    return growth.data(growth.owned(db, principal, version_id, "resume_version"))


def version_status(db, principal, version):
    current = [fact(db, principal, f["id"]) for f in version["facts"]]
    return {
        "valid": all(f["source_status"] == "current" for f in current),
        "facts": [{"id": f["id"], "source_status": f["source_status"]} for f in current],
    }


def job_context(db, principal, job_id):
    if not job_id:
        return None
    job = growth.data(growth.owned(db, principal, job_id, "job"))
    return {
        "id": job["id"],
        "version": job["version"],
        "content_hash": job["content_hash"],
        "title": job["fields"]["title"]["value"],
        "company": job["fields"]["company"]["value"],
        "deadline": job["fields"]["deadline"]["value"],
        "sources": job["sources"],
        "skills": job["skills"],
        "status": job["status"],
    }


def append_version(
    db,
    principal,
    root,
    fact_ids,
    job_id,
    origin,
    trace,
    expected,
    restored_from=None,
    job_snapshot=None,
):
    facts = valid_facts(db, principal, fact_ids)
    job = job_snapshot if job_snapshot is not None else job_context(db, principal, job_id)
    content = {"facts": facts, "job": job, "layout": "ats-single-column.v1"}
    previous = growth.data(root)
    result = growth.save(
        db,
        principal,
        "resume_version",
        {
            "resume_id": root.id,
            "sequence": expected,
            **content,
            "content_hash": payload_hash(content),
            "origin": origin,
            "parent_version_id": previous["current_version_id"],
            "restored_from": restored_from,
            "rule_version": "resume-facts.v1",
        },
        trace,
    )
    metadata = {k: v for k, v in previous.items() if k not in {"id", "version", "created_at"}}
    metadata.update(current_version_id=result["id"], version_count=expected)
    growth.save(db, principal, "resume", metadata, trace, root, expected)
    return result


def create_resume(db, principal, title, fact_ids, job_id, key, trace):
    growth.lock_owner(db, principal)
    scan_upload(title.encode(), "resume-title.txt")
    digest = payload_hash(
        {"action": "resume.create", "title": title, "facts": fact_ids, "job_id": job_id}
    )
    previous = db.get(IdempotencyRecord, (principal.user_id, principal.tenant_id, key))
    if previous:
        if previous.request_hash != digest:
            raise DomainError("IDEMPOTENCY_CONFLICT", "幂等键已用于不同简历", 409)
        return owned_version(db, principal, json.loads(previous.response_json)["id"])
    root = growth.save(
        db,
        principal,
        "resume",
        {"title": title, "current_version_id": None, "version_count": 0},
        trace,
    )
    row = growth.owned(db, principal, root["id"], "resume")
    result = append_version(db, principal, row, fact_ids, job_id, "created", trace, 1)
    db.add(
        IdempotencyRecord(
            user_id=principal.user_id,
            tenant_id=principal.tenant_id,
            key=key,
            request_hash=digest,
            response_json=json.dumps({"id": result["id"]}),
        )
    )
    return result


def revise(
    db,
    principal,
    resume_id,
    fact_ids,
    job_id,
    expected,
    trace,
    origin="edited",
    restored_from=None,
    job_snapshot=None,
):
    growth.lock_owner(db, principal)
    root = growth.owned(db, principal, resume_id, "resume")
    if root.version != expected:
        raise DomainError("VERSION_CONFLICT", "简历已更新，请刷新版本列表", 412)
    return append_version(
        db, principal, root, fact_ids, job_id, origin, trace, expected, restored_from, job_snapshot
    )


def restore(db, principal, resume_id, version_id, expected, trace):
    version = owned_version(db, principal, version_id)
    if version["resume_id"] != resume_id:
        raise DomainError("NOT_FOUND", "版本不属于此简历", 404)
    return revise(
        db,
        principal,
        resume_id,
        [f["id"] for f in version["facts"]],
        version["job"]["id"] if version["job"] else None,
        expected,
        trace,
        origin="restored",
        restored_from=version_id,
        job_snapshot=version["job"],
    )


def tailor(db, principal, resume_id, job_id, expected, trace):
    growth.lock_owner(db, principal)
    root = growth.owned(db, principal, resume_id, "resume")
    if root.version != expected:
        raise DomainError("VERSION_CONFLICT", "简历已更新，请刷新版本列表", 412)
    version = owned_version(db, principal, growth.data(root)["current_version_id"])
    job = growth.data(growth.owned(db, principal, job_id, "job"))
    skills = {s["skill_id"] for s in job["skills"] if s["requirement"] != "mentioned"}

    def relevance(item):
        return -sum(has_skill(key, item["text"]) for key in skills if key in growth.SKILLS)

    facts = sorted(version["facts"], key=lambda f: (list(KINDS).index(f["kind"]), relevance(f)))
    return revise(
        db,
        principal,
        resume_id,
        [f["id"] for f in facts],
        job_id,
        expected,
        trace,
        origin="tailored",
    )


def ats(db, principal, version):
    status = version_status(db, principal, version)
    text = "\n".join(f["text"] for f in version["facts"])
    names = [f for f in version["facts"] if f["kind"] == "name"]
    contact = "\n".join(f["text"] for f in version["facts"] if f["kind"] == "contact")
    email = bool(re.search(r"[^\s@]+@[^\s@]+\.[^\s@]+", contact))
    phone = bool(re.search(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)", contact))
    issues = []
    for missing, code, message in [
        (not names, "NAME_MISSING", "请确认并加入姓名"),
        (not email and not phone, "CONTACT_MISSING", "请核对并加入邮箱或手机号"),
        (not status["valid"], "STALE_FACT", "来源变更或撤销，需要重新确认"),
    ]:
        if missing:
            issues.append(
                {
                    "code": code,
                    "message": message,
                    "original": None,
                    "suggestion": "补充已确认事实并创建新版本，不自动添加经历",
                }
            )
    required = set()
    if version["job"]:
        required = {
            s["skill_id"] for s in version["job"]["skills"] if s["requirement"] == "required"
        }
    missing_keywords = sorted(k for k in required if not has_skill(k, text))
    for item in version["facts"]:
        if len(item["text"]) > 500:
            issues.append(
                {
                    "code": "LONG_PARAGRAPH",
                    "message": "建议拆成易读的真实经历陈述",
                    "original": item["text"],
                    "suggestion": "在事实库确认更清楚的陈述后创建新版本",
                }
            )
    return {
        "issues": issues,
        "missing_keywords": missing_keywords,
        "source_status": status,
        "layout": "单列文本，A4，固定标题层级，无图片/文本框/嵌套表格",
        "notice": "本地规则诊断，不保证通过任何 ATS。缺失关键词不得凭空补写成已掌握技能。",
        "ready_for_preparation": bool(names) and (email or phone) and status["valid"],
        "rule_version": "ats-check.v1",
    }


def material_draft(db, principal, version, kind, tone, trace):
    growth.lock_owner(db, principal)
    valid_facts(db, principal, [f["id"] for f in version["facts"]])
    title = (version["job"]["title"] if version["job"] else None) or "相关岗位"
    opening = {
        "cover_letter": f"您好，我希望应聘{title}。以下是我确认的经历：",
        "email": f"您好，随附我的简历，申请{title}。以下经历供您参考：",
        "introduction": "您好，以下是我的背景与经历：",
    }[kind]
    body = opening + "\n\n" + "\n".join(f["text"] for f in version["facts"])
    if tone == "formal":
        body += "\n\n感谢您审阅我的申请。"
    return growth.save(
        db,
        principal,
        "application_draft",
        {
            "resume_version_id": version["id"],
            "resume_sequence": version["sequence"],
            "kind": kind,
            "tone": tone,
            "text": body,
            "fact_ids": [f["id"] for f in version["facts"]],
            "status": "draft_only",
            "sent": False,
            "rule_version": "materials.v1",
        },
        trace,
    )
