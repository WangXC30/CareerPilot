"""Host-only Docker drill: disposable app DB + real isolated code + browser recovery."""

import argparse
import json
import subprocess
from pathlib import Path
from textwrap import dedent
from uuid import uuid4

from project_sandbox import docker, evaluate

try:
    from .temporary_artifacts import temporary_artifacts
except ImportError:
    from temporary_artifacts import temporary_artifacts


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("project-drill"))
    args = parser.parse_args()
    with temporary_artifacts(args.output) as args.output:
        report_dir = args.output
        report_dir.mkdir(parents=True, exist_ok=True)
        name = "careerpilot-project-drill-" + uuid4().hex
        image_id = (
            docker("image", "inspect", "careerpilot-sandbox:local", "--format", "{{.Id}}")
            .stdout.decode()
            .strip()
        )
        try:
            docker(
                "compose",
                "run",
                "-d",
                "--no-deps",
                "--name",
                name,
                "-e",
                "CAREERPILOT_DATABASE_URL=sqlite:////tmp/project-drill.db",
                "-e",
                "PYTHONPATH=/workspace",
                "checks",
                "sleep",
                "600",
            )

            def app(script, *args, body=None):
                return docker(
                    "exec",
                    "-i",
                    name,
                    "python",
                    script,
                    *args,
                    input=json.dumps(body).encode() if body else None,
                )

            app("scripts/project_contract.py", "prepare")
            work = json.loads(app("-m", "careerpilot.project_worker", "claim").stdout)
            receipt = work["receipt"]
            result = evaluate(work["code"], work["cases"], receipt["test_version"], image_id)
            assert result["status"] == "passed", result
            app(
                "-m",
                "careerpilot.project_worker",
                "complete",
                body={
                    "assessment_id": receipt["id"],
                    "lease_token": receipt["lease_token"],
                    "result": result,
                },
            )
            browser = json.loads(app("scripts/project_contract.py", "verify").stdout)
            for width in [1440, 375]:
                for section in ["", "stages-", "star-"]:
                    filename = f"project-{section}{width}.png"
                    docker("cp", f"{name}:/tmp/{filename}", str(report_dir / filename))
            probe = "import os, socket\n" + dedent("""\
    def solve(value):
        assert os.getuid() == 65534
        assert not os.path.exists('/workspace')
        assert not os.path.exists('/var/run/docker.sock')
        assert not os.path.exists('/var/lib/careerpilot/careerpilot.db')
        assert not any(k.startswith('CAREERPILOT_') for k in os.environ)
        try:
            open('/escape', 'w').write('blocked')
            return False
        except OSError:
            pass
        for address in [('1.1.1.1', 80), ('169.254.169.254', 80), ('127.0.0.1', 8000)]:
            try:
                socket.create_connection(address, timeout=0.2)
                return False
            except OSError:
                pass
        return True
    """)
            compile(probe, "isolation-probe.py", "exec")
            isolation = evaluate(probe, [("isolation", None, True)], "isolation-probe.v1", image_id)
            assert isolation["status"] == "passed", isolation
            failure = evaluate(
                "def solve(x): return None", work["cases"], receipt["test_version"], image_id
            )
            assert failure["status"] == "user_failed", failure
            timeout = evaluate("while True: pass", work["cases"], receipt["test_version"], image_id)
            assert timeout["status"] == "timeout", timeout
            flood = evaluate(
                "while True: print('x'*4096, flush=True)",
                work["cases"],
                receipt["test_version"],
                image_id,
            )
            assert flood["status"] == "user_failed", flood
            assert all(r["reason"] == "OUTPUT_LIMIT" for r in flood["results"])
            malformed = evaluate(
                "print('['*2000 + ']'*2000)", work["cases"], receipt["test_version"], image_id
            )
            assert malformed["status"] == "user_failed", malformed
            missing = evaluate("pass", work["cases"], receipt["test_version"], "sha256:" + "0" * 64)
            assert missing["status"] == "system_error", missing
            report = {
                "real_docker": True,
                "image_id": image_id,
                "passed": result,
                "isolation_probe": isolation,
                "user_failed": failure["status"],
                "timeout": timeout["status"],
                "output_flood": "bounded",
                "malformed_output": malformed["status"],
                "missing_image": missing["status"],
                "browser": browser,
                "boundary": "Local single-user Docker isolation; not a production escape audit.",
            }
            (report_dir / "sandbox-report.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(json.dumps(report, ensure_ascii=True))
        except subprocess.CalledProcessError as error:
            # Synthetic drill failures only; never include production payloads here.
            raise RuntimeError(error.stderr.decode(errors="replace")[-4000:]) from None
        finally:
            subprocess.run(
                ["docker", "rm", "-f", name],
                check=False,
                timeout=30,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )


if __name__ == "__main__":
    main()
