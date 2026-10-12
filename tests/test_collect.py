import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from steam_pulse.collect import (
    SOURCES,
    Response,
    collect,
    collect_source,
    fetch_with_retries,
)

PLAYERS, USERS = SOURCES
NOW = datetime(2026, 10, 12, 14, 17, 3, 250_000, tzinfo=UTC)
# 2026-10-12T14:15:00Z, the moment Steam says the ranking was computed
LAST_UPDATE = 1_791_814_500


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
                },
                {"rank": 2, "appid": 570, "concurrent_in_game": 610_000, "peak_in_game": 700_000},
            ],
        }
    }


def answering(*replies):
    """A fake fetch that returns (or raises) the given replies in order, then repeats the last."""
    pending = list(replies)

    def fetch(url):
        reply = pending.pop(0) if len(pending) > 1 else pending[0]
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, Response):
            return reply
        return Response(200, json.dumps(reply))

    return fetch


def run(source, root, fetch, now=NOW):
    return collect_source(source, root, fetch=fetch, now=lambda: now, sleep=lambda _: None)


def files(root: Path):
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())


def read(root: Path, relative: str):
    return json.loads((root / relative).read_text(encoding="utf-8"))


def test_snapshot_is_filed_under_the_time_steam_reports(tmp_path):
    assert run(PLAYERS, tmp_path, answering(ranking())) == "stored"

    stored = "raw/concurrent_players/date=2026-10-12/20261012T141500Z.json"
    assert files(tmp_path) == [stored]
    envelope = read(tmp_path, stored)
    assert envelope["payload"] == ranking()
    assert envelope["collected_at"] == "2026-10-12T14:17:03Z"
    assert envelope["http_status"] == 200
    assert envelope["source"] == "concurrent_players"
    assert envelope["url"] == PLAYERS.url


def test_snapshot_near_midnight_goes_to_the_day_of_the_data(tmp_path):
    just_before_midnight = int(datetime(2026, 10, 11, 23, 55, tzinfo=UTC).timestamp())
    after_midnight = datetime(2026, 10, 12, 0, 2, tzinfo=UTC)

    run(PLAYERS, tmp_path, answering(ranking(just_before_midnight)), now=after_midnight)

    assert files(tmp_path) == ["raw/concurrent_players/date=2026-10-11/20261011T235500Z.json"]


def test_same_snapshot_collected_twice_keeps_the_first(tmp_path):
    later = datetime(2026, 10, 12, 14, 19, tzinfo=UTC)

    assert run(PLAYERS, tmp_path, answering(ranking())) == "stored"
    assert run(PLAYERS, tmp_path, answering(ranking()), now=later) == "duplicate"

    assert len(files(tmp_path)) == 1
    assert read(tmp_path, files(tmp_path)[0])["collected_at"] == "2026-10-12T14:17:03Z"


def test_a_newer_snapshot_is_stored_next_to_the_older_one(tmp_path):
    run(PLAYERS, tmp_path, answering(ranking()))
    run(PLAYERS, tmp_path, answering(ranking(LAST_UPDATE + 300)))

    assert files(tmp_path) == [
        "raw/concurrent_players/date=2026-10-12/20261012T141500Z.json",
        "raw/concurrent_players/date=2026-10-12/20261012T142000Z.json",
    ]


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"response": {}},
        {"response": {"last_update": LAST_UPDATE}},
        {"response": {"last_update": LAST_UPDATE, "ranks": []}},
        {"response": {"last_update": 0, "ranks": [{"rank": 1}]}},
        {"response": {"last_update": "1791814500", "ranks": [{"rank": 1}]}},
        {"response": {"last_update": True, "ranks": [{"rank": 1}]}},
        {"response": None},
        [],
        None,
    ],
)
def test_unusable_ranking_is_kept_apart_not_lost(tmp_path, payload):
    assert run(PLAYERS, tmp_path, answering(payload)) == "rejected"

    rejected = "raw/_rejected/concurrent_players/date=2026-10-12/20261012T141703Z.json"
    assert files(tmp_path) == [rejected]
    assert read(tmp_path, rejected)["payload"] == payload


