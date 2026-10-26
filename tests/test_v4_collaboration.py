"""Workspace access, scoped projections and revocation contracts."""

from sqlalchemy.orm import Session

from careerpilot import growth
from careerpilot.domain import Principal
from careerpilot.persistence import User


def ok(response, status=200):
    assert response.status_code == status, response.text
    return response.json()


def fixture(system):
    client, engine, alice, bob, _, ids = system
    with Session(engine) as db:
        user = db.get(User, ids[0])
        principal = Principal(user.id, user.tenant_id)
        source = growth.save(
            db,
            principal,
            "project_asset",
            {
                "status": "active",
                "title": "Synthetic project",
                "kind": "text",
                "content": "Selected public content",
                "url": "",
                "secret": "NEVER SHARE",
            },
            "test",
        )
        db.commit()
    space = ok(
        client.post("/api/v1/workspaces", headers=alice, json={"name": "Study", "confirmed": True}),
        201,
    )
    return client, alice, bob, ids, source, space


def join(client, alice, bob, ids, space):
    invitation = ok(
        client.post(
            f"/api/v1/workspaces/{space['id']}/invitations",
            headers=alice,
            json={"recipient_id": ids[1], "confirmed": True},
        ),
        201,
    )
    result = client.post(
        f"/api/v1/workspace-invitations/{invitation['id']}/accept",
        headers=bob,
        json={"ticket": invitation["ticket"], "confirmed": True},
    )
    ok(result)
    return invitation


def share(client, alice, source, space=None, public=False):
    return ok(
        client.post(
            "/api/v1/object-shares",
            headers=alice,
            json={
                "kind": "project_asset",
                "source_id": source["id"],
                "source_version": source["version"],
                "fields": ["title", "content"],
                "workspace_id": space["id"] if space else None,
                "public": public,
                "confirmed": True,
            },
        ),
        201,
    )


def test_workspace_cannot_be_enumerated_or_joined_without_ticket(system):
    client, alice, bob, ids, source, space = fixture(system)
    assert ok(client.get("/api/v1/workspaces", headers=bob))["spaces"] == []
    assert (
        client.get(
            "/api/v1/object-shares", headers=bob, params={"workspace_id": space["id"]}
        ).status_code
        == 404
    )
    invitation = join(client, alice, bob, ids, space)
    assert len(ok(client.get("/api/v1/workspaces", headers=bob))["spaces"]) == 1
    assert (
        client.post(
            f"/api/v1/workspace-invitations/{invitation['id']}/accept",
            headers=bob,
            json={"ticket": invitation["ticket"], "confirmed": True},
        ).status_code
        == 404
    )
    assert client.delete("/api/v1/workspaces/" + space["id"], headers=bob).status_code == 404


def test_scoped_share_and_member_removal(system):
    client, alice, bob, ids, source, space = fixture(system)
    join(client, alice, bob, ids, space)
    shared = share(client, alice, source, space)
    endpoint = "/api/v1/object-shares/" + shared["id"]
    response = client.get(endpoint, headers=bob)
    assert "NEVER SHARE" not in response.text
    assert "token_digest" not in response.text
    assert ok(response)["content"] == {
        "title": "Synthetic project",
        "content": "Selected public content",
    }
    assert client.delete(endpoint, headers=bob).status_code == 404
    ok(client.delete(f"/api/v1/workspaces/{space['id']}/members/{ids[1]}", headers=alice))
    assert client.get(endpoint, headers=bob).status_code == 404


def test_share_source_picker_contains_only_owned_metadata(system):
    client, alice, bob, _, source, _ = fixture(system)
    response = client.get("/api/v1/object-share-sources", headers=alice)
    selected = next(item for item in ok(response) if item["id"] == source["id"])
    assert selected["kind"] == "project_asset" and "content" in selected["fields"]
    assert "NEVER SHARE" not in response.text
    assert "Selected public content" not in response.text
    assert ok(client.get("/api/v1/object-share-sources", headers=bob)) == []


def test_public_ticket_revocation_and_private_owner_boundary(system):
    client, alice, bob, _, source, _ = fixture(system)
    shared = share(client, alice, source, public=True)
    endpoint = "/api/v1/public-shares/" + shared["id"]
    headers = {"X-Share-Ticket": shared["ticket"]}
    assert client.get(endpoint).status_code == 422
    assert client.get(endpoint, headers={"X-Share-Ticket": "x" * 40}).status_code == 404
    response = client.get(endpoint, headers=headers)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert set(response.json()) == {"kind", "content", "notice"}
    assert client.get("/api/v1/object-shares/" + shared["id"], headers=bob).status_code == 404
    ok(client.delete("/api/v1/object-shares/" + shared["id"], headers=alice))
    assert client.get(endpoint, headers=headers).status_code == 404


def test_source_change_invalidates_public_share(system):
    client, alice, _, ids, source, _ = fixture(system)
    shared = share(client, alice, source, public=True)
    with Session(system[1]) as db:
        user = db.get(User, ids[0])
        principal = Principal(user.id, user.tenant_id)
        row = growth.owned(db, principal, source["id"], "project_asset")
        growth.save(
            db,
            principal,
            row.kind,
            {**growth.data(row), "content": "new private version"},
            "test",
            row,
            row.version,
        )
        db.commit()
    response = client.get(
        "/api/v1/public-shares/" + shared["id"], headers={"X-Share-Ticket": shared["ticket"]}
    )
    assert response.status_code == 409
    assert "new private version" not in response.text


