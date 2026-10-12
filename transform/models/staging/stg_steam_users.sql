-- One row per snapshot: how many people Steam as a whole had online and in a game.

select
    snapshot_id,
    -- This source carries no time of its own: the data is as old as the question.
    collected_at as snapshot_at,
    -- Steam sends these as text with thousands separators: "38,882,059".
    -- try_cast: an unexpected format becomes a null that the tests catch, instead of an
    -- error that would take down every query on this view.
    try_cast(replace(payload ->> 'users_online', ',', '') as bigint) as users_online,
    try_cast(replace(payload ->> 'users_ingame', ',', '') as bigint) as users_in_game
from {{ source('raw', 'snapshots') }}
where source = 'steam_users'
  and not rejected
