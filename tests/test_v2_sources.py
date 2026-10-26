"""Source boundaries, honest fallback, deletion and real transport contracts."""

import json
import socket
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from careerpilot import accounts, github_sources, growth, source_library
from careerpilot.domain import DomainError
from careerpilot.growth_models import GrowthRecord, GrowthRevision
from careerpilot.platform_models import Consent, SourceRecord
from tests.test_v1_release import ok, version
from tests.test_v2_opportunities import job, principal_for

BODY = {
    "title": "本人整理的学习经验",
    "url": "https://www.xiaohongshu.com/explore/test",
    "text": "每周练习三次。",
    "kind": "experience",
    "skill_ids": ["python"],
    "declared_type": "personal",
    "author": "本人录入作者",
    "published_on": "2025-01-01",
    "authorization_scope": "local_excerpt",
    "rights_confirmed": True,
    "confirmed": True,
    "claims": [{"topic": "每周练习", "value": "三次", "start": 0, "end": 7}],
}
METADATA = {
    "full_name": "python/cpython",
    "description": "合成公开描述",
    "language": "C",
    "updated_at": "2026-10-01T00:00:00Z",
    "pushed_at": "2026-10-01T00:00:00Z",
    "stargazers_count": 7,
    "forks_count": 2,
    "archived": False,
    "license_spdx": "PSF-2.0",
}


def imported(client, headers, **changes):
    return ok(
        client.post("/api/v1/source-documents", headers=headers, json={**BODY, **changes}), 201
    )


def grant(client, headers):
    ok(client.put("/api/v1/consents/public_web", headers=headers, json={"granted": True}))


def read(client, headers, **changes):
    return client.post(
        "/api/v1/source-github-metadata",
        headers=headers,
        json={
            "url": "https://github.com/python/cpython",
            "skill_ids": ["python"],
            "confirmed": True,
            **changes,
        },
    )


def test_discovery_is_local_has_types_dates_job_and_skill_filters(system, monkeypatch):
    client, _, alice, bob, *_ = system
    monkeypatch.setattr(
        github_sources, "fetch_metadata", lambda *_: pytest.fail("unexpected network")
    )
    j = job(client, alice)
    result = ok(client.get("/api/v1/source-discovery", headers=alice))
    assert {r["kind"] for r in result["results"]} >= {
        "project",
        "video",
        "community",
        "documentation",
    }
    assert result["mode"] == "curated_and_private_index"
    assert all(
        not p["direct_search"] and not p["authorized_api"]
        for p in result["restricted_platforms"].values()
    )
    rows = ok(
        client.get(
            "/api/v1/source-discovery", headers=alice, params={"job_id": j["id"], "kind": "project"}
        )
    )["results"]
    assert rows and all("python" in r["skill_ids"] for r in rows)
    assert all(r["published_on"] is None for r in rows)
    assert (
        ok(
            client.get(
                "/api/v1/source-discovery",
                headers=alice,
                params={"job_id": j["id"], "skill_id": "git"},
            )
        )["results"]
        == []
    )
    assert (
        client.get("/api/v1/source-discovery", headers=bob, params={"job_id": j["id"]}).status_code
        == 404
    )
    assert client.get("/api/v1/source-discovery?skill_id=made-up", headers=alice).status_code == 422
    assert (
        ok(client.get("/api/v1/source-discovery?q=impossible-query", headers=alice))["results"]
        == []
    )


