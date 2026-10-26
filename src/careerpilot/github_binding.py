"""Owner-bound OAuth with encrypted secrets and explicit, public-only synchronization."""

import base64
import hashlib
import hmac
import http.client
import json
import os
import re
import secrets
import socket
import ssl
import time
from urllib.parse import urlencode, urlsplit
from uuid import NAMESPACE_URL, uuid5

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import delete, select

from careerpilot import growth
from careerpilot.application import audit
from careerpilot.domain import DomainError
from careerpilot.growth_models import GrowthRecord, GrowthRevision
from careerpilot.safety import validate_public_url
from careerpilot.v1_models import DeletionRequest

SECRET_KIND = "github_secret"
PUBLIC_KIND = "github_binding"
CONFIG = ("GITHUB_CLIENT_ID", "GITHUB_CLIENT_SECRET", "GITHUB_CALLBACK_URL", "OAUTH_ENCRYPTION_KEY")


def configuration():
    values = [os.environ.get("CAREERPILOT_" + key, "") for key in CONFIG]
    try:
        client, secret, callback, key = values
        parsed = urlsplit(callback)
        if (
            not all(values)
            or not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", client)
            or parsed.scheme not in {"https", "http"}
            or not parsed.hostname
            or (parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1"})
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path != "/app/github-callback.html"
        ):
            raise ValueError()
        cipher = Fernet(key.encode())
    except (ValueError, UnicodeError):
        raise DomainError(
            "GITHUB_NOT_CONFIGURED", "GitHub OAuth 应用或加密密钥未正确配置", 503
        ) from None
    return client, secret, callback, cipher


def remote(host, method, path, *, token=None, body=None, basic=None):
    """Only fixed provider endpoints, pinned public IP, TLS, no redirect or body logging."""
    if host not in {"github.com", "api.github.com"}:
        raise ValueError("Unsupported OAuth host")
    connection = None
    try:
        _, addresses = validate_public_url("https://" + host + path, {host}, socket.getaddrinfo)
        connection = http.client.HTTPSConnection(host, timeout=8)
        sock = socket.create_connection((sorted(addresses)[0], 443), timeout=8)
        try:
            connection.sock = ssl.create_default_context().wrap_socket(sock, server_hostname=host)
        except Exception:
            sock.close()
            raise
        headers = {
            "Accept": "application/json",
            "Accept-Encoding": "identity",
            "User-Agent": "CareerPilot-OAuth",
            "X-GitHub-Api-Version": "2026-03-10",
            "Content-Type": "application/json",
        }
        if token:
            headers["Authorization"] = "Bearer " + token
        if basic:
            headers["Authorization"] = "Basic " + base64.b64encode(basic.encode()).decode()
        connection.request(method, path, json.dumps(body) if body is not None else None, headers)
        response = connection.getresponse()
        if response.status == 204:
            return {}
        if response.status in {401, 403, 429}:
            raise DomainError(
                "GITHUB_ACCESS_DENIED", "GitHub 拒绝访问或限流，请稍后重试或重新绑定", 502
            )
        if response.status != 200 or "application/json" not in response.getheader(
            "Content-Type", ""
        ):
            raise ValueError()
        if response.getheader("Content-Encoding", "identity") != "identity":
            raise ValueError()
        raw = response.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError()
        return json.loads(raw)
    except (OSError, ValueError, http.client.HTTPException, RecursionError):
        raise DomainError(
            "GITHUB_UNAVAILABLE", "GitHub 响应不可用，未保存此次同步结果", 502
        ) from None
    finally:
        if connection:
            connection.close()


def record(db, principal, kind):
    return db.scalar(growth.owner_query(principal, kind).execution_options(populate_existing=True))


def write(db, principal, kind, payload):
    # Credentials and snapshots deliberately have NO immutable revision copies.
    row = record(db, principal, kind)
    if row is None:
        row = GrowthRecord(
            id=str(uuid5(NAMESPACE_URL, f"{principal.tenant_id}/{principal.user_id}/{kind}")),
            user_id=principal.user_id,
            tenant_id=principal.tenant_id,
            kind=kind,
            payload_json=growth.dump(payload),
        )
        db.add(row)
    else:
        row.payload_json = growth.dump(payload)
        row.version += 1
    db.flush()
    return row


def lock(db, principal):
    growth.lock_owner(db, principal)
    if db.scalar(
        select(DeletionRequest.id).where(
            DeletionRequest.user_id == principal.user_id,
            DeletionRequest.tenant_id == principal.tenant_id,
            DeletionRequest.status == "queued",
        )
    ):
        raise DomainError("ACCOUNT_DELETING", "账号正在删除，不能访问 GitHub", 409)


def secret_data(row, cipher):
    if row is None:
        return {}
    try:
        return json.loads(cipher.decrypt(json.loads(row.payload_json)["encrypted"].encode()))
    except (InvalidToken, ValueError, KeyError, TypeError):
        raise DomainError("GITHUB_KEY_CHANGED", "无法读取绑定凭据，请解绑后重新授权", 409) from None


def store_secret(db, principal, cipher, data):
    write(
        db,
        principal,
        SECRET_KIND,
        {"encrypted": cipher.encrypt(growth.dump(data).encode()).decode()},
    )


def status(db, principal):
    try:
        configuration()
        configured = True
    except DomainError:
        configured = False
    row = record(db, principal, PUBLIC_KIND)
    return {
        "configured": configured,
        "configuration_names": ["CAREERPILOT_" + key for key in CONFIG] if not configured else [],
        "binding": growth.data(row) if row else None,
        "scope": "无额外 scope：只读公开账号和本人公开仓库元数据",
        "sync_mode": "manual",
        "notice": "仅点击同步时访问；不读取代码或私有仓库，不作为能力验证。",
    }


def start(db, principal, trace):
    client, _, callback, cipher = configuration()
    lock(db, principal)
    if record(db, principal, PUBLIC_KIND):
        raise DomainError("GITHUB_ALREADY_BOUND", "请先解绑当前账号再重新授权", 409)
    state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
    store_secret(
        db,
        principal,
        cipher,
        {"state": state, "verifier": verifier, "until": int(time.time()) + 600},
    )
    audit(db, principal, principal.user_id, "github.authorization_started", 1, trace)
    return {
        "state": state,
        "authorization_url": "https://github.com/login/oauth/authorize?"
        + urlencode(
            {
                "client_id": client,
                "redirect_uri": callback,
                "scope": "",
                "state": state,
                "code_challenge": base64.urlsafe_b64encode(
                    hashlib.sha256(verifier.encode()).digest()
                )
                .decode()
                .rstrip("="),
                "code_challenge_method": "S256",
            }
        ),
    }


def revoke(token, client, client_secret):
    try:
        remote(
            "api.github.com",
            "DELETE",
            f"/applications/{client}/token",
            basic=client + ":" + client_secret,
            body={"access_token": token},
        )
        return True
    except DomainError:
        return False


def identity(token):
    user = remote("api.github.com", "GET", "/user", token=token)
    if (
        not isinstance(user, dict)
        or type(user.get("id")) is not int
        or user["id"] <= 0
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}", str(user.get("login", "")))
    ):
        raise ValueError()
    return {"id": user["id"], "login": user["login"]}