def test_response_that_is_not_json_is_kept_as_text(tmp_path):
    page = "<html>Steam is down for maintenance</html>"

    assert run(PLAYERS, tmp_path, answering(Response(200, page))) == "rejected"

    envelope = read(tmp_path, files(tmp_path)[0])
    assert envelope["text"] == page
    assert "payload" not in envelope


def test_error_status_is_rejected_even_with_a_valid_body(tmp_path):
    reply = Response(503, json.dumps(ranking()))

    assert run(PLAYERS, tmp_path, answering(reply)) == "rejected"

    envelope = read(tmp_path, files(tmp_path)[0])
    assert envelope["http_status"] == 503
    assert files(tmp_path)[0].startswith("raw/_rejected/")


def test_unreachable_server_leaves_a_record_of_the_failure(tmp_path):
    assert run(PLAYERS, tmp_path, answering(TimeoutError("timed out"))) == "rejected"

    envelope = read(tmp_path, files(tmp_path)[0])
    assert envelope["error"] == "TimeoutError: timed out"
    assert "http_status" not in envelope


def test_steam_users_are_filed_under_the_collection_time(tmp_path):
    totals = {"users_online": "38,882,059", "users_ingame": "11,853,306"}

    assert run(USERS, tmp_path, answering(totals)) == "stored"

    stored = "raw/steam_users/date=2026-10-12/20261012T141703Z.json"
    assert files(tmp_path) == [stored]
    # Stored exactly as received: turning "38,882,059" into a number is a modelling job
    assert read(tmp_path, stored)["payload"] == totals


def test_steam_users_without_the_expected_fields_are_rejected(tmp_path):
    assert run(USERS, tmp_path, answering({"users_online": "1"})) == "rejected"
    assert files(tmp_path)[0].startswith("raw/_rejected/steam_users/")


def test_nothing_is_left_half_written(tmp_path):
    run(PLAYERS, tmp_path, answering(ranking()))
    assert not list(tmp_path.rglob("*.tmp"))


def by_url(replies):
    def fetch(url):
        return answering(replies[url])(url)

    return fetch


def test_run_fails_when_the_ranking_cannot_be_stored(tmp_path):
    fetch = by_url({PLAYERS.url: {}, USERS.url: {"users_online": "1", "users_ingame": "1"}})

    assert collect(tmp_path, fetch=fetch, now=lambda: NOW, sleep=lambda _: None) is False
    # The source that did work is stored all the same
    assert any(f.startswith("raw/steam_users/") for f in files(tmp_path))


def test_run_succeeds_when_only_the_optional_source_fails(tmp_path):
    fetch = by_url({PLAYERS.url: ranking(), USERS.url: OSError("unreachable")})

    assert collect(tmp_path, fetch=fetch, now=lambda: NOW, sleep=lambda _: None) is True


def test_run_succeeds_on_a_duplicate(tmp_path):
    fetch = by_url({PLAYERS.url: ranking(), USERS.url: {"users_online": "1", "users_ingame": "1"}})
    later = datetime(2026, 10, 12, 14, 19, tzinfo=UTC)

    collect(tmp_path, fetch=fetch, now=lambda: NOW, sleep=lambda _: None)
    assert collect(tmp_path, fetch=fetch, now=lambda: later, sleep=lambda _: None) is True


def test_retries_until_the_server_answers():
    waits = []
    fetch = answering(OSError("reset"), Response(502, "bad gateway"), Response(200, "{}"))

    result = fetch_with_retries(fetch, "https://example.test", sleep=waits.append)

    assert result == Response(200, "{}")
    assert waits == [2.0, 4.0]


def test_rate_limiting_is_retried():
    fetch = answering(Response(429, "slow down"), Response(200, "{}"))

    assert fetch_with_retries(fetch, "https://example.test", sleep=lambda _: None).status == 200


def test_client_error_is_not_retried():
    calls = []

    def fetch(url):
        calls.append(url)
        return Response(404, "not found")

    result = fetch_with_retries(fetch, "https://example.test", sleep=lambda _: None)

    assert result.status == 404
    assert len(calls) == 1


def test_gives_up_after_the_last_attempt_without_waiting_again():
    waits = []
    error = OSError("down")

    result = fetch_with_retries(answering(error), "https://example.test", sleep=waits.append)

    assert result is error
    assert waits == [2.0, 4.0]
