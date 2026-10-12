import json
from datetime import UTC, datetime, timedelta

import duckdb
import pytest

from steam_pulse.collect import SOURCES, Response, collect_source
from steam_pulse.load import load

PLAYERS, USERS = SOURCES
NOW = datetime(2026, 10, 12, 14, 17, 3, tzinfo=UTC)
# 2026-10-12T14:15:00Z
LAST_UPDATE = 1_791_814_500
RANKING_ID = "20261012T141500Z"
NOW_ID = "20261012T141703Z"


def ranking(last_update=LAST_UPDATE):
    return {
        "response": {
            "last_update": last_update,
            "ranks": [
                {
                    "rank": 1,
                    "appid": 730,
                    "concurrent_in_game": 1_208_155,
                    "peak_in_game": 1_400_000,
                }
            ],
        }
    }


def collected(root, source, reply, now=NOW):
    """Writes a snapshot with the real collector, so the loader reads what it really writes."""

    def fetch(url):
        if isinstance(reply, Exception):
            raise reply
        return reply if isinstance(reply, Response) else Response(200, json.dumps(reply))

    return collect_source(source, root, fetch=fetch, now=lambda: now, sleep=lambda _: None)


@pytest.fixture
def warehouse():
    with duckdb.connect(":memory:") as connection:
        yield connection


def rows(warehouse, columns="source, snapshot_id, rejected"):
    return warehouse.execute(
        f"select {columns} from raw.snapshots order by source, snapshot_id, rejected"
    ).fetchall()


def test_each_file_becomes_one_row_with_its_payload_untouched(tmp_path, warehouse):
    collected(tmp_path, PLAYERS, ranking())
    collected(tmp_path, USERS, {"users_online": "38,882,059", "users_ingame": "11,853,306"})

    assert load(tmp_path, warehouse) == (2, 0)

    assert rows(warehouse) == [
        ("concurrent_players", RANKING_ID, False),
        ("steam_users", NOW_ID, False),
    ]
    payload, status, url, collected_at = warehouse.execute(
        "select payload, http_status, url, collected_at at time zone 'UTC' "
        "from raw.snapshots where source = 'concurrent_players'"
    ).fetchone()
    assert json.loads(payload) == ranking()
    assert status == 200
    assert url == PLAYERS.url
    assert collected_at == NOW.replace(tzinfo=None)


def test_payload_can_be_queried_as_json(tmp_path, warehouse):
    collected(tmp_path, PLAYERS, ranking())
    load(tmp_path, warehouse)

    assert warehouse.execute(
        "select (payload -> 'response' ->> 'last_update')::bigint, "
        "(payload -> 'response' -> 'ranks' -> 0 ->> 'appid')::integer from raw.snapshots"
    ).fetchone() == (LAST_UPDATE, 730)
    assert warehouse.execute("select typeof(payload) from raw.snapshots").fetchone() == ("JSON",)


def test_loading_twice_adds_nothing(tmp_path, warehouse):
    collected(tmp_path, PLAYERS, ranking())

    assert load(tmp_path, warehouse) == (1, 0)
    first = rows(warehouse, "source, snapshot_id, epoch(loaded_at)")
    assert load(tmp_path, warehouse) == (0, 1)

    assert rows(warehouse, "source, snapshot_id, epoch(loaded_at)") == first


def test_only_the_files_that_arrived_since_the_last_load_are_added(tmp_path, warehouse):
    collected(tmp_path, PLAYERS, ranking())
    load(tmp_path, warehouse)
    collected(tmp_path, PLAYERS, ranking(LAST_UPDATE + 300), now=NOW + timedelta(minutes=5))

    assert load(tmp_path, warehouse) == (1, 1)

    assert rows(warehouse) == [
        ("concurrent_players", RANKING_ID, False),
        ("concurrent_players", "20261012T142000Z", False),
    ]


def test_rejected_responses_are_loaded_and_marked(tmp_path, warehouse):
    collected(tmp_path, PLAYERS, Response(200, "<html>maintenance</html>"))
    collected(tmp_path, USERS, TimeoutError("timed out"))

    assert load(tmp_path, warehouse) == (2, 0)

    assert rows(warehouse, "source, rejected, http_status, payload, text, error") == [
        ("concurrent_players", True, 200, None, "<html>maintenance</html>", None),
        ("steam_users", True, None, None, None, "TimeoutError: timed out"),
    ]


def test_accepted_and_rejected_at_the_same_second_are_both_kept(tmp_path, warehouse):
    collected(tmp_path, USERS, {"users_online": "1", "users_ingame": "1"})
    collected(tmp_path, USERS, Response(503, "busy"))

    assert load(tmp_path, warehouse) == (2, 0)

    assert rows(warehouse) == [("steam_users", NOW_ID, False), ("steam_users", NOW_ID, True)]


def test_the_same_snapshots_in_another_folder_are_the_same_snapshots(tmp_path, warehouse):
    for folder in ("first", "second"):
        collected(tmp_path / folder, PLAYERS, ranking())

    assert load(tmp_path / "first", warehouse) == (1, 0)
    assert load(tmp_path / "second", warehouse) == (0, 1)


def test_nothing_to_load_is_not_an_error(tmp_path, warehouse):
    assert load(tmp_path, warehouse) == (0, 0)
    assert rows(warehouse) == []


def test_a_file_that_cannot_be_read_leaves_the_warehouse_as_it_was(tmp_path, warehouse):
    collected(tmp_path, PLAYERS, ranking())
    load(tmp_path, warehouse)
    collected(tmp_path, PLAYERS, ranking(LAST_UPDATE + 300))
    collected(tmp_path, USERS, Response(503, "busy"))
    broken = tmp_path / "raw" / "_rejected" / "steam_users" / "date=2026-10-12" / f"{NOW_ID}.json"
    broken.write_text("{ not json", encoding="utf-8")

    with pytest.raises(duckdb.Error):
        load(tmp_path, warehouse)

    assert rows(warehouse) == [("concurrent_players", RANKING_ID, False)]


def test_the_warehouse_survives_being_closed(tmp_path):
    collected(tmp_path / "data", PLAYERS, ranking())
    database = str(tmp_path / "warehouse.duckdb")

    with duckdb.connect(database) as connection:
        assert load(tmp_path / "data", connection) == (1, 0)
    with duckdb.connect(database) as connection:
        assert load(tmp_path / "data", connection) == (0, 1)