def finish(db, principal, state, code, trace):
    client, client_secret, callback, cipher = configuration()
    lock(db, principal)
    row = record(db, principal, SECRET_KIND)
    pending = secret_data(row, cipher)
    if (
        not pending.get("state")
        or not hmac.compare_digest(pending["state"], state)
        or pending["until"] <= time.time()
    ):
        raise DomainError(
            "GITHUB_INVALID_STATE", "授权已过期、已使用或不属于当前账号，请重新开始", 409
        )
    # Commit state consumption even if exchange fails; never make an authorization code replayable.
    store_secret(db, principal, cipher, {})
    token = None
    try:
        result = remote(
            "github.com",
            "POST",
            "/login/oauth/access_token",
            body={
                "client_id": client,
                "client_secret": client_secret,
                "code": code,
                "redirect_uri": callback,
                "code_verifier": pending["verifier"],
            },
        )
        if not isinstance(result, dict):
            raise ValueError()
        token = result.get("access_token")
        if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_\-]{10,512}", token):
            token = None
            raise ValueError()
        if result.get("scope") != "" or str(result.get("token_type", "")).lower() != "bearer":
            raise DomainError(
                "GITHUB_EXCESS_SCOPE",
                "授权范围超过公开只读需求；已拒绝保存，请撤销旧授权后重试",
                409,
            )
        user = identity(token)
        lifetime = result.get("expires_in", 0)
        if type(lifetime) is not int or lifetime < 0:
            raise ValueError()
        store_secret(
            db,
            principal,
            cipher,
            {
                "token": token,
                "client": client,
                "expires": int(time.time()) + lifetime if lifetime else 0,
            },
        )
        row = write(
            db,
            principal,
            PUBLIC_KIND,
            {
                "login": user["login"],
                "github_id": user["id"],
                "scopes": [],
                "repositories": [],
                "synced_at": None,
                "last_attempt": 0,
                "expires_at": int(time.time()) + lifetime if lifetime else None,
            },
        )
        audit(db, principal, row.id, "github.bound", row.version, trace)
    except (DomainError, ValueError, TypeError, AttributeError) as exc:
        if token:
            revoke(token, client, client_secret)
        db.commit()
        if isinstance(exc, DomainError):
            raise
        raise DomainError("GITHUB_INVALID_RESPONSE", "授权响应无效，请重新授权", 502) from None
    return status(db, principal)


