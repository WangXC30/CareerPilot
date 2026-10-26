"""Build a bounded V1 local technical acceptance manifest from existing successful evidence."""

import argparse
import hashlib
import json
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TASK_FILES = {
    "CP-101": ["accounts.py", "v1_api.py", "v1_models.py"],
    "CP-110": ["growth.py", "growth_api.py"],
    "CP-111": ["growth.py", "growth_api.py"],
    "CP-112": ["growth.py", "v1_api.py"],
    "CP-120": ["catalogue.py"],
    "CP-122": ["growth.py", "growth_api.py", "v1_api.py"],
    "CP-130": ["market.py", "v1_api.py"],
    "CP-131": ["growth.py"],
    "CP-132": ["market.py", "growth.py"],
    "CP-140": ["catalogue.py"],
    "CP-141": ["learning.py", "v1_models.py"],
    "CP-142": ["learning.py"],
    "CP-150": ["learning.py"],
    "CP-151": ["catalogue.py", "web_sources.py"],
    "CP-152": ["learning.py", "v1_api.py"],
    "CP-153": ["learning.py", "growth_api.py"],
    "CP-160": ["assessment.py", "learning.py"],
    "CP-170": ["web_sources.py", "safety.py"],
    "CP-220": ["assistant.py", "runtime.py", "worker.py", "tools.py"],
}


try:
    from .temporary_artifacts import temporary_artifacts
except ImportError:
    from temporary_artifacts import temporary_artifacts


def read_json(relative):
    return json.loads((ROOT / relative).read_text(encoding="utf-8-sig"))


def digest(path):
    content = path.read_bytes()
    try:
        content = content.decode("utf-8-sig").replace("\r\n", "\n").encode("utf-8")
    except UnicodeDecodeError:
        pass
    return hashlib.sha256(content).hexdigest()


def validate():
    root = ET.parse(ROOT / "docs/v1-release/v1-pytest.xml").getroot()
    cases = root.findall(".//testcase")
    assert len(cases) >= 100
    assert (
        not root.findall(".//failure")
        and not root.findall(".//error")
        and not root.findall(".//skipped")
    )
    browser = read_json("docs/v1-release/ui-v1/browser-report.json")
    assert browser["e2e"] == {f"E2E-{i:02}": "passed" for i in range(1, 8)}
    assert browser["console_errors"] == browser["worker_errors"] == 0
    assert browser["locked_task_preserved"] and browser["account_export_deletion"]
    platform = read_json("docs/v1-release/ui-platform/browser-report.json")
    assert platform["routes"] == 9 and platform["console_errors"] == 0
    evaluation = read_json("docs/v1-release/evaluation-v1.json")
    assert len(evaluation["results"]) == 5 and all(r["passed"] for r in evaluation["results"])
    operations = read_json("docs/v1-release/operations-v0.json")
    assert operations["restored_integrity"] == "ok" and operations["restored_growth_data"]
    runtime = read_json("docs/runtime-v1.log")
    assert runtime["separate_worker"] and runtime["tool_results_match_persisted_plan"]
    assert runtime["deletion_status"] == "completed" and runtime["sessions_revoked"]
    assert "V1 PostgreSQL profile, JD, budget plan, objective assessment and replan passed" in (
        ROOT / "docs/postgres-v1.log"
    ).read_text(encoding="utf-8-sig")
    return len(cases)


def build():
    count = validate()
    evidence = [
        "docs/v1-release/v1-pytest.xml",
        "docs/v1-release/ui-v1/browser-report.json",
        "docs/v1-release/ui-platform/browser-report.json",
        "docs/v1-release/evaluation-v1.json",
    ]
    tasks = [
        {
            "id": key,
            "technical_status": "passed",
            "acceptance_owner": "Codex local technical verification",
            "implementation": ["src/careerpilot/" + file for file in files],
            "evidence": evidence,
        }
        for key, files in TASK_FILES.items()
    ]
    paths = []
    for folder in ["src/careerpilot", "migrations", "scripts", "tests", "evaluation"]:
        paths += [
            p
            for p in (ROOT / folder).rglob("*")
            if p.is_file()
            and "__pycache__" not in p.parts
            and p.suffix in {".py", ".js", ".css", ".html", ".json"}
        ]
    paths += [
        ROOT / p
        for p in [
            "PLAN.md",
            "PRD.md",
            "TASKS.md",
            "ARCHITECTURE.md",
            "README.md",
            "compose.yaml",
            "pyproject.toml",
            "requirements.lock",
            "Dockerfile",
            "alembic.ini",
            ".github/workflows/ci.yml",
            "docs/V1-EXIT.md",
            "docs/ADR-013-v1-growth-release.md",
            "docs/runtime-v1.log",
            "docs/postgres-v1.log",
            "docs/traceability.json",
        ]
        + evidence
    ]
    paths += [ROOT / "docs/v1-release/operations-v0.json"]
    return {
        "scope": "V1 local growth release",
        "schema_revision": "0004",
        "tests": count,
        "tasks": tasks,
        "quality_packages": ["CP-301", "CP-302"],
        "stage_e2e_passed": 7,
        "e2e05_scope": "V1 objective quiz; V3 code test remains unexecuted",
        "e2e06_scope": "V1 evidence, gap and plan; V2 matching remains later",
        "final_user_signoff": "pending",
        "remote_ci_executed": False,
        "production_release": False,
        "assistant_mode": "local_controlled_tools",
        "material_formats": ["txt", "md", "json"],
        "digest_mode": "utf8-bom-and-newline-normalized",
        "evidence_sha256": {p.relative_to(ROOT).as_posix(): digest(p) for p in sorted(set(paths))},
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    with temporary_artifacts(
        args.output or Path("verification.json"), filename=True
    ) as args.output:
        result = build()
        args.output.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(
            json.dumps(
                {
                    "tasks": len(result["tasks"]),
                    "tests": result["tests"],
                    "quality_packages": 2,
                    "stage_e2e_passed": 7,
                }
            )
        )
