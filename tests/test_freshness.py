from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from steam_pulse.freshness import MAX_AGE_HOURS, check, latest_ranking, main

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
LIMIT = timedelta(hours=MAX_AGE_HOURS)


def stored(root: Path, name: str, source: str = "concurrent_players", rejected: bool = False):
    """Leaves an empty file where the collector would have written one."""
    base = root / "raw" / "_rejected" if rejected else root / "raw"
    path = base / source / f"date={name[:4]}-{name[4:6]}-{name[6:8]}" / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}", encoding="utf-8")


def test_the_newest_ranking_is_found_across_days(tmp_path):
    stored(tmp_path, "20261017T235904Z")
    stored(tmp_path, "20261018T014904Z")
    stored(tmp_path, "20261016T120000Z")

    assert latest_ranking(tmp_path) == datetime(2026, 10, 18, 1, 49, 4, tzinfo=UTC)


def test_only_accepted_rankings_count(tmp_path):
    stored(tmp_path, "20261018T010000Z")
    # Newer, but not a ranking, or not accepted
    stored(tmp_path, "20261018T110000Z", source="steam_users")
    stored(tmp_path, "20261018T110000Z", source="app_details")
    stored(tmp_path, "20261018T110000Z", rejected=True)
    # Not a snapshot file at all
    (tmp_path / "raw" / "concurrent_players" / "date=2026-10-18" / "notes.json").write_text("")

    assert latest_ranking(tmp_path) == datetime(2026, 10, 18, 1, 0, tzinfo=UTC)


def test_no_ranking_at_all_is_not_fresh(tmp_path, capsys):
    assert latest_ranking(tmp_path) is None
    assert check(tmp_path, LIMIT, now=lambda: NOW) is False
    assert "no ranking found" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("newest", "fresh"),
    [
        ("20261018T110000Z", True),  # 1 hour old
        ("20261018T000000Z", True),  # exactly the limit: still fine
        ("20261017T235959Z", False),  # one second past it
        ("20261015T120000Z", False),  # three days old
    ],
)
def test_fresh_only_within_the_limit(tmp_path, newest, fresh):
    stored(tmp_path, newest)

    assert check(tmp_path, LIMIT, now=lambda: NOW) is fresh


def test_the_age_is_reported_either_way(tmp_path, capsys):
    stored(tmp_path, "20261018T083000Z")
    check(tmp_path, LIMIT, now=lambda: NOW)
    assert "2026-10-18 08:30 UTC, 3.5 hours ago (ok)" in capsys.readouterr().out

    check(tmp_path, timedelta(hours=2), now=lambda: NOW)
    assert "3.5 hours ago (STALE: the limit is 2 h)" in capsys.readouterr().out


def test_the_command_fails_when_stale(tmp_path, monkeypatch):
    stored(tmp_path, "20261001T000000Z")
    monkeypatch.setattr("sys.argv", ["freshness", "--input", str(tmp_path)])

    with pytest.raises(SystemExit) as stopped:
        main()
    assert stopped.value.code == 1


def test_the_limit_can_be_changed(tmp_path, monkeypatch):
    stored(tmp_path, "20261001T000000Z")
    monkeypatch.setattr(
        "sys.argv", ["freshness", "--input", str(tmp_path), "--max-age-hours", "1e6"]
    )

    with pytest.raises(SystemExit) as stopped:
        main()
    assert stopped.value.code == 0
