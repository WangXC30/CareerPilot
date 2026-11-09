"""Consistent quiesced data-root backup and non-overwriting isolated restore."""

import argparse
import hashlib
import json
import shutil
import sqlite3
from pathlib import Path

from careerpilot.backup import snapshot
from careerpilot.persistence import utc_now


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def verify(directory):
    root = directory.resolve()
    manifest = json.loads((root / "backup.json").read_text(encoding="utf-8"))
    if manifest.get("format") != "careerpilot.storage.v1":
        raise ValueError("Unsupported backup format")
    for name, expected in manifest["files"].items():
        path = (root / name).resolve()
        if not path.is_relative_to(root) or path.is_symlink() or not path.is_file():
            raise ValueError("Backup reference is unavailable")
        if digest(path) != expected:
            raise ValueError("Backup content mismatch")
        if path.suffix in {".db", ".sqlite"}:
            with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as conn:
                if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("Database integrity mismatch")
                if conn.execute("PRAGMA foreign_key_check").fetchall():
                    raise ValueError("Database reference mismatch")
    return manifest


def backup(source: Path, target: Path):
    """Call only after API/Worker stop; do not copy live WAL main files."""
    source, target = source.resolve(), target.resolve()
    if not source.is_dir() or target == source or source.is_relative_to(target):
        raise ValueError("Invalid backup location")
    target.mkdir(parents=False, exist_ok=False)
    try:
        files = {}
        for path in sorted(source.rglob("*")):
            relative = path.relative_to(source)
            if path.is_relative_to(target) or relative.parts[0] == "backups":
                continue
            if path.is_symlink():
                raise ValueError("Data roots cannot contain symlinks")
            if not path.is_file() or path.name.endswith(("-wal", "-shm", "-journal")):
                continue
            output = target / relative
            output.parent.mkdir(parents=True, exist_ok=True)
            if path.suffix in {".db", ".sqlite"}:
                snapshot(path, output)
            else:
                shutil.copyfile(path, output)
            files[relative.as_posix()] = digest(output)
        manifest = {"format": "careerpilot.storage.v1", "created_at": utc_now(), "files": files}
        (target / "backup.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        verify(target)
    except BaseException:
        # Only the new directory created by this call can be removed.
        if target != source and target.is_dir():
            shutil.rmtree(target)
        raise


def restore(source: Path, target: Path):
    source, target = source.resolve(), target.resolve()
    manifest = verify(source)
    if target == source or target.is_relative_to(source) or source.is_relative_to(target):
        raise ValueError("Restore must use an independent new directory")
    target.mkdir(parents=False, exist_ok=False)
    try:
        for name in manifest["files"]:
            output = target / name
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / name, output)
            if digest(output) != manifest["files"][name]:
                raise ValueError("Restore content mismatch")
    except BaseException:
        shutil.rmtree(target)
        raise


def main():
    parser = argparse.ArgumentParser(description="Back up or restore the whole local data root")
    parser.add_argument("action", choices=["backup", "restore", "verify"])
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path, nargs="?")
    parser.add_argument("--quiesced", action="store_true", help="API and Worker have stopped")
    args = parser.parse_args()
    if args.action == "verify":
        verify(args.source)
    elif not args.quiesced or args.target is None:
        parser.error("Stop API/Worker and supply --quiesced and a new target directory")
    elif args.action == "backup":
        backup(args.source, args.target)
    else:
        restore(args.source, args.target)
    print("Storage operation complete; original preserved and integrity verified")


if __name__ == "__main__":
    main()