def sync(db, principal, trace):
    client, _, _, cipher = configuration()
    lock(db, principal)
    binding = record(db, principal, PUBLIC_KIND)
    if binding is None:
        raise DomainError("GITHUB_NOT_BOUND", "请先绑定 GitHub", 409)
    payload = json.loads(binding.payload_json)
    secret = secret_data(record(db, principal, SECRET_KIND), cipher)
    now = int(time.time())
    if (
        not secret.get("token")
        or secret.get("client") != client
        or (secret.get("expires") and secret["expires"] <= now)
    ):
        raise DomainError("GITHUB_REAUTHORIZE", "授权已失效或应用已变更，请解绑后重新授权", 409)
    if now - payload["last_attempt"] < 60:
        raise DomainError("GITHUB_SYNC_RATE_LIMIT", "每分钟最多同步一次，请稍后再试", 429)
    payload["last_attempt"] = now
    write(db, principal, PUBLIC_KIND, payload)
    # Hold the owner lock through the bounded request: unbind/deletion cannot race a late write.
    try:
        user = identity(secret["token"])
        if user["id"] != payload["github_id"]:
            raise ValueError()
        payload["login"] = user["login"]
        repositories = remote(
            "api.github.com",
            "GET",
            "/user/repos?visibility=public&affiliation=owner&per_page=100&sort=updated",
            token=secret["token"],
        )
        if not isinstance(repositories, list) or len(repositories) > 100:
            raise ValueError()
        clean = []
        for repo in repositories:
            if not isinstance(repo, dict) or repo.get("private") is not False:
                continue
            owner = repo.get("owner")
            if not isinstance(owner, dict) or owner.get("id") != payload["github_id"]:
                continue
            name = repo.get("name")
            if (
                not isinstance(name, str)
                or not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", name)
                or name in {".", ".."}
            ):
                continue
            clean.append({"name": name, "url": f"https://github.com/{payload['login']}/{name}"})
        payload.update(
            repositories=clean,
            synced_at=growth.utc_now(),
            possibly_truncated=len(repositories) == 100,
        )
        write(db, principal, PUBLIC_KIND, payload)
        audit(db, principal, binding.id, "github.synced", binding.version, trace)
    except (DomainError, ValueError, TypeError) as exc:
        db.commit()
        if isinstance(exc, DomainError):
            raise
        raise DomainError(
            "GITHUB_INVALID_RESPONSE", "仓库响应无效，保留上次同步结果", 502
        ) from None
    return status(db, principal)


def purge(db, principal):
    ids = select(GrowthRecord.id).where(
        GrowthRecord.user_id == principal.user_id,
        GrowthRecord.tenant_id == principal.tenant_id,
        GrowthRecord.kind.in_((SECRET_KIND, PUBLIC_KIND)),
    )
    db.execute(delete(GrowthRevision).where(GrowthRevision.record_id.in_(ids)))
    db.execute(delete(GrowthRecord).where(GrowthRecord.id.in_(ids)))


def unbind(db, principal, trace):
    growth.lock_owner(db, principal)
    remote_revoked = False
    try:
        client, client_secret, _, cipher = configuration()
        secret = secret_data(record(db, principal, SECRET_KIND), cipher)
        if secret.get("token") and secret.get("client") == client:
            remote_revoked = revoke(secret["token"], client, client_secret)
    except DomainError:
        pass
    purge(db, principal)
    audit(db, principal, principal.user_id, "github.unbound", 1, trace)
    return {
        "cleared": True,
        "remote_revoked": remote_revoked,
        "message": "本地凭据和同步记录已清理；远端撤销未确认时请到 GitHub 设置撤销应用。",
    }
