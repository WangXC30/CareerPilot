"""Reproducible installed SBOM + license inventory + vulnerability review artifacts."""

import argparse
import importlib.metadata as metadata
import json
import subprocess
import sys
from pathlib import Path

try:
    from .temporary_artifacts import temporary_artifacts
except ImportError:
    from temporary_artifacts import temporary_artifacts


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    with temporary_artifacts(args.output or Path("verification.json")) as args.output:
        args.output.mkdir(parents=True, exist_ok=True)
        inventory = []
        for dist in metadata.distributions():
            info = dist.metadata
            inventory.append(
                {
                    "name": info["Name"],
                    "version": dist.version,
                    "license_expression": info.get("License-Expression"),
                    "license": info.get("License"),
                    "requires": dist.requires or [],
                }
            )
        (args.output / "sbom.json").write_text(
            json.dumps(inventory, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        licenses = subprocess.check_output(
            [sys.executable, "-m", "piplicenses", "--format=json", "--with-urls"], text=True
        )
        (args.output / "licenses.json").write_text(licenses, encoding="utf-8")
        # Only package names and versions are sent to PyPI's advisory service.
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pip_audit",
                "--disable-pip",
                "--no-deps",
                "-r",
                "requirements.lock",
                "--format=json",
                "--output",
                str(args.output / "vulnerabilities.json"),
            ],
            check=False,
        )
        if result.returncode:
            report_path = args.output / "vulnerabilities.json"
            if report_path.exists():
                report = json.loads(report_path.read_text(encoding="utf-8"))
                for dependency in report.get("dependencies", []):
                    for vulnerability in dependency.get("vulns", []):
                        print(
                            f"{dependency['name']}=={dependency['version']}: "
                            f"{vulnerability['id']}; fixes: "
                            f"{', '.join(vulnerability.get('fix_versions', [])) or 'unavailable'}",
                            flush=True,
                        )
            raise SystemExit(result.returncode)
        print("Dependency report and advisory scan passed; temporary output cleaned on exit")


if __name__ == "__main__":
    main()
