from datetime import UTC, datetime

import duckdb
import pandas as pd
import pytest

from steam_pulse import dashboard

NOW = datetime(2026, 10, 18, 16, 0, tzinfo=UTC)


@pytest.fixture
def warehouse(tmp_path):
    """The tables the dashboard reads, with a few hand-written rows.

    Hours collected: 10:00, 11:00 and 14:00 (12:00 and 13:00 were missed).
    Games: 730 in every hour; 570 not ranked at 11:00; 440 entered at 14:00.
    """
    path = tmp_path / "warehouse.duckdb"
    with duckdb.connect(str(path)) as db:
        db.execute(
            """
            create schema intermediate;
            create schema marts;
            create table intermediate.int_hourly_snapshots as
                select unnest([
                    timestamptz '2026-10-18 10:00:00+00',
                    timestamptz '2026-10-18 11:00:00+00',
                    timestamptz '2026-10-18 14:00:00+00',
                    timestamptz '2026-10-17 09:00:00+00'
                ]) as hour_at;
            create table marts.dim_games (
                appid integer, display_name varchar, is_free boolean, genres varchar[],
                peak_players bigint
            );
            insert into marts.dim_games values
                (730, 'Counter-Strike 2', true, ['Action'], 1300),
                (570, 'Dota 2', true, ['Strategy'], 900),
                (440, 'App 440', null, null, 500);
            create table marts.fct_game_hourly (
                hour_at timestamptz, appid integer, rank integer, concurrent_players bigint
            );
            insert into marts.fct_game_hourly values
                ('2026-10-18 10:00:00+00', 730, 1, 1000),
                ('2026-10-18 10:00:00+00', 570, 2, 800),
                ('2026-10-18 11:00:00+00', 730, 1, 1100),
                ('2026-10-18 14:00:00+00', 730, 2, 1300),
                ('2026-10-18 14:00:00+00', 570, 3, 900),
                ('2026-10-18 14:00:00+00', 440, 1, 1500);
            create table marts.fct_platform_hourly (
                hour_at timestamptz, snapshot_at timestamptz, users_online bigint,
                users_in_game bigint, ranked_players bigint, ranked_share_of_in_game double,
                top_appid integer, top_game_players bigint
            );
            insert into marts.fct_platform_hourly values
                ('2026-10-18 10:00:00+00', '2026-10-18 10:55:00+00', 30, 10, 6, 0.6, 730, 1000),
                ('2026-10-18 11:00:00+00', '2026-10-18 11:50:00+00', 32, null, 7, null, 730, 1100),
                ('2026-10-18 14:00:00+00', '2026-10-18 14:45:00+00', 40, 14, 9, 0.64, 440, 1500),
                ('2026-10-17 09:00:00+00', '2026-10-17 09:30:00+00', 20, 11, 5, 0.45, 730, 900);
            create table marts.fct_ranking_changes (
                hour_at timestamptz, appid integer, change varchar, rank_when_seen integer,
                players_when_seen bigint, hours_since_previous integer
            );
            insert into marts.fct_ranking_changes values
                ('2026-10-18 11:00:00+00', 570, 'left', 2, 800, 1),
                ('2026-10-18 14:00:00+00', 440, 'entered', 1, 1500, 3),
                ('2026-10-18 14:00:00+00', 570, 'entered', 3, 900, 3);
            """
        )
    connection = dashboard.connect(path)
    yield connection
    connection.close()


def test_the_connection_is_read_only_and_in_utc(warehouse):
    assert warehouse.execute("select current_setting('TimeZone')").fetchone()[0] == "UTC"
    with pytest.raises(duckdb.Error):
        warehouse.execute("create table marts.anything (x integer)")


def test_status_is_the_newest_hour_and_how_much_of_the_day_was_collected(warehouse):
    status = dashboard.status(warehouse, NOW)

    assert status["hour_at"] == datetime(2026, 10, 18, 14, tzinfo=UTC)
    assert status["users_online"] == 40
    assert status["age_hours"] == pytest.approx(1.25)
    # 10:00, 11:00 and 14:00; 09:00 the day before is 31 hours old
    assert status["hours_collected_24h"] == 3


