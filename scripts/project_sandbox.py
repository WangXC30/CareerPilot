"""Standard-library-only host runner; submitted code runs exclusively in Docker.

The host computes verdicts from bounded outputs. Expected values and credentials
never enter the sandbox. API/worker containers receive no Docker socket.
"""

import argparse
import json
import subprocess
import threading
import time
from uuid import uuid4

HARNESS = """
import json, sys
payload = json.load(sys.stdin)
namespace = {}
try:
    exec(compile(payload['code'], 'solution.py', 'exec'), namespace)
    results = []
    for value in payload['inputs']:
        try:
            results.append({'value': namespace['solve'](value)})
        except BaseException:
            results.append({'error': 'USER_EXCEPTION'})
    print(json.dumps(results, ensure_ascii=True, allow_nan=False))
except BaseException:
    print('[]')
"""


def docker(*args, **kwargs):
    return subprocess.run(["docker", *args], check=True, timeout=30, capture_output=True, **kwargs)


def sandbox_command(image_id, name):
    return [
        "docker",
        "run",
        "--rm",
        "-i",
        "--name",
        name,
        "--network",
        "none",
        "--read-only",
        "--user",
        "65534:65534",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--cpus",
        "0.5",
        "--memory",
        "128m",
        "--memory-swap",
        "128m",
        "--pids-limit",
        "32",
        "--log-driver",
        "none",
        "--ulimit",
        "nofile=64:64",
        "--ulimit",
        "fsize=65536:65536",
        "--ulimit",
        "core=0:0",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,nodev,size=16m",
        image_id,
        HARNESS,
    ]


def run_bounded(command, content, name, timeout=12, limit=16384):
    process = subprocess.Popen(
        command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
    )
    output = bytearray()
    overflow = threading.Event()

    def read():
        assert process.stdout is not None
        while chunk := process.stdout.read(4096):
            if len(output) + len(chunk) > limit:
                overflow.set()
                # Continue draining without retaining bytes so Docker's attach stream
                # cannot block container removal when the process floods stdout.
                continue
            output.extend(chunk)

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    try:
        assert process.stdin is not None
        # Source payload is bounded at 32 KiB by the API; the harness reads it immediately.
        process.stdin.write(content)
        process.stdin.close()
        deadline = time.monotonic() + timeout
        while process.poll() is None and not overflow.wait(0.05):
            if time.monotonic() >= deadline:
                return "timeout", b""
        reader.join(timeout=1)
        if overflow.is_set():
            return "output_limit", b""
        return process.returncode, bytes(output)
    except BrokenPipeError:
        return "system_error", b""
    finally:
        # Kill the container as well as its CLI on timeout/overflow; --rm alone is insufficient.
        subprocess.run(
            ["docker", "rm", "-f", name],
            timeout=15,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        reader.join(timeout=2)


def evaluate(code, cases, test_version, image_id):
    name = "careerpilot-eval-" + uuid4().hex
    base = {"test_version": test_version, "image_id": image_id}
    try:
        status, output = run_bounded(
            sandbox_command(image_id, name),
            json.dumps(
                {"code": code, "inputs": [case[1] for case in cases]}, ensure_ascii=True
            ).encode(),
            name,
        )
    except (OSError, subprocess.SubprocessError):
        return {**base, "status": "system_error", "results": []}
    if status == "timeout":
        return {**base, "status": "timeout", "results": []}
    if status == "system_error" or status in {125, 126, 127, 137}:
        return {**base, "status": "system_error", "results": []}
    try:
        values = json.loads(output)
        if not isinstance(values, list) or len(values) != len(cases):
            values = []
    except (ValueError, UnicodeDecodeError, RecursionError):
        values = []
    results = []
    for i, (key, _, expected) in enumerate(cases):
        value = values[i] if values and isinstance(values[i], dict) else {}
        passed = (
            status == 0
            and "value" in value
            and json.dumps(value["value"], sort_keys=True, ensure_ascii=True)
            == json.dumps(expected, sort_keys=True, ensure_ascii=True)
        )
        results.append(
            {
                "id": key,
                "passed": passed,
                "reason": "MATCHED"
                if passed
                else ("OUTPUT_LIMIT" if status == "output_limit" else "OUTPUT_MISMATCH"),
            }
        )
    return {
        **base,
        "status": "passed" if all(r["passed"] for r in results) else "user_failed",
        "results": results,
    }


def bridge(action, body=None):
    result = docker(
        "compose",
        "exec",
        "-T",
        "worker",
        "python",
        "-m",
        "careerpilot.project_worker",
        action,
        input=json.dumps(body, ensure_ascii=True).encode() if body else None,
    )
    return json.loads(result.stdout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-jobs", type=int, default=1, choices=range(1, 21))
    args = parser.parse_args()
    # Resolve the dedicated image to an immutable local ID before claiming anything.
    image_id = (
        docker("image", "inspect", "careerpilot-sandbox:local", "--format", "{{.Id}}")
        .stdout.decode()
        .strip()
    )
    for _ in range(args.max_jobs):
        work = bridge("claim")
        if work is None:
            print(json.dumps({"status": "idle"}))
            break
        receipt = work["receipt"]
        result = evaluate(work["code"], work["cases"], receipt["test_version"], image_id)
        bridge(
            "complete",
            {
                "assessment_id": receipt["id"],
                "lease_token": receipt["lease_token"],
                "result": result,
            },
        )
        print(json.dumps({"assessment_id": receipt["id"], "status": result["status"]}))


if __name__ == "__main__":
    main()
