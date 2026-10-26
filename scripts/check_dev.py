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
            ["scripts/browser_smoke.py"]
            + (["--output", str(report / "ui-platform")] if report else []),
            ["scripts/browser_v1.py"] + (["--output", str(report / "ui-v1")] if report else []),
            ["scripts/browser_v2.py"] + (["--output", str(report / "ui-v2")] if report else []),
            ["scripts/browser_v2_discovery.py"]
            + (["--output", str(report / "ui-discovery")] if report else []),
            ["scripts/browser_v2_hiring.py"]
            + (["--output", str(report / "ui-hiring")] if report else []),
            ["scripts/browser_v2_interviews.py"]
            + (["--output", str(report / "ui-interviews")] if report else []),
            ["scripts/browser_v2_sources.py"]
            + (["--output", str(report / "ui-sources")] if report else []),
            ["scripts/browser_v2_progress.py"]
            + (["--output", str(report / "ui-progress")] if report else []),
            ["scripts/browser_v3_graph.py"]
            + (["--output", str(report / "ui-graph")] if report else []),
            ["scripts/browser_v3_study.py"]
            + (["--output", str(report / "ui-study")] if report else []),
            ["scripts/browser_v3_profile_history.py"],
            ["scripts/browser_v4.py"],
            ["scripts/browser_v4_voice.py"],
            ["scripts/browser_v4_collaboration.py"],
            ["scripts/browser_v4_application.py"],
            ["scripts/browser_v4_calendar.py"],
            ["scripts/browser_calendar_regions.py"],
            ["scripts/browser_v4_governance.py"],
            ["scripts/browser_v4_learning.py"],
            ["scripts/browser_v4_remaining.py"],
            ["scripts/browser_v4_locale.py"],
            ["scripts/browser_matching_locale.py"],
            ["scripts/browser_recruiting_locale.py"],
            ["scripts/browser_project_locale.py"],
            ["scripts/browser_community_locale.py"],
            ["scripts/browser_remaining_locale.py"],
            ["scripts/browser_model_comparison.py"],
            ["scripts/browser_expert_comparison.py"],
        ]
        for command in commands:
            print(f"Running: python {' '.join(command)}", flush=True)
            subprocess.run([sys.executable, *command], check=True)


if __name__ == "__main__":
    main()
