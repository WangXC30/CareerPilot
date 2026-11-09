"""Reviewable local interview follow-up using existing application and calendar records."""

from datetime import date
from uuid import NAMESPACE_URL, uuid5

from pydantic import Field, ValidationError

from careerpilot import applications, catalogue, growth, interviews, personal_tools
from careerpilot.domain import DomainError
from careerpilot.growth_api import Input
from careerpilot.interview_api import SelfReviewInput
from careerpilot.safety import payload_hash, scan_upload
from careerpilot.states import validate_transition


class InterviewSchedule(Input):
    application_id: str = Field(min_length=1, max_length=36)
    version: int = Field(ge=1)
    date: date
    time: str | None = Field(default=None, pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    timezone: str = Field(min_length=1, max_length=100)
    preparation_note: str = Field(default="", max_length=2000)


TOOLS = {"record_interview": InterviewSchedule.model_json_schema()}

review_schema = SelfReviewInput.model_json_schema()
review_schema["properties"].pop("confirmed")
review_schema["required"].remove("confirmed")
review_schema["properties"].update(
    {
        "review_id": {"type": ["string", "null"], "minLength": 1, "maxLength": 36},
        "version": {"type": "integer", "minimum": 0},
    }
)
review_schema["required"].append("version")
TOOLS["save_interview_reflection"] = review_schema
TOOLS["plan_interview_review"] = {
    "type": "object",
    "properties": {
        "review_id": {"type": "string", "minLength": 1, "maxLength": 36},
        "review_version": {"type": "integer", "minimum": 1},
        "plan_id": {"type": "string", "minLength": 1, "maxLength": 36},
        "plan_version": {"type": "integer", "minimum": 1},
    },
    "required": ["review_id", "review_version", "plan_id", "plan_version"],
    "additionalProperties": False,
}


def review_proposal(db, principal, action, args):
    if action == "save_interview_reflection":
        try:
            body = SelfReviewInput.model_validate(
                {
                    **{k: v for k, v in args.items() if k not in {"review_id", "version"}},
                    "confirmed": True,
                }
            ).model_dump(exclude={"confirmed"})
        except ValidationError:
            raise DomainError(
                "CHAT_FOLLOWUP_INVALID", "请核对复盘内容、技能和时长。", 422
            ) from None
        review_id, version = args.get("review_id"), args["version"]
        old = (
            growth.data(growth.owned(db, principal, review_id, "interview_review"))
            if review_id
            else None
        )
        if (
            old and (old["version"] != version or old.get("basis") != "user_reported_interview")
        ) or (not old and version != 0):
            raise DomainError("VERSION_CONFLICT", "复盘已改变，请重新读取并预览。", 412)
        application = (
            growth.data(growth.owned(db, principal, body["application_id"], "job_application"))
            if body.get("application_id")
            else None
        )
        skills = [
            {
                "id": item["skill_id"],
                "name": catalogue.require_skill(db, item["skill_id"])["name"],
                "minutes": item["minutes"],
                "note": item["note"],
            }
            for item in body["decisions"]
        ]
        scan_upload(growth.dump(body).encode(), "interview-self-review.json")
        preview = {
            "kind": "followup_review",
            "action": action,
            "before": old,
            "summary": body["summary"],
            "status": body["status"],
            "skills": skills,
            "application": application,
            "basis": "user_reported_interview",
        }
        return {
            "summary": "核对本人面试复盘与复习项，确认后保存；自述不构成能力考核。",
            "preview": preview,
            "candidate": body,
            "context_hash": payload_hash(preview),
        }
    review = growth.data(growth.owned(db, principal, args["review_id"], "interview_review"))
    plan = growth.data(growth.owned(db, principal, args["plan_id"], "plan"))
    if review["version"] != args["review_version"] or plan["version"] != args["plan_version"]:
        raise DomainError("VERSION_CONFLICT", "复盘或计划已改变，请重新读取。", 412)
    if review["status"] != "confirmed" or not review["decisions"]:
        raise DomainError("REVIEW_REQUIRED", "请先确认至少一项复习内容。", 422)
    if plan["status"] != "active":
        raise DomainError("ACTIVE_PLAN_REQUIRED", "请选择当前已发布计划。", 409)
    if any(task.get("interview_review_id") == review["id"] for task in plan["tasks"]):
        raise DomainError("REVIEW_ALREADY_PLANNED", "此复盘已加入该计划，不重复添加。", 409)
    preview = {
        "kind": "followup_review",
        "action": action,
        "summary": review["summary"],
        "skills": review["decisions"],
        "plan_id": plan["id"],
        "plan_version": plan["version"],
        "basis": review.get("basis"),
    }
    return {
        "summary": "根据已确认复盘生成计划调整预览；生成后仍需单独确认接受。",
        "preview": preview,
        "context_hash": payload_hash(preview),
    }


def proposal(db, principal, action, args):
    if action in {"save_interview_reflection", "plan_interview_review"}:
        return review_proposal(db, principal, action, args)
    try:
        body = InterviewSchedule.model_validate(args).model_dump(mode="json")
    except ValidationError:
        raise DomainError(
            "CHAT_FOLLOWUP_INVALID", "请核对申请、具体日期、时间和时区。", 422
        ) from None
    app = growth.data(growth.owned(db, principal, body["application_id"], "job_application"))
    if app["version"] != body["version"]:
        raise DomainError("VERSION_CONFLICT", "申请已改变，请重新读取并预览。", 412)
    if app["status"] != "interviewing":
        validate_transition("application", app["status"], "interviewing")
    scan_upload(body["preparation_note"].encode(), "interview-preparation.txt")
    title = (app["job"].get("title") or "目标岗位") + " · 面试"
    event = personal_tools.calendar_candidate(
        {
            "title": title,
            "kind": "interview",
            "date": body["date"],
            "time": body["time"],
            "timezone": body["timezone"],
            "application_id": app["id"],
            "job_id": app["job"]["id"],
            "resume_version_id": app["resume_version_id"],
            "preparation_note": body["preparation_note"],
        }
    )
    existing = next(
        (
            item
            for item in (
                growth.data(row)
                for row in db.scalars(growth.owner_query(principal, "calendar_event"))
            )
            if item.get("status") == "active"
            and item.get("application_id") == app["id"]
            and all(item.get(key) == event.get(key) for key in ("date", "time", "timezone"))
        ),
        None,
    )
    if existing and existing.get("preparation_note", "") != body["preparation_note"]:
        raise DomainError("INTERVIEW_EXISTS", "该时间已有面试安排，请先修改已有记录。", 409)
    preview = {
        "kind": "followup",
        "action": "record_interview",
        "job": app["job"],
        "resume_title": app["resume_title"],
        "before_status": app["status"],
        "after_status": "interviewing",
        "event": existing or event,
        "existing": {"id": existing["id"], "version": existing["version"]} if existing else None,
    }
    return {
        "summary": "核对以下准确日期与申请，确认后记录本地面试安排并更新申请阶段。",
        "preview": preview,
        "candidate": event,
        "context_hash": payload_hash(preview),
    }


def execute(db, principal, action, args, proposed, trace, run_id):
    growth.lock_owner(db, principal)
    fresh = proposal(db, principal, action, args)
    if fresh["context_hash"] != proposed["context_hash"]:
        raise DomainError("STALE_PREVIEW", "申请或面试安排已改变，请重新预览。", 412)
    if action == "save_interview_reflection":
        saved = interviews.save_self_review(
            db,
            principal,
            proposed["candidate"],
            trace,
            review_id=args.get("review_id"),
            expected=args["version"],
            key="chat.interview-review:" + run_id,
        )
        return {
            **proposed["preview"],
            "saved": True,
            "id": saved["id"],
            "version": saved["version"],
            "href": "/app/career",
            "sources": [
                {
                    "id": saved["id"],
                    "version": saved["version"],
                    "label": "本人面试复盘",
                    "href": "/app/career",
                }
            ],
        }
    if action == "plan_interview_review":
        saved = interviews.plan_preview(
            db,
            principal,
            args["review_id"],
            args["plan_id"],
            args["review_version"],
            "chat.review-plan:" + run_id,
            trace,
        )
        return {
            **saved,
            "saved": True,
            "href": "/app/learn",
            "sources": [
                {
                    "id": saved["id"],
                    "version": saved["version"],
                    "label": "计划调整预览",
                    "href": "/app/learn",
                }
            ],
        }
    app = growth.data(growth.owned(db, principal, args["application_id"], "job_application"))
    if app["status"] != "interviewing":
        app = applications.change(
            db, principal, app["id"], "interviewing", app["version"], "", trace
        )
    existing = proposed["preview"]["existing"]
    if existing:
        event = growth.data(growth.owned(db, principal, existing["id"], "calendar_event"))
    else:
        event_id = str(
            uuid5(
                NAMESPACE_URL, f"chat.interview:{principal.tenant_id}:{principal.user_id}:{run_id}"
            )
        )
        event = personal_tools.save_calendar_event(
            db, principal, proposed["candidate"], trace, record_id=event_id
        )
    return {
        **proposed["preview"],
        "saved": True,
        "deduplicated": bool(existing),
        "id": event["id"],
        "version": event["version"],
        "application_version": app["version"],
        "href": "/app/career",
        "sources": [
            {
                "id": app["id"],
                "version": app["version"],
                "label": "申请记录",
                "href": "/app/career",
            },
            {
                "id": event["id"],
                "version": event["version"],
                "label": "面试安排",
                "href": "/app/career",
            },
        ],
    }
