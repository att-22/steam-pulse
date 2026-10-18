import re
import stat
from pathlib import Path

import duckdb
import pytest

from steam_pulse import dashboard
from steam_pulse.publish import TABLES, export


@pytest.fixture
def warehouse(tmp_path):
    """A warehouse named like the real one, with raw data, a table and a view on it."""
    path = tmp_path / "steam_pulse.duckdb"
    with duckdb.connect(str(path)) as db:
        db.execute(
            """
            create schema raw;
            create schema marts;
            create schema intermediate;
            create table raw.snapshots as select 'secret' as payload;
            create table marts.dim_games as
                select * from (values (730, 'CS2'), (570, 'Dota 2')) t(appid, name);
            create view intermediate.hours as select appid from marts.dim_games where appid > 600;
            """
        )
    return path


def test_only_the_tables_asked_for_are_copied(warehouse, tmp_path):
    target = tmp_path / "dashboard.duckdb"

    rows = export(warehouse, target, ["marts.dim_games", "intermediate.hours"])

    assert rows == {"marts.dim_games": 2, "intermediate.hours": 1}
    with duckdb.connect(str(target), read_only=True) as db:
        tables = db.execute(
            "select table_schema || '.' || table_name, table_type from information_schema.tables"
        ).fetchall()
    # The view arrives as a table, and the raw data does not arrive at all
    assert sorted(tables) == [
        ("intermediate.hours", "BASE TABLE"),
        ("marts.dim_games", "BASE TABLE"),
    ]


def test_the_file_stands_alone(warehouse, tmp_path):
    target = tmp_path / "dashboard.duckdb"
    export(warehouse, target, ["intermediate.hours"])
    warehouse.unlink()

    with duckdb.connect(str(target), read_only=True) as db:
        assert db.execute("select appid from intermediate.hours").fetchall() == [(730,)]


def test_an_existing_file_is_replaced_whole(warehouse, tmp_path):
    target = tmp_path / "dashboard.duckdb"
    target.write_text("an older, broken file")

    export(warehouse, target, ["marts.dim_games"])

    assert not target.with_name("dashboard.duckdb.tmp").exists()
    with duckdb.connect(str(target), read_only=True) as db:
        assert db.execute("select count(*) from marts.dim_games").fetchone() == (2,)


def test_a_failed_export_leaves_the_previous_file(warehouse, tmp_path):
    target = tmp_path / "dashboard.duckdb"
    export(warehouse, target, ["marts.dim_games"])

    # Fails after copying a table that the previous file did not have
    with pytest.raises(duckdb.Error):
        export(warehouse, target, ["intermediate.hours", "marts.missing"])

    with duckdb.connect(str(target), read_only=True) as db:
        assert db.execute("select count(*) from marts.dim_games").fetchone() == (2,)
        assert db.execute(
            "select count(*) from information_schema.tables where table_name = 'hours'"
        ).fetchone() == (0,)


def test_a_leftover_from_an_interrupted_export_is_cleared(warehouse, tmp_path):
    target = tmp_path / "dashboard.duckdb"
    with duckdb.connect(str(tmp_path / "dashboard.duckdb.tmp")) as db:
        db.execute("create schema marts; create table marts.dim_games (appid integer)")

    assert export(warehouse, target, ["marts.dim_games"]) == {"marts.dim_games": 2}


def test_the_warehouse_is_only_read(warehouse, tmp_path):
    # A warehouse the process may not write to: attaching it for writing would fail.
    warehouse.chmod(stat.S_IREAD)
    try:
        assert export(warehouse, tmp_path / "dashboard.duckdb", ["marts.dim_games"])
    finally:
        warehouse.chmod(stat.S_IREAD | stat.S_IWRITE)


def test_the_output_cannot_take_the_warehouse_name(warehouse, tmp_path):
    with pytest.raises(ValueError):
        export(warehouse, tmp_path / "other" / "steam_pulse.duckdb", ["marts.dim_games"])


def test_every_table_the_dashboard_reads_is_published():
    source = Path(dashboard.__file__).read_text(encoding="utf-8")
    read = set(re.findall(r"\b(?:marts|intermediate)\.\w+", source))

    assert read
    assert read <= set(TABLES)


def test_download_writes_the_whole_file(tmp_path):
    source = tmp_path / "published.duckdb"
    source.write_bytes(b"x" * 100_000)
    target = tmp_path / "copy.duckdb"

    assert dashboard.download(source.as_uri(), target) == target
    assert target.read_bytes() == source.read_bytes()
    assert not target.with_name("copy.duckdb.tmp").exists()
