-- One row per hour that has data: the ranking snapshot that stands for that hour.
--
-- Collection is roughly hourly but not regular: a run can be late, be launched by hand
-- or land twice in the same hour. Everything built on top wants one value per hour, so
-- one snapshot is chosen here, once: the last one Steam computed within the hour.
--
-- The staging model has one row per game, so every snapshot arrives here a hundred
-- times. Keeping a single row per hour takes care of that as well.

with snapshots as (

    select
        snapshot_id,
        snapshot_at,
        collected_at,
        {{ utc_hour('snapshot_at') }} as hour_at
    from {{ ref('stg_concurrent_players') }}

)

select
    hour_at,
    snapshot_id,
    snapshot_at,
    collected_at
from snapshots
qualify row_number() over (partition by hour_at order by snapshot_at desc) = 1
