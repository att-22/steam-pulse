"""Building in two steps must give exactly what building at once gives.

The incremental models only rebuild recent hours, so a mistake in them does not fail
anything: it leaves wrong rows behind, quietly. This builds two warehouses from the same
snapshots and compares every table the models make:

    in steps   load the snapshots before --split, build; load the rest, build again
    at once    load everything, build with --full-refresh

Choose --split inside an hour that has two snapshots, so that the second step replaces
the snapshot chosen for an hour already built.

    uv run python scripts/check_incremental.py --input tests/sample --split 20261013T173000Z
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
SCHEMAS = ("intermediate", "marts")


def run(*command: str, database: Path, cwd: Path = ROOT) -> None:
    env = {**os.environ, "STEAM_PULSE_DATABASE": str(database)}
    done = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True)
    if done.returncode:
        print(done.stdout, done.stderr)
        done.check_returncode()


def load_and_build(raw: Path, database: Path, *dbt_flags: str) -> None:
    run("uv", "run", "--no-sync", "load", "--input", str(raw), "--database", str(database),
        database=database)  # fmt: skip
    run("uv", "run", "--no-sync", "dbt", "build", "--quiet", *dbt_flags,
        database=database, cwd=ROOT / "transform")  # fmt: skip


def snapshots_before(source: Path, split: str, target: Path) -> int:
    """Copies into ``target`` the snapshot files named before ``split``. Returns how many."""
    copied = 0
    for path in (source / "raw").rglob("*.json"):
        if path.stem < split or not path.stem[:1].isdigit():
            # Game details are named by app id, not by time: they all go in the first step.
            destination = target / path.relative_to(source)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
            copied += 1
    return copied


def differences(first: Path, second: Path) -> dict[str, int]:
    """Rows found in one warehouse and not in the other, per table. Empty when identical."""
    found = {}
    with duckdb.connect() as db:
        db.execute(f"attach '{first}' as a (read_only)")
        db.execute(f"attach '{second}' as b (read_only)")
        tables = db.execute(
            "select table_schema || '.' || table_name from information_schema.tables "
            "where table_catalog = 'a' and table_schema in ? and table_type = 'BASE TABLE' "
            "order by 1",
            [list(SCHEMAS)],
        ).fetchall()
        for (table,) in tables:
            missing = db.execute(
                f"select count(*) from (select * from a.{table} except all select * from b.{table})"
            ).fetchone()[0]
            extra = db.execute(
                f"select count(*) from (select * from b.{table} except all select * from a.{table})"
            ).fetchone()[0]
            rows = db.execute(f"select count(*) from a.{table}").fetchone()[0]
            print(f"{table}: {rows} rows, {missing + extra} different")
            if missing or extra:
                found[table] = missing + extra
    return found


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--input", type=Path, required=True, help="folder that contains raw/")
    parser.add_argument("--split", required=True, help="first file name of the second step")
    arguments = parser.parse_args()
    source = arguments.input.resolve()

    with tempfile.TemporaryDirectory() as folder:
        work = Path(folder)
        first_step = work / "first_step"
        copied = snapshots_before(source, arguments.split, first_step)
        total = sum(1 for _ in (source / "raw").rglob("*.json"))
        print(f"first step: {copied} of {total} files")

        in_steps, at_once = work / "in_steps.duckdb", work / "at_once.duckdb"
        load_and_build(first_step, in_steps)
        load_and_build(source, in_steps)
        load_and_build(source, at_once, "--full-refresh")

        found = differences(in_steps, at_once)
    if found:
        print(f"the incremental build differs from the full one: {found}")
        sys.exit(1)
    print("the incremental build matches the full one")


if __name__ == "__main__":
    main()
