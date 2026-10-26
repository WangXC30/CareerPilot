"""Allowlisted HTTPS reads with pinned DNS, bounded excerpts and no executable content."""

import hashlib
import http.client
import socket
import ssl
import time
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from careerpilot import catalogue
from careerpilot.domain import DomainError
from careerpilot.gateway import reserve
from careerpilot.persistence import utc_now
from careerpilot.platform_models import SourceRecord
from careerpilot.safety import require_consent, require_source, validate_public_url

ALLOWED_HOSTS = {
    "docs.python.org",
    "www.postgresql.org",
    "docs.pytorch.org",
    "pytorch.org",
    "git-scm.com",
    "docs.docker.com",
    "fastapi.tiangolo.com",
    "react.dev",
    "developer.mozilla.org",
    "www.onetonline.org",
    "www.youtube.com",
    "github.com",
}


class TextExcerpt(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.ignored = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript"}:
            self.ignored += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript"}:
            self.ignored = max(0, self.ignored - 1)

    def handle_data(self, data):
        if not self.ignored and data.strip():
            self.parts.append(data.strip())


def fetch_public(url, *, resolver=socket.getaddrinfo, connection_factory=None):
    initial = url
    for _ in range(4):
        try:
            host, addresses = validate_public_url(url, ALLOWED_HOSTS, resolver)
        except ValueError:
            raise DomainError("UNSAFE_URL", "URL 地址或端口无效", 422) from None
        parsed = urlsplit(url)
        address = sorted(addresses)[0]
        if connection_factory:
            connection = connection_factory(host, address)
        else:
            connection = http.client.HTTPSConnection(
                host, timeout=8, context=ssl.create_default_context()
            )
            # Pin the validated IP, preserving the original SNI and certificate hostname.
            sock = socket.create_connection((address, 443), timeout=8)
            try:
                connection.sock = ssl.create_default_context().wrap_socket(
                    sock, server_hostname=host
                )
            except Exception:
                sock.close()
                raise
        try:
            path = parsed.path or "/"
            if parsed.query:
                path += "?" + parsed.query
            connection.request(
                "GET",
                path,
                headers={
                    "Host": host,
                    "Accept": "text/html,text/plain",
                    "Accept-Encoding": "identity",
                    "User-Agent": "CareerPilot/1.0 (bounded public source lookup)",
                },
            )
            response = connection.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                destination = response.getheader("Location")
                if not destination:
                    raise DomainError("SOURCE_UNAVAILABLE", "来源重定向无效", 502)
                url = urljoin(url, destination)
                continue
            if response.status != 200:
                raise DomainError("SOURCE_UNAVAILABLE", "来源暂不可访问，可使用手动导入", 502)
            if response.getheader("Content-Encoding", "identity") != "identity":
                raise DomainError("SOURCE_UNAVAILABLE", "来源响应编码暂不支持", 502)
            content_type = response.getheader("Content-Type", "")
            if not any(t in content_type for t in ("text/html", "text/plain")):
                raise DomainError("SOURCE_UNAVAILABLE", "来源不是可引用的文本页面", 502)
            body = response.read(512 * 1024 + 1)
            if len(body) > 512 * 1024:
                raise DomainError("SOURCE_TOO_LARGE", "页面过大，请使用链接或手动摘录", 422)
            parser = TextExcerpt()
            parser.feed(body.decode("utf-8", errors="replace"))
            excerpt = " ".join(parser.parts)[:500]
            return {
                "source_url": url,
                "requested_url": initial,
                "retrieved_at": utc_now(),
                "access_mode": "public_web",
                "excerpt": excerpt,
                "sha256": hashlib.sha256(body).hexdigest(),
                "trust": "untrusted_data",
                "citation": f"{url} · {utc_now()}",
            }
        finally:
            connection.close()
    raise DomainError("SOURCE_UNAVAILABLE", "来源重定向过多", 502)


def fetch(db, principal, url):
    require_consent(db, principal, "public_web")
    source = db.get(SourceRecord, "public_docs")
    require_source(source, "fetch", int(time.time()))
    reserve(
        db, principal, f"source:public_docs:{int(time.time()) // 60}", 1, source.rate_per_minute
    )
    try:
        return fetch_public(url)
    except DomainError:
        raise
    except (OSError, ssl.SSLError, http.client.HTTPException):
        raise DomainError(
            "SOURCE_UNAVAILABLE", "来源连接失败，可继续使用本地资源与手动导入", 502
        ) from None


def search(db, query, principal=None):
    terms = query.casefold().split()
    resources = catalogue.entries(db, "resource")
    if principal is not None:
        from careerpilot.learning_resources import entries

        resources = entries(db, principal)
    resources = [r for r in resources if r["status"] == "available"]

    def score(item):
        text = " ".join(
            str(item.get(k, "")) for k in ("title", "skill_id", "source", "type")
        ).casefold()
        return sum(term in text for term in terms)

    result = sorted(resources, key=lambda r: (-score(r), r["id"]))
    return {
        "mode": "curated_official_index",
        "query": query,
        "results": [r for r in result if not terms or score(r) > 0],
        "notice": "搜索已登记的官方学习来源；摘要不代表已核实岗位详情。",
    }
