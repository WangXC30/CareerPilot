"""The same checks run inside Compose locally and in CI."""

import argparse
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

try:
    from .temporary_artifacts import temporary_artifacts
except ImportError:
    from temporary_artifacts import temporary_artifacts


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--report-dir", type=Path)
    args = parser.parse_args()
    with (
        TemporaryDirectory(prefix="careerpilot-checks-") as scratch,
        temporary_artifacts(args.report_dir) as args.report_dir,
    ):
        report = args.report_dir
        if report:
            report.mkdir(parents=True, exist_ok=True)
        commands = [
            ["-m", "pip", "check"],
            ["-m", "ruff", "check", "--no-cache", "."],
            ["-m", "ruff", "format", "--check", "--no-cache", "."],
            ["-m", "mypy", f"--cache-dir={Path(scratch) / 'mypy'}"],
            ["scripts/scan_secrets.py"],
            ["scripts/export_web_contract.py", "--check"],
            ["scripts/check_traceability.py", "--check"],
            [
                "-m",
                "pytest",
                "-q",
                "-p",
                "no:cacheprovider",
                f"--basetemp={Path(scratch) / 'pytest'}",
            ]
            + ([f"--junitxml={report / 'v1-pytest.xml'}"] if report else []),
            ["scripts/evaluate.py"]
            + (["--output", str(report / "evaluation-v0.json")] if report else []),
            ["scripts/evaluate_v1.py"]
            + (["--output", str(report / "evaluation-v1.json")] if report else []),
            ["scripts/evaluate_v2.py"]
            + (["--output", str(report / "evaluation-v2.json")] if report else []),
            ["scripts/evaluate_interviews.py"]
            + (["--output", str(report / "evaluation-interviews.json")] if report else []),
            ["scripts/evaluate_v4_matching.py"],
            ["scripts/smoke_http.py"],
            ["scripts/operations_drill.py"]
            + (["--output", str(report / "operations-v0.json")] if report else []),
            ["scripts/browser_web.py"],
            ["scripts/browser_web.py", "--navigation-only"],
            ["scripts/browser_web.py", "--asset-loading-only"],
            ["scripts/browser_web.py", "--journey-only"],
            ["scripts/browser_web.py", "--followup-only"],
            ["scripts/browser_business_editor.py"],
        ]
        for command in commands:
            print(f"Running: python {' '.join(command)}", flush=True)
            subprocess.run([sys.executable, *command], check=True)


if __name__ == "__main__":
    main()