def test_ranking_compares_with_the_previous_hour_collected(warehouse):
    assert dashboard.previous_hour(warehouse) == (
        datetime(2026, 10, 18, 14, tzinfo=UTC),
        datetime(2026, 10, 18, 11, tzinfo=UTC),
    )
    ranking = dashboard.ranking_now(warehouse).set_index("appid")

    assert list(ranking["game"]) == ["App 440", "Counter-Strike 2", "Dota 2"]
    # 14:00 against 11:00, not against 10:00
    assert ranking.loc[730, "players_change"] == 200
    assert ranking.loc[730, "places_gained"] == -1
    # Not ranked at 11:00: unknown, not +900
    assert ranking.loc[570].isna()["players_change"]
    assert ranking.loc[440].isna()["players_change"]
    # A game without genres gets an empty list, not a null
    assert list(ranking.loc[440, "genres"]) == []


def test_ranking_respects_the_limit(warehouse):
    assert list(dashboard.ranking_now(warehouse, limit=1)["appid"]) == [440]


def test_games_are_offered_most_played_first(warehouse):
    assert list(dashboard.games(warehouse)["appid"]) == [730, 570, 440]


def test_a_game_out_of_the_ranking_is_a_gap_not_a_zero(warehouse):
    series = dashboard.players_over_time(warehouse, [570])

    # One row per collected hour, none for the hours that were not collected
    assert len(series) == 4
    by_hour = dict(zip(series["hour_at"].dt.hour, series["players"], strict=True))
    assert by_hour[10] == 800
    assert by_hour[14] == 900
    assert pd.isna(by_hour[11])  # not in the ranking that hour


def test_no_games_picked_gives_an_empty_series(warehouse):
    assert dashboard.players_over_time(warehouse, []).empty


def test_platform_series_has_both_measures_per_hour(warehouse):
    series = dashboard.platform_over_time(warehouse)

    assert set(series["measure"]) == {"Online", "In game"}
    newest = series[series["hour_at"] == datetime(2026, 10, 18, 14, tzinfo=UTC)]
    assert dict(zip(newest["measure"], newest["people"], strict=True)) == {
        "Online": 40,
        "In game": 14,
    }


def test_hours_of_the_day_say_how_many_samples_they_rest_on(warehouse, monkeypatch):
    profile = dashboard.hour_of_day(warehouse).set_index("hour_of_day")

    # 11:00 has no total of people in game: it does not count
    assert list(profile.index) == [9, 10, 14]
    assert profile.loc[10, "samples"] == 1
    assert not profile["enough"].any()

    monkeypatch.setattr(dashboard, "MIN_SAMPLES_PER_HOUR", 1)
    assert dashboard.hour_of_day(warehouse)["enough"].all()


def test_ranking_changes_newest_first_with_names(warehouse):
    changes = dashboard.ranking_changes(warehouse)

    assert list(changes["game"]) == ["App 440", "Dota 2", "Dota 2"]
    assert list(changes["change"]) == ["entered", "entered", "left"]
    assert list(dashboard.ranking_changes(warehouse, limit=1)["game"]) == ["App 440"]


def test_status_is_none_on_an_empty_warehouse(tmp_path):
    path = tmp_path / "empty.duckdb"
    with duckdb.connect(str(path)) as db:
        db.execute(
            "create schema intermediate; create schema marts;"
            "create table intermediate.int_hourly_snapshots (hour_at timestamptz);"
            "create table marts.fct_platform_hourly (hour_at timestamptz, snapshot_at timestamptz,"
            " users_online bigint, users_in_game bigint, ranked_players bigint,"
            " ranked_share_of_in_game double, top_appid integer, top_game_players bigint)"
        )
    assert dashboard.status(dashboard.connect(path), NOW) is None
