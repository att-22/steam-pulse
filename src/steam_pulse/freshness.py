"""Fail when the latest stored ranking is older than it should be.

The collection can stop without anything failing: GitHub may skip scheduled runs for
hours, and disables the schedule altogether after 60 days without activity in the
repository. A run that never starts reports no error. This looks at the result instead
of the runs: how old is the newest ranking on the ``data`` branch?

The age is measured from the moment Steam computed that ranking (the file name), not
from when it was collected, so a collector that keeps storing a stale cached answer is
caught as well. Only accepted rankings count: rejected responses are not data.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from steam_pulse.collect import STAMP

RANKING = "concurrent_players"
# Scheduled runs have been seen up to 7.4 hours apart. Twelve hours without a ranking
# is no longer GitHub being slow: the collection has stopped.
MAX_AGE_HOURS = 12


def latest_ranking(root: Path) -> datetime | None:
    """The moment of the newest accepted ranking under ``root/raw``, or None if there is none."""
    latest = None
    for path in (root / "raw" / RANKING).glob("date=*/*.json"):
        try:
            moment = datetime.strptime(path.stem, STAMP).replace(tzinfo=UTC)
        except ValueError:
            continue  # not a snapshot file
        if latest is None or moment > latest:
            latest = moment
    return latest


def check(
    root: Path,
    max_age: timedelta,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> bool:
    """Prints the age of the newest ranking. True if it is recent enough."""
    latest = latest_ranking(root)
    if latest is None:
        print(f"no ranking found under {root / 'raw' / RANKING}")
        return False
    age = now() - latest
    hours = age.total_seconds() / 3600
    fresh = age <= max_age
    verdict = "ok" if fresh else f"STALE: the limit is {max_age.total_seconds() / 3600:g} h"
    print(f"newest ranking: {latest:%Y-%m-%d %H:%M} UTC, {hours:.1f} hours ago ({verdict})")
    return fresh


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("data"),
        help="folder that contains raw/ (default: data)",
    )
    parser.add_argument(
        "--max-age-hours",
        type=float,
        default=MAX_AGE_HOURS,
        help=f"oldest the newest ranking may be (default: {MAX_AGE_HOURS})",
    )
    arguments = parser.parse_args()
    sys.exit(0 if check(arguments.input, timedelta(hours=arguments.max_age_hours)) else 1)


if __name__ == "__main__":
    main()
