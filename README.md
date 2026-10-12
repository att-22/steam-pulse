# steam-pulse: raw data

This branch holds nothing but data. Every commit is one collection, made by the
`Collect` workflow of the `main` branch.

```
raw/concurrent_players/date=YYYY-MM-DD/<time Steam computed the ranking>.json
raw/steam_users/date=YYYY-MM-DD/<time of collection>.json
raw/_rejected/<source>/date=YYYY-MM-DD/<time of collection>.json
```

Times are UTC. See the README of the `main` branch for the format of each file.

Not affiliated with Valve. The data comes from public Steam endpoints and is offered as
is, with no guarantee of accuracy.
