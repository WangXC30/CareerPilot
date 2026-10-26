"""Read-only local provenance inventory; not a complete SBOM or license clearance."""

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = {
    "MyCareer": [
        "LICENSE",
        "pyproject.toml",
        "uv.lock",
        "src/career_agent/api/single_worker.py",
        "src/career_agent/storage/api_keys.py",
        "src/career_agent/agent/capabilities/registry.py",
    ],
    "Viora_AI": ["LICENSE", "package.json", "package-lock.json"],
    "Jobyn": ["LICENSE", "frontend/package.json", "backend/requirements.txt"],
}


def main():
    records = []
    for name, files in FILES.items():
        source = Path("D:/Github_Project") / name
        entries = []
        for relative in files:
            path = source / relative
            entries.append(
                {
                    "path": relative,
                    "exists": path.is_file(),
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest()
                    if path.is_file()
                    else None,
                }
            )
        records.append(
            {
                "project": name,
                "local_path": str(source),
                "files": entries,
                "root_license": "MIT" if (source / "LICENSE").is_file() else "unknown",
                "dependency_license_review": "pending",
                "copied_code": False,
            }
        )
    target = ROOT / "docs" / "reference-inventory.json"
    target.write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Recorded {len(records)} reference projects (read-only)")


if __name__ == "__main__":
    main()
