"""Import the previous local SQLite database before the first Compose startup."""

import sqlite3
from contextlib import closing
from pathlib import Path

SOURCE = Path("/workspace/data/careerpilot.db")
TARGET = Path("/var/lib/careerpilot/careerpilot.db")


def import_database(source: Path, target: Path):
    if not source.is_file():
        raise FileNotFoundError(f"Source database does not exist: {source}")
    # Exclusive creation prevents accidentally overwriting an existing volume.
    with target.open("xb"):
        pass
    try:
        with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as origin:
            with closing(sqlite3.connect(target)) as destination:
                origin.backup(destination)
                if destination.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                    raise RuntimeError("Imported database failed integrity check")
    except Exception:
        # This call created the target and all connections are now closed.
        target.unlink()
        raise
    print("Imported local database; original file preserved")


if __name__ == "__main__":
    import_database(SOURCE, TARGET)
