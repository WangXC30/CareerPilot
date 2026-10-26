"""Scan project-owned source/config only; never print a credential value."""

import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = [
    re.compile(r"(?:cpk_|cak_|sk-)[A-Za-z0-9_\-]{24,}"),
    re.compile(r"AKIA[A-Z0-9]{16}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
]
SKIP = {
    ".git",
    ".venv",
    ".secrets",
    "data",
    "backups",
    "artifacts",
    "skills",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
}


def scan():
    failures = []
    for directory, folders, files in os.walk(ROOT):
        folders[:] = [name for name in folders if name not in SKIP]
        for name in files:
            path = Path(directory) / name
            if path.suffix.lower() not in {
                ".py",
                ".json",
                ".yaml",
                ".yml",
                ".toml",
                ".md",
                ".js",
                ".html",
                ".txt",
                ".lock",
            }:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if any(pattern.search(text) for pattern in PATTERNS):
                failures.append(str(path.relative_to(ROOT)))
    return failures


if __name__ == "__main__":
    failures = scan()
    if failures:
        print("Credential patterns detected in: " + ", ".join(failures))
        raise SystemExit(1)
    print("Project credential scan passed")
