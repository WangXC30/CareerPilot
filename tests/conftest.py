import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from careerpilot.api import create_app
from careerpilot.cli import provision
from careerpilot.persistence import make_engine
from careerpilot.seed import seed


def pytest_make_parametrize_id(config, val, argname):
    # Upload boundary cases must not turn megabytes of content into a test ID
    # and overflow PYTEST_CURRENT_TEST on Windows (or a child process's env).
    if isinstance(val, (bytes, str)) and len(val) > 100:
        import hashlib

        content = val.encode() if isinstance(val, str) else val
        return f"{argname}-{len(content)}bytes-{hashlib.sha256(content).hexdigest()[:8]}"


@pytest.fixture
def system(tmp_path):
    url = f"sqlite:///{(tmp_path / 'test.db').as_posix()}"
    config = Config("alembic.ini")
    config.attributes["database_url"] = url
    command.upgrade(config, "head")
    engine = make_engine(url)
    seed(engine)
    alice_id, alice = provision(engine, "Alice")
    bob_id, bob = provision(engine, "Bob")
    with TestClient(create_app(url, local_only=False)) as client:
        yield (
            client,
            engine,
            {"Authorization": f"Bearer {alice}"},
            {"Authorization": f"Bearer {bob}"},
            config,
            (alice_id, bob_id),
        )
    engine.dispose()
