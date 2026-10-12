-- The ranking is by players right now: a game cannot have more players than the one
-- ranked just above it, nor a negative count. Returns the rows that break the order.

with ordered as (

    select
        snapshot_id,
        appid,
        rank,
        concurrent_players,
        lag(concurrent_players) over (partition by snapshot_id order by rank) as players_above
    from {{ ref('stg_concurrent_players') }}

)

select *
from ordered
where concurrent_players < 0
   or concurrent_players > players_above
