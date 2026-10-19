"""The questions the dashboard answers, as queries on the tables built by the models.

Kept apart from the page itself so that every figure on it can be tested without a
browser. Each function takes an open connection and returns a pandas DataFrame (or a
small dict); the page only lays them out.

Two rules from the models hold here too: hours are UTC, and a game that was not in the
ranking has no figure for that hour, never a zero.
"""

from __future__ import annotations

import os
import shutil
import urllib.request
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

# The tables the dashboard reads, rebuilt after every collection by the Build workflow.
PUBLISHED = "https://raw.githubusercontent.com/att-22/steam-pulse/warehouse/dashboard.duckdb"

# Below this many collected hours, an average for one hour of the day is not shown:
# with two or three samples it says more about those days than about that hour.
MIN_SAMPLES_PER_HOUR = 3


def download(url: str, target: Path) -> Path:
    """Saves ``url`` at ``target``, written aside and renamed so it is never half there."""
    temporary = target.with_name(target.name + ".tmp")
    with urllib.request.urlopen(url, timeout=60) as response, temporary.open("wb") as file:
        shutil.copyfileobj(response, file)
    os.replace(temporary, target)
    return target


def connect(database: str | Path) -> duckdb.DuckDBPyConnection:
    """A read-only connection whose timestamps come out in UTC, wherever it runs."""
    connection = duckdb.connect(str(database), read_only=True)
    connection.execute("set TimeZone = 'UTC'")
    return connection


def session(connection: duckdb.DuckDBPyConnection) -> duckdb.DuckDBPyConnection:
    """A cursor of ``connection`` for one thread, also in UTC.

    The page keeps one connection for every visitor, and Streamlit runs each visit and
    each click in a thread of its own. A DuckDB connection must not run queries from
    several threads at once: two of them can swap results, and a page got the columns
    of another query. A cursor is a connection of its own to the same database, cheap to
    open. It does not inherit the time zone, so it is set again.
    """
    cursor = connection.cursor()
    cursor.execute("set TimeZone = 'UTC'")
    return cursor


def status(connection: duckdb.DuckDBPyConnection, now: datetime) -> dict[str, Any] | None:
    """The newest hour with data, its totals, and how much of the last day was collected."""
    row = connection.execute(
        """
        select
            hour_at, snapshot_at, users_online, users_in_game, ranked_players,
            ranked_share_of_in_game, top_appid, top_game_players,
            (select count(*) from intermediate.int_hourly_snapshots
             where hour_at > $now - interval 24 hour) as hours_collected_24h
        from marts.fct_platform_hourly
        order by hour_at desc
        limit 1
        """,
        {"now": now},
    ).fetchone()
    if row is None:
        return None
    names = [
        "hour_at", "snapshot_at", "users_online", "users_in_game", "ranked_players",
        "ranked_share_of_in_game", "top_appid", "top_game_players", "hours_collected_24h",
    ]  # fmt: skip
    found = dict(zip(names, row, strict=True))
    found["age_hours"] = (now - found["snapshot_at"]).total_seconds() / 3600
    return found


def hours(connection: duckdb.DuckDBPyConnection) -> list[datetime]:
    """Every collected hour, newest first: the choices for the hour picker."""
    rows = connection.execute(
        "select hour_at from intermediate.int_hourly_snapshots order by hour_at desc"
    ).fetchall()
    return [row[0] for row in rows]


def previous_hour(connection: duckdb.DuckDBPyConnection, hour: datetime) -> datetime | None:
    """The hour collected before ``hour``, if there is one."""
    return connection.execute(
        "select max(hour_at) from intermediate.int_hourly_snapshots where hour_at < $hour",
        {"hour": hour},
    ).fetchone()[0]


def totals_at(connection: duckdb.DuckDBPyConnection, hour: datetime) -> dict[str, Any] | None:
    """People on Steam and in the ranking at ``hour``."""
    row = connection.execute(
        """
        select users_online, users_in_game, ranked_players, ranked_share_of_in_game
        from marts.fct_platform_hourly
        where hour_at = $hour
        """,
        {"hour": hour},
    ).fetchone()
    if row is None:
        return None
    names = ["users_online", "users_in_game", "ranked_players", "ranked_share_of_in_game"]
    return dict(zip(names, row, strict=True))


