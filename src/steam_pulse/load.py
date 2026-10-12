"""Load the raw snapshots into the warehouse, one row per file, still untouched.

The warehouse is disposable: everything in it can be rebuilt from the snapshot files, so
this step only copies. Each envelope becomes one row of ``raw.snapshots`` with its
payload kept as JSON; turning that into players per game is the job of the models.

A file is identified by its source, its name and whether it was rejected, never by where
it sits on disk. Loading again adds only the files that are not in the warehouse yet, so
the load can run after every collection, be interrupted or be repeated without
duplicating anything.

The database is a local DuckDB file or a MotherDuck database (``md:steam_pulse``, with
the token in the ``MOTHERDUCK_TOKEN`` environment variable): the code is the same.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import duckdb

SCHEMA = "create schema if not exists raw"
TABLE = """
create table if not exists raw.snapshots (
    source       varchar not null,
    snapshot_id  varchar not null,       -- the file name: when the data is from, in UTC
    rejected     boolean not null,       -- kept under raw/_rejected/ by the collector
    collected_at timestamptz not null,
    http_status  integer,                -- null when the server was never reached
    url          varchar,
    payload      json,                   -- the response, when it was JSON
    text         varchar,                -- the response, when it was not
    error        varchar,                -- why there was no response at all
    loaded_at    timestamptz not null,
    primary key (source, snapshot_id, rejected)
)
"""
INSERT = """
insert into raw.snapshots
select
    source,
    parse_filename(filename, true),
    ?,
    collected_at,
    http_status,
    url,
    payload,
    text,
    error,
    now()
from read_json(
    ?,
    filename = true,
    columns = {
        source: 'varchar',
        url: 'varchar',
        collected_at: 'timestamptz',
        http_status: 'integer',
        payload: 'json',
        text: 'varchar',
        error: 'varchar'
    }
)
"""

Key = tuple[str, str, bool]


def snapshot_files(root: Path) -> dict[Key, Path]:
    """Every snapshot file under ``root/raw``, by (source, snapshot_id, rejected)."""
    raw = root / "raw"
    found: dict[Key, Path] = {}
    for rejected, pattern in ((False, "*/date=*/*.json"), (True, "_rejected/*/date=*/*.json")):
        for path in sorted(raw.glob(pattern)):
            found[(path.parts[-3], path.stem, rejected)] = path
    return found


def load(root: Path, connection: duckdb.DuckDBPyConnection) -> tuple[int, int]:
    """Loads the files that are not in the warehouse yet. Returns (new, already there)."""
    connection.execute(SCHEMA)
    connection.execute(TABLE)
    files = snapshot_files(root)
    known = set(
        connection.execute("select source, snapshot_id, rejected from raw.snapshots").fetchall()
    )
    new = {key: path for key, path in files.items() if key not in known}

    # All or nothing: a load that fails halfway leaves the warehouse as it was.
    connection.execute("begin")
    try:
        for rejected in (False, True):
            paths = [path.as_posix() for key, path in new.items() if key[2] is rejected]
            if paths:
                connection.execute(INSERT, [rejected, paths])
        connection.execute("commit")
    except Exception:
        connection.execute("rollback")
        raise
    return len(new), len(files) - len(new)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("data"),
        help="folder that contains raw/ (default: data)",
    )
    parser.add_argument(
        "--database",
        default="steam_pulse.duckdb",
        help="DuckDB file or MotherDuck database, e.g. md:steam_pulse "
        "(default: steam_pulse.duckdb)",
    )
    arguments = parser.parse_args()
    with duckdb.connect(arguments.database) as connection:
        new, known = load(arguments.input, connection)
    print(f"snapshots: {new} loaded, {known} already in the warehouse")


if __name__ == "__main__":
    main()
