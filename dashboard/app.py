"""What is being played on Steam, hour by hour: the dashboard.

Run it with ``uv run streamlit run dashboard/app.py``. It reads the tables built by the
models from, in this order: the DuckDB file named by STEAM_PULSE_DATABASE; a local
steam_pulse.duckdb; or the file the Build workflow publishes after every collection,
which is what the hosted copy uses. The figures come from ``steam_pulse.dashboard``;
this file only lays them out.
"""

from __future__ import annotations

import contextlib
import os
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import altair as alt
import streamlit as st

# The hosted copy installs the dependencies but not this repository as a package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from steam_pulse import dashboard  # noqa: E402
from steam_pulse.freshness import MAX_AGE_HOURS  # noqa: E402

LOCAL = Path(os.environ.get("STEAM_PULSE_DATABASE", "steam_pulse.duckdb"))
REPOSITORY = "https://github.com/att-22/steam-pulse"

st.set_page_config(page_title="steam-pulse", page_icon="🎮", layout="wide")


# The published file changes after each collection: fetch it again every 15 minutes.
@st.cache_resource(ttl=15 * 60, show_spinner="Fetching the latest data...")
def connection():
    if LOCAL.exists():
        return dashboard.connect(LOCAL)
    folder = Path(tempfile.gettempdir())
    # A new name each time: the copy being read by the previous connection is left alone
    # while the new one downloads. Older copies are removed so they do not pile up.
    for old in folder.glob("steam_pulse_*.duckdb"):
        # On Windows a file still open cannot be removed; it goes next time.
        with contextlib.suppress(OSError):
            old.unlink(missing_ok=True)
    target = folder / f"steam_pulse_{datetime.now(UTC):%Y%m%dT%H%M%S}.duckdb"
    return dashboard.connect(dashboard.download(dashboard.PUBLISHED, target))


def millions(value: float | None) -> str:
    return "n/a" if value is None else f"{value / 1e6:.1f} M"


st.title("steam-pulse")
st.caption(
    "What is being played on Steam, hour by hour. Steam publishes how many people are "
    "playing its 100 most played games right now, but not how that changes: this "
    "history is built from a snapshot collected about every hour."
)

try:
    db = connection()
except OSError as error:
    st.error(f"The data could not be fetched ({error}). Try again in a few minutes.")
    st.stop()
now = datetime.now(UTC)
status = dashboard.status(db, now)
if status is None:
    st.warning("The warehouse has no snapshots yet.")
    st.stop()

# ---------------------------------------------------------------- how fresh is this
freshness = (
    f"Newest snapshot: **{status['snapshot_at']:%Y-%m-%d %H:%M} UTC**, "
    f"{status['age_hours']:.1f} hours ago. "
    f"Hours collected in the last day: **{status['hours_collected_24h']} of 24**."
)
if status["age_hours"] > MAX_AGE_HOURS:
    st.warning(freshness + " The collection may have stopped.")
else:
    st.info(freshness)

# ---------------------------------------------------------------- explore the history
# Every section can look back: the ranking at any collected hour, the charts over any
# span of days. By default, the newest hour and the whole history.
collected = dashboard.hours(db)
with st.sidebar:
    st.header("Explore the history")
    hour = st.selectbox(
        "Ranking at (UTC)",
        collected,
        format_func=lambda moment: f"{moment:%a %d %b %Y, %H}:00",
        help="Only the hours that were collected can be picked.",
    )
    first_day, last_day = collected[-1].date(), collected[0].date()
    period = st.date_input(
        "Charts from / to (UTC)",
        value=(first_day, last_day),
        min_value=first_day,
        max_value=last_day,
    )
    # While the second day is being picked, the widget holds only the first.
    start, end = (period[0], period[-1]) if period else (first_day, last_day)
    st.caption(f"{len(collected)} hours collected since {first_day:%d %b %Y}.")
is_latest = hour == collected[0]
when = "right now" if is_latest else f"on {hour:%a %d %b %Y} at {hour:%H}:00 UTC"

# ---------------------------------------------------------------- the pulse
totals = dashboard.totals_at(db, hour) or {}
online, in_game, ranked, share = st.columns(4)
online.metric("Online on Steam", millions(totals.get("users_online")))
in_game.metric("Playing something", millions(totals.get("users_in_game")))
ranked.metric("Playing a top 100 game", millions(totals.get("ranked_players")))
share.metric(
    "Top 100 share of players",
    "n/a"
    if totals.get("ranked_share_of_in_game") is None
    else f"{totals['ranked_share_of_in_game']:.0%}",
)

# ---------------------------------------------------------------- the ranking
st.header(f"Most played {when}")
ranking = dashboard.ranking_at(db, hour, limit=25)
previous = dashboard.previous_hour(db, hour)
if previous is None:
    since, gap_help = "Change", "No hour was collected before this one."
