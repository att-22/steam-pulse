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
| Modelling with dbt on a cloud warehouse | Planned |
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

## Run it locally

You only need [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/att-22/steam-pulse.git
cd steam-pulse
uv run collect --output data    # one snapshot, written to data/raw/
uv run pytest                   # the tests never call Steam
```

The collector uses the Python standard library only, so the hourly job installs
nothing: fewer steps, fewer things that can fail 8,760 times a year.
