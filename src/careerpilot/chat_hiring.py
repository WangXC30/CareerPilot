"""Assistant adapters over the same confirmed facts and hiring services as the career page."""

import copy

from pydantic import BaseModel, ValidationError

from careerpilot import applications, growth, resumes
from careerpilot.domain import DomainError
from careerpilot.hiring_api import (
    ApplicationInput,
    DraftInput,
    FactInput,
    ResumeCreate,
    ResumeInput,
    TailorInput,
)
from careerpilot.safety import payload_hash, scan_upload

ID = {"type": "string", "minLength": 1, "maxLength": 100}
VERSION = {"type": "integer", "minimum": 1}
MODELS: dict[str, type[BaseModel]] = {
    "create_resume_fact": FactInput,
    "create_resume": ResumeCreate,
    "revise_resume": ResumeInput,
    "tailor_resume": TailorInput,
    "draft_material": DraftInput,
    "create_application": ApplicationInput,
}


def tool_schema(model, extra=None):
    result = copy.deepcopy(model.model_json_schema())
    result["properties"].pop("confirmed")
    result["required"].remove("confirmed")
    for name, definition in (extra or {}).items():
        result["properties"][name] = definition
        result["required"].append(name)
    return result


TOOLS = {
    name: tool_schema(
        model,
        {"resume_id": ID, "version": VERSION}
        if name in {"revise_resume", "tailor_resume"}
        else {"version_id": ID}
        if name == "draft_material"
        else {},
    )
    for name, model in MODELS.items()
}
TOOLS["check_resume"] = {
    "type": "object",
    "properties": {"version_id": ID},
    "required": ["version_id"],
    "additionalProperties": False,
}
TOOLS["create_resume_facts"] = {
    "type": "object",
    "properties": {
        "facts": {"type": "array", "minItems": 1, "maxItems": 20, "items": tool_schema(FactInput)}
    },
    "required": ["facts"],
    "additionalProperties": False,
}
WRITES = set(MODELS) | {"create_resume_facts"}
SECTIONS = {
    "resume_facts": "resume_fact",
    "resumes": "resume",
    "resume_versions": "resume_version",
    "applications": "job_application",
    "application_drafts": "application_draft",
}
SUMMARIES = {
    "create_resume_facts": "一次核对以下简历事实；均为本人自述，未经独立验证。",
    "create_resume_fact": "确认以下陈述加入求职事实库，仍标记为本人自述，未经独立验证。",
    "create_resume": "使用以下已确认事实创建简历，不补写未知经历或技能。",
    "revise_resume": "使用以下事实保存新的简历版本，原版本保留。",
    "tailor_resume": "按目标岗位调整以下事实的顺序，保存新的定制版本，不新增经历。",
    "draft_material": "保存以下求职材料草稿，不发送邮件或投递。",
    "create_application": "为以下岗位和简历版本建立申请草稿，尚未投递。",
}


