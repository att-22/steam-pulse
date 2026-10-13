import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from steam_pulse.collect import SOURCES, Response, collect_source
from steam_pulse.details import PAUSE_SECONDS, URL, collect, collect_details, pending, ranked_appids

PLAYERS = SOURCES[0]
NOW = datetime(2026, 10, 13, 9, 30, 5, 250_000, tzinfo=UTC)
# 2026-10-12T14:15:00Z
LAST_UPDATE = 1_791_814_500


def ranking(root: Path, appids, last_update=LAST_UPDATE):
    """Stores a ranking with these games through the real collector."""
    ranks = [
        {"rank": position, "appid": appid, "concurrent_in_game": 1000, "peak_in_game": 2000}
        for position, appid in enumerate(appids, start=1)
    ]
    body = json.dumps({"response": {"last_update": last_update, "ranks": ranks}})
    outcome = collect_source(
        PLAYERS, root, fetch=lambda url: Response(200, body), now=lambda: NOW, sleep=lambda _: None
    )
    assert outcome == "stored"


def details(appid, name="A game"):
    return {str(appid): {"success": True, "data": {"name": name, "steam_appid": appid}}}


class Store:
    """A fake store: answers by game and remembers what it was asked and the pauses."""

    def __init__(self, answers=None):
        self.answers = answers or {}
        self.asked: list[int] = []
        self.pauses: list[float] = []

    def fetch(self, url):
        appid = int(url.split("appids=")[1].split("&")[0])
        self.asked.append(appid)
        reply = self.answers.get(appid, details(appid))
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, Response):
            return reply
        return Response(200, json.dumps(reply))

    def run(self, root, now=NOW, **kwargs):
        return collect(root, fetch=self.fetch, now=lambda: now, sleep=self.pauses.append, **kwargs)

    def one(self, root, appid, now=NOW):
        return collect_details(
            appid, root, fetch=self.fetch, now=lambda: now, sleep=self.pauses.append
        )


def files(root: Path, under="raw/app_details"):
    return sorted(
        p.relative_to(root).as_posix()
        for p in root.rglob("*")
        if p.is_file() and under in p.as_posix()
    )


def read(root: Path, relative: str):
    return json.loads((root / relative).read_text(encoding="utf-8"))


def test_details_are_filed_by_game_under_the_day_of_the_request(tmp_path):
    assert Store().one(tmp_path, 730) == "stored"

    stored = "raw/app_details/date=2026-10-13/730.json"
    assert files(tmp_path) == [stored]
    envelope = read(tmp_path, stored)
    assert envelope["payload"] == details(730)
    assert envelope["source"] == "app_details"
    assert envelope["url"] == URL.format(appid=730)
    assert envelope["collected_at"] == "2026-10-13T09:30:05Z"
    assert envelope["http_status"] == 200


def test_games_come_from_the_rankings_latest_first_and_by_position(tmp_path):
    ranking(tmp_path, [570, 730, 440])
    ranking(tmp_path, [730, 10, 570], last_update=LAST_UPDATE + 3600)

    assert ranked_appids(tmp_path) == [730, 10, 570, 440]


def test_a_ranking_that_cannot_be_read_does_not_stop_the_rest(tmp_path):
    ranking(tmp_path, [730, 570])
    folder = tmp_path / "raw" / "concurrent_players" / "date=2026-10-12"
    (folder / "20261012T150000Z.json").write_text("not json", encoding="utf-8")
    (folder / "20261012T160000Z.json").write_text('{"payload": {"response": 3}}', encoding="utf-8")
    (folder / "20261012T170000Z.json").write_text(
        '{"payload": {"response": {"ranks": [{"appid": "x"}, {"appid": true}, {"appid": 10}]}}}',
        encoding="utf-8",
    )

    assert ranked_appids(tmp_path) == [10, 730, 570]


def test_every_ranked_game_is_asked_for_once(tmp_path):
    ranking(tmp_path, [730, 570, 440])
    store = Store()

    assert store.run(tmp_path) is True
    assert store.run(tmp_path) is True

    assert store.asked == [730, 570, 440]
    assert files(tmp_path) == [
        "raw/app_details/date=2026-10-13/440.json",
        "raw/app_details/date=2026-10-13/570.json",
        "raw/app_details/date=2026-10-13/730.json",
    ]


def test_details_stored_on_another_day_are_not_asked_for_again(tmp_path):
    ranking(tmp_path, [730, 570])
    store = Store()
    store.run(tmp_path)

    ranking(tmp_path, [730, 570, 10], last_update=LAST_UPDATE + 3600)
    store.run(tmp_path, now=datetime(2026, 10, 20, 9, 30, tzinfo=UTC))

    assert store.asked == [730, 570, 10]


def test_there_is_a_pause_between_requests_but_not_before_the_first(tmp_path):
    ranking(tmp_path, [730, 570, 440])
    store = Store()

    store.run(tmp_path)

    assert store.pauses == [PAUSE_SECONDS, PAUSE_SECONDS]


def test_a_run_asks_for_no_more_than_the_limit_and_the_next_one_continues(tmp_path, capsys):
    ranking(tmp_path, [730, 570, 440, 10, 20])
    store = Store()

    store.run(tmp_path, limit=2)
    assert store.asked == [730, 570]
    assert "2 stored, 0 unavailable, 0 rejected, 3 left for later" in capsys.readouterr().out

    store.run(tmp_path, limit=2)
    assert store.asked == [730, 570, 440, 10]


