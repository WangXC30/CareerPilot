"""Private resource feedback overlays, without changing public editorial facts."""

from careerpilot import catalogue, growth
from careerpilot.domain import DomainError
from careerpilot.safety import scan_upload


def feedback(db, principal, resource_id, args, expected, trace):
    resource = next((r for r in catalogue.entries(db, "resource") if r["id"] == resource_id), None)
    if resource is None:
        raise DomainError("NOT_FOUND", "资源不存在", 404)
    try:
        scan_upload(args["note"].encode(), "feedback.txt")
    except UnicodeEncodeError:
        raise DomainError("INVALID_TEXT", "反馈必须为有效文本", 422) from None
    growth.lock_owner(db, principal)
    row = next(
        (
            r
            for r in db.scalars(growth.owner_query(principal, "resource_feedback"))
            if growth.data(r)["resource_id"] == resource_id
        ),
        None,
    )
    return growth.save(
        db,
        principal,
        "resource_feedback",
        {**args, "resource_id": resource_id},
        trace,
        row,
        expected,
    )


def entries(db, principal):
    records = {
        growth.data(r)["resource_id"]: growth.data(r)
        for r in db.scalars(growth.owner_query(principal, "resource_feedback"))
    }
    resources = catalogue.entries(db, "resource")
    blocked = {
        r["url"]
        for r in resources
        if records.get(r["id"], {}).get("broken") or r["status"] != "available"
    }
    result = []
    for r in resources:
        personal = records.get(
            r["id"],
            {"version": 0, "favorite": False, "quality": "unrated", "broken": False, "note": ""},
        )
        result.append(
            {
                **r,
                "status": "reported_unavailable"
                if r["url"] in blocked and r["status"] == "available"
                else r["status"],
                "feedback": personal,
            }
        )
    for skill in catalogue.entries(db, "skill"):
        if not any(r["skill_id"] == skill["id"] and r["status"] == "available" for r in result):
            result.append(
                {
                    "id": "local-practice-" + skill["id"],
                    "skill_id": skill["id"],
                    "title": skill["name"] + " 本地测验与错题复习",
                    "url": "/app/#skills",
                    "type": "exercise",
                    "language": "zh",
                    "source": "local_question_bank",
                    "reviewed_at": skill["reviewed_at"],
                    "status": "available",
                    "stale": False,
                    "local_fallback": True,
                    "updated_at_source": None,
                    "alternative": "官方材料暂不可用，可先用本地测验定位薄弱点；不是等价课程替代",
                }
            )
    return result