def proposal(db, principal, action, args):
    if action == "create_resume_facts":
        try:
            bodies = [
                FactInput.model_validate({**item, "confirmed": True}).model_dump(
                    mode="json", exclude={"confirmed"}
                )
                for item in args["facts"]
            ]
        except (ValidationError, KeyError, TypeError):
            raise DomainError(
                "CHAT_HIRING_INVALID", "请核对求职字段、来源和事实选择。", 422
            ) from None
        if not 1 <= len(bodies) <= 20:
            raise DomainError("CHAT_HIRING_INVALID", "请核对求职事实数量。", 422)
        preview = {
            "kind": "hiring",
            "action": action,
            "facts": [resumes.fact_candidate(db, principal, body) for body in bodies],
        }
        return {
            "summary": SUMMARIES[action],
            "preview": preview,
            "candidate": bodies,
            "context_hash": payload_hash(preview),
        }
    extras = {"resume_id", "version", "version_id"}
    try:
        body = (
            MODELS[action]
            .model_validate(
                {**{k: v for k, v in args.items() if k not in extras}, "confirmed": True}
            )
            .model_dump(mode="json", exclude={"confirmed"})
        )
    except ValidationError:
        raise DomainError("CHAT_HIRING_INVALID", "请核对求职字段、来源和事实选择。", 422) from None
    preview = {"kind": "hiring", "action": action}
    if action == "create_resume_fact":
        preview["facts"] = [resumes.fact_candidate(db, principal, body)]
    else:
        root = {}
        if action in {"revise_resume", "tailor_resume"}:
            root = growth.data(growth.owned(db, principal, args["resume_id"], "resume"))
            if root["version"] != args["version"]:
                raise DomainError("VERSION_CONFLICT", "简历已更新，请重新读取并预览。", 412)
            preview.update(title=root["title"], resume_id=root["id"], base_version=root["version"])
        if action == "tailor_resume":
            version = resumes.owned_version(db, principal, root["current_version_id"])
            body["fact_ids"] = resumes.tailored_fact_ids(db, principal, version, body["job_id"])
        if action in {"create_resume", "revise_resume", "tailor_resume"}:
            if action == "create_resume":
                scan_upload(body["title"].encode(), "resume-title.txt")
                preview["title"] = body["title"]
            preview.update(
                facts=resumes.valid_facts(db, principal, body["fact_ids"]),
                job=resumes.job_context(db, principal, body.get("job_id")),
            )
        else:
            version_id = args.get("version_id") or body["resume_version_id"]
            version = resumes.owned_version(db, principal, version_id)
            root = growth.data(growth.owned(db, principal, version["resume_id"], "resume"))
            preview.update(
                title=root["title"],
                resume_version_id=version_id,
                sequence=version["sequence"],
                facts=resumes.valid_version_facts(db, principal, version),
                job=version["job"],
            )
            if action == "draft_material":
                preview.update(
                    text=resumes.material_text(version, body["kind"], body["tone"]),
                    material_kind=body["kind"],
                    tone=body["tone"],
                )
            else:
                job = resumes.job_context(db, principal, body["job_id"])
                if version["job"] and version["job"]["id"] != body["job_id"]:
                    raise DomainError("JOB_MISMATCH", "简历定制岗位与申请岗位不同", 422)
                if version["job"] and version["job"]["content_hash"] != job["content_hash"]:
                    raise DomainError("JOB_CHANGED", "岗位原文已更新，请重新核对定制简历", 409)
                preview.update(job=job, status="draft", verification="not_submitted")
    return {
        "summary": SUMMARIES[action],
        "preview": preview,
        "candidate": body,
        "context_hash": payload_hash(preview),
    }


def execute(db, principal, action, args, proposed, trace, run_id):
    if action == "check_resume":
        version = resumes.owned_version(db, principal, args["version_id"])
        return {"kind": "hiring", "action": action, **resumes.ats(db, principal, version)}
    # Resolve the sources again after the human's pause; do not approve changed content.
    fresh = proposal(db, principal, action, args)
    if fresh["context_hash"] != proposed["context_hash"]:
        raise DomainError("STALE_PREVIEW", "求职资料已改变，请重新预览。", 412)
    body = proposed["candidate"]
    key = "chat.hiring:" + run_id + ":" + payload_hash({"action": action, "args": args})
    if action == "create_resume_facts":
        facts = [resumes.create_fact(db, principal, item, trace) for item in body]
        return {
            **proposed["preview"],
            "records": facts,
            "kind": "hiring",
            "action": action,
            "saved": True,
            "href": "/app/career",
            "sources": [
                {
                    "id": fact["id"],
                    "label": "求职",
                    "version": fact["version"],
                    "href": "/app/career",
                }
                for fact in facts
            ],
        }
    if action == "create_resume_fact":
        saved = resumes.create_fact(db, principal, body, trace)
    elif action == "create_resume":
        saved = resumes.create_resume(
            db, principal, body["title"], body["fact_ids"], body["job_id"], key, trace
        )
    elif action in {"revise_resume", "tailor_resume"}:
        saved = resumes.revise(
            db,
            principal,
            args["resume_id"],
            body["fact_ids"],
            body["job_id"],
            args["version"],
            trace,
            origin="tailored" if action == "tailor_resume" else "edited",
        )
    elif action == "draft_material":
        saved = resumes.material_draft(
            db,
            principal,
            resumes.owned_version(db, principal, args["version_id"]),
            body["kind"],
            body["tone"],
            trace,
        )
    else:
        saved = applications.create(
            db, principal, body["job_id"], body["resume_version_id"], key, trace
        )
    return {
        **proposed["preview"],
        **saved,
        "kind": "hiring",
        "action": action,
        "saved": True,
        "href": "/app/career",
        "sources": [
            {
                "id": saved["id"],
                "label": proposed["preview"].get("title", "求职"),
                "version": saved["version"],
                "href": "/app/career",
            }
        ],
    }