def test_a_game_the_store_has_nothing_for_is_kept_apart_and_the_run_goes_on(tmp_path, capsys):
    ranking(tmp_path, [730, 999, 440])
    store = Store({999: {"999": {"success": False}}})

    assert store.run(tmp_path) is True

    assert store.asked == [730, 999, 440]
    refused = "raw/_rejected/app_details/date=2026-10-13/999.json"
    assert files(tmp_path, "_rejected/app_details") == [refused]
    assert read(tmp_path, refused)["payload"] == {"999": {"success": False}}
    assert len(files(tmp_path, "raw/app_details")) == 2
    assert "2 stored, 1 unavailable, 0 rejected, 0 left for later" in capsys.readouterr().out


def test_an_unavailable_game_is_asked_for_again_only_on_another_day(tmp_path):
    ranking(tmp_path, [999])
    store = Store({999: {"999": {"success": False}}})

    store.run(tmp_path)
    store.run(tmp_path, now=datetime(2026, 10, 13, 23, 59, tzinfo=UTC))
    assert store.asked == [999]

    store.run(tmp_path, now=datetime(2026, 10, 14, 0, 1, tzinfo=UTC))
    assert store.asked == [999, 999]
    assert files(tmp_path, "_rejected/app_details") == [
        "raw/_rejected/app_details/date=2026-10-13/999.json",
        "raw/_rejected/app_details/date=2026-10-14/999.json",
    ]


def test_a_game_that_becomes_available_is_stored_and_left_alone(tmp_path):
    ranking(tmp_path, [999])
    store = Store({999: {"999": {"success": False}}})
    store.run(tmp_path)

    store.answers = {}
    store.run(tmp_path, now=datetime(2026, 10, 14, 9, 0, tzinfo=UTC))
    store.run(tmp_path, now=datetime(2026, 10, 15, 9, 0, tzinfo=UTC))

    assert store.asked == [999, 999]
    assert files(tmp_path) == ["raw/app_details/date=2026-10-14/999.json"]
    assert pending(tmp_path, NOW) == []


@pytest.mark.parametrize(
    "reply",
    [
        Response(429, "Too Many Requests"),
        Response(503, "<html>down</html>"),
        Response(403, "Access Denied"),
        Response(200, "<html>Steam is down for maintenance</html>"),
        Response(200, "null"),
        Response(200, "[]"),
        Response(200, "{}"),
        Response(200, json.dumps({"570": {"success": True, "data": {"name": "Other"}}})),
        Response(200, json.dumps({"730": {"success": True}})),
        Response(200, json.dumps({"730": {"success": True, "data": []}})),
        Response(200, json.dumps({"730": {"success": "true", "data": {"name": "x"}}})),
        Response(200, json.dumps({"730": None})),
        Response(500, json.dumps({"730": {"success": True, "data": {"name": "x"}}})),
        Response(500, json.dumps({"730": {"success": False}})),
        TimeoutError("timed out"),
    ],
)
def test_anything_that_is_not_an_answer_about_the_game_stops_the_run(tmp_path, reply):
    ranking(tmp_path, [730, 570, 440])
    store = Store({730: reply})

    assert store.run(tmp_path) is False

    assert set(store.asked) == {730}
    assert files(tmp_path, "raw/app_details") == []
    assert files(tmp_path, "_rejected/app_details") == [
        "raw/_rejected/app_details/date=2026-10-13/730.json"
    ]


def test_a_rejected_response_is_kept_as_it_came(tmp_path):
    page = "<html>Access Denied</html>"

    assert Store({730: Response(403, page)}).one(tmp_path, 730) == "rejected"

    envelope = read(tmp_path, "raw/_rejected/app_details/date=2026-10-13/730.json")
    assert envelope["text"] == page
    assert envelope["http_status"] == 403
    assert "payload" not in envelope


def test_a_network_error_is_recorded_after_the_retries(tmp_path):
    store = Store({730: ConnectionResetError("reset")})

    assert store.one(tmp_path, 730) == "rejected"

    assert store.asked == [730, 730, 730]
    envelope = read(tmp_path, "raw/_rejected/app_details/date=2026-10-13/730.json")
    assert envelope["error"] == "ConnectionResetError: reset"
    assert "http_status" not in envelope


def test_nothing_to_ask_when_there_are_no_rankings(tmp_path, capsys):
    store = Store()

    assert store.run(tmp_path) is True

    assert store.asked == []
    assert "0 stored, 0 unavailable, 0 rejected, 0 left for later" in capsys.readouterr().out


def test_the_stored_details_can_be_loaded_like_any_snapshot(tmp_path):
    duckdb = pytest.importorskip("duckdb")
    from steam_pulse.load import load

    ranking(tmp_path, [730, 999])
    Store({999: {"999": {"success": False}}}).run(tmp_path)

    with duckdb.connect() as connection:
        load(tmp_path, connection)
        rows = connection.execute(
            "select snapshot_id, rejected, payload ->> '$.\"730\".data.name' "
            "from raw.snapshots where source = 'app_details' order by 1"
        ).fetchall()

    assert rows == [("730", False, "A game"), ("999", True, None)]