def test_import_labels_dates_scope_tracking_dedup_and_no_promotion(system):
    client, _, alice, bob, *_ = system
    before = ok(client.get("/api/v1/skill-evidence", headers=alice))
    a = imported(
        client, alice, url=BODY["url"] + "?tracking=do-not-store#fragment", declared_type="official"
    )
    assert a["url"] == BODY["url"] and a["platform"] == "xiaohongshu"
    assert a["source_type"] == "user_declared_official" and not a["capability_evidence"]
    assert a["retrieved_at"] is None and a["published_on"] == "2025-01-01"
    assert imported(client, alice, declared_type="official")["id"] == a["id"]
    b = imported(client, alice, url="https://www.douyin.com/video/123", title="另一经验")
    assert b["platform"] == "douyin"
    rows = ok(client.get("/api/v1/source-discovery?platform=douyin", headers=alice))["results"]
    assert [r["id"] for r in rows] == [b["id"]]
    assert ok(client.get("/api/v1/source-documents", headers=bob)) == []
    assert ok(client.get("/api/v1/skill-evidence", headers=alice)) == before
    assert ok(client.get("/api/v1/memories", headers=alice)) == []
    assert ok(client.get("/api/v1/resume-facts", headers=alice)) == []


@pytest.mark.parametrize(
    "changes",
    [
        {"rights_confirmed": False},
        {"authorization_scope": "reference_only"},
        {"text": "", "url": "", "claims": []},
        {"published_on": "2999-01-01"},
        {"claims": [{"topic": "x", "value": "y", "start": 2, "end": 200}]},
        {"claims": [BODY["claims"][0], BODY["claims"][0]]},
        {"text": "sk_" + "z" * 24},
        {"verified": True},
        {"url": "javascript:alert(1)"},
        {"url": "https://user:password@example.com/a"},
        {"url": "https://127.0.0.1/a"},
        {"url": "https://example.com:8443/a"},
    ],
)
def test_import_rejects_invalid_scope_sources_quotes_and_credentials(system, changes):
    client, _, alice, *_ = system
    assert (
        client.post("/api/v1/source-documents", headers=alice, json={**BODY, **changes}).status_code
        == 422
    )
    assert ok(client.get("/api/v1/source-documents", headers=alice)) == []


def test_link_only_never_fetches_and_similar_domain_does_not_impersonate_platform(
    system, monkeypatch
):
    client, _, alice, *_ = system
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *_: pytest.fail("manual link must not resolve")
    )
    a = imported(
        client,
        alice,
        url="https://douyin.com.example.com/a",
        text="",
        claims=[],
        authorization_scope="reference_only",
    )
    assert a["platform"] == "other" and a["text"] == ""
    assert (
        source_library.reference_url("https://xhslink.com/a?share=private")
        == "https://xhslink.com/a"
    )


def test_comparison_preserves_conflicts_quotes_uncertainty_and_foreign_isolation(system):
    client, _, alice, bob, *_ = system
    a = imported(client, alice)
    b = imported(
        client,
        alice,
        title="广告经验",
        url="https://www.douyin.com/video/1",
        declared_type="advertising",
        text="每周练习五次。",
        claims=[{"topic": "每周练习", "value": "五次", "start": 0, "end": 7}],
    )
    c = imported(client, alice, title="同一页面转述")
    path = "/api/v1/source-comparisons"
    result = ok(client.post(path, headers=alice, json={"source_ids": [a["id"], b["id"]]}))
    group = result["groups"][0]
    assert group["status"] == "conflicting" and not group["verified"]
    assert group["distinct_hosts"] == 2
    assert {c["quote"] for c in group["claims"]} == {"每周练习三次。", "每周练习五次。"}
    same = ok(client.post(path, headers=alice, json={"source_ids": [a["id"], c["id"]]}))["groups"][
        0
    ]
    assert same["status"] == "agreement_unverified" and same["distinct_hosts"] == 1
    assert (
        client.post(path, headers=bob, json={"source_ids": [a["id"], b["id"]]}).status_code == 404
    )
    assert (
        client.post(path, headers=alice, json={"source_ids": [a["id"], a["id"]]}).status_code == 422
    )


def test_delete_cas_physically_removes_content_revisions_and_comparison_access(system):
    client, engine, alice, bob, *_ = system
    a = imported(client, alice)
    b = imported(client, alice, title="另一条")
    path = f"/api/v1/source-documents/{a['id']}"
    assert client.delete(path, headers=version(bob, 1)).status_code == 404
    assert client.delete(path, headers=version(alice, 2)).status_code == 412
    assert client.delete(path, headers=version(alice, 1)).status_code == 204
    assert client.get(path, headers=alice).status_code == 404
    assert (
        client.post(
            "/api/v1/source-comparisons", headers=alice, json={"source_ids": [a["id"], b["id"]]}
        ).status_code
        == 404
    )
    with Session(engine) as db:
        assert db.get(GrowthRecord, a["id"]) is None
        assert db.get(GrowthRevision, (a["id"], 1)) is None