def ranking_at(
    connection: duckdb.DuckDBPyConnection, hour: datetime, limit: int = 25
) -> pd.DataFrame:
    """The most played games at ``hour``, with their names.

    The change is measured against the previous hour that was collected, which is not
    always the hour before: the page says how far apart the two are. It is null for a
    game that was not in the ranking then.
    """
    return connection.execute(
        """
        select
            now.rank,
            games.display_name as game,
            now.concurrent_players as players,
            now.concurrent_players - before.concurrent_players as players_change,
            before.rank - now.rank as places_gained,
            games.is_free,
            coalesce(games.genres, []) as genres,
            now.appid
        from marts.fct_game_hourly as now
        inner join marts.dim_games as games
            on games.appid = now.appid
        left join marts.fct_game_hourly as before
            on before.appid = now.appid
            and before.hour_at = (
                select max(hour_at) from intermediate.int_hourly_snapshots
                where hour_at < $hour
            )
        where now.hour_at = $hour
        order by now.rank
        limit $limit
        """,
        {"hour": hour, "limit": limit},
    ).df()


def games(connection: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Every game ever ranked, most played first: the choices for the game picker."""
    return connection.execute(
        "select appid, display_name as game from marts.dim_games order by peak_players desc"
    ).df()


def _in_period(column: str) -> str:
    """SQL condition: ``column`` is within the period given by $since and $before.

    Either can be null, meaning no limit on that side. The bounds are worked out in
    Python by ``_period``: casting a date parameter inside the query hit an internal
    error in DuckDB 1.5.6 when the query read a view.
    """
    return f"($since is null or {column} >= $since) and ($before is null or {column} < $before)"


def _period(first: date | None, last: date | None) -> dict[str, datetime | None]:
    """Whole UTC days from ``first`` to ``last``, both included, as [since, before)."""
    return {
        "since": None if first is None else datetime.combine(first, time(), UTC),
        "before": None if last is None else datetime.combine(last + timedelta(days=1), time(), UTC),
    }


def players_over_time(
    connection: duckdb.DuckDBPyConnection,
    appids: list[int],
    first: date | None = None,
    last: date | None = None,
) -> pd.DataFrame:
    """Players per collected hour for these games, between two days if given.

    One row per collected hour and game. When the game was not in the ranking in an hour
    that was collected, ``players`` is null: the chart breaks the line there instead of
    drawing it down to zero. Hours that were not collected at all have no row.
    """
    return connection.execute(
        f"""
        select hours.hour_at, games.display_name as game, hourly.concurrent_players as players
        from intermediate.int_hourly_snapshots as hours
        cross join marts.dim_games as games
        left join marts.fct_game_hourly as hourly
            on hourly.hour_at = hours.hour_at
            and hourly.appid = games.appid
        where games.appid in (select unnest($appids))
          and {_in_period("hours.hour_at")}
        order by games.peak_players desc, hours.hour_at
        """,
        {"appids": appids, **_period(first, last)},
    ).df()


def platform_over_time(
    connection: duckdb.DuckDBPyConnection, first: date | None = None, last: date | None = None
) -> pd.DataFrame:
    """People online and in game on all of Steam, per collected hour, long format."""
    return connection.execute(
        f"""
        unpivot (
            select hour_at, users_online as "Online", users_in_game as "In game"
            from marts.fct_platform_hourly
            where {_in_period("hour_at")}
        )
        on "Online", "In game"
        into name measure value people
        order by hour_at, measure
        """,
        _period(first, last),
    ).df()


def hour_of_day(connection: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Average people in game by hour of the day (UTC), with how many hours it rests on."""
    return connection.execute(
        """
        select
            extract(hour from hour_at)::integer as hour_of_day,
            avg(users_in_game) as avg_in_game,
            count(*) as samples,
            count(*) >= $minimum as enough
        from marts.fct_platform_hourly
        where users_in_game is not null
        group by 1
        order by 1
        """,
        {"minimum": MIN_SAMPLES_PER_HOUR},
    ).df()


def ranking_changes(
    connection: duckdb.DuckDBPyConnection, until: datetime | None = None, limit: int = 20
) -> pd.DataFrame:
    """The latest games to enter or leave the ranking, up to ``until`` if given."""
    return connection.execute(
        """
        select
            changes.hour_at,
            changes.change,
            games.display_name as game,
            changes.rank_when_seen as rank,
            changes.players_when_seen as players,
            changes.hours_since_previous
        from marts.fct_ranking_changes as changes
        inner join marts.dim_games as games
            on games.appid = changes.appid
        where $until is null or changes.hour_at <= $until
        order by changes.hour_at desc, changes.change, changes.rank_when_seen
        limit $limit
        """,
        {"until": until, "limit": limit},
    ).df()


def game_by_day(connection: duckdb.DuckDBPyConnection, appid: int) -> pd.DataFrame:
    """One row per UTC day in which the game was in the ranking, newest first."""
    return connection.execute(
        """
        select
            day,
            peak_players,
            peak_hour_at,
            round(avg_players_while_ranked)::bigint as avg_players_while_ranked,
            best_rank,
            hours_in_ranking,
            hours_collected
        from marts.fct_game_daily
        where appid = $appid
        order by day desc
        """,
        {"appid": appid},
    ).df()
