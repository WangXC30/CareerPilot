"""Independent fixed-date cases for the V2 deterministic matching rules."""

import argparse
import json
from datetime import date
from pathlib import Path

from careerpilot import growth, matching

ROOT = Path(__file__).resolve().parents[1]


try:
    from .temporary_artifacts import temporary_artifacts
except ImportError:
    from temporary_artifacts import temporary_artifacts


def run():
    data = json.loads((ROOT / "evaluation/matching-v2.json").read_text(encoding="utf-8"))
    ctx = {"profile": data["profile"], "constraints": data["constraints"], "states": {}}
    records = []
    for case in data["cases"]:
        content = data["base_jd"]
        for old, new in case["replace"]:
            content = content.replace(old, new)
        content += case.get("append", "")
        job = {
            "id": case["id"],
            "version": 1,
            "content": content,
            "status": case.get("job_status", "verified"),
            "sources": [],
            **growth.extract_job(content),
        }
        result = matching.evaluate(job, ctx, date.fromisoformat(data["as_of"]))
        assert result["eligibility"] == case["eligibility"], case["id"]
        if "required_points" in case:
            assert result["components"]["required_skills"]["points"] == case["required_points"]
        for check in result["checks"]:
            span = check["job_evidence"]
            if span:
                assert content[span["start"] : span["end"]] == span["quote"], case["id"]
        records.append(
            {
                "id": case["id"],
                "expected": case["eligibility"],
                "actual": result["eligibility"],
                "passed": True,
            }
        )
    return {
        "scope": data["scope"],
        "as_of": data["as_of"],
        "rule_version": matching.RULE_VERSION,
        "weight_version": matching.WEIGHT_VERSION,
        "total": len(records),
        "passed": len(records),
        "cases": records,
        "hard_fail_cases": sum(r["expected"] == "FAIL" for r in records),
        "hard_fail_in_high_match": 0,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    with temporary_artifacts(args.output, filename=True) as args.output:
        result = run()
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
        print(json.dumps(result, ensure_ascii=False))
