"""Repeat the isolated MyCareer Spike with a pinned installed dependency list."""

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory


def run(directory):
    target, output = directory / "mycareer", directory
    shutil.copytree(
        "/reference",
        target,
        ignore=shutil.ignore_patterns(
            ".git",
            ".venv",
            "node_modules",
            "data",
            "__pycache__",
            ".env",
            ".env.*",
            ".secrets",
            "backups",
        ),
    )
    lock = Path("/workspace/docs/mycareer-installed.lock")
    subprocess.run([sys.executable, "-m", "pip", "install", "-r", str(lock)], check=True)
    tests = ["tests/storage/test_api_keys.py", "tests/api/test_single_worker.py"]
    commands = [
        [sys.executable, "-m", "career_agent.cli", "--help"],
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *tests],
        [sys.executable, "/workspace/scripts/mycareer_poc.py"],
    ]
    for index, command in enumerate(commands):
        result = subprocess.run(command, cwd=target, text=True, capture_output=True)
        (output / f"mycareer-spike-{index}.log").write_text(
            result.stdout + result.stderr, encoding="utf-8"
        )
        result.check_returncode()
    licenses = subprocess.check_output(
        [sys.executable, "-m", "piplicenses", "--format=json", "--with-urls"], text=True
    )
    (output / "mycareer-licenses.json").write_text(licenses, encoding="utf-8")
    manifest = []
    for path in sorted(target.rglob("*")):
        if (
            path.is_file()
            and "__pycache__" not in path.parts
            and not any(part.endswith(".egg-info") for part in path.parts)
        ):
            manifest.append(
                {
                    "path": path.relative_to(target).as_posix(),
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
            )
    (output / "mycareer-source-manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    print("Reference CLI, original tests and adapter PoC passed; temporary verification finished")


def main():
    with TemporaryDirectory(prefix="careerpilot-reference-") as directory:
        run(Path(directory))


if __name__ == "__main__":
    main()
