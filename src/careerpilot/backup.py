"""SQLite snapshot primitive, shared by CLI and recovery drills."""

import os
import sqlite3
from contextlib import closing
from pathlib import Path


def snapshot(source: Path, target: Path):
    if not source.is_file():
        raise FileNotFoundError("Source database missing")
    with target.open("xb"):
        pass
    os.chmod(target, 0o600)
    try:
        with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as origin:
            with closing(sqlite3.connect(target)) as destination:
                origin.backup(destination)
                if destination.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise RuntimeError("Integrity check failed")
    except Exception:
        target.unlink()
        raise
