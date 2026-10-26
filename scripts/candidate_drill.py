"""Isolated local release drill. All secrets, backups and Docker data are disposable.

Requires Docker Compose and the existing careerpilot-dev:local toolchain image.
Does not read .env or touch the business Compose project. No production SLA claim.
"""

import json
import math
import os
import re
import secrets
import socket
import subprocess
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
PREFIX = "careerpilot-candidate-drill-"


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def validate_config(config, project, scratch):
    """Refuse unsafe changes before creating any containers or disposable volumes."""
    require(re.fullmatch(PREFIX + r"[0-9a-f]{12}", project), "Invalid disposable project")
    require(config["name"] == project, "Unexpected project name")
    require(config["networks"]["default"].get("internal"), "Network must be internal")
    for network in config["networks"].values():
        require(not network.get("external"), "External network prohibited")
        require(network["name"].startswith(project + "_"), "Network outside disposable namespace")
    for volume in config["volumes"].values():
        require(not volume.get("external"), "External volume prohibited")
        require(volume["name"].startswith(project + "_"), "Volume outside disposable namespace")
    for secret in config["secrets"].values():
        require(
            Path(secret["file"]).resolve().parent == scratch.resolve(), "Secret outside scratch"
        )
    services = config["services"]
    require(
        set(services)
        == {
            "postgres",
            "migrate",
            "api-a",
            "api-b",
            "worker-a",
            "worker-b",
            "frontdoor",
            "postgres-restore",
            "restored-api",
        },
        "Unexpected service set",
    )
    for name, service in services.items():
        require(not service.get("privileged"), "Privileged container prohibited")
        require(not service.get("network_mode"), "Network override prohibited")
        networks = {"default", "edge"} if name in {"frontdoor", "restored-api"} else {"default"}
        require(set(service.get("networks", {})) == networks, "Unexpected network")
        for mount in service.get("volumes", []):
            require(mount["type"] == "volume", "Source bind mount prohibited")
            require(mount["source"] in config["volumes"], "Unknown volume")
            require(name.startswith("postgres"), "Application volume prohibited")
        for port in service.get("ports", []):
            require(port.get("host_ip") == "127.0.0.1", "Public port prohibited")
        if not name.startswith("postgres"):
            require(service.get("user") == "10001:10001", "Non-root user required")
            require(service.get("read_only"), "Read-only filesystem required")
            require("ALL" in service.get("cap_drop", []), "Capabilities must be dropped")
            require(not service.get("cap_add"), "Additional capabilities prohibited")
            require(
                "no-new-privileges:true" in service.get("security_opt", []),
                "Privilege escalation prohibited",
            )
        env = service.get("environment", {})
        require("CAREERPILOT_DATABASE_URL" not in env, "Inline database credentials prohibited")
        require(not any("API_KEY" in key for key in env), "Model credentials prohibited")