def test_share_requires_confirmation_and_explicit_fields(system):
    client, alice, bob, _, source, _ = fixture(system)
    body = {"kind": "project_asset", "source_id": source["id"], "fields": ["secret"]}
    assert client.post("/api/v1/object-shares/preview", headers=alice, json=body).status_code == 422
    body["fields"] = ["title"]
    assert client.post("/api/v1/object-shares/preview", headers=bob, json=body).status_code == 404
    assert (
        client.post(
            "/api/v1/object-shares",
            headers=alice,
            json={**body, "source_version": 1, "public": True},
        ).status_code
        == 422
    )


def test_comments_moderation_and_leave(system):
    client, alice, bob, ids, source, space = fixture(system)
    join(client, alice, bob, ids, space)
    shared = share(client, alice, source, space)
    endpoint = "/api/v1/object-shares/" + shared["id"] + "/comments"
    comment = ok(
        client.post(
            endpoint, headers=bob, json={"text": "Please clarify result", "confirmed": True}
        ),
        201,
    )
    assert len(ok(client.get(endpoint, headers=alice))) == 1
    ok(
        client.post(
            "/api/v1/share-comments/" + comment["id"] + "/hide",
            headers=alice,
            json={"reason": "Resolved privately", "confirmed": True},
        )
    )
    assert ok(client.get(endpoint, headers=alice)) == []
    ok(client.delete(f"/api/v1/workspaces/{space['id']}/members/{ids[1]}", headers=bob))
    assert (
        client.post(
            endpoint, headers=bob, json={"text": "after leaving", "confirmed": True}
        ).status_code
        == 404
    )


def test_space_closure_removes_access(system):
    client, alice, bob, ids, source, space = fixture(system)
    join(client, alice, bob, ids, space)
    shared = share(client, alice, source, space)
    ok(client.delete("/api/v1/workspaces/" + space["id"], headers=alice))
    assert client.get("/api/v1/object-shares/" + shared["id"], headers=bob).status_code == 404


def test_account_deletion_cuts_public_and_member_access_immediately(system):
    client, alice, bob, ids, source, space = fixture(system)
    join(client, alice, bob, ids, space)
    public = share(client, alice, source, public=True)
    member = share(client, alice, source, space)
    public_url = "/api/v1/public-shares/" + public["id"]
    ticket = {"X-Share-Ticket": public["ticket"]}
    assert client.get(public_url, headers=ticket).status_code == 200
    assert client.get("/api/v1/object-shares/" + member["id"], headers=bob).status_code == 200
    result = ok(
        client.post(
            "/api/v1/account/deletion",
            headers=alice,
            json={"confirmation": "DELETE MY ACCOUNT"},
        ),
        202,
    )
    assert client.get(public_url, headers=ticket).status_code == 404
    assert client.get("/api/v1/object-shares/" + member["id"], headers=bob).status_code == 404
    assert client.get("/api/v1/me", headers=alice).status_code == 401
    status = ok(
        client.get(
            "/api/v1/account/deletion/" + result["id"],
            headers={"X-Deletion-Receipt": result["receipt"]},
        )
    )
    assert status["status"] == "queued"


def test_leaving_and_rejoining_cannot_resurrect_contributed_shares(system):
    client, alice, bob, ids, _, space = fixture(system)
    join(client, alice, bob, ids, space)
    with Session(system[1]) as db:
        user = db.get(User, ids[1])
        source = growth.save(
            db,
            Principal(user.id, user.tenant_id),
            "project_asset",
            {"status": "active", "title": "Member work", "content": "Selected contribution"},
            "test",
        )
        db.commit()
    shared = share(client, bob, source, space, public=True)
    endpoint = "/api/v1/object-shares/" + shared["id"]
    public = "/api/v1/public-shares/" + shared["id"]
    ticket = {"X-Share-Ticket": shared["ticket"]}
    assert client.get(endpoint, headers=alice).status_code == 200
    assert client.get(public, headers=ticket).status_code == 200
    ok(client.delete(f"/api/v1/workspaces/{space['id']}/members/{ids[1]}", headers=bob))
    assert client.get(public, headers=ticket).status_code == 404
    join(client, alice, bob, ids, space)
    assert client.get(endpoint, headers=alice).status_code == 404
    assert client.get(public, headers=ticket).status_code == 404
    fresh = share(client, bob, source, space)
    assert client.get("/api/v1/object-shares/" + fresh["id"], headers=alice).status_code == 200


def test_removal_revokes_pending_invitation_before_acceptance(system):
    client, alice, bob, ids, _, space = fixture(system)
    invitation = ok(
        client.post(
            f"/api/v1/workspaces/{space['id']}/invitations",
            headers=alice,
            json={"recipient_id": ids[1], "confirmed": True},
        ),
        201,
    )
    ok(client.delete(f"/api/v1/workspaces/{space['id']}/members/{ids[1]}", headers=alice))
    assert (
        client.post(
            f"/api/v1/workspace-invitations/{invitation['id']}/accept",
            headers=bob,
            json={"ticket": invitation["ticket"], "confirmed": True},
        ).status_code
        == 404
    )
