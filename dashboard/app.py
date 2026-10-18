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

# ---------------------------------------------------------------- the pulse
online, in_game, ranked, share = st.columns(4)
online.metric("Online on Steam", millions(status["users_online"]))
in_game.metric("Playing something", millions(status["users_in_game"]))
ranked.metric("Playing a top 100 game", millions(status["ranked_players"]))
share.metric(
    "Top 100 share of players",
    "n/a"
    if status["ranked_share_of_in_game"] is None
    else f"{status['ranked_share_of_in_game']:.0%}",
)

# ---------------------------------------------------------------- now
st.header("Most played right now")
ranking = dashboard.ranking_now(db, limit=25)
hours = dashboard.previous_hour(db)
if hours is None:
    since, gap_help = "Change", "Only one hour has been collected so far."
else:
    latest, previous = hours
    gap = int((latest - previous).total_seconds() // 3600)
    since = f"Change since {previous:%H}:00 UTC ({gap} h)"
    gap_help = (
        f"Against the previous hour collected, {gap} h before. "
        "Empty when the game was not in the ranking then."
    )
st.dataframe(
    ranking.drop(columns=["appid"]),
    hide_index=True,
    use_container_width=True,
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
series = dashboard.players_over_time(db, [int(appid) for appid in picked])
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
    use_container_width=True,
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
        alt.Chart(dashboard.platform_over_time(db))
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
        use_container_width=True,
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
        use_container_width=True,
    )
    st.caption(
        f"Faded bars rest on fewer than {dashboard.MIN_SAMPLES_PER_HOUR} collected hours: "
        "too few to say much about that hour of the day."
    )

# ---------------------------------------------------------------- in and out
st.header("In and out of the top 100")
st.dataframe(
    dashboard.ranking_changes(db, limit=20),
    hide_index=True,
    use_container_width=True,
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
