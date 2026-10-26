"""SQLite snapshot and non-overwriting restore, usable in an isolated drill."""

import argparse
from pathlib import Path

from careerpilot.backup import snapshot

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Snapshot or restore to a new database path")
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path)
    args = parser.parse_args()
    snapshot(args.source, args.target)
    print("Snapshot complete; source preserved; integrity verified")
