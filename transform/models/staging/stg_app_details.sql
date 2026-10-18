-- One row per game the store gave details for: what it is called and what kind of game it is.
--
-- The details are asked for once per game, so only what hardly ever changes is kept here.
-- Prices, discounts, review counts and scores are in the payload too, but they would be
-- frozen at the day of the request and read as if they were current.

with details as (

    select
        -- The store answers under the id that was asked for, which is the file name and the
        -- id the ranking uses.
        snapshot_id::integer as appid,
        collected_at,
        payload -> snapshot_id -> 'data' as data
    from {{ source('raw', 'snapshots') }}
    where source = 'app_details'
      and not rejected

)

select
    appid,
    data ->> 'name' as name,
    -- game, demo, mod, dlc, advertising... A demo or a mod can be among the most played.
    data ->> 'type' as app_type,
    (data ->> 'is_free')::boolean as is_free,
    -- Asked for in English, so released games read "May 16, 2011". Games not out yet carry
    -- free text ("Coming soon", "To be announced", or nothing): no date, the text is kept.
    try_strptime(data -> 'release_date' ->> 'date', '%b %d, %Y')::date as release_date,
    nullif(data -> 'release_date' ->> 'date', '') as release_date_text,
    (data -> 'release_date' ->> 'coming_soon')::boolean as coming_soon,
    -- Lists, as the store sends them. Null when the store lists none.
    list_transform(
        from_json(data -> 'genres', '[{"description": "varchar"}]'), genre -> genre.description
    ) as genres,
    from_json(data -> 'developers', '["varchar"]') as developers,
    from_json(data -> 'publishers', '["varchar"]') as publishers,
    -- For a demo or a mod: the game it belongs to.
    (data -> 'fullgame' ->> 'appid')::integer as base_appid,
    data ->> 'header_image' as header_image_url,
    collected_at as details_collected_at
from details
