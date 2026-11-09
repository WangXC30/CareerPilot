"""Local email/password sessions, private export and tracked account deletion."""

import hashlib
import hmac
import json
import secrets
import time

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from careerpilot.application import audit
from careerpilot.domain import DomainError
from careerpilot.persistence import Base, SessionToken, User, affected, new_id, utc_now
from careerpilot.platform_models import Consent, RoleGrant
from careerpilot.v1_models import Credential, DeletionRequest, LoginWindow


def password_digest(password, salt):
    return hashlib.scrypt(
        password.encode(), salt=bytes.fromhex(salt), n=32768, r=8, p=1, maxmem=64 * 1024 * 1024
    ).hex()


def device_description(user_agent):
    """User-Agent is untrusted: store only an allowlisted coarse label."""
    ua = user_agent[:1000].lower()
    browser = next(
        (
            label
            for marker, label in (
                ("edg/", "Edge"),
                ("firefox/", "Firefox"),
                ("chrome/", "Chrome"),
                ("safari/", "Safari"),
            )
            if marker in ua
        ),
        "未知浏览器",
    )
    system = next(
        (
            label
            for marker, label in (
                ("android", "Android"),
                ("iphone", "iOS"),
                ("ipad", "iPadOS"),
                ("windows", "Windows"),
                ("macintosh", "macOS"),
                ("linux", "Linux"),
            )
            if marker in ua
        ),
        "未知系统",
    )
    return f"{browser} / {system}"


def session_token(db, user_id, tenant_id, user_agent=""):
    label = device_description(user_agent)
    existing = list(
        db.scalars(
            select(SessionToken).where(
                SessionToken.user_id == user_id,
                SessionToken.tenant_id == tenant_id,
                SessionToken.expires_at > int(time.time()),
            )
        )
    )
    notice = (
        "检测到不同浏览器或系统的登录；设备信息可伪装，请核对是否本人操作。"
        if existing and label not in {row.device_label for row in existing}
        else ""
    )
    raw = "cpk_" + secrets.token_urlsafe(32)
    db.add(
        SessionToken(
            digest=hashlib.sha256(raw.encode()).hexdigest(),
            user_id=user_id,
            tenant_id=tenant_id,
            expires_at=int(time.time()) + 86400,
            device_label=label,
            risk_notice=notice,
        )
    )
    return {"access_token": raw, "token_type": "bearer", "expires_in": 86400}


def register(db, email, password, name, user_agent=""):
    if db.scalar(select(Credential).where(Credential.email == email)):
        raise DomainError("REGISTRATION_UNAVAILABLE", "此邮箱无法注册，请尝试登录", 409)
    user = User(id=new_id(), tenant_id=new_id(), display_name=name)
    salt = secrets.token_hex(16)
    db.add(user)
    db.flush()
    db.add(
        Credential(
            user_id=user.id,
            tenant_id=user.tenant_id,
            email=email,
            password_hash=password_digest(password, salt),
            salt=salt,
        )
    )
    db.add(RoleGrant(user_id=user.id, tenant_id=user.tenant_id, role="student"))
    db.add(
        Consent(user_id=user.id, tenant_id=user.tenant_id, purpose="privacy_policy.v1", granted=1)
    )
    return session_token(db, user.id, user.tenant_id, user_agent)


def throttle(db, email, remote):
    key = hashlib.sha256(f"{remote}|{email}".encode()).hexdigest()
    now = int(time.time())
    row = db.get(LoginWindow, key)
    if row is None:
        db.add(LoginWindow(id=key, attempts=1, until=now + 60))
    else:
        result = db.execute(
            update(LoginWindow)
            .where(LoginWindow.id == key, (LoginWindow.until <= now) | (LoginWindow.attempts < 10))
            .values(
                attempts=1 if row.until <= now else LoginWindow.attempts + 1,
                until=now + 60 if row.until <= now else row.until,
            )
        )
        if affected(result) != 1:
            raise DomainError("LOGIN_RATE_LIMIT", "请求过于频繁，请稍后再试", 429)
    # Failed attempts must survive the request transaction's rollback.
    db.commit()


def login(db, email, password, user_agent=""):
    credential = db.scalar(select(Credential).where(Credential.email == email))
    # Apply the same KDF work to unknown users to limit account enumeration timing.
    salt = credential.salt if credential else "00" * 16
    actual = password_digest(password, salt)
    expected = credential.password_hash if credential else "00" * 64
    if not hmac.compare_digest(actual, expected):
        raise DomainError("LOGIN_FAILED", "邮箱或密码不正确", 401)
    return session_token(db, credential.user_id, credential.tenant_id, user_agent)


