"""Owner-authored plan revisions use the existing preview/accept transaction."""

import copy
import json
from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, Request
from pydantic import Field, field_validator

from careerpilot import catalogue, growth, learning
from careerpilot.domain import DomainError
from careerpilot.growth_api import Input


class PlanTaskEdit(Input):
    id: str = Field(min_length=1, max_length=36)
    title: str = Field(min_length=1, max_length=200)
    skill_id: str = Field(min_length=1, max_length=80)
    minutes: int = Field(ge=1, le=10080, strict=True)
    date: date
    requires: list[str] = Field(default_factory=list, max_length=200)
    resources: list[str] = Field(min_length=1, max_length=20)
    definition_of_done: str = Field(min_length=1, max_length=2000)

    @field_validator("id")
    @classmethod
    def task_id(cls, value):
        UUID(value)
        return value

    @field_validator("requires", "resources")
    @classmethod
    def entries(cls, value):
        if len(set(value)) != len(value) or any(not item or len(item) > 2000 for item in value):
            raise ValueError("Entries must be nonempty, distinct and bounded")
        return value


class PlanEdit(Input):
    title: str = Field(min_length=1, max_length=200)
    start_date: date
    deadline: date | None = None
    tasks: list[PlanTaskEdit] = Field(max_length=200)


def edit_preview(db, principal, plan_id, body, version, trace):
    growth.lock_owner(db, principal)
    row = growth.owned(db, principal, plan_id, "plan")
    if row.version != version:
        raise DomainError("VERSION_CONFLICT", "计划已变化，请刷新后重新编辑", 412)
    old = json.loads(row.payload_json)
    args = body.model_dump(mode="json")
    growth.scan_upload(growth.dump(args).encode(), "plan-edit.txt")
    if args["deadline"] and args["deadline"] < args["start_date"]:
        raise DomainError("DATE_CONFLICT", "截止日期不能早于开始日期", 422)
    by_id = {task["id"]: task for task in old["tasks"]}
    protected = {task["id"] for task in learning.protected_tasks(old)}
    protected.update(
        task["id"] for task in old["tasks"] if task["status"] in {"running", "skipped"}
    )
    candidate = copy.deepcopy(old)
    candidate.update(title=args["title"], start_date=args["start_date"], deadline=args["deadline"])
    candidate["tasks"] = []
    for fields in args["tasks"]:
        previous = by_id.get(fields["id"])
        if previous is None:
            task = {
                **fields,
                "status": "pending",
                "locked": False,
                "outcome": "",
                "verification": "learning_record",
            }
        else:
            task = {**previous, **fields}
            if previous["skill_id"] != fields["skill_id"]:
                task["verification"] = "learning_record"
        if fields["id"] in protected and task != previous:
            raise DomainError(
                "LOCKED_TASK", "已完成、进行中、锁定及其先修任务不能由编辑器改写", 422
            )
        candidate["tasks"].append(task)
    if protected - {task["id"] for task in candidate["tasks"]}:
        raise DomainError("LOCKED_TASK", "不能删除已完成、进行中、锁定及其先修任务", 422)
    profile = learning.profile_context(db, principal, old)
    active_goal = next(
        (goal["id"] for goal in growth.goal_snapshot(db, principal) if goal["status"] == "active"),
        None,
    )
    if old["goal_id"] != active_goal:
        raise DomainError("STALE_PLAN", "职业目标已改变，请先切回目标或重新创建计划", 412)
    candidate["verified_skills"] = [
        skill["skill_id"]
        for skill in learning.snapshots(db, principal)
        if skill["status"] == "verified"
    ]
    candidate["profile_version"] = profile["version"]
    candidate["source_context"] = learning.context_hash(db, principal)
    candidate["validation"] = learning.validate_plan(
        candidate,
        profile["weekly_minutes"],
        catalogue.skill_map(db),
        [by_id[key] for key in protected],
    )
    after = {task["id"]: task for task in candidate["tasks"]}
    changes = [
        {"before": by_id.get(key), "after": after.get(key)}
        for key in sorted(by_id.keys() | after.keys())
        if by_id.get(key) != after.get(key)
    ]
    return growth.save(
        db,
        principal,
        "plan_preview",
        {
            "plan_id": plan_id,
            "base_version": row.version,
            "reason": "OwnerEdited",
            "status": "pending",
            "context_hash": candidate["source_context"],
            "candidate": candidate,
            "validation_error": None,
            "diff": {
                "added": [after[key] for key in after.keys() - by_id.keys()],
                "removed": [by_id[key] for key in by_id.keys() - after.keys()],
                "changed": changes,
            },
            "metadata_changes": {
                key: {"before": old.get(key), "after": candidate.get(key)}
                for key in ("title", "start_date", "deadline")
                if old.get(key) != candidate.get(key)
            },
        },
        trace,
    )


def register_planning_editor(app, session_dependency, identity):
    Db, Identity = Depends(session_dependency), Depends(identity)
    Version = Annotated[str, Header(pattern=r'^"[1-9][0-9]*"$')]

    @app.post("/api/v1/plans/{plan_id}/edit-preview", status_code=201)
    def preview(
        plan_id: str, body: PlanEdit, if_match: Version, request: Request, db=Db, principal=Identity
    ):
        result = edit_preview(
            db, principal, plan_id, body, int(if_match.strip('"')), request.state.trace_id
        )
        db.commit()
        return result
