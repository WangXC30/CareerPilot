import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from careerpilot.api import create_app
from careerpilot.cli import provision
from careerpilot.persistence import make_engine


@pytest.fixture
def system(tmp_path):
    url = f"sqlite:///{(tmp_path / 'test.db').as_posix()}"
    config = Config("alembic.ini")
    config.attributes["database_url"] = url
    command.upgrade(config, "head")
    engine = make_engine(url)
    alice_id, alice = provision(engine, "Alice")
    bob_id, bob = provision(engine, "Bob")
    with TestClient(create_app(url)) as client:
        yield (
            client,
            engine,
            {"Authorization": f"Bearer {alice}"},
            {"Authorization": f"Bearer {bob}"},
            config,
            (alice_id, bob_id),
        )
    engine.dispose()