def export_data(db, principal):
    result: dict = {"format": "careerpilot.export.v1", "exported_at": utc_now(), "data": {}}
    # Credentials, quotas and token digests are never exported.
    excluded = {
        "session_tokens",
        "account_credentials",
        "quota_buckets",
        "role_grants",
        "account_deletions",
    }
    for table in Base.metadata.sorted_tables:
        if "user_id" not in table.c or "tenant_id" not in table.c or table.name in excluded:
            continue
        query = select(table).where(
            table.c.user_id == principal.user_id, table.c.tenant_id == principal.tenant_id
        )
        if table.name == "growth_records":
            query = query.where(
                ~table.c.kind.in_({"github_secret", "workspace_invitation", "object_share"})
            )
        if table.name == "growth_revisions":
            from careerpilot.growth_models import GrowthRecord

            query = query.where(
                ~table.c.record_id.in_(
                    select(GrowthRecord.id).where(
                        GrowthRecord.kind.in_(
                            {"github_secret", "workspace_invitation", "object_share"}
                        )
                    )
                )
            )
        result["data"][table.name] = [dict(row) for row in db.execute(query).mappings()]
    result["data"]["account"] = {"display_name": db.get(User, principal.user_id).display_name}
    from careerpilot import growth, workflows

    run_ids = list(
        db.scalars(
            growth.owner_query(principal, "agent_run").with_only_columns(growth.GrowthRecord.id)
        )
    )
    result["data"]["workflow_checkpoints"] = workflows.export_checkpoints(db.bind, run_ids)
    return result


def request_deletion(db, principal, trace):
    from careerpilot import github_binding, growth

    growth.lock_owner(db, principal)
    github_binding.purge(db, principal)
    from careerpilot import collaboration

    collaboration.purge_access(db, principal, trace)
    raw = secrets.token_urlsafe(32)
    row = DeletionRequest(
        user_id=principal.user_id,
        tenant_id=principal.tenant_id,
        receipt_digest=hashlib.sha256(raw.encode()).hexdigest(),
    )
    db.add(row)
    db.flush()
    audit(db, principal, row.id, "account.deletion_requested", 1, trace)
    # Revoke every session now; worker clears all owned content atomically.
    db.execute(
        delete(SessionToken).where(
            SessionToken.user_id == principal.user_id, SessionToken.tenant_id == principal.tenant_id
        )
    )
    return {"id": row.id, "status": row.status, "receipt": raw}


def process_deletion(engine):
    with Session(engine) as db, db.begin():
        row = db.scalar(
            select(DeletionRequest)
            .where(DeletionRequest.status == "queued")
            .order_by(DeletionRequest.requested_at)
            .limit(1)
            .with_for_update()
        )
        if row is None:
            return False
        user_id, tenant_id = row.user_id, row.tenant_id
        from careerpilot import growth, workflows
        from careerpilot.domain import Principal

        principal = Principal(user_id, tenant_id)
        growth.lock_owner(db, principal)
        run_ids = list(
            db.scalars(
                growth.owner_query(principal, "agent_run").with_only_columns(growth.GrowthRecord.id)
            )
        )
        # A conversation may already be inaccessible while its single-Worker
        # checkpoint cleanup is pending. Workspace deletion must include those IDs
        # before removing the cleanup queue itself.
        for pending in db.scalars(growth.owner_query(principal, "conversation_cleanup")):
            run_ids.extend(json.loads(pending.payload_json)["run_ids"])
        run_ids = list(dict.fromkeys(run_ids))
        workflows.purge_checkpoints(engine, run_ids)
        for table in reversed(Base.metadata.sorted_tables):
            if table.name == "account_deletions":
                continue
            if "user_id" in table.c and "tenant_id" in table.c:
                db.execute(
                    delete(table).where(table.c.user_id == user_id, table.c.tenant_id == tenant_id)
                )
        db.execute(delete(User).where(User.id == user_id, User.tenant_id == tenant_id))
        row.user_id, row.tenant_id = "", ""
        row.status, row.completed_at = "completed", utc_now()
        return True


def session_id(digest):
    """Public management handle; never expose a bearer token or its stored digest."""
    return hashlib.sha256(f"session-management:{digest}".encode()).hexdigest()


def active_sessions(db, principal, current_digest):
    rows = db.scalars(
        select(SessionToken)
        .where(
            SessionToken.user_id == principal.user_id,
            SessionToken.tenant_id == principal.tenant_id,
            SessionToken.expires_at > int(time.time()),
        )
        .order_by(SessionToken.expires_at.desc(), SessionToken.digest)
    )
    return [
        {
            "id": session_id(row.digest),
            "expires_at": row.expires_at,
            "current": hmac.compare_digest(row.digest, current_digest),
            "device_label": row.device_label,
            "created_at": row.created_at,
            "risk_notice": row.risk_notice,
        }
        for row in rows
    ]


def revoke_session(db, principal, handle, trace):
    rows = db.scalars(
        select(SessionToken).where(
            SessionToken.user_id == principal.user_id,
            SessionToken.tenant_id == principal.tenant_id,
        )
    )
    row = next((row for row in rows if hmac.compare_digest(session_id(row.digest), handle)), None)
    if row is None:
        raise DomainError("NOT_FOUND", "会话不存在", 404)
    db.delete(row)
    audit(db, principal, handle, "account.session_revoked", 1, trace)
    return {"status": "revoked"}