class Drill:
    def __init__(self, project, scratch):
        self.project = project
        self.scratch = scratch
        self.validated = False
        self.env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("CAREERPILOT_", "CANDIDATE_", "DEEPSEEK_", "COMPOSE_"))
        }
        self.env["CANDIDATE_SECRETS_DIR"] = str(scratch)
        # Reserve both ports together. A later binding race fails closed in Docker.
        with socket.socket() as first, socket.socket() as second:
            first.bind(("127.0.0.1", 0))
            second.bind(("127.0.0.1", 0))
            self.env["CANDIDATE_HTTP_PORT"] = str(first.getsockname()[1])
            self.env["CANDIDATE_RESTORE_PORT"] = str(second.getsockname()[1])
        self.base = [
            "docker",
            "compose",
            "--env-file",
            str(scratch / "empty.env"),
            "--project-directory",
            str(ROOT),
            "-p",
            project,
            "-f",
            str(ROOT / "infra/compose.candidate.yaml"),
            "--profile",
            "restore",
        ]
        (scratch / "empty.env").write_text("", encoding="utf-8")
        app_password = secrets.token_hex(32)
        for name, value in {
            "admin_password": secrets.token_hex(32),
            "app_password": app_password,
            "database_url": f"postgresql+psycopg://careerpilot_app:{app_password}"
            "@postgres/careerpilot_test",
            "restored_database_url": f"postgresql+psycopg://careerpilot_app:{app_password}"
            "@postgres-restore/careerpilot_test",
        }.items():
            path = scratch / name
            path.write_text(value, encoding="utf-8")
            path.chmod(0o600)

    def run(self, args, **kwargs):
        result = subprocess.run(
            args, cwd=ROOT, env=self.env, capture_output=True, timeout=240, **kwargs
        )
        # Never print subprocess output: SQL exceptions and provisioned sessions are private.
        require(result.returncode == 0, "Docker operation failed (output suppressed)")
        return result.stdout.decode("utf-8")

    def compose(self, *args, **kwargs):
        return self.run([*self.base, *args], **kwargs)

    def freeze_config(self):
        config = json.loads(self.compose("config", "--format", "json"))
        validate_config(config, self.project, self.scratch)
        # Both startup and teardown use exactly the reviewed resource names. A
        # source edit during a drill must never redirect down --volumes elsewhere.
        frozen = self.scratch / "compose.frozen.json"
        frozen.write_text(json.dumps(config), encoding="utf-8")
        self.base[self.base.index("-f") + 1] = str(frozen)
        self.validated = True

    def python(self, code):
        return self.compose("exec", "-T", "api-b", "python", "-", input=code.encode())

    def health(self, service):
        container = self.compose("ps", "-aq", service).strip()
        if not container:
            return "missing"
        value = self.run(["docker", "inspect", "--format", "{{json .State}}", container])
        state = json.loads(value)
        return state.get("Health", {}).get("Status", state["Status"])

    def identity(self, service):
        container = self.compose("ps", "-aq", service).strip()
        value = self.run(
            [
                "docker",
                "inspect",
                "--format",
                "{{.Id}} {{.State.StartedAt}} {{.RestartCount}}",
                container,
            ]
        )
        return value.strip()

    def endpoint(self, service, port):
        value = self.compose("port", service, str(port)).strip()
        require(
            re.fullmatch(r"127\.0\.0\.1:[0-9]+", value),
            "Non-loopback endpoint: " + repr(value[:100]),
        )
        return "http://" + value

    def cleanup(self):
        require(re.fullmatch(PREFIX + r"[0-9a-f]{12}", self.project), "Unsafe cleanup project")
        if not self.validated:
            return  # No resources created; never tear down a rejected configuration.
        frozen = Path(self.base[self.base.index("-f") + 1])
        validate_config(json.loads(frozen.read_text(encoding="utf-8")), self.project, self.scratch)
        self.compose("down", "--volumes", "--remove-orphans", "--timeout", "10")
        for resource in ["container", "volume", "network"]:
            output = self.run(
                [
                    "docker",
                    resource,
                    "ls",
                    "-q",
                    "--filter",
                    "label=com.docker.compose.project=" + self.project,
                ]
            )
            require(not output.strip(), "Disposable Docker resource cleanup incomplete")


def request(base, path, token=None, method="GET", payload=None, headers=None):
    outgoing = dict(headers or {})
    if token:
        outgoing["Authorization"] = "Bearer " + token
    body = None
    if payload is not None:
        body = json.dumps(payload).encode()
        outgoing["Content-Type"] = "application/json"
    req = urllib.request.Request(base + path, data=body, headers=outgoing, method=method)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        response = opener.open(req, timeout=8)
    except urllib.error.HTTPError as exc:
        response = exc
    with response:
        raw = response.read(2_000_000)
        try:
            value = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            value = None
        return response.code, value


def wait_for(predicate, label, seconds=90, *, consecutive=1):
    start = time.monotonic()
    successes = 0
    while time.monotonic() - start < seconds:
        try:
            successes = successes + 1 if predicate() else 0
            if successes >= consecutive:
                return time.monotonic() - start
        except (OSError, urllib.error.URLError):
            successes = 0
        time.sleep(0.5)
    raise RuntimeError("Timed out: " + label)


def verify_snapshot(base, alice, bob, goal, job, plan, task):
    for path, resource in [
        ("career-goals", goal),
        ("jobs", job),
        ("plans", plan),
        ("agent-tasks", task),
    ]:
        url = f"/api/v1/{path}/{resource['id']}"
        status, result = request(base, url, alice)
        require(status == 200 and result["id"] == resource["id"], "Owner resource read failed")
        for _ in range(5):
            require(request(base, url, bob)[0] == 404, "Cross-tenant access not denied")
    status, repeated = request(
        base,
        "/api/v1/career-goals",
        alice,
        "POST",
        {"title": "Synthetic candidate goal"},
        {"Idempotency-Key": "candidate-goal"},
    )
    require(status == 201 and repeated == goal, "Idempotent write changed across replicas/restore")


