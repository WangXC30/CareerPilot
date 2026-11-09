"""Reviewable personal information additions sharing existing profile/memory rules."""

import copy
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from pydantic import ValidationError

from careerpilot import growth, learning
from careerpilot.domain import DomainError
from careerpilot.growth_api import ProfileInput
from careerpilot.profile_editor import ExperienceInput, save_experience
from careerpilot.safety import scan_upload

PROFILE_FIELDS = ProfileInput.model_json_schema()["properties"]
EXPERIENCE = copy.deepcopy(ExperienceInput.model_json_schema())
EXPERIENCE["properties"].pop("confirmed")
EXPERIENCE["required"].remove("confirmed")
TOOLS = {
    "save_profile_draft": {
        "type": "object",
        "properties": {
            "fields": {
                "type": "object",
                "properties": PROFILE_FIELDS,
                "minProperties": 1,
                "additionalProperties": False,
            }
        },
        "required": ["fields"],
        "additionalProperties": False,
    },
    "update_profile": {
        "type": "object",
        "properties": {
            "version": {"type": "integer", "minimum": 0},
            "fields": {
                "type": "object",
                "properties": PROFILE_FIELDS,
                "minProperties": 1,
                "additionalProperties": False,
            },
        },
        "required": ["version", "fields"],
        "additionalProperties": False,
    },
    "add_profile_note": {
        "type": "object",
        "properties": {
            "text": {"type": "string", "minLength": 1, "maxLength": 2000},
        },
        "required": ["text"],
        "additionalProperties": False,
    },
    "add_experience": EXPERIENCE,
}


def validated(model, payload):
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        fields = ", ".join(dict.fromkeys(".".join(map(str, e["loc"])) for e in exc.errors()))
        raise DomainError(
            "CHAT_PROFILE_INVALID",
            "请补充或核对资料字段：" + fields + "。缺少的信息不要猜测，也可先保存为文字资料补充。",
            422,
        ) from None


def proposal(db, principal, action, args):
    if action == "save_profile_draft":
        from careerpilot.workflow_api import draft_id

        draft = db.get(growth.GrowthRecord, draft_id(principal, "profile", "personal"))
        current: dict[str, Any] = growth.data(draft) if draft else {"version": 0, "values": {}}
        profile_row = growth.profile(db, principal)
        profile = growth.data(profile_row) if profile_row else {"version": 0}
        if draft and current.get("base_record_version", 0) != profile["version"]:
            raise DomainError("STALE_DRAFT", "正式档案已改变，请先在资料页比较并处理原草稿。", 412)
        changes = {
            key: {
                "before": current["values"].get(key, profile.get(key)),
                "official": profile.get(key),
                "after": value,
            }
            for key, value in args["fields"].items()
        }
        candidate = {**current["values"], **args["fields"]}
        scan_upload(growth.dump(candidate).encode(), "draft.json")
        preview = {
            "kind": "personal_info",
            "section": "profile_draft",
            "changes": changes,
            "profile_version": profile["version"],
            "draft_version": current["version"],
        }
        return {
            "summary": "将以下信息保存为私人草稿，尚未更新正式档案；请核对现有值和新值。",
            "preview": preview,
            "candidate": candidate,
            "base_version": current["version"],
            "profile_version": profile["version"],
            "context_hash": growth.payload_hash(preview),
        }
    if action == "update_profile":
        row = growth.profile(db, principal)
        if (row.version if row else 0) != args["version"]:
            raise DomainError("VERSION_CONFLICT", "档案已改变，请重新读取并预览。", 412)
        current = growth.data(row) if row else {}
        candidate = validated(
            ProfileInput,
            {
                **{k: v for k, v in current.items() if k in PROFILE_FIELDS},
                **args["fields"],
            },
        ).model_dump(mode="json")
        scan_upload(growth.dump(candidate).encode(), "profile.json")
        impact = growth.profile_impact(db, principal, candidate)
        if not impact["changes"]:
            raise DomainError("CHAT_PROFILE_UNCHANGED", "这些档案内容已经保存，无需重复写入。", 422)
        return {
            "summary": "将以下内容保存到基础档案；其余字段保持，保留历史，现有计划不会自动替换。",
            "preview": {"kind": "personal_info", "section": "profile", **impact},
            "candidate": candidate,
            "base_version": args["version"],
        }
    if action == "add_experience":
        candidate = validated(ExperienceInput, {**args, "confirmed": True}).model_dump(
            mode="json",
            exclude={"confirmed"},
        )
        scan_upload(growth.dump(candidate).encode(), "experience.txt")
        preview = {"experience": candidate}
    else:
        text = args["text"].strip()
        if not text:
            raise DomainError("CHAT_PROFILE_INVALID", "资料内容不能为空。", 422)
        scan_upload(text.encode(), "profile-note.txt")
        candidate = {"text": text}
        preview = candidate
    return {
        "summary": "将以下内容添加到我的资料，标记为本人确认的自述，不代表独立验证或技能考核通过。",
        "preview": {
            "kind": "personal_info",
            "section": "experience" if action == "add_experience" else "note",
            **preview,
        },
        "candidate": candidate,
    }


