-- One row per game and snapshot: the ranking Steam returned, unfolded.
--
-- A game is only here while it is among the most played. When it drops out of the
-- ranking it has no row: that means "not reported", never "zero players".

with snapshots as (

    select
        snapshot_id,
        collected_at,
        payload -> 'response' as response
    from {{ source('raw', 'snapshots') }}
    where source = 'concurrent_players'
      and not rejected

),

games as (

    select
        snapshot_id,
        collected_at,
        -- Steam dates the ranking itself; the collector's clock only says when it asked.
        to_timestamp((response ->> 'last_update')::bigint) as snapshot_at,
        unnest(
            from_json(
                response -> 'ranks',
                '[{"rank": "integer", "appid": "integer",
                   "concurrent_in_game": "bigint", "peak_in_game": "bigint"}]'
            )
        ) as game
    from snapshots

)

select
    snapshot_id || '-' || game.appid as snapshot_game_id,
    snapshot_id,
    snapshot_at,
    collected_at,
    game.appid as appid,
    game.rank as rank,
    game.concurrent_in_game as concurrent_players,
    game.peak_in_game as peak_players
from games
