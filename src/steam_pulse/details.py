"""Collect the store details of every game seen in the ranking, once per game.

The ranking only carries numbers: app 730 has so many players. Its name, type, genres
and release date come from the store, one request per game. They hardly ever change, so
each game is asked for once and the answer is kept, in the same envelope as the
snapshots:

    raw/app_details/date=YYYY-MM-DD/<appid>.json              the details
    raw/_rejected/app_details/date=YYYY-MM-DD/<appid>.json    no details that day

The date is the day of the request. A game the store gives no details for (removed, or
not sold in the country asked for) is asked for again, but at most once a day.

The store publishes no limits, so this stays well below what it is said to tolerate: one
request at a time, a pause between them and a cap per run. It also stops at the first
response that is not an answer about a game: if the store is failing or refusing, more
requests will not help.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from steam_pulse.collect import Fetch, _write, fetch, fetch_with_retries

SOURCE = "app_details"
RANKING = "concurrent_players"
# English, so that release dates and genres come in one known format.
URL = "https://store.steampowered.com/api/appdetails?appids={appid}&cc=us&l=english"
PAUSE_SECONDS = 1.5
LIMIT = 100


def ranked_appids(root: Path) -> list[int]:
    """Every game in the stored rankings: latest ranking first, then by position in it.

    That is the order in which they are asked for, so that when there are more games
    than one run may ask about, the ones being played now go first.
    """
    found: dict[int, None] = {}
    for path in sorted((root / "raw" / RANKING).glob("date=*/*.json"), reverse=True):
        try:
            ranks = json.loads(path.read_text(encoding="utf-8"))["payload"]["response"]["ranks"]
            appids = [row["appid"] for row in ranks]
        except (ValueError, KeyError, TypeError):
            continue  # not this module's job: the collector accepted it, the models judge it
        for appid in appids:
            if isinstance(appid, int) and not isinstance(appid, bool):
                found.setdefault(appid)
    return list(found)


def pending(root: Path, today: datetime) -> list[int]:
    """The ranked games with no details stored and not already asked for today."""
    raw = root / "raw"
    stored = {path.stem for path in (raw / SOURCE).glob("date=*/*.json")}
    refused = {path.stem for path in _folder(raw / "_rejected", today).glob("*.json")}
    return [appid for appid in ranked_appids(root) if str(appid) not in stored | refused]


def _folder(base: Path, moment: datetime) -> Path:
    return base / SOURCE / f"date={moment:%Y-%m-%d}"


def collect_details(
    appid: int,
    root: Path,
    fetch: Fetch = fetch,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    """Asks for one game. Returns ``stored``, ``unavailable`` or ``rejected``.

    ``unavailable`` is the store saying it has nothing for that game; ``rejected`` is
    anything that is not an answer about the game at all.
    """
    url = URL.format(appid=appid)
    result = fetch_with_retries(fetch, url, sleep=sleep)
    collected_at = now().replace(microsecond=0)
    envelope: dict[str, Any] = {
        "source": SOURCE,
        "url": url,
        "collected_at": collected_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    refused = _folder(root / "raw" / "_rejected", collected_at) / f"{appid}.json"

    if isinstance(result, Exception):
        envelope["error"] = f"{type(result).__name__}: {result}"
        _write(refused, envelope)
        return "rejected"

    envelope["http_status"] = result.status
    try:
        payload = json.loads(result.text)
    except ValueError:
        payload = None
        envelope["text"] = result.text
    else:
        envelope["payload"] = payload

    # The answer is keyed by the game asked for: {"730": {"success": true, "data": {...}}}
    answer = payload.get(str(appid)) if isinstance(payload, dict) else None
    success = answer.get("success") if isinstance(answer, dict) else None
    if result.status == 200 and success is True and isinstance(answer.get("data"), dict):
        _write(_folder(root / "raw", collected_at) / f"{appid}.json", envelope)
        return "stored"

    _write(refused, envelope)
    return "unavailable" if result.status == 200 and success is False else "rejected"


def collect(
    root: Path,
    limit: int = LIMIT,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    sleep: Callable[[float], None] = time.sleep,
    **kwargs: Any,
) -> bool:
    """Asks for the pending games, up to ``limit``. Returns False if it had to stop."""
    waiting = pending(root, now())
    outcomes: Counter[str] = Counter()
    for position, appid in enumerate(waiting[:limit]):
        if position:
            sleep(PAUSE_SECONDS)
        outcome = collect_details(appid, root, now=now, sleep=sleep, **kwargs)
        outcomes[outcome] += 1
        if outcome == "rejected":
            break
    left = len(waiting) - outcomes.total()
    print(
        f"{SOURCE}: {outcomes['stored']} stored, {outcomes['unavailable']} unavailable, "
        f"{outcomes['rejected']} rejected, {left} left for later"
    )
    return outcomes["rejected"] == 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data"),
        help="folder that contains raw/ (default: data)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=LIMIT,
        help=f"most games to ask for in one run (default: {LIMIT})",
    )
    arguments = parser.parse_args()
    sys.exit(0 if collect(arguments.output, arguments.limit) else 1)


if __name__ == "__main__":
    main()