def execute(db, principal, action, proposed, trace, conversation_id, run_id):
    if action == "save_profile_draft":
        from careerpilot.workflow_api import draft_id

        growth.lock_owner(db, principal)
        row = db.get(growth.GrowthRecord, draft_id(principal, "profile", "personal"))
        profile_row = growth.profile(db, principal)
        if (row.version if row else 0) != proposed["base_version"] or (
            profile_row.version if profile_row else 0
        ) != proposed["profile_version"]:
            raise DomainError("VERSION_CONFLICT", "档案或草稿已改变，请重新预览。", 412)
        saved = growth.save(
            db,
            principal,
            "private_draft",
            {
                "record_kind": "profile",
                "record_key": "personal",
                "values": proposed["candidate"],
                "base_record_version": proposed["profile_version"],
            },
            trace,
            row,
            proposed["base_version"],
            record_id=draft_id(principal, "profile", "personal"),
        )
        return {
            **proposed["preview"],
            "saved": True,
            "id": saved["id"],
            "version": saved["version"],
            "href": "/app/profile",
        }
    candidate = proposed["candidate"]
    plan_preview = None
    if action == "update_profile":
        saved = growth.save_profile(db, principal, candidate, proposed["base_version"], trace)
        plan_preview = learning.auto_preview(db, principal, "ProfileChanged", trace)
        href = "/app/profile"
    else:
        # Repeated identical additions within one turn use one record as well as
        # the runtime's per-round transactional receipt.
        key = str(
            uuid5(
                NAMESPACE_URL, f"chat.personal:{run_id}:{action}:{growth.payload_hash(candidate)}"
            )
        )
        existing = db.get(growth.GrowthRecord, key)
        origin = {"type": "assistant_chat", "conversation_id": conversation_id, "run_id": run_id}
        if existing:
            saved = growth.data(growth.owned(db, principal, key, "memory"))
        elif action == "add_experience":
            saved = save_experience(
                db,
                principal,
                validated(ExperienceInput, {**candidate, "confirmed": True}),
                trace,
                record_id=key,
                origin=origin,
            )
        else:
            saved = growth.save(
                db,
                principal,
                "memory",
                {
                    **candidate,
                    "category": "self_report",
                    "trust": "user_confirmed",
                    "skill_status": "unverified",
                    "deleted": False,
                    "origin": origin,
                    "verification": "user_confirmed_not_independently_verified",
                },
                trace,
                record_id=key,
            )
        href = "/app/profile#materials"
    return {
        **proposed["preview"],
        "saved": True,
        "id": saved["id"],
        "version": saved["version"],
        "href": href,
        "plan_preview_id": plan_preview["id"] if plan_preview else None,
        "sources": [
            {"id": saved["id"], "version": saved["version"], "label": "我的资料", "href": href}
        ],
    }
