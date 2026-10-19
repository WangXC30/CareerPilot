"""Validate authoritative requirement mappings without inventing acceptance evidence."""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def build():
    tasks = (ROOT / "TASKS.md").read_text(encoding="utf-8")
    prd = (ROOT / "PRD.md").read_text(encoding="utf-8")
    plan = (ROOT / "PLAN.md").read_text(encoding="utf-8")
    pattern = r"(?:FR-[A-Z]+-\d{3}|NFR-\d{3}|E2E-\d{2})"
    expected = set(re.findall(pattern, prd))
    records = {}
    for line in tasks.splitlines():
        match = re.match(r"\| `(" + pattern + r")` \|", line)
        if not match:
            continue
        requirement = match.group(1)
        assert requirement not in records, f"Duplicate mapping: {requirement}"
        owners = re.findall(r"CP-\d{3}", line)
        assert owners, f"No task: {requirement}"
        records[requirement] = {
            "tasks": owners,
            "acceptance_owner": "待指定",
            "status": "未验收",
            "test_evidence": [],
        }
    assert records.keys() == expected, f"Requirement mismatch: {records.keys() ^ expected}"
    counts = {
        prefix: sum(k.startswith(prefix) for k in records) for prefix in ("FR-", "NFR-", "E2E-")
    }
    assert list(counts.values()) == [110, 18, 18], counts
    task_ids = set(re.findall(r"^### (CP-\d{3})｜|^\| \*\*(CP-30\d) ·", tasks, re.M))
    task_ids = {a or b for a, b in task_ids}
    board = re.findall(r"^\| (CP-\d{3}) \|", plan, re.M)
    assert len(board) == len(set(board)) == 84
    assert set(board) == task_ids, set(board) ^ task_ids
    assert all(set(r["tasks"]) <= task_ids for r in records.values())
    return {"counts": counts, "requirements": records}


if __name__ == "__main__":
    result = build()
    target = ROOT / "docs" / "traceability.json"
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Validated {result['counts']}; 84 tasks; wrote {target.name}")
