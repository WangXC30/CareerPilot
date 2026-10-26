"""Verify local V0 exit evidence; never substitutes for product/production approval."""

import hashlib
import json
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = {f"CP-{number:03}" for number in range(1, 17)} | {"CP-250"}


def check():
    if not (ROOT / "docs/v0-acceptance.json").exists():
        raise SystemExit(
            "Historical evidence was deleted by request. Run scripts/check_dev.py "
            "for current verification using temporary artifacts."
        )
    manifest = json.loads((ROOT / "docs/v0-acceptance.json").read_text(encoding="utf-8"))
    tasks = manifest["tasks"]
    assert {task["id"] for task in tasks} == EXPECTED
    assert len(tasks) == 17
    for task in tasks:
        assert task["technical_status"] == "passed" and task["acceptance_owner"]
        assert task["implementation"] and task["evidence"]
        for relative in task["implementation"] + task["evidence"]:
            path = ROOT / relative
            assert path.resolve().is_relative_to(ROOT.resolve()) and path.is_file(), relative
    xml = ET.parse(ROOT / "docs/v0-pytest.xml").getroot()
    cases = xml.findall(".//testcase")
    assert len(cases) >= 46
    assert (
        not xml.findall(".//failure")
        and not xml.findall(".//error")
        and not xml.findall(".//skipped")
    )
    evaluation = json.loads((ROOT / "docs/evaluation-v0.json").read_text(encoding="utf-8"))
    assert evaluation["mode"] == "offline_deterministic_rules"
    assert all(row["passed"] for row in evaluation["results"])
    browser = json.loads((ROOT / "docs/ui-v0/browser-report.json").read_text(encoding="utf-8"))
    assert browser["routes"] == 9 and browser["console_errors"] == 0
    assert browser["real_goal_write"] and browser["operations_data"]
    operations = json.loads((ROOT / "docs/operations-v0.json").read_text(encoding="utf-8"))
    assert operations["restored_auth_and_data"] and operations["restored_integrity"] == "ok"
    scan = json.loads((ROOT / "docs/dependencies/vulnerabilities.json").read_text(encoding="utf-8"))
    assert not any(dep.get("vulns") for dep in scan["dependencies"])
    runtime = json.loads(
        (ROOT / "docs/fresh-runtime-v0.log").read_text(encoding="utf-8-sig").strip()
    )
    assert (
        runtime["separate_worker"]
        and runtime["task_status"] == "success"
        and runtime["trace_preserved"]
    )
    trace = json.loads((ROOT / "docs/traceability.json").read_text(encoding="utf-8"))
    assert trace["counts"] == {"FR-": 110, "NFR-": 18, "E2E-": 18}
    assert manifest["remote_ci_executed"] is False and manifest["product_e2e_passed"] == 0
    assert "35 passed" in (ROOT / "docs/mycareer-spike-1.log").read_text(encoding="utf-8")
    assert "PostgreSQL migration roundtrip" in (ROOT / "docs/postgres-v0.log").read_text(
        encoding="utf-8"
    )
    assert manifest["evidence_sha256"]
    for relative, expected in manifest["evidence_sha256"].items():
        path = (ROOT / relative).resolve()
        assert path.is_relative_to(ROOT.resolve()), relative
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected, (
            f"Stale evidence: {relative}"
        )
    return {
        "scope": "V0 local technical exit",
        "tasks": 17,
        "tests": len(cases),
        "passed": True,
        "final_user_signoff": "pending",
        "production_release": False,
    }


if __name__ == "__main__":
    print(json.dumps(check()))
