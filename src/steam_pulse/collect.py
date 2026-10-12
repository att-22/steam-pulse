"""Collect one snapshot of what is being played on Steam and store it untouched.

This is the only piece that cannot be re-run later: an hour that is not collected is a
hole in the history forever. So it does as little as possible (ask, wrap, write) and
leaves every interpretation of the data to the models built on top.

Each response is stored as one JSON file, wrapped in an envelope that records when and
where it was collected:

    raw/<source>/date=YYYY-MM-DD/<YYYYMMDDTHHMMSSZ>.json     accepted responses
    raw/_rejected/<source>/date=YYYY-MM-DD/<...>.json        anything unexpected

Nothing is thrown away: a response that does not look right is kept under ``_rejected``
so that outages and format changes can be studied afterwards.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

USER_AGENT = "steam-pulse/0.1 (+https://github.com/att-22/steam-pulse)"
TIMEOUT_SECONDS = 30
ATTEMPTS = 3
STAMP = "%Y%m%dT%H%M%SZ"


@dataclass(frozen=True)
class Response:
    status: int
    text: str


@dataclass(frozen=True)
class Source:
    name: str
    url: str
    # Returns the moment the data refers to, or None if the payload is not usable.
    # A source with no timestamp of its own returns the collection time.
    data_time: Callable[[Any, datetime], datetime | None]
    # A failing required source makes the whole run fail; an optional one only warns.
    required: bool


Fetch = Callable[[str], Response]


def _concurrent_players_time(payload: Any, collected_at: datetime) -> datetime | None:
    """Steam says when the ranking was computed: that, not our clock, dates the data.

    Responses are served from a cache and may be older than a previous one, so two
    collections can return the very same snapshot. Naming the file after
    ``last_update`` makes that harmless.
    """
    try:
        response = payload["response"]
        last_update = response["last_update"]
        ranks = response["ranks"]
    except (KeyError, TypeError):
        return None
    if isinstance(last_update, bool) or not isinstance(last_update, int) or last_update <= 0:
        return None
    if not isinstance(ranks, list) or not ranks:
        return None
    return datetime.fromtimestamp(last_update, UTC)


def _steam_users_time(payload: Any, collected_at: datetime) -> datetime | None:
    if not isinstance(payload, dict):
        return None
    if "users_online" not in payload or "users_ingame" not in payload:
        return None
    return collected_at


SOURCES = (
    Source(
        name="concurrent_players",
        url="https://api.steampowered.com/ISteamChartsService/GetGamesByConcurrentPlayers/v1/",
        data_time=_concurrent_players_time,
        required=True,
    ),
    Source(
        name="steam_users",
        url="https://www.valvesoftware.com/about/stats",
        data_time=_steam_users_time,
        required=False,
    ),
)


def fetch(url: str) -> Response:
    """One HTTP GET. An HTTP error status is a response too, not an exception."""
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as reply:
            return Response(reply.status, reply.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as error:
        return Response(error.code, error.read().decode("utf-8", errors="replace"))


def fetch_with_retries(
    fetch: Fetch,
    url: str,
    attempts: int = ATTEMPTS,
    sleep: Callable[[float], None] = time.sleep,
) -> Response | Exception:
    """Retries what is worth retrying: network errors, rate limiting and server errors.

    Returns the last response, or the last exception if the server was never reached.
    """
    result: Response | Exception = RuntimeError("no attempt was made")
    for attempt in range(1, attempts + 1):
        try:
            result = fetch(url)
        except (OSError, TimeoutError) as error:
            result = error
        else:
            if result.status != 429 and result.status < 500:
                return result
        if attempt < attempts:
            sleep(2.0**attempt)
    return result


def _write(path: Path, envelope: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(envelope, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _path(root: Path, source: Source, moment: datetime, rejected: bool = False) -> Path:
    base = root / "raw" / "_rejected" if rejected else root / "raw"
    return base / source.name / f"date={moment:%Y-%m-%d}" / f"{moment.strftime(STAMP)}.json"


def collect_source(
    source: Source,
    root: Path,
    fetch: Fetch = fetch,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    """Collects one source. Returns ``stored``, ``duplicate`` or ``rejected``."""
    result = fetch_with_retries(fetch, source.url, sleep=sleep)
    collected_at = now().replace(microsecond=0)
    envelope: dict[str, Any] = {
        "source": source.name,
        "url": source.url,
        "collected_at": collected_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    if isinstance(result, Exception):
        envelope["error"] = f"{type(result).__name__}: {result}"
        _write(_path(root, source, collected_at, rejected=True), envelope)
        return "rejected"

    envelope["http_status"] = result.status
    try:
        payload = json.loads(result.text)
    except ValueError:
        payload = None
        envelope["text"] = result.text
    else:
        envelope["payload"] = payload

    data_time = source.data_time(payload, collected_at) if result.status == 200 else None
    if data_time is None:
        _write(_path(root, source, collected_at, rejected=True), envelope)
        return "rejected"

    path = _path(root, source, data_time)
    if path.exists():
        # Same snapshot as an earlier collection: the first one is the one that stays.
        return "duplicate"
    _write(path, envelope)
    return "stored"


def collect(root: Path, **kwargs: Any) -> bool:
    """Collects every source. Returns False if a required one could not be stored."""
    healthy = True
    for source in SOURCES:
        outcome = collect_source(source, root, **kwargs)
        print(f"{source.name}: {outcome}")
        if outcome == "rejected" and source.required:
            healthy = False
    return healthy


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data"),
        help="folder where raw/ is written (default: data)",
    )
    arguments = parser.parse_args()
    sys.exit(0 if collect(arguments.output) else 1)


if __name__ == "__main__":
    main()
