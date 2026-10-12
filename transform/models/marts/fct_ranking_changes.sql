-- One row per game that entered or left the ranking between two consecutive hours of
-- data.
--
-- "Consecutive" means the previous hour that was collected, which may be more than one
-- hour back: hours_since_previous says how far. The first hour ever has nothing before
-- it, so it produces no rows instead of a hundred false entries.

with hours as (

    select
        hour_at,
        lag(hour_at) over (order by hour_at) as previous_hour_at
    from {{ ref('int_hourly_snapshots') }}

),

pairs as (

    select *
    from hours
    where previous_hour_at is not null

),

games as (

    select
        hour_at,
        appid,
        rank,
        concurrent_players
    from {{ ref('fct_game_hourly') }}

),

entered as (

    select
        pairs.hour_at,
        pairs.previous_hour_at,
        games.appid,
        'entered' as change,
        games.rank,
        games.concurrent_players
    from pairs
    inner join games
        on games.hour_at = pairs.hour_at
    where not exists (
        select 1
        from games as before
        where before.hour_at = pairs.previous_hour_at
          and before.appid = games.appid
    )

),

departed as (

    select
        pairs.hour_at,
        pairs.previous_hour_at,
        games.appid,
        'left' as change,
        games.rank,
        games.concurrent_players
    from pairs
    inner join games
        on games.hour_at = pairs.previous_hour_at
    where not exists (
        select 1
        from games as after
        where after.hour_at = pairs.hour_at
          and after.appid = games.appid
    )

),

changes as (

    select * from entered
    union all
    select * from departed

)

select
    hour_at,
    previous_hour_at,
    date_diff('hour', previous_hour_at, hour_at) as hours_since_previous,
    appid,
    change,
    -- For a game that entered, where it is now. For one that left, where it last was.
    rank as rank_when_seen,
    concurrent_players as players_when_seen
from changes