else:
    gap = int((hour - previous).total_seconds() // 3600)
    since = f"Change since {previous:%H}:00 UTC ({gap} h)"
    gap_help = (
        f"Against the previous hour collected, {gap} h before. "
        "Empty when the game was not in the ranking then."
    )
st.dataframe(
    ranking.drop(columns=["appid"]),
    hide_index=True,
    width="stretch",
    column_config={
        "rank": st.column_config.NumberColumn("#", width="small"),
        "game": st.column_config.TextColumn("Game", width="large"),
        "players": st.column_config.NumberColumn("Players", format="localized"),
        "players_change": st.column_config.NumberColumn(since, format="localized", help=gap_help),
        "places_gained": st.column_config.NumberColumn("Places gained", help=gap_help),
        "is_free": st.column_config.CheckboxColumn("Free"),
        "genres": st.column_config.ListColumn("Genres"),
    },
)

# ---------------------------------------------------------------- over time
st.header("Players over time")
choices = dashboard.games(db)
names = dict(zip(choices["appid"], choices["game"], strict=True))
picked = st.multiselect(
    "Games",
    options=list(names),
    default=ranking["appid"].head(5).tolist(),
    format_func=names.get,
)
series = dashboard.players_over_time(db, [int(appid) for appid in picked], start, end)
st.altair_chart(
    alt.Chart(series)
    # invalid=None: a null breaks the line. A game out of the ranking is not at zero.
    .mark_line(point=True, invalid=None)
    .encode(
        x=alt.X("hour_at:T", title="Hour (UTC)"),
        y=alt.Y("players:Q", title="Players"),
        color=alt.Color("game:N", title=None, sort=None, scale=alt.Scale(scheme="tableau10")),
        tooltip=[
            alt.Tooltip("game:N"),
            alt.Tooltip("hour_at:T", title="Hour (UTC)", format="%Y-%m-%d %H:%M"),
            alt.Tooltip("players:Q", format=","),
        ],
    ),
    width="stretch",
)
st.caption(
    "One point per hour collected. A gap in a line is an hour in which the game was "
    "not among the 100 most played: how many played it then is not known."
)

# ---------------------------------------------------------------- all of Steam
left, right = st.columns(2)
with left:
    st.subheader("Steam as a whole")
    st.altair_chart(
        alt.Chart(dashboard.platform_over_time(db, start, end))
        .mark_line(point=True)
        .encode(
            x=alt.X("hour_at:T", title="Hour (UTC)"),
            y=alt.Y("people:Q", title="People"),
            color=alt.Color("measure:N", title=None),
            tooltip=[
                alt.Tooltip("measure:N"),
                alt.Tooltip("hour_at:T", title="Hour (UTC)", format="%Y-%m-%d %H:%M"),
                alt.Tooltip("people:Q", format=","),
            ],
        ),
        width="stretch",
    )
with right:
    st.subheader("When Steam plays")
    st.altair_chart(
        alt.Chart(dashboard.hour_of_day(db))
        .mark_bar()
        .encode(
            # Every hour of the day on the axis, so that one with no data shows as a gap.
            x=alt.X(
                "hour_of_day:O",
                title="Hour of the day (UTC)",
                scale=alt.Scale(domain=list(range(24))),
            ),
            y=alt.Y("avg_in_game:Q", title="In game, average"),
            opacity=alt.condition("datum.enough", alt.value(1.0), alt.value(0.25)),
            tooltip=[
                alt.Tooltip("hour_of_day:O", title="Hour (UTC)"),
                alt.Tooltip("avg_in_game:Q", title="Average in game", format=",.0f"),
                alt.Tooltip("samples:Q", title="Hours it rests on"),
            ],
        ),
        width="stretch",
    )
    st.caption(
        f"Faded bars rest on fewer than {dashboard.MIN_SAMPLES_PER_HOUR} collected hours: "
        "too few to say much about that hour of the day."
    )

# ---------------------------------------------------------------- one game, day by day
st.header("A game, day by day")
appid = st.selectbox(
    "Game",
    options=list(names),
    index=list(names).index(int(ranking["appid"].iloc[0])) if len(ranking) else 0,
    format_func=names.get,
)
daily = dashboard.game_by_day(db, int(appid))
st.dataframe(
    daily,
    hide_index=True,
    width="stretch",
    column_config={
        "day": st.column_config.DateColumn("Day (UTC)", format="ddd DD MMM YYYY"),
        "peak_players": st.column_config.NumberColumn("Peak", format="localized"),
        "peak_hour_at": st.column_config.DatetimeColumn("Peak at (UTC)", format="HH:mm"),
        "avg_players_while_ranked": st.column_config.NumberColumn(
            "Average while ranked",
            format="localized",
            help="Over the hours it was in the ranking: its quieter hours are not known.",
        ),
        "best_rank": st.column_config.NumberColumn("Best rank"),
        "hours_in_ranking": st.column_config.NumberColumn("Hours in ranking"),
        "hours_collected": st.column_config.NumberColumn(
            "Hours collected", help="Hours of that day with data at all. 24 on a full day."
        ),
    },
)

# ---------------------------------------------------------------- in and out
st.header("In and out of the top 100")
if not is_latest:
    st.caption(f"Up to {hour:%a %d %b %Y, %H}:00 UTC.")
st.dataframe(
    dashboard.ranking_changes(db, until=hour, limit=20),
    hide_index=True,
    width="stretch",
    column_config={
        "hour_at": st.column_config.DatetimeColumn("Hour (UTC)", format="YYYY-MM-DD HH:mm"),
        "change": st.column_config.TextColumn("Change"),
        "game": st.column_config.TextColumn("Game", width="large"),
        "rank": st.column_config.NumberColumn("Rank"),
        "players": st.column_config.NumberColumn("Players", format="localized"),
        "hours_since_previous": st.column_config.NumberColumn(
            "Hours since the previous snapshot",
            help="More than 1 means the hours in between were not collected.",
        ),
    },
)

st.divider()
st.caption(
    "**Not affiliated with Valve.** A personal project, not endorsed by, sponsored by or "
    "affiliated with Valve Corporation or Steam. Steam is a trademark of Valve "
    "Corporation. Data from public Steam endpoints, offered as is. No data about "
    f"individual users is collected: only public, aggregate counts. Code: {REPOSITORY}"
)
