"""Unauthenticated repository metadata only; fixed host, pinned DNS, no redirects."""

import http.client
import json
import re
import socket
import ssl
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit

from sqlalchemy import update

from careerpilot import growth, source_library
from careerpilot.domain import DomainError
from careerpilot.platform_models import SourceRecord
from careerpilot.safety import require_consent, require_source, scan_upload, validate_public_url

SOURCE = "github_metadata"


def repository(url):
    clean = source_library.reference_url(url)
    parsed = urlsplit(clean)
    if parsed.hostname != "github.com" or not re.fullmatch(
        r"/[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]{1,100}/?", parsed.path
    ):
        raise DomainError("INVALID_REPOSITORY", "请提供 github.com/所有者/仓库 的公开仓库链接", 422)
    owner, repo = parsed.path.strip("/").split("/")
    repo = repo.removesuffix(".git")
    if repo in {".", "..", ""}:
        raise DomainError("INVALID_REPOSITORY", "仓库名称无效", 422)
    return owner + "/" + repo


def fetch_metadata(name, *, resolver=socket.getaddrinfo, connection_factory=None):
    endpoint = "https://api.github.com/repos/" + name
    host, addresses = validate_public_url(endpoint, {"api.github.com"}, resolver)
    address = sorted(addresses)[0]
    if connection_factory:
        connection = connection_factory(host, address)
    else:
        connection = http.client.HTTPSConnection(host, timeout=8)
        sock = socket.create_connection((address, 443), timeout=8)
        try:
            connection.sock = ssl.create_default_context().wrap_socket(sock, server_hostname=host)
        except Exception:
            sock.close()
            raise
    try:
        connection.request(
            "GET",
            "/repos/" + name,
            headers={
                "Host": host,
                "Accept": "application/vnd.github+json",
                "Accept-Encoding": "identity",
                "User-Agent": "CareerPilot-public-metadata",
                "X-GitHub-Api-Version": "2026-03-10",
            },
        )
        response = connection.getresponse()
        if response.status in {403, 429}:
            raise DomainError(
                "SOURCE_RATE_LIMITED", "GitHub 拒绝访问或限流；稍后重试或手动导入", 429
            )
        if response.status != 200:
            raise DomainError(
                "SOURCE_UNAVAILABLE", "公开仓库不可读取；未追随跳转，可手动导入新链接", 502
            )
        if (
            "application/json" not in response.getheader("Content-Type", "")
            or response.getheader("Content-Encoding", "identity") != "identity"
        ):
            raise ValueError()
        body = response.read(128 * 1024 + 1)
        if len(body) > 128 * 1024:
            raise ValueError()
        data = json.loads(body)
        if (
            data.get("private") is not False
            or data.get("full_name", "").casefold() != name.casefold()
        ):
            raise ValueError()
        counts = {}
        for field in ("stargazers_count", "forks_count"):
            value = data.get(field)
            if type(value) is not int or value < 0:
                raise ValueError()
            counts[field] = value
        result = {"full_name": data["full_name"], **counts}
        for field, limit in (
            ("description", 500),
            ("language", 100),
            ("updated_at", 40),
            ("pushed_at", 40),
        ):
            value = data.get(field)
            if value is not None and not isinstance(value, str):
                raise ValueError()
            result[field] = value[:limit] if value else None
        license_data = data.get("license")
        result["license_spdx"] = (
            license_data.get("spdx_id", "unknown")[:80]
            if isinstance(license_data, dict)
            else "unknown"
        )
        result["archived"] = data.get("archived") is True
        scan_upload(growth.dump(result).encode(), "github-metadata.json")
        return result
    finally:
        connection.close()


def read(db, principal, url, skill_ids, trace):
    name = repository(url)
    require_consent(db, principal, "public_web")
    # One global attempt per two minutes, persisted before the network call, including failures.
    db.execute(
        update(SourceRecord).where(SourceRecord.id == SOURCE).values(enabled=SourceRecord.enabled)
    )
    source = db.get(SourceRecord, SOURCE)
    require_source(source, "metadata", int(time.time()))
    now = datetime.now(timezone.utc)
    if source.last_checked_at:
        last = datetime.fromisoformat(source.last_checked_at)
        interval = 1800 if source.last_error_class else 120
        if (now - last).total_seconds() < interval:
            raise DomainError(
                "SOURCE_RATE_LIMITED", "公开元数据读取处于全局限频/退避中，可先手动导入", 429
            )
    source.last_checked_at = now.isoformat()
    db.commit()
    error: DomainError | None
    try:
        metadata = fetch_metadata(name)
    except (OSError, http.client.HTTPException):
        error = DomainError("SOURCE_UNAVAILABLE", "GitHub 连接失败，可继续本地索引与手动导入", 502)
    except (ValueError, TypeError, AttributeError, DomainError) as exc:
        error = (
            exc
            if isinstance(exc, DomainError)
            else DomainError("SOURCE_INVALID_RESPONSE", "仓库响应无效，未保存结果", 502)
        )
    else:
        error = None
    # Recheck current consent and source review before saving returned data.
    db.expire_all()
    source = db.get(SourceRecord, SOURCE)
    source.last_error_class = error.code if error else ""
    source.status = "degraded" if error else "ready"
    db.commit()
    if error:
        raise error
    growth.lock_owner(db, principal)
    require_consent(db, principal, "public_web")
    require_source(db.get(SourceRecord, SOURCE), "metadata", int(time.time()))
    return source_library.store(
        db,
        principal,
        {
            "title": metadata["full_name"],
            "url": "https://github.com/" + name,
            "text": metadata["description"] or "",
            "metadata": metadata,
            "kind": "project",
            "skill_ids": skill_ids,
            "platform": "github",
            "source_type": "publisher_metadata",
            "declared_type": "unknown",
            "author": "",
            "authorization_scope": "public_repository_metadata",
            "access_mode": "github_public_api",
            "published_on": None,
            "claims": [],
            "retrieved_at": growth.utc_now(),
            "imported_at": None,
            "verification": (
                "仅核对公开仓库元数据；星数、许可证标记和 README 不证明质量、复用许可或本人能力"
            ),
        },
        trace,
    )
