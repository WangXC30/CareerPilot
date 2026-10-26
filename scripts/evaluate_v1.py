"""Check field gold and every citation span against the imported raw text."""

import argparse
import json
from pathlib import Path

from careerpilot.growth import extract_job

ROOT = Path(__file__).resolve().parents[1]


try:
    from .temporary_artifacts import temporary_artifacts
except ImportError:
    from temporary_artifacts import temporary_artifacts


def evaluate():
    dataset = json.loads((ROOT / "evaluation/jd-v1.json").read_text(encoding="utf-8"))
    results = []
    for case in dataset["cases"]:
        actual = extract_job(case["text"])
        fields_ok = all(
            actual["fields"][key]["value"] == value for key, value in case["fields"].items()
        )
        skills_ok = {s["skill_id"]: s["requirement"] for s in actual["skills"]} == case["skills"]
        spans = [f["evidence"] for f in actual["fields"].values() if f["evidence"]]
        spans += [s["evidence"] for s in actual["skills"]]
        citations_ok = all(
            case["text"][span["start"] : span["end"]] == span["quote"] for span in spans
        )
        results.append(
            {
                "id": case["id"],
                "passed": fields_ok and skills_ok and citations_ok,
                "fields": fields_ok,
                "skills": skills_ok,
                "citations": citations_ok,
            }
        )
    assert all(r["passed"] for r in results), results
    return {
        "dataset": dataset["version"],
        "mode": "deterministic_extractor",
        "results": results,
        "limitations": (
            "Small local gold set with one short real-source excerpt; "
            "not a large-market accuracy claim."
        ),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    with temporary_artifacts(args.output, filename=True) as args.output:
        result = evaluate()
        if args.output:
            args.output.write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        print(json.dumps(result))
