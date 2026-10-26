"""Secret configuration and process health during real database disconnects."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy.exc import InterfaceError, OperationalError

from careerpilot import persistence, worker
from scripts import candidate_drill
from scripts.candidate_drill import PREFIX, Drill, validate_config


@pytest.mark.parametrize("interruption", [False, OSError("synthetic connection failure")])
def test_recovery_requires_consecutive_success_after_any_interruption(monkeypatch, interruption):
    clock = [0.0]
    monkeypatch.setattr(
        candidate_drill,
        "time",
        SimpleNamespace(
            monotonic=lambda: clock[0],
            sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
        ),
    )
    probe = Mock(side_effect=[True, interruption, True, True, True])
    elapsed = candidate_drill.wait_for(probe, "routing", seconds=5, consecutive=3)
    assert probe.call_count == 5 and elapsed == 2.0


def test_alternating_healthy_and_failed_backends_never_count_as_recovered(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(
        candidate_drill,
        "time",
        SimpleNamespace(
            monotonic=lambda: clock[0],
            sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
        ),
    )
    probe = Mock(side_effect=[True, False] * 5)
    with pytest.raises(RuntimeError, match="Timed out: routing"):
        candidate_drill.wait_for(probe, "routing", seconds=2, consecutive=3)


@pytest.fixture(autouse=True)
def clean_database_env(monkeypatch):
    for key in [
        "CAREERPILOT_DATABASE_URL",
        "CAREERPILOT_DATABASE_URL_FILE",
        "CAREERPILOT_DEPLOYMENT_MODE",
    ]:
        monkeypatch.delenv(key, raising=False)


def test_secret_file_and_development_default(monkeypatch, tmp_path):
    assert persistence.database_url() == "sqlite:///./data/careerpilot.db"
    secret = tmp_path / "database_url"
    secret.write_text("postgresql+psycopg://candidate@postgres/disposable\n", encoding="utf-8")
    monkeypatch.setenv("CAREERPILOT_DATABASE_URL_FILE", str(secret))
    monkeypatch.setenv("CAREERPILOT_DEPLOYMENT_MODE", "candidate")
    assert persistence.database_url() == "postgresql+psycopg://candidate@postgres/disposable"
    monkeypatch.setenv("CAREERPILOT_DATABASE_URL", "sqlite:///ambiguous.db")
    with pytest.raises(ValueError, match="exactly one"):
        persistence.database_url()


@pytest.mark.parametrize(
    "contents", ["", "secret\nprivate", "x" * 8193, " " * 9000, "sqlite:///forbidden.db", "\udcff"]
)
def test_candidate_invalid_secret_is_redacted(monkeypatch, tmp_path, contents):
    secret = tmp_path / "database_url"
    secret.write_bytes(contents.encode("utf-8", errors="surrogateescape"))
    monkeypatch.setenv("CAREERPILOT_DATABASE_URL_FILE", str(secret))
    monkeypatch.setenv("CAREERPILOT_DEPLOYMENT_MODE", "candidate")
    with pytest.raises(ValueError) as error:
        persistence.database_url()
    assert str(error.value) == "Database secret file is unavailable or invalid"


def test_candidate_requires_file_and_missing_file_is_redacted(monkeypatch, tmp_path):
    monkeypatch.setenv("CAREERPILOT_DEPLOYMENT_MODE", "candidate")
    with pytest.raises(ValueError, match="requires a secret file"):
        persistence.database_url()
    monkeypatch.setenv("CAREERPILOT_DATABASE_URL_FILE", str(tmp_path / "missing-private-name"))
    with pytest.raises(ValueError) as error:
        persistence.database_url()
    assert "missing-private-name" not in str(error.value)


def test_connection_pool_checks_stale_connections_and_bounds_candidate_queries(monkeypatch):
    factory = Mock()
    monkeypatch.setattr(persistence, "create_engine", factory)
    monkeypatch.setenv("CAREERPILOT_DEPLOYMENT_MODE", "candidate")
    persistence.make_engine("postgresql+psycopg://candidate@postgres/disposable")
    options = factory.call_args.kwargs
    assert options["pool_pre_ping"] is True
    assert options["pool_timeout"] == 3 and options["max_overflow"] == 5
    assert options["connect_args"]["connect_timeout"] == 3
    assert options["connect_args"]["options"] == "-c statement_timeout=5000"


@pytest.fixture
def worker_process(monkeypatch, tmp_path):
    engine = Mock()
    heartbeat = tmp_path / "heartbeat"
    heartbeat.touch()
    monkeypatch.setattr(worker, "HEARTBEAT", heartbeat)
    monkeypatch.setattr(worker, "make_engine", lambda _: engine)
    monkeypatch.setattr(worker.signal, "signal", Mock())
    monkeypatch.setattr(sys, "argv", ["worker", "--once"])
    return engine, heartbeat


@pytest.mark.parametrize("error_class", [OperationalError, InterfaceError])
def test_database_failure_marks_worker_unhealthy_without_private_logs(
    monkeypatch,
    worker_process,
    caplog,
    error_class,
):
    engine, heartbeat = worker_process
    monkeypatch.setattr(
        worker,
        "cycle",
        Mock(
            side_effect=error_class(
                "private SQL", {"token": "private"}, Exception("private database credentials")
            )
        ),
    )
    assert worker.main() == 1
    assert not heartbeat.exists()
    engine.dispose.assert_called_once()
    assert "worker_database_unavailable" in caplog.text
    assert "private" not in caplog.text


def test_worker_recovers_without_restarting_process(monkeypatch, worker_process, caplog):
    engine, heartbeat = worker_process
    callbacks = {}
    monkeypatch.setattr(
        worker.signal, "signal", lambda number, callback: callbacks.update({number: callback})
    )
    monkeypatch.setattr(sys, "argv", ["worker"])
    attempts = 0

    def cycle(*_):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OperationalError("private", {}, Exception("private"))
        return 1.0

    def sleep(_):
        if attempts == 1:
            assert not heartbeat.exists()
        else:
            assert heartbeat.exists()
            callbacks[worker.signal.SIGTERM]()

    monkeypatch.setattr(worker, "cycle", cycle)
    monkeypatch.setattr(worker.time, "sleep", sleep)
    assert worker.main() == 0 and attempts == 2
    engine.dispose.assert_called_once()
    assert "private" not in caplog.text


def test_unexpected_worker_failure_exits_unhealthy(monkeypatch, worker_process, caplog):
    _, heartbeat = worker_process
    monkeypatch.setattr(worker, "cycle", Mock(side_effect=ValueError("private")))
    assert worker.main() == 1 and not heartbeat.exists()
    assert "worker_iteration_failed" in caplog.text and "private" not in caplog.text


@pytest.fixture
def candidate_config(tmp_path):
    project = PREFIX + "a" * 12
    services = {}
    for name in ["migrate", "api-a", "api-b", "worker-a", "worker-b", "frontdoor", "restored-api"]:
        services[name] = {
            "user": "10001:10001",
            "read_only": True,
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges:true"],
            "networks": {"default": {}},
        }
    for name in ["postgres", "postgres-restore"]:
        services[name] = {"networks": {"default": {}}}
    for name in ["frontdoor", "restored-api"]:
        services[name]["networks"]["edge"] = {}
    config = {
        "name": project,
        "networks": {
            "default": {"internal": True, "name": project + "_default"},
            "edge": {"name": project + "_edge"},
        },
        "volumes": {"candidate_db": {"name": project + "_candidate_db"}},
        "secrets": {"database_url": {"file": str(tmp_path / "database_url")}},
        "services": services,
    }
    validate_config(config, project, tmp_path)
    return config, project, tmp_path


@pytest.mark.parametrize(
    "mutation",
    [
        lambda c: c["services"]["api-a"].update(user="0:0"),
        lambda c: c["services"]["api-a"].update(read_only=False),
        lambda c: c["services"]["api-a"].update(cap_add=["SYS_ADMIN"]),
        lambda c: c["services"]["api-a"].update(network_mode="host"),
        lambda c: c["services"]["api-a"].update(ports=[{"host_ip": "0.0.0.0"}]),
        lambda c: c["services"]["api-a"].update(volumes=[{"type": "bind", "source": "."}]),
        lambda c: c["services"]["api-a"].update(
            environment={"CAREERPILOT_DATABASE_URL": "private"}
        ),
        lambda c: c["services"]["api-a"].update(environment={"DEEPSEEK_API_KEY": "private"}),
        lambda c: c["networks"]["default"].update(internal=False),
        lambda c: c["volumes"]["candidate_db"].update(external=True),
        lambda c: c["volumes"]["candidate_db"].update(name="careerpilot-data"),
        lambda c: c["secrets"]["database_url"].update(file="/business/.env"),
    ],
)
def test_candidate_preflight_rejects_unsafe_configuration(candidate_config, mutation):
    config, project, scratch = candidate_config
    mutation(config)
    with pytest.raises(RuntimeError):
        validate_config(config, project, scratch)


def test_rejected_configuration_never_triggers_volume_cleanup(candidate_config):
    _, project, scratch = candidate_config
    drill = Drill(project, scratch)
    drill.compose = Mock(return_value='{"name":"business"}')
    with pytest.raises(RuntimeError):
        drill.freeze_config()
    drill.compose.reset_mock()
    drill.cleanup()
    drill.compose.assert_not_called()


def test_teardown_uses_frozen_resource_names(candidate_config):
    config, project, scratch = candidate_config
    drill = Drill(project, scratch)
    drill.compose = Mock(return_value=json.dumps(config))
    drill.freeze_config()
    frozen = Path(drill.base[drill.base.index("-f") + 1])
    assert frozen.parent == scratch
    config["volumes"]["candidate_db"]["name"] = "business-data"
    assert json.loads(frozen.read_text())["volumes"]["candidate_db"]["name"].startswith(project)
    drill.compose.reset_mock()
    drill.run = Mock(return_value="")
    drill.cleanup()
    assert drill.compose.call_args.args[0] == "down"
