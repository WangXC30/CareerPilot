"""Versioned offline rule regression; reports do not claim real LLM accuracy."""

import argparse
import json
from pathlib import Path

from careerpilot.baseline_rules import (
    canonical_skill,
    extract_jd,
    hard_match,
    job_validity,
    plan_feasible,
    verified_evidence,
)

ROOT = Path(__file__).resolve().parents[1]


try:
    from .temporary_artifacts import temporary_artifacts
except ImportError:
    from temporary_artifacts import temporary_artifacts


def evaluate():
    dataset = json.loads((ROOT / "evaluation/golden-v1.json").read_text(encoding="utf-8"))
    results, cited, aligned, false_inclusions, definite_failures = [], 0, 0, 0, 0
    for case in dataset["cases"]:
        data, category = case["input"], case["category"]
        if category == "jd":
            extracted = extract_jd(data["text"])
            prediction = {
                "education": extracted["education"],
                "graduation_cohort": extracted["graduation_cohort"],
                "skills": [s["skill_id"] for s in extracted["skills"]],
            }
            if "availability" in case["expected"]:
                prediction["availability"] = job_validity(data["text"], data["as_of"])
            for skill in extracted["skills"]:
                cited += 1
                aligned += int(skill["evidence_span"] in data["text"])
        elif "alias" in data:
            prediction = canonical_skill(data["alias"])
        elif category == "hard_constraints":
            prediction = hard_match(data["actual"], data["required"])
            if case["expected"] == "fail":
                definite_failures += 1
                false_inclusions += int(prediction == "pass")
        elif category == "planning":
            prediction = plan_feasible(data["tasks"], data["budget"])
        else:
            prediction = verified_evidence(data["records"])
        results.append(
            {"id": case["id"], "category": category, "passed": prediction == case["expected"]}
        )
    return {
        "dataset_version": dataset["version"],
        "mode": "offline_deterministic_rules",
        "model_id": None,
        "prompt_version": None,
        "rule_version": "baseline.v1",
        "cost_micros": 0,
        "accuracy": sum(r["passed"] for r in results) / len(results),
        "citation_alignment": aligned / cited if cited else None,
        "false_inclusion_rate": false_inclusions / definite_failures if definite_failures else None,
        "results": results,
        "limitations": "Small synthetic regression, not real provider performance.",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    with temporary_artifacts(args.output, filename=True) as args.output:
        report = evaluate()
        if args.output:
            args.output.write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        print(json.dumps({k: v for k, v in report.items() if k != "results"}, ensure_ascii=False))
        if not all(r["passed"] for r in report["results"]):
            raise SystemExit(1)
