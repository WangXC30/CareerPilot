"""Single-shot migration and non-sensitive seed initialization."""

from alembic import command
from alembic.config import Config

from careerpilot.persistence import database_url, make_engine
from careerpilot.seed import seed


def main():
    command.upgrade(Config("alembic.ini"), "head")
    engine = make_engine(database_url())
    try:
        seed(engine)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
