"""Validate V1 local technical evidence and the pinned implementation snapshot."""

import json

from finalize_v1_evidence import ROOT, TASK_FILES, digest, read_json, validate


def check():
    if not (ROOT / "docs/v1-acceptance.json").exists():
        raise SystemExit(
            "Historical evidence was deleted by request. Run scripts/check_dev.py "
            "for current verification using temporary artifacts."
        )
    tests = validate()
    manifest = read_json("docs/v1-acceptance.json")
    assert {t["id"] for t in manifest["tasks"]} == set(TASK_FILES) and len(manifest["tasks"]) == 19
    assert all(t["technical_status"] == "passed" for t in manifest["tasks"])
    assert (
        manifest["quality_packages"] == ["CP-301", "CP-302"] and manifest["stage_e2e_passed"] == 7
    )
    assert not manifest["production_release"] and not manifest["remote_ci_executed"]
    for relative, expected in manifest["evidence_sha256"].items():
        path = (ROOT / relative).resolve()
        assert path.is_relative_to(ROOT.resolve()) and path.is_file(), relative
        assert digest(path) == expected, f"V1 snapshot changed: {relative}"
    return {
        "scope": manifest["scope"],
        "tasks": 19,
        "quality_packages": 2,
        "tests": tests,
        "stage_e2e_passed": 7,
        "passed": True,
        "final_user_signoff": "pending",
        "production_release": False,
    }


if __name__ == "__main__":
    print(json.dumps(check()))