def exercise(drill):
    print("Candidate: building current source without business mounts", flush=True)
    drill.run(
        [
            "docker",
            "build",
            "-q",
            "-f",
            "infra/Dockerfile.candidate",
            "-t",
            "careerpilot-candidate:local",
            ".",
        ]
    )
    image = drill.run(
        [
            "docker",
            "image",
            "inspect",
            "--format",
            "{{.Id}}",
            "careerpilot-candidate:local",
        ]
    ).strip()
    require(re.fullmatch(r"sha256:[0-9a-f]{64}", image), "Unknown immutable image")
    drill.env["CANDIDATE_IMAGE"] = image
    drill.freeze_config()
    drill.compose(
        "up", "-d", "--wait", "--wait-timeout", "150", "frontdoor", "worker-a", "worker-b"
    )
    base = drill.endpoint("frontdoor", 8080)
    wait_for(lambda: request(base, "/health/ready")[0] == 200, "initial readiness")
    credentials = json.loads(
        drill.python("""
import json
from sqlalchemy import text
from careerpilot.persistence import make_engine, database_url
from careerpilot.cli import provision
engine = make_engine(database_url())
with engine.connect() as db:
    assert db.scalar(text("SELECT current_user")) == "careerpilot_app"
    assert not db.scalar(text("SELECT rolsuper FROM pg_roles WHERE rolname=current_user"))
print(json.dumps([provision(engine, "Synthetic candidate Alice"),
                  provision(engine, "Synthetic candidate Bob")]))
engine.dispose()
""")
    )
    (alice_id, alice), (bob_id, bob) = credentials
    require(request(base, "/api/v1/me")[0] == 401, "Anonymous access not denied")
    status, goal = request(
        base,
        "/api/v1/career-goals",
        alice,
        "POST",
        {"title": "Synthetic candidate goal"},
        {"Idempotency-Key": "candidate-goal"},
    )
    require(status == 201, "Goal creation failed")
    status, _ = request(
        base,
        "/api/v1/profile",
        alice,
        "PUT",
        {
            "education": "bachelor",
            "major": "CS",
            "academic_year": "3",
            "graduation_date": "2027-06-30",
            "preferred_cities": ["上海"],
            "weekly_minutes": 600,
            "confirmed": True,
        },
        {"If-Match": '"0"'},
    )
    require(status == 200, "Profile creation failed")
    status, job = request(
        base, "/api/v1/jobs/import", alice, "POST", {"content": "岗位：工程师\n要求：Python"}
    )
    require(status == 201, "Job import failed")
    status, plan = request(
        base, "/api/v1/plans", alice, "POST", {"job_ids": [job["id"]], "start_date": "2026-10-20"}
    )
    require(status == 201, "Plan creation failed")
    status, task = request(
        base, "/api/v1/agent-tasks", alice, "POST", {}, {"Idempotency-Key": "candidate-task"}
    )
    require(status == 202, "Task enqueue failed")
    wait_for(
        lambda: request(base, f"/api/v1/agent-tasks/{task['id']}", alice)[1]["status"] == "success",
        "two-worker task completion",
    )
    verify_snapshot(base, alice, bob, goal, job, plan, task)

    def timed_read(index):
        user, token = (alice_id, alice) if index % 2 == 0 else (bob_id, bob)
        start = time.monotonic()
        status, result = request(base, "/api/v1/me", token)
        require(status == 200 and result["user_id"] == user, "Concurrent identity isolation failed")
        return (time.monotonic() - start) * 1000

    with ThreadPoolExecutor(max_workers=12) as pool:
        times = sorted(pool.map(timed_read, range(120)))
    p95 = times[math.ceil(len(times) * 0.95) - 1]
    require(p95 <= 2000, "Local 120-request p95 exceeded 2 seconds")
    print(f"Candidate: 120/120 concurrent identity reads; local p95={p95:.0f}ms", flush=True)

    drill.compose("stop", "api-a")
    # One round-robin success can precede detection of the failed replica.
    # Require sustained authenticated reads before declaring routing recovered.
    node_rto = wait_for(
        lambda: request(base, "/api/v1/me", alice)[0] == 200,
        "stable API node loss recovery",
        consecutive=6,
    )
    for _ in range(20):
        require(request(base, "/api/v1/me", alice)[0] == 200, "Surviving API unavailable")
    verify_snapshot(base, alice, bob, goal, job, plan, task)
    drill.compose("start", "api-a")
    wait_for(lambda: drill.health("api-a") == "healthy", "API rejoin")

    # Configuration canary failure: no app fault flag or dangerous test route is introduced.
    override = drill.scratch / "failed-canary.yaml"
    override.write_text(
        'services:\n  api-a:\n    command: [python, -c, "import time; time.sleep(600)"]\n',
        encoding="utf-8",
    )
    drill.run(
        [*drill.base, "-f", str(override), "up", "-d", "--no-deps", "--force-recreate", "api-a"]
    )
    wait_for(lambda: drill.health("api-a") == "unhealthy", "failed canary excluded")
    for _ in range(20):
        require(request(base, "/api/v1/me", bob)[0] == 200, "Canary affected surviving API")
    drill.compose("up", "-d", "--no-deps", "--force-recreate", "--wait", "api-a")
    print(
        f"Candidate: API loss stable after {node_rto:.2f}s (includes 6 passing samples); "
        "failed canary/rollback passed",
        flush=True,
    )

    services = ["api-a", "api-b", "worker-a", "worker-b"]
    before = {service: drill.identity(service) for service in services}
    drill.compose("stop", "postgres")
    wait_for(
        lambda: all(drill.health(s) == "unhealthy" for s in services),
        "database outage health signals",
    )
    require(request(base, "/health/ready")[0] == 503, "Unavailable database still routed")
    start = time.monotonic()
    drill.compose("start", "postgres")
    wait_for(lambda: all(drill.health(s) == "healthy" for s in services), "database recovery")
    db_rto = time.monotonic() - start
    require(before == {s: drill.identity(s) for s in services}, "App/worker unexpectedly restarted")
    verify_snapshot(base, alice, bob, goal, job, plan, task)
    print(
        f"Candidate: DB restart recovered in {db_rto:.2f}s without app/worker restart", flush=True
    )

    backup = drill.scratch / "candidate.dump"
    with backup.open("wb") as output:
        result = subprocess.run(
            [
                *drill.base,
                "exec",
                "-T",
                "postgres",
                "pg_dump",
                "-U",
                "careerpilot_admin",
                "-d",
                "careerpilot_test",
                "-Fc",
            ],
            cwd=ROOT,
            env=drill.env,
            stdout=output,
            stderr=subprocess.PIPE,
            timeout=90,
        )
    require(result.returncode == 0 and backup.stat().st_size > 0, "Native PG backup failed")
    drill.compose("up", "-d", "--wait", "postgres-restore")
    with backup.open("rb") as source:
        result = subprocess.run(
            [
                *drill.base,
                "exec",
                "-T",
                "postgres-restore",
                "pg_restore",
                "--exit-on-error",
                "--no-owner",
                "--no-acl",
                "--role=careerpilot_app",
                "-U",
                "careerpilot_admin",
                "-d",
                "careerpilot_test",
            ],
            cwd=ROOT,
            env=drill.env,
            stdin=source,
            capture_output=True,
            timeout=90,
        )
    require(result.returncode == 0, "Native PG restore failed")
    drill.compose("up", "-d", "--wait", "restored-api")
    restored = drill.endpoint("restored-api", 8000)
    verify_snapshot(restored, alice, bob, goal, job, plan, task)
    print(
        "Candidate: native PG snapshot restored; 80/80 cross-tenant resource reads denied; "
        "session/idempotency/business snapshot preserved",
        flush=True,
    )


def main():
    stage = "initialization"
    try:
        with TemporaryDirectory(prefix="careerpilot-candidate-") as directory:
            drill = Drill(PREFIX + uuid4().hex[:12], Path(directory))
            try:
                stage = "local candidate drill"
                exercise(drill)
            finally:
                drill.cleanup()
        print("Candidate: disposable containers/volumes/network and temporary files removed")
    except Exception as exc:
        # No SQL, response bodies, credential-bearing argv or exception text on stdout.
        print(f"Candidate FAILED during {stage}: {type(exc).__name__}", flush=True)
        if isinstance(exc, RuntimeError):
            print(str(exc), flush=True)  # Only fixed messages raised by this script.
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
