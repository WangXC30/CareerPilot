"""Optional live public metadata probe, separate from offline acceptance gates."""

import argparse
import json
from pathlib import Path

from careerpilot import github_sources, growth

try:
    from .temporary_artifacts import temporary_artifacts
except ImportError:
    from temporary_artifacts import temporary_artifacts


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    with temporary_artifacts(args.output, filename=True) as args.output:
        result = {
            "source": "https://api.github.com/repos/python/cpython",
            "checked_at": growth.utc_now(),
            "authentication": "none",
            "personal_data_or_readme_requested": False,
        }
        try:
            data = github_sources.fetch_metadata("python/cpython")
            result.update(status="available", metadata=data)
        except Exception as exc:
            result.update(
                status="unavailable",
                error_class=getattr(exc, "code", type(exc).__name__),
                fallback="curated local index and user import remain available",
            )
        if args.output:
            args.output.write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
