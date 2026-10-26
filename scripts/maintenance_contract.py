"""Synthetic two-actor public publication contract; no private content or external send."""

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from careerpilot.cli import provision
from careerpilot.v1_models import CatalogueEntry


def create_public_maintenance(client, engine):
    _, editor = provision(engine, "Synthetic public maintenance editor", role="operator")
    _, reviewer = provision(engine, "Synthetic independent public reviewer", role="admin")
    eh, rh = {"Authorization": "Bearer " + editor}, {"Authorization": "Bearer " + reviewer}
    original = next(
        r
        for r in client.get("/api/v1/public-catalogue", headers=eh).json()
        if r["id"] == "resource-python"
    )
    payload = {
        k: v for k, v in original.items() if k not in {"id", "version", "kind", "updated_at"}
    }
    response = client.post(
        "/api/v1/admin/catalogue-proposals",
        headers=eh,
        json={
            "entry_id": original["id"],
            "entry_kind": "resource",
            "base_version": original["version"],
            "payload": {**payload, "title": "合成维护标题"},
            "note": "合成公共维护",
            "public_only_confirmed": True,
            "confirmed": True,
        },
    )
    assert response.status_code == 201, response.text

    def approve_publish(proposal):
        path = f"/api/v1/admin/catalogue-proposals/{proposal['id']}"
        approved = client.post(
            path + "/review",
            headers={**rh, "If-Match": f'"{proposal["version"]}"'},
            json={"accepted": True, "note": "独立核对公共字段", "confirmed": True},
        )
        assert approved.status_code == 200, approved.text
        published = client.post(
            path + "/publish",
            headers={**rh, "If-Match": f'"{approved.json()["version"]}"'},
            json={"confirmed": True},
        )
        assert published.status_code == 200, published.text
        return published.json()

    a = approve_publish(response.json())
    response = client.post(
        "/api/v1/admin/catalogue/resource-python/rollback-proposal",
        headers={**eh, "If-Match": f'"{original["version"] + 1}"'},
        json={"target_version": original["version"], "note": "合成回滚", "confirmed": True},
    )
    assert response.status_code == 201, response.text
    b = approve_publish(response.json())
    return {
        "entry_id": original["id"],
        "original_payload": payload,
        "restored_version": original["version"] + 2,
        "proposal_ids": [a["id"], b["id"]],
    }


def verify_public_maintenance(engine, saved):
    with Session(engine) as db:
        entry = db.get(CatalogueEntry, saved["entry_id"])
        assert entry.version == saved["restored_version"]
        assert json.loads(entry.payload_json) == saved["original_payload"]
        assert all(
            json.loads(db.get(CatalogueEntry, key).payload_json)["status"] == "published"
            for key in saved["proposal_ids"]
        )
        versions = {
            json.loads(r.payload_json)["entry_version"]
            for r in db.scalars(select(CatalogueEntry).where(CatalogueEntry.kind == "ops_revision"))
            if json.loads(r.payload_json)["entry_id"] == saved["entry_id"]
        }
        assert {
            saved["restored_version"] - 2,
            saved["restored_version"] - 1,
            saved["restored_version"],
        } <= versions
