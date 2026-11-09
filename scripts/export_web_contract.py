"""Export public request schemas used by the React forms; no database reads."""

import argparse
import json
from pathlib import Path

from careerpilot.api import create_app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    schema = create_app("sqlite:///:memory:").openapi()
    target = Path("frontend/src/shared/contracts.json")
    if args.check:
        actual = json.loads(target.read_text(encoding="utf-8"))
        if actual != schema["components"]["schemas"]:
            raise SystemExit("React request schemas are stale; export_web_contract.py must run")
        print("React request schemas match current OpenAPI")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(schema["components"]["schemas"], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