def test_expiry_hides_data_and_worker_cleanup_purges_all_revisions(system):
    client, engine, alice, bob, *_ = system
    a = imported(client, alice)
    b = imported(client, bob)
    with Session(engine) as db, db.begin():
        row = db.get(GrowthRecord, a["id"])
        payload = json.loads(row.payload_json)
        payload["expires_at"] = "2020-01-01T00:00:00+00:00"
        row.payload_json = growth.dump(payload)
    assert ok(client.get("/api/v1/source-documents", headers=alice)) == []
    assert client.get(f"/api/v1/source-documents/{a['id']}", headers=alice).status_code == 410
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: source_library.purge_expired(engine), range(2)))
    with Session(engine) as db:
        assert db.get(GrowthRecord, a["id"]) is None
        assert db.get(GrowthRevision, (a["id"], 1)) is None
        assert db.get(GrowthRecord, b["id"]) is not None


def transport(data=None, status=200, content_type="application/json", raw=None):
    calls = []

    class Response:
        def getheader(self, key, default=None):
            return {"Content-Type": content_type}.get(key, default)

        def read(self, limit):
            return raw if raw is not None else json.dumps(data).encode()

    class Connection:
        def request(self, method, path, headers):
            calls.append((method, path, headers))

        def getresponse(self):
            result = Response()
            result.status = status
            return result

        def close(self):
            calls.append("closed")

    def resolver(*args, **kwargs):
        return [(None, None, None, None, ("140.82.112.5", 443))]

    return resolver, lambda host, ip: Connection(), calls


def test_github_transport_pins_dns_reads_minimal_metadata_and_never_user_details():
    data = {
        **METADATA,
        "private": False,
        "license": {"spdx_id": "PSF-2.0"},
        "owner": {"email": "not-retained@example.com"},
        "readme": "untrusted",
    }
    resolver, factory, calls = transport(data)
    result = github_sources.fetch_metadata(
        "python/cpython", resolver=resolver, connection_factory=factory
    )
    assert "owner" not in result and "readme" not in result
    assert result["stargazers_count"] == 7
    assert calls[0][0:2] == ("GET", "/repos/python/cpython")
    assert "Authorization" not in calls[0][2] and calls[-1] == "closed"
    assert (
        github_sources.repository("https://github.com/python/cpython.git?tracking=removed")
        == "python/cpython"
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"status": 403},
        {"status": 429},
        {"status": 301},
        {"status": 404},
        {"content_type": "text/html"},
        {"raw": b"{"},
        {"raw": b"x" * (128 * 1024 + 1)},
        {"data": {"full_name": "python/cpython", "private": True}},
        {"data": {"full_name": "evil/repo", "private": False}},
        {"data": {"full_name": "python/cpython", "private": False, "stargazers_count": True}},
    ],
)
def test_github_transport_rejects_bad_status_redirect_private_or_malformed_data(kwargs):
    resolver, factory, calls = transport(**kwargs)
    with pytest.raises((DomainError, ValueError)):
        github_sources.fetch_metadata(
            "python/cpython", resolver=resolver, connection_factory=factory
        )
    assert calls[-1] == "closed"


def test_github_transport_rejects_private_dns_and_arbitrary_paths():
    with pytest.raises(DomainError):
        github_sources.fetch_metadata(
            "python/cpython",
            resolver=lambda *a, **k: [(None, None, None, None, ("127.0.0.1", 443))],
            connection_factory=lambda *_: pytest.fail("private DNS reached connection"),
        )
    for url in [
        "https://api.github.com/repos/python/cpython",
        "https://github.com/python/cpython/issues",
        "https://github.com/../..",
        "https://github.com/python/%2e%2e",
    ]:
        with pytest.raises(DomainError):
            github_sources.repository(url)


