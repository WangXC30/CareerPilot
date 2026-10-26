"""Fail-closed trust boundaries; external content never grants permissions."""

import hashlib
import ipaddress
import json
import os
import re
import socket
from pathlib import Path
from urllib.parse import urlsplit

from cryptography.fernet import Fernet
from sqlalchemy.orm import Session

from careerpilot.domain import DomainError, Principal
from careerpilot.platform_models import Consent, SourceRecord

SECRET_PATTERN = re.compile(
    r"(?i)(?:bearer\s+[A-Za-z0-9_.\-]{12,}|(?:sk|cak|cpk)[_-][A-Za-z0-9_\-]{16,}"
    r"|AKIA[A-Z0-9]{16}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)"
)


def redact(value: str) -> str:
    return SECRET_PATTERN.sub("[REDACTED]", value)


def scan_upload(data: bytes, filename: str) -> str:
    if len(data) > 1024 * 1024 or Path(filename).suffix.lower() not in {".txt", ".json", ".md"}:
        raise DomainError("UPLOAD_REJECTED", "仅接受 1MiB 内的纯文本文件", 422)
    try:
        content = data.decode("utf-8")
    except UnicodeDecodeError:
        raise DomainError("UPLOAD_REJECTED", "文件必须为 UTF-8 文本", 422) from None
    if "\x00" in content or "EICAR-STANDARD-ANTIVIRUS-TEST-FILE" in content:
        raise DomainError("UPLOAD_REJECTED", "文件包含不允许的内容", 422)
    if SECRET_PATTERN.search(content):
        raise DomainError("CREDENTIAL_DETECTED", "请移除凭据后再提交", 422)
    return content


def require_consent(db: Session, principal: Principal, purpose: str):
    consent = db.get(Consent, (principal.user_id, principal.tenant_id, purpose))
    if not consent or not consent.granted:
        raise DomainError("CONSENT_REQUIRED", "请先授权相应用途", 403)


def source_scopes(source: SourceRecord) -> list[str]:
    """Malformed registry data must never grant a scope or break the operations view."""
    raw = source.scopes_json
    if not isinstance(raw, str) or len(raw) > 4000:
        return []
    try:
        scopes = json.loads(raw)
    except (ValueError, RecursionError):
        return []
    if (
        not isinstance(scopes, list)
        or len(scopes) > 32
        or any(
            not isinstance(scope, str) or not re.fullmatch(r"[a-z][a-z0-9_.:-]{0,79}", scope)
            for scope in scopes
        )
        or len(set(scopes)) != len(scopes)
    ):
        return []
    return scopes


def require_source(source: SourceRecord | None, operation: str, now: int):
    if not source or not (
        source.enabled == 1
        and isinstance(source.reviewed_by, str)
        and source.reviewed_by.strip()
        and isinstance(source.access_basis, str)
        and source.access_basis.strip()
        and source.reviewed_until > now
        and source.rate_per_minute > 0
        and source.retention_days > 0
    ):
        raise DomainError("SOURCE_DISABLED", "来源尚未通过有效接入审查", 403)
    if operation not in source_scopes(source):
        raise DomainError("SOURCE_SCOPE_DENIED", "来源操作超出审核范围", 403)


def validate_public_url(url: str, allowed_hosts: set[str], resolver=socket.getaddrinfo):
    """Validate every redirect again. Callers must pin the validated IP at connect time."""
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme != "https"
        or not host
        or host not in allowed_hosts
        or parsed.username
        or parsed.password
        or parsed.port not in {None, 443}
    ):
        raise DomainError("UNSAFE_URL", "URL 不在允许范围", 422)
    try:
        addresses = {item[4][0] for item in resolver(host, 443, type=socket.SOCK_STREAM)}
        if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
            raise ValueError("non-public address")
    except (OSError, ValueError):
        raise DomainError("UNSAFE_URL", "URL 地址不能安全访问", 422) from None
    return host, addresses


class SecretVault:
    """Key provided through a mounted secret file; no key is stored with ciphertext."""

    def __init__(self, key_file: str | None = None):
        path = key_file or os.environ.get("CAREERPILOT_VAULT_KEY_FILE", "")
        if not path:
            raise DomainError("KEY_UNAVAILABLE", "未配置密钥文件", 503)
        self.cipher = Fernet(Path(path).read_bytes().strip())

    def encrypt(self, value: str) -> str:
        return self.cipher.encrypt(value.encode()).decode()

    def decrypt(self, value: str) -> str:
        return self.cipher.decrypt(value.encode()).decode()


def payload_hash(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
