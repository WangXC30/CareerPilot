"""Fingerprint reviewed source and local reports after verification, without changing status."""

import hashlib
import json
from pathlib import Path

from temporary_artifacts import temporary_artifacts

ROOT = Path(__file__).resolve().parents[1]


def main():
    path = ROOT / "docs/v0-acceptance.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    files = set()
    for task in manifest["tasks"]:
        files.update(task["implementation"] + task["evidence"])
    for folder in ["src", "scripts", "tests", "migrations", "evaluation", "infra", ".github"]:
        for item in (ROOT / folder).rglob("*"):
            if (
                item.is_file()
                and "__pycache__" not in item.parts
                and item.suffix != ".pyc"
                and not any(part.endswith(".egg-info") for part in item.parts)
            ):
                files.add(item.relative_to(ROOT).as_posix())
    files.update(
        [
            "Dockerfile",
            "compose.yaml",
            "requirements.lock",
            "pyproject.toml",
            "README.md",
            "PLAN.md",
            "ARCHITECTURE.md",
            "PRD.md",
            "TASKS.md",
            "alembic.ini",
            ".env.example",
            ".dockerignore",
            ".gitignore",
        ]
    )
    manifest["evidence_sha256"] = {
        relative: hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
        for relative in sorted(files)
    }
    with temporary_artifacts(path, filename=True) as output:
        output.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    print(f"Recorded {len(files)} source/evidence fingerprints; no test status was changed")


if __name__ == "__main__":
    main()
