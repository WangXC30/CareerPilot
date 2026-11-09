"""Build only reviewed CodePrep questions from the pinned, unexecuted JSON source."""

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "question_sources" / "codeprep"
OUTPUT = ROOT / "src" / "careerpilot" / "reviewed_codeprep.json"
COMMIT = "1bc3a641cabdcef8bec6771bded34761911a60f5"


def fingerprint(item):
    content = json.dumps(item, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def build():
    source = json.loads((SOURCE / "questions.json").read_text(encoding="utf-8"))["questions"]
    reviewed = json.loads((SOURCE / "reviewed_manifest.json").read_text(encoding="utf-8"))
    if reviewed["source_commit"] != COMMIT:
        raise ValueError("Unexpected CodePrep source revision")
    by_id = {item["id"]: item for item in source}
    if len(by_id) != len(source):
        raise ValueError("Duplicate source ID")
    result = []
    seen = set()
    for entry in reviewed["items"]:
        item = by_id[entry["id"]]
        if item["id"] in seen or fingerprint(item) != entry["fingerprint"]:
            raise ValueError(f"Duplicate or changed item: {item['id']}")
        seen.add(item["id"])
        if item["type"] != "MCQ_SINGLE" or not entry["reference"].startswith("https://"):
            raise ValueError(f"Unsupported or unreferenced item: {item['id']}")
        options = item["answer"]["options"]
        choices = [option["text"] for option in options]
        matches = [
            i
            for i, option in enumerate(options)
            if option["id"] == item["answer"]["correctOptionId"]
        ]
        if len(choices) != 4 or len(set(choices)) != 4 or len(matches) != 1:
            raise ValueError(f"Invalid choices: {item['id']}")
        result.append(
            {
                "id": "codeprep-" + item["id"],
                "topic": entry["topic"],
                "question": item["prompt"],
                "choices": choices,
                "correct": matches[0],
                "explanation": item["explanation"],
                "reference": entry["reference"],
                "origin": f"https://github.com/codeprepdev/codeprep-questions/blob/{COMMIT}/data/questions.json",
                "attribution": "Questions from CodePrep, CC BY 4.0; unmodified",
                "author_url": "https://codeprep.dev",
                "license_url": "https://creativecommons.org/licenses/by/4.0/",
            }
        )
    return json.dumps(result, ensure_ascii=False, indent=2) + "\n"


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    payload = build()
    if args.check:
        if OUTPUT.read_text(encoding="utf-8") != payload:
            raise ValueError("Reviewed CodePrep snapshot is stale")
        print(f"Verified {len(json.loads(payload))} reviewed CodePrep questions")
    else:
        OUTPUT.write_text(payload, encoding="utf-8", newline="\n")
        print(f"Built {len(json.loads(payload))} reviewed CodePrep questions")
