-- One row per game and hour in which the game was in the ranking.
--
-- The changes compare with exactly one hour before. If the game was not in the ranking
-- then, or that hour was not collected, the change is unknown (null), never measured
-- against whatever older row happens to exist.

with hourly as (

    select
        hours.hour_at,
        players.snapshot_id,
        players.snapshot_at,
        players.appid,
        players.rank,
        players.concurrent_players
    from {{ ref('int_hourly_snapshots') }} as hours
    inner join {{ ref('stg_concurrent_players') }} as players
        on players.snapshot_id = hours.snapshot_id

)

select
    this_hour.hour_at,
    this_hour.appid,
    this_hour.snapshot_id,
    this_hour.snapshot_at,
    this_hour.rank,
    this_hour.concurrent_players,
    this_hour.concurrent_players - hour_before.concurrent_players as players_change_1h,
    -- Positive when the game climbed: from 5th to 3rd is 2 positions gained.
    hour_before.rank - this_hour.rank as positions_gained_1h
from hourly as this_hour
left join hourly as hour_before
    on hour_before.appid = this_hour.appid
    and hour_before.hour_at = this_hour.hour_at - interval 1 hour
