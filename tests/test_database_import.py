import sqlite3

import pytest

from scripts.import_dev_database import import_database


def test_import_preserves_source_and_refuses_overwrite(tmp_path):
    source, target = tmp_path / "old.db", tmp_path / "new.db"
    connection = sqlite3.connect(source)
    connection.execute("CREATE TABLE example (value TEXT)")
    connection.execute("INSERT INTO example VALUES ('preserved')")
    connection.commit()
    connection.close()
    import_database(source, target)
    for path in (source, target):
        connection = sqlite3.connect(path)
        assert connection.execute("SELECT value FROM example").fetchone() == ("preserved",)
        connection.close()
    before = target.read_bytes()
    with pytest.raises(FileExistsError):
        import_database(source, target)
    assert target.read_bytes() == before


def test_missing_source_does_not_create_target(tmp_path):
    target = tmp_path / "new.db"
    with pytest.raises(FileNotFoundError):
        import_database(tmp_path / "missing.db", target)
    assert not target.exists()
