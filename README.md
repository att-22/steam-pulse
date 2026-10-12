# steam-pulse

What is being played on Steam, hour by hour.

Steam publishes how many people are playing each of its 100 most played games *right
now*, but not how that changed over time. This project builds that history itself: it
takes a snapshot every hour, keeps it untouched, and turns the pile of snapshots into
tables and a dashboard.

> **Not affiliated with Valve.** This is a personal project. It is not endorsed by,
> sponsored by or affiliated with Valve Corporation or Steam. Steam and the Steam logo
> are trademarks of Valve Corporation. The data comes from public Steam endpoints and is
> offered as is, with no guarantee of accuracy. No data about individual Steam users is
> collected or stored: only public, aggregate counts.

## Status

| Stage | State |
|---|---|
| Hourly collection of raw snapshots | Running |
| Load of the raw snapshots into the warehouse | Working locally (DuckDB); cloud warehouse planned |
| Modelling with dbt | Hourly and daily tables built; game names planned |
| Dashboard | Planned |
| Data quality and freshness alerts | Planned |

## How the collection works

```
GitHub Actions (every hour)  ->  steam_pulse.collect  ->  commit on the `data` branch
```

- **One request per snapshot.** A single Steam endpoint returns the 100 most played
  games with their player counts. A second request stores the total number of Steam
  users online and in game.
- **The data is dated by Steam, not by the collector.** The ranking carries the moment
  it was computed (`last_update`). Responses come through a cache and can be older than
  a previous one, so each snapshot is filed under that moment: collecting the same
  snapshot twice stores it once.
- **Nothing is thrown away.** A response that does not look right (an error status, a
  maintenance page, a changed format) is kept under `raw/_rejected/`, and the run is
  marked as failed.
- **Raw data lives on its own branch.** `main` holds code and only changes through pull
  requests; the [`data`](../../tree/data) branch holds nothing but snapshots, one commit
  per collection. Everything downstream can be rebuilt from it.

```
raw/concurrent_players/date=2026-10-12/20261012T154904Z.json
raw/steam_users/date=2026-10-12/20261012T155449Z.json
raw/_rejected/<source>/date=.../....json
```

Each file is the response wrapped in an envelope:

```json
{
  "source": "concurrent_players",
  "url": "https://api.steampowered.com/ISteamChartsService/GetGamesByConcurrentPlayers/v1/",
  "collected_at": "2026-10-12T15:54:48Z",
  "http_status": 200,
  "payload": { "response": { "last_update": 1791820144, "ranks": [ ... ] } }
}
```

## How the load works

```
`data` branch (JSON files)  ->  steam_pulse.load  ->  raw.snapshots (one row per file)
```

- **The warehouse is disposable.** It holds nothing that is not in the snapshot files:
  delete it, run the load, and it is back.
- **The load only copies.** Each file becomes one row with its payload kept as JSON,
  rejected responses included. Turning that into players per game is left to the models.
- **Loading twice adds nothing.** A snapshot is identified by its source and its file
  name, so only the files that are not in the warehouse yet are read. A load that fails
  halfway leaves the warehouse as it was.
- **One code path, two warehouses.** `--database` takes a local DuckDB file or a
  MotherDuck database (`md:steam_pulse`).

## How the modelling works

```
raw.snapshots
  -> staging        stg_concurrent_players   one row per game and snapshot
                    stg_steam_users          one row per snapshot
  -> intermediate   int_hourly_snapshots     the snapshot that stands for each hour
  -> marts          fct_game_hourly          one row per game and hour
                    fct_game_daily           one row per game and day
                    fct_platform_hourly      one row per hour, with the totals of Steam
                    fct_ranking_changes      games entering and leaving the ranking
```

- **Staging only renames and types.** The ranking is unfolded into one row per game,
  `"38,882,059"` becomes a number, rejected responses are left out. No business logic yet.
- **The time of a row is the time of the data.** `snapshot_at` is the moment Steam says
  it computed the ranking; `collected_at` is kept next to it, so the age of the cached
  response can be measured.
- **Missing is not zero.** A game has rows only while it is in the ranking. Nothing
  fills the gaps with zeros, and a change is only computed against exactly one hour
  before: if that hour is not known, the change is null.
- **One snapshot per hour, chosen once.** Runs can be late, repeated or launched by
  hand. `int_hourly_snapshots` picks the last snapshot of each hour and every table
  builds on that choice.
- **Hours and days are cut in UTC**, explicitly, so the same snapshot falls on the same
  day whatever the time zone of the machine that runs the models.
- **Tested three ways.** Unit tests feed the models hand-written snapshots, data tests
  check every row actually loaded (unique keys, consecutive positions, a ranking that
  follows the player count, totals that can be true), and CI builds everything on a few
  real snapshots kept in `tests/sample/`.

## Run it locally

You only need [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/att-22/steam-pulse.git
cd steam-pulse
uv run collect --output data    # one snapshot, written to data/raw/
uv run pytest                   # the tests never call Steam
```

To query the history collected so far, check out the `data` branch next to the code and
load it into a local DuckDB file:

```bash
git worktree add data origin/data     # the snapshots, in data/raw/
uv run load                           # creates steam_pulse.duckdb
cd transform && uv run dbt build      # builds the models and runs their tests
```

The collector uses the Python standard library only, so the hourly job installs
nothing: fewer steps, fewer things that can fail 8,760 times a year. Only the load needs
a dependency (DuckDB), and the collector never imports it.
