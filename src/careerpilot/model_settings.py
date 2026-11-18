"""Workspace-scoped model selection and credential storage (never exported or logged)."""

import ipaddress
import json
import re
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from careerpilot import growth
from careerpilot.application import audit
from careerpilot.domain import DomainError, Principal
from careerpilot.growth_models import GrowthRecord

SETTINGS_KIND = "model_settings"
SECRET_KIND = "model_secret"
PRESET_MODELS = ("deepseek-flash", "deepseek-chat", "deepseek-reasoner")
DEFAULT_HOST = "api.deepseek.com"
DEFAULT_PATH = "/chat/completions"
DEFAULT_MODEL = "deepseek-flash"
DEFAULT_TIMEOUT = 40
HOST_RE = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$"
)
MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,119}$")


def validate_host(raw: str) -> str:
    value = raw.strip()
    if "://" in value:
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise DomainError("INVALID_MODEL_HOST", "模型服务地址仅支持 HTTPS 域名", 422)
        value = parsed.hostname
    host = value.lower().rstrip(".")
    if not host or not HOST_RE.fullmatch(host):
        raise DomainError("INVALID_MODEL_HOST", "模型服务域名格式无效", 422)
    try:
        ip = ipaddress.ip_address(host)
        if not ip.is_global:
            raise DomainError("INVALID_MODEL_HOST", "不允许内网或本地地址作为模型主机", 422)
    except ValueError:
        pass
    return host


def validate_path(raw: str) -> str:
    path = raw.strip() or DEFAULT_PATH
    if not path.startswith("/") or any(c.isspace() or c in "?#" for c in path) or len(path) > 120:
        raise DomainError("INVALID_MODEL_PATH", "模型接口路径必须以 / 开头且不含查询参数", 422)
    return path


def validate_model(raw: str) -> str:
    model = raw.strip()
    if not MODEL_RE.fullmatch(model):
        raise DomainError("INVALID_MODEL_NAME", "模型名称格式无效", 422)
    return model


def validate_api_key(raw: str) -> str:
    key = raw.strip()
    if not key or len(key) > 512 or any(not 33 <= ord(char) <= 126 for char in key):
        raise DomainError("INVALID_API_KEY", "API Key 必须为可见 ASCII 字符且不超过 512 位", 422)
    return key


def mask_key(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 8:
        return "*" * len(key)
    return f"{key[:3]}…{key[-4:]}"


def _latest_record(db: Session, principal: Principal, kind: str) -> GrowthRecord | None:
    return db.scalar(
        growth.owner_query(principal, kind)
        .order_by(growth.GrowthRecord.created_at.desc(), growth.GrowthRecord.id.desc())
        .limit(1)
    )


def _env_key_present() -> bool:
    from careerpilot.deepseek_provider import configured_key

    try:
        configured_key()
        return True
    except ValueError:
        return False


def get_public_settings(db: Session, principal: Principal) -> dict:
    row = _latest_record(db, principal, SETTINGS_KIND)
    secret_row = _latest_record(db, principal, SECRET_KIND)
    settings = json.loads(row.payload_json) if row else {}
    secret = json.loads(secret_row.payload_json) if secret_row else {}
    raw_key = secret.get("api_key", "")
    return {
        "model": settings.get("model", DEFAULT_MODEL),
        "host": settings.get("host", DEFAULT_HOST),
        "path": settings.get("path", DEFAULT_PATH),
        "timeout": settings.get("timeout", DEFAULT_TIMEOUT),
        "has_workspace_key": bool(raw_key),
        "key_hint": mask_key(raw_key) if raw_key else "",
        "env_key_available": _env_key_present(),
        "presets": list(PRESET_MODELS),
        "version": row.version if row else 0,
    }


def update_settings(
    db: Session,
    principal: Principal,
    *,
    model: str,
    host: str,
    path: str,
    timeout: int,
    api_key: str | None,
    clear_api_key: bool,
    expected_version: int,
    trace: str,
) -> dict:
    growth.lock_owner(db, principal)
    row = _latest_record(db, principal, SETTINGS_KIND)
    current_version = row.version if row else 0
    if current_version != expected_version:
        raise DomainError("VERSION_CONFLICT", "模型配置已更新，请刷新后重试", 412)
    clean_model = validate_model(model)
    clean_host = validate_host(host)
    clean_path = validate_path(path)
    if not 5 <= timeout <= 60:
        raise DomainError("VALIDATION_ERROR", "超时时间应在 5 到 60 秒之间", 422)

    growth.save(
        db,
        principal,
        SETTINGS_KIND,
        {
            "model": clean_model,
            "host": clean_host,
            "path": clean_path,
            "timeout": timeout,
        },
        trace,
        row,
        current_version,
    )

    secret_row = _latest_record(db, principal, SECRET_KIND)
    if clear_api_key:
        if secret_row is not None:
            growth.save(
                db,
                principal,
                SECRET_KIND,
                {"api_key": ""},
                trace,
                secret_row,
                secret_row.version,
            )
    elif api_key is not None and api_key.strip():
        clean_key = validate_api_key(api_key)
        growth.save(
            db,
            principal,
            SECRET_KIND,
            {"api_key": clean_key},
            trace,
            secret_row,
            secret_row.version if secret_row else 0,
        )

    audit(db, principal, principal.user_id, "model_settings.updated", current_version + 1, trace)
    return get_public_settings(db, principal)


def resolve_runtime_config(engine, principal: Principal | None = None) -> dict:
    from careerpilot.deepseek_provider import configured_key

    settings: dict = {}
    workspace_key = ""
    with Session(engine) as db:
        owner = principal
        if owner is None:
            from careerpilot.persistence import LocalWorkspace

            workspace = db.get(LocalWorkspace, "personal")
            if workspace is not None:
                owner = Principal(workspace.user_id, workspace.tenant_id)
        if owner is not None:
            row = _latest_record(db, owner, SETTINGS_KIND)
            if row is not None:
                settings = json.loads(row.payload_json)
            secret_row = _latest_record(db, owner, SECRET_KIND)
            if secret_row is not None:
                workspace_key = json.loads(secret_row.payload_json).get("api_key", "")
    api_key = workspace_key or configured_key()
    return {
        "api_key": api_key,
        "model": settings.get("model", DEFAULT_MODEL),
        "host": settings.get("host", DEFAULT_HOST),
        "path": settings.get("path", DEFAULT_PATH),
        "timeout": settings.get("timeout", DEFAULT_TIMEOUT),
    }


def purge_secrets(db: Session, principal: Principal) -> None:
    rows = list(
        db.scalars(
            select(GrowthRecord).where(
                GrowthRecord.user_id == principal.user_id,
                GrowthRecord.tenant_id == principal.tenant_id,
                GrowthRecord.kind == SECRET_KIND,
            )
        )
    )
    for row in rows:
        db.delete(row)
