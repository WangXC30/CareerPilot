"""JD-only intersections, owned source provenance and safe prerequisite projection."""

import json

import pytest
from sqlalchemy.orm import Session

from careerpilot.v1_models import CatalogueEntry
from tests.test_v1_release import exam, ok, version


def jobs(client, headers, contents=None):
    return [
        ok(client.post("/api/v1/jobs/import", headers=headers, json={"content": content}), 201)
        for content in (
            contents
            or [
                "岗位：甲\n要求：Python SQL FastAPI\n优先：Python",
                "岗位：乙\n要求：Python SQL Docker",
                "岗位：丙\n要求：Python PyTorch",
            ]
        )
    ]


def graph(client, headers, selected):
    return client.post(
        "/api/v1/skill-graph", headers=headers, json={"job_ids": [j["id"] for j in selected]}
    )


def test_jd_intersections_provenance_and_prerequisite_closure(system):
    client, _, alice, *_ = system
    selected = jobs(client, alice)
    result = ok(graph(client, alice, selected))
    nodes = {n["skill_id"]: n for n in result["nodes"]}
    assert nodes["python"]["category"] == "common"
    assert nodes["python"]["job_count"] == 3
    assert len(nodes["python"]["sources"]) == 4  # repeated mention, not a fourth job
    assert nodes["sql"]["category"] == "shared"
    assert nodes["docker"]["category"] == "unique"
    assert nodes["git"]["category"] == "prerequisite"
    assert nodes["git"]["job_count"] == 0 and nodes["git"]["sources"] == []
    assert nodes["docker"]["prerequisite_path"] == ["git"]
    assert "react" not in nodes
    for node in nodes.values():
        assert node["verification"]["scope"] == "foundation"
        assert node["resources"]
        for source in node["sources"]:
            original = next(j for j in selected if j["id"] == source["job_id"])
            assert source["job_version"] == original["version"]
            span = source["evidence"]
            assert original["content"][span["start"] : span["end"]] == span["quote"]
    positions = {n["skill_id"]: i for i, n in enumerate(result["nodes"])}
    assert all(positions[e["from"]] < positions[e["to"]] for e in result["edges"])
    assert sum(len(c["skill_ids"]) for c in result["clusters"]) == len(nodes) - 1


def test_empty_jd_remains_in_denominator_and_duplicate_selection_rejected(system):
    client, _, alice, *_ = system
    selected = jobs(
        client,
        alice,
        ["岗位：甲\n要求：Python", "岗位：乙\n要求：Python", "岗位：丙\n要求：沟通协调"],
    )
    result = ok(graph(client, alice, selected))
    assert result["nodes"][0]["category"] == "shared"
    assert result["nodes"][0]["job_count"] == 2
    assert result["jobs"][2]["recognized_skill_ids"] == []
    assert graph(client, alice, [selected[0]] * 3).status_code == 422
    assert graph(client, alice, selected[:2]).status_code == 422
    assert graph(client, alice, selected * 7).status_code == 422
    deduplicated = ok(graph(client, alice, selected + [selected[0]]))
    assert len(deduplicated["jobs"]) == 3
    assert deduplicated["nodes"][0]["job_count"] == 2


def test_graph_rejects_other_owner_and_closed_job_without_mutations(system):
    client, _, alice, bob, *_ = system
    selected = jobs(client, alice)
    before = ok(client.get("/api/v1/account/export", headers=alice))["data"]
    assert graph(client, bob, selected).status_code == 404
    assert (
        client.post(
            "/api/v1/skill-graph", json={"job_ids": [j["id"] for j in selected]}
        ).status_code
        == 401
    )
    ok(graph(client, alice, selected))
    after = ok(client.get("/api/v1/account/export", headers=alice))["data"]
    assert before == after
    ok(
        client.post(
            f"/api/v1/jobs/{selected[0]['id']}/status",
            headers=version(alice, 1),
            json={"status": "closed", "reason": "已确认截止", "confirmed": True},
        )
    )
    response = graph(client, alice, selected)
    assert response.status_code == 422 and response.json()["code"] == "INACTIVE_JOB"


@pytest.mark.parametrize("requires", [["docker"], ["unknown-skill"]])
def test_invalid_catalogue_is_rejected_before_graph_traversal(system, requires):
    client, engine, alice, *_ = system
    selected = jobs(client, alice)
    with Session(engine) as db:
        row = db.get(CatalogueEntry, "git")
        payload = json.loads(row.payload_json)
        payload["requires"] = requires
        row.payload_json = json.dumps(payload)
        db.commit()
    response = graph(client, alice, selected)
    assert response.status_code == 422 and response.json()["code"] == "INVALID_DAG"


def test_graph_uses_current_foundation_state_and_resource_status(system):
    client, engine, alice, *_ = system
    selected = jobs(client, alice)
    _, response = exam(client, alice)
    ok(response)
    with Session(engine) as db:
        row = db.get(CatalogueEntry, "resource-python")
        payload = json.loads(row.payload_json)
        payload["status"] = "unavailable"
        row.payload_json = json.dumps(payload)
        db.commit()
    node = next(n for n in ok(graph(client, alice, selected))["nodes"] if n["skill_id"] == "python")
    assert node["status"] == "verified"
    assert node["resources"][0]["status"] == "unavailable"
    evidence = ok(client.get("/api/v1/skill-evidence", headers=alice))[0]
    ok(
        client.post(
            f"/api/v1/skill-evidence/{evidence['id']}/revoke",
            headers=alice,
            json={"reason": "重新核对", "confirmed": True},
        )
    )
    node = next(n for n in ok(graph(client, alice, selected))["nodes"] if n["skill_id"] == "python")
    assert node["status"] == "needs_reassessment"


def test_negated_and_merely_mentioned_skills_are_not_requirements(system):
    client, _, alice, *_ = system
    selected = jobs(
        client,
        alice,
        [
            "岗位：甲\n要求：Python\n不要求：SQL",
            "岗位：乙\n要求：Python\n资料：SQL",
            "岗位：丙\n要求：Python SQL",
        ],
    )
    result = ok(graph(client, alice, selected))
    sql = next(n for n in result["nodes"] if n["skill_id"] == "sql")
    assert sql["category"] == "unique" and sql["job_ids"] == [selected[2]["id"]]
