"""Single-shot migration and non-sensitive seed initialization."""

from alembic import command
from alembic.config import Config

from careerpilot.persistence import database_url, make_engine
from careerpilot.seed import seed


def main():
    command.upgrade(Config("alembic.ini"), "head")
    engine = make_engine(database_url())
    try:
        if engine.dialect.name == "sqlite":
            from sqlalchemy import text

            with engine.connect() as connection:
                connection.execute(text("PRAGMA journal_mode=WAL"))
        seed(engine)
        from careerpilot.workflows import saver

        with saver(engine):
            pass
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
