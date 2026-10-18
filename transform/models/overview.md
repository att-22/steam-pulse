{% docs __overview__ %}
# steam-pulse: the models

What is being played on Steam, hour by hour. A snapshot of the 100 most played games is
collected about every hour and kept untouched on the `data` branch; these models turn it
into the tables behind the [dashboard](https://steam-pulse-att.streamlit.app).
Code: [github.com/att-22/steam-pulse](https://github.com/att-22/steam-pulse).

## Layers

- **raw**: `raw.snapshots`, one row per collected file, the response kept as JSON.
- **staging**: one row per thing Steam reported, with names and types and nothing else.
  Views.
- **intermediate**: `int_hourly_snapshots`, the snapshot that stands for each hour.
- **marts**: what the dashboard reads. `fct_game_hourly` is incremental: each run rebuilds
  only the most recent hours, whole hours at a time.

## Rules that hold everywhere

- Hours and days are cut in UTC.
- A game out of the ranking has no row for that hour: not reported, never zero.
- The time of a row is the time Steam computed the data, not the time it was collected.

Open the lineage graph (bottom right) to see how each table is built.

*Not affiliated with Valve. Data from public Steam endpoints, offered as is; only public,
aggregate counts are stored.*
{% enddocs %}
