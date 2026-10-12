-- One row per hour: the ranking as a whole, next to the totals of all of Steam.

with ranking as (

    select
        hours.hour_at,
        hours.snapshot_id,
        hours.snapshot_at,
        hours.collected_at,
        count(*) as games_in_ranking,
        sum(players.concurrent_players) as ranked_players,
        arg_min(players.appid, players.rank) as top_appid,
        max(players.concurrent_players) as top_game_players
    from {{ ref('int_hourly_snapshots') }} as hours
    inner join {{ ref('stg_concurrent_players') }} as players
        on players.snapshot_id = hours.snapshot_id
    group by all

),

-- The totals have no time of their own, only the moment they were asked for, and each
-- run asks for the ranking and the totals seconds apart. So they are paired by when
-- they were collected: the totals asked for closest to the ranking, if within 5 minutes.
users as (

    select
        ranking.hour_at,
        users.users_online,
        users.users_in_game
    from ranking
    inner join {{ ref('stg_steam_users') }} as users
        on abs(epoch(users.snapshot_at) - epoch(ranking.collected_at)) <= 300
    qualify row_number() over (
        partition by ranking.hour_at
        order by abs(epoch(users.snapshot_at) - epoch(ranking.collected_at)), users.snapshot_id
    ) = 1

)

select
    ranking.hour_at,
    ranking.snapshot_id,
    ranking.snapshot_at,
    ranking.games_in_ranking,
    ranking.ranked_players,
    ranking.top_appid,
    ranking.top_game_players,
    users.users_online,
    users.users_in_game,
    -- How much of everything being played on Steam the ranking covers. Approximate: the
    -- ranking is a few minutes older than the totals it is compared with.
    ranking.ranked_players / nullif(users.users_in_game, 0) as ranked_share_of_in_game
from ranking
left join users
    on users.hour_at = ranking.hour_at
