"""Explicit workspace membership and revocable, field-scoped object sharing."""

import hashlib
import hmac
import json
import secrets
import time

from sqlalchemy import select

from careerpilot import growth
from careerpilot.domain import DomainError, Principal
from careerpilot.growth_models import GrowthRecord
from careerpilot.persistence import User
from careerpilot.safety import scan_upload

FIELDS = {
    "brand_profile": {"headline", "items", "locale"},
    "practice_project": {"template", "milestones"},
    "plan": {"status", "start_date", "deadline", "tasks", "milestones"},
    "project_asset": {"title", "kind", "content", "url"},
    "project_narrative": {"situation", "task", "action", "result", "limitations"},
}


def share_sources(db, principal):
    result = []
    for kind, fields in FIELDS.items():
        for row in db.scalars(growth.owner_query(principal, kind)):
            value = payload(row)
            if value.get("status") in {"withdrawn", "revoked", "archived"}:
                continue
            if kind == "project_narrative" and value.get("status") != "confirmed":
                continue
            result.append(
                {
                    "id": row.id,
                    "kind": kind,
                    "version": row.version,
                    "title": value.get("title")
                    or value.get("template", {}).get("title")
                    or value.get("start_date")
                    or "项目陈述",
                    "fields": sorted(fields),
                }
            )
    return result


def payload(row):
    return json.loads(row.payload_json)


def owner(row):
    return Principal(row.user_id, row.tenant_id)


def record(db, key, kind):
    row = db.get(GrowthRecord, key)
    if row is None or row.kind != kind:
        raise DomainError("NOT_FOUND", "资源不存在", 404)
    return row


def workspace(db, principal, key, admin=False):
    row = record(db, key, "workspace")
    value = payload(row)
    own = row.user_id == principal.user_id and row.tenant_id == principal.tenant_id
    if value["status"] != "active" or (
        not own and (admin or principal.user_id not in value["members"])
    ):
        raise DomainError("NOT_FOUND", "空间不存在或权限已撤销", 404)
    return row


def workspace_view(row, principal):
    value = payload(row)
    return {
        "id": row.id,
        "version": row.version,
        "name": value["name"],
        "owner": row.user_id == principal.user_id,
        "members": value["members"] if row.user_id == principal.user_id else [],
    }


def workspaces(db, principal):
    return [
        workspace_view(row, principal)
        for row in db.scalars(select(GrowthRecord).where(GrowthRecord.kind == "workspace"))
        if payload(row)["status"] == "active"
        and (row.user_id == principal.user_id or principal.user_id in payload(row)["members"])
    ]


def create_workspace(db, principal, name, trace):
    scan_upload(name.encode(), "workspace.txt")
    result = growth.save(
        db, principal, "workspace", {"name": name, "members": [], "status": "active"}, trace
    )
    return workspace_view(record(db, result["id"], "workspace"), principal)


def invite(db, principal, key, recipient, trace):
    growth.lock_owner(db, principal)
    row = workspace(db, principal, key, admin=True)
    if recipient == principal.user_id or db.get(User, recipient) is None:
        raise DomainError("INVITATION_UNAVAILABLE", "无法邀请该账户", 422)
    raw = secrets.token_urlsafe(32)
    result = growth.save(
        db,
        principal,
        "workspace_invitation",
        {
            "workspace_id": row.id,
            "recipient_id": recipient,
            "digest": hashlib.sha256(raw.encode()).hexdigest(),
            "expires_at": int(time.time()) + 86400,
            "status": "pending",
        },
        trace,
    )
    return {"id": result["id"], "ticket": raw, "expires_at": result["expires_at"]}


def accept(db, principal, key, ticket, trace):
    row = record(db, key, "workspace_invitation")
    value = payload(row)
    if (
        value["recipient_id"] != principal.user_id
        or not hmac.compare_digest(value["digest"], hashlib.sha256(ticket.encode()).hexdigest())
        or value["status"] != "pending"
        or value["expires_at"] <= int(time.time())
    ):
        raise DomainError("NOT_FOUND", "邀请无效或已过期", 404)
    space = workspace(db, owner(row), value["workspace_id"], admin=True)
    growth.lock_owner(db, owner(space))
    db.refresh(space)
    db.refresh(row)
    workspace(db, owner(row), value["workspace_id"], admin=True)
    value = payload(row)
    if value["status"] != "pending" or value["expires_at"] <= int(time.time()):
        raise DomainError("NOT_FOUND", "邀请已使用", 404)
    members = list(dict.fromkeys(payload(space)["members"] + [principal.user_id]))
    growth.save(
        db,
        owner(space),
        "workspace",
        {**payload(space), "members": members},
        trace,
        space,
        space.version,
    )
    growth.save(db, owner(row), row.kind, {**value, "status": "accepted"}, trace, row, row.version)
    return workspace_view(space, principal)


