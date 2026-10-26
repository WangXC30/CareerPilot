"""Synthetic, owned reference workflow for database and recovery checks."""


def create_sources(client, headers):
    records = []
    for value, host in [
        ("三次", "www.xiaohongshu.com/explore/test"),
        ("五次", "www.douyin.com/video/1"),
    ]:
        text = "每周练习" + value + "。"
        response = client.post(
            "/api/v1/source-documents",
            headers=headers,
            json={
                "title": "合成来源 " + value,
                "url": "https://" + host,
                "text": text,
                "kind": "experience",
                "skill_ids": ["python"],
                "declared_type": "personal",
                "authorization_scope": "local_excerpt",
                "rights_confirmed": True,
                "confirmed": True,
                "claims": [{"topic": "每周练习", "value": value, "start": 0, "end": len(text)}],
            },
        )
        assert response.status_code == 201, response.text
        records.append(response.json())
    return records


def verify_sources(client, headers, records):
    for record in records:
        assert (
            client.get(f"/api/v1/source-documents/{record['id']}", headers=headers).json() == record
        )
    response = client.post(
        "/api/v1/source-comparisons",
        headers=headers,
        json={"source_ids": [r["id"] for r in records]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["groups"][0]["status"] == "conflicting"
    assert response.json()["groups"][0]["verified"] is False
    response = client.delete(
        f"/api/v1/source-documents/{records[0]['id']}", headers={**headers, "If-Match": '"1"'}
    )
    assert response.status_code == 204, response.text
    assert (
        client.get(f"/api/v1/source-documents/{records[0]['id']}", headers=headers).status_code
        == 404
    )
