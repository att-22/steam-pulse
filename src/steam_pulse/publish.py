"""Copy the tables the dashboard reads into a small DuckDB file of their own.

The warehouse holds every raw response as JSON, and grows with each snapshot. The
dashboard only needs a few tables built by the models, so the published file carries
those and nothing else: it is small to download, and it holds no raw data.

The file is written next to its final place and renamed at the end, so a reader never
finds it half written.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import duckdb

# What the dashboard queries. A test checks that every table it names is here.
TABLES = [
    "intermediate.int_hourly_snapshots",
    "marts.dim_games",
    "marts.fct_game_hourly",
    "marts.fct_game_daily",
    "marts.fct_platform_hourly",
    "marts.fct_ranking_changes",
]


def export(warehouse: Path, target: Path, tables: list[str] = TABLES) -> dict[str, int]:
    """Copies ``tables`` from ``warehouse`` into a new file at ``target``. Returns their rows."""
    # The views dbt writes name their database after the file (steam_pulse.duckdb is
    # "steam_pulse"), so the warehouse is attached under that same name or they break.
    alias = warehouse.stem
    if target.stem == alias:
        raise ValueError(f"the output cannot share its name with the warehouse: {alias}")
    temporary = target.with_name(target.name + ".tmp")
    temporary.unlink(missing_ok=True)
    rows = {}
    with duckdb.connect(str(temporary)) as connection:
        # attach takes no parameters: the path goes in as a quoted string literal.
        path = str(warehouse).replace("'", "''")
        connection.execute(f"attach '{path}' as \"{alias}\" (read_only)")
        for table in tables:
            schema = table.split(".")[0]
            connection.execute(f"create schema if not exists {schema}")
            # A view in the warehouse becomes a table here: the file has to stand alone.
            connection.execute(f'create table {table} as select * from "{alias}".{table}')
            rows[table] = connection.execute(f"select count(*) from {table}").fetchone()[0]
        connection.execute(f'detach "{alias}"')
    os.replace(temporary, target)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--database",
        type=Path,
        default=Path("steam_pulse.duckdb"),
        help="the warehouse built by the models (default: steam_pulse.duckdb)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("dashboard.duckdb"),
        help="the file to write (default: dashboard.duckdb)",
    )
    arguments = parser.parse_args()
    for table, count in export(arguments.database, arguments.output).items():
        print(f"{table}: {count} rows")
    print(f"{arguments.output}: {arguments.output.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