def remove_member(db, principal, key, member, trace):
    space = workspace(db, principal, key, admin=member != principal.user_id)
    if member == space.user_id:
        raise DomainError("OWNER_CANNOT_LEAVE", "空间所有者请关闭空间", 409)
    growth.lock_owner(db, owner(space))
    db.refresh(space)
    value = payload(space)
    growth.save(
        db,
        owner(space),
        "workspace",
        {**value, "members": [m for m in value["members"] if m != member]},
        trace,
        space,
        space.version,
    )
    for invitation in db.scalars(growth.owner_query(owner(space), "workspace_invitation")):
        item = payload(invitation)
        if (
            item["workspace_id"] == key
            and item["recipient_id"] == member
            and item["status"] == "pending"
        ):
            growth.save(
                db,
                owner(space),
                invitation.kind,
                {**item, "status": "revoked"},
                trace,
                invitation,
                invitation.version,
            )
    # Leaving ends this grant permanently: rejoining must not resurrect prior shares.
    member_user = db.get(User, member)
    if member_user:
        member_owner = Principal(member_user.id, member_user.tenant_id)
        for shared in db.scalars(growth.owner_query(member_owner, "object_share")):
            item = payload(shared)
            if item["workspace_id"] == key and item["status"] == "active":
                revoke(db, member_owner, shared.id, trace)
    return {"status": "removed"}


def close_workspace(db, principal, key, trace):
    growth.lock_owner(db, principal)
    row = workspace(db, principal, key, admin=True)
    growth.save(
        db,
        principal,
        row.kind,
        {**payload(row), "status": "closed", "members": []},
        trace,
        row,
        row.version,
    )
    return {"status": "closed"}


def projection(db, principal, kind, key, fields):
    if kind not in FIELDS or not fields or not set(fields) <= FIELDS[kind]:
        raise DomainError("INVALID_FIELDS", "分享字段不在允许范围", 422)
    row = growth.owned(db, principal, key, kind)
    value = payload(row)
    if value.get("status") in {"withdrawn", "revoked", "archived"}:
        raise DomainError("SOURCE_UNAVAILABLE", "来源已失效", 409)
    if kind == "project_narrative" and value.get("status") != "confirmed":
        raise DomainError("SOURCE_UNAVAILABLE", "请先确认项目陈述", 409)
    if kind == "brand_profile":
        from careerpilot.brand_profile import ensure_current

        ensure_current(db, principal, value)
    # Tasks contain private outcomes; projection deliberately omits notes, IDs and resources.
    selected = {field: value.get(field) for field in fields}
    if "tasks" in selected:
        selected["tasks"] = [
            {k: task.get(k) for k in ("title", "status", "minutes", "date")}
            for task in value.get("tasks", [])
        ]
    if "milestones" in selected and isinstance(selected["milestones"], dict):
        selected["milestones"] = {
            k: (v.get("status") if isinstance(v, dict) else v)
            for k, v in selected["milestones"].items()
        }
    return {
        "source_id": row.id,
        "source_version": row.version,
        "kind": kind,
        "fields": sorted(set(fields)),
        "content": selected,
    }


def share(db, principal, body, trace):
    if body["workspace_id"]:
        space = workspace(db, principal, body["workspace_id"])
        growth.lock_owner(db, owner(space))
        db.refresh(space)
        workspace(db, principal, body["workspace_id"])
    if body["public"] and body["kind"] == "plan":
        raise DomainError("PRIVATE_PLAN", "学习计划仅可在协作空间分享", 422)
    if not body["workspace_id"] and not body["public"]:
        raise DomainError("SHARE_TARGET_REQUIRED", "请选择分享空间或公开链接", 422)
    preview = projection(db, principal, body["kind"], body["source_id"], body["fields"])
    if preview["source_version"] != body["source_version"]:
        raise DomainError("VERSION_CONFLICT", "来源已更新，请重新预览", 412)
    raw = secrets.token_urlsafe(32) if body["public"] else ""
    result = growth.save(
        db,
        principal,
        "object_share",
        {
            **preview,
            "workspace_id": body["workspace_id"],
            "public": body["public"],
            "status": "active",
            "expires_at": int(time.time()) + body["lifetime_hours"] * 3600,
            "token_digest": hashlib.sha256(raw.encode()).hexdigest() if raw else "",
        },
        trace,
    )
    return {
        "id": result["id"],
        "version": result["version"],
        "ticket": raw,
        "expires_at": result["expires_at"],
        "preview": preview,
    }