def test_metadata_opt_in_global_quota_across_users_and_no_capability_mutation(system, monkeypatch):
    client, engine, alice, bob, *_ = system
    calls = []
    monkeypatch.setattr(
        github_sources, "fetch_metadata", lambda name: calls.append(name) or METADATA
    )
    assert read(client, alice).status_code == 403 and not calls
    grant(client, alice)
    grant(client, bob)
    a = ok(read(client, alice), 201)
    assert a["access_mode"] == "github_public_api" and a["retrieved_at"]
    assert not a["capability_evidence"] and a["claims"] == []
    assert read(client, bob).status_code == 429 and len(calls) == 1
    with Session(engine) as db:
        source = db.get(SourceRecord, github_sources.SOURCE)
        assert source.last_checked_at and source.last_error_class == ""
    assert ok(client.get("/api/v1/skill-evidence", headers=alice)) == []


def test_parallel_metadata_requests_have_one_global_attempt(system, monkeypatch):
    client, _, alice, bob, *_ = system
    grant(client, alice)
    grant(client, bob)
    calls = []
    monkeypatch.setattr(
        github_sources, "fetch_metadata", lambda name: calls.append(name) or METADATA
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        codes = list(pool.map(lambda h: read(client, h).status_code, (alice, bob)))
    assert sorted(codes) == [201, 429] and len(calls) == 1


@pytest.mark.parametrize(
    "failure",
    [
        OSError("offline"),
        DomainError("SOURCE_RATE_LIMITED", "rate limited", 429),
        ValueError("invalid JSON"),
    ],
)
def test_metadata_failure_is_honest_persisted_and_backed_off(system, monkeypatch, failure):
    client, engine, alice, *_ = system
    grant(client, alice)

    def fail(_):
        raise failure

    monkeypatch.setattr(github_sources, "fetch_metadata", fail)
    assert read(client, alice).status_code in {429, 502}
    assert ok(client.get("/api/v1/source-documents", headers=alice)) == []
    with Session(engine) as db:
        assert db.get(SourceRecord, github_sources.SOURCE).last_error_class
    assert read(client, alice).status_code == 429
    assert imported(client, alice)["access_mode"] == "user_import"


@pytest.mark.parametrize("withdraw", ["consent", "source_review"])
def test_consent_or_source_withdrawn_inflight_prevents_saving_metadata(
    system, monkeypatch, withdraw
):
    client, engine, alice, _, _, ids = system
    grant(client, alice)

    def revoke(_):
        with Session(engine) as db, db.begin():
            if withdraw == "consent":
                principal = principal_for(engine, ids[0])
                db.get(Consent, (principal.user_id, principal.tenant_id, "public_web")).granted = 0
            else:
                db.get(SourceRecord, github_sources.SOURCE).enabled = 0
        return METADATA

    monkeypatch.setattr(github_sources, "fetch_metadata", revoke)
    assert read(client, alice).status_code == 403
    assert ok(client.get("/api/v1/source-documents", headers=alice)) == []


def test_account_export_delete_include_private_sources_without_foreign_leak(system):
    client, engine, alice, bob, _, ids = system
    a = imported(client, alice)
    b = imported(client, bob, title="Bob 私人资料")
    with Session(engine) as db:
        exported = json.dumps(accounts.export_data(db, principal_for(engine, ids[0])))
        assert a["id"] in exported and b["id"] not in exported
    ok(
        client.post(
            "/api/v1/account/deletion", headers=alice, json={"confirmation": "DELETE MY ACCOUNT"}
        ),
        202,
    )
    accounts.process_deletion(engine)
    with Session(engine) as db:
        assert db.get(GrowthRecord, a["id"]) is None
        assert db.get(GrowthRecord, b["id"]) is not None
        assert not list(
            db.scalars(select(GrowthRevision).where(GrowthRevision.record_id == a["id"]))
        )