def visible_share(db, principal, key, public_ticket=None):
    row = record(db, key, "object_share")
    value = payload(row)
    own = (
        principal is not None
        and row.user_id == principal.user_id
        and row.tenant_id == principal.tenant_id
    )
    if value["status"] != "active" or value["expires_at"] <= int(time.time()):
        raise DomainError("NOT_FOUND", "分享不存在或已撤销", 404)
    if not own:
        if public_ticket is not None:
            if not value["public"] or not hmac.compare_digest(
                value["token_digest"], hashlib.sha256(public_ticket.encode()).hexdigest()
            ):
                raise DomainError("NOT_FOUND", "分享不存在或已撤销", 404)
        elif principal is None or not value["workspace_id"]:
            raise DomainError("NOT_FOUND", "分享不存在", 404)
        else:
            workspace(db, principal, value["workspace_id"])
            workspace(db, owner(row), value["workspace_id"])
    latest = projection(db, owner(row), value["kind"], value["source_id"], value["fields"])
    if latest["source_version"] != value["source_version"]:
        raise DomainError("SHARE_STALE", "来源已更新，须所有者重新分享", 409)
    return row


def share_view(row):
    value = payload(row)
    return {
        k: v
        for k, v in {"id": row.id, "version": row.version, **value}.items()
        if k not in {"token_digest"}
    }


def list_shares(db, principal, workspace_id=None):
    if workspace_id:
        workspace(db, principal, workspace_id)
        rows = db.scalars(select(GrowthRecord).where(GrowthRecord.kind == "object_share"))
    else:
        rows = db.scalars(growth.owner_query(principal, "object_share"))
    result = []
    for row in rows:
        if workspace_id and payload(row)["workspace_id"] != workspace_id:
            continue
        try:
            visible_share(db, principal, row.id)
        except DomainError:
            continue
        result.append(share_view(row))
    return result


def revoke(db, principal, key, trace):
    row = growth.owned(db, principal, key, "object_share")
    growth.save(
        db,
        principal,
        row.kind,
        {**payload(row), "status": "revoked", "token_digest": ""},
        trace,
        row,
        row.version,
    )
    return {"status": "revoked"}


def comments(db, principal, key):
    shared = visible_share(db, principal, key)
    return [
        {**growth.data(r), "can_hide": principal.user_id in {r.user_id, shared.user_id}}
        for r in db.scalars(select(GrowthRecord).where(GrowthRecord.kind == "share_comment"))
        if payload(r)["share_id"] == key and payload(r)["status"] == "active"
    ]


def comment(db, principal, key, text, trace):
    row = visible_share(db, principal, key)
    if not payload(row)["workspace_id"]:
        raise DomainError("COMMENTS_PRIVATE", "评论需要空间成员身份", 403)
    workspace(db, principal, payload(row)["workspace_id"])
    scan_upload(text.encode(), "comment.txt")
    return growth.save(
        db,
        principal,
        "share_comment",
        {"share_id": key, "text": text, "status": "active", "author_id": principal.user_id},
        trace,
    )


def moderate(db, principal, key, reason, trace):
    row = record(db, key, "share_comment")
    share_row = visible_share(db, principal, payload(row)["share_id"])
    if principal.user_id not in {row.user_id, share_row.user_id}:
        raise DomainError("NOT_FOUND", "评论不存在", 404)
    scan_upload(reason.encode(), "moderation.txt")
    growth.save(
        db,
        owner(row),
        row.kind,
        {**payload(row), "status": "hidden", "reason": reason},
        trace,
        row,
        row.version,
    )
    return {"status": "hidden"}


def purge_access(db, principal, trace):
    """Deleting an account cuts off anonymous and collaborative access immediately."""
    for row in list(db.scalars(growth.owner_query(principal, "object_share"))):
        if payload(row)["status"] == "active":
            revoke(db, principal, row.id, trace)
    for row in list(db.scalars(growth.owner_query(principal, "workspace"))):
        if payload(row)["status"] == "active":
            close_workspace(db, principal, row.id, trace)
    for row in list(db.scalars(select(GrowthRecord).where(GrowthRecord.kind == "workspace"))):
        if payload(row)["status"] == "active" and principal.user_id in payload(row)["members"]:
            remove_member(db, principal, row.id, principal.user_id, trace)
