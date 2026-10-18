-- One row per game that has ever been in the ranking, with its details when there are any.
--
-- The list of games comes from the ranking, not from the details: a game enters the
-- ranking before its details can be asked for, and the store may never give them. Such a
-- game is still here, with its details null, so that no figure loses its game.

with seen as (

    select
        appid,
        min(snapshot_at) as first_seen_at,
        max(snapshot_at) as last_seen_at,
        min(rank) as best_rank,
        max(concurrent_players) as peak_players
    from {{ ref('stg_concurrent_players') }}
    group by appid

)

select
    seen.appid,
    -- Something to show on a chart even before the details arrive.
    coalesce(details.name, 'App ' || seen.appid) as display_name,
    details.name,
    details.app_type,
    details.is_free,
    details.release_date,
    details.release_date_text,
    details.coming_soon,
    details.genres,
    details.developers,
    details.publishers,
    details.base_appid,
    details.header_image_url,
    details.appid is not null as has_details,
    seen.first_seen_at,
    seen.last_seen_at,
    seen.best_rank,
    seen.peak_players
from seen
left join {{ ref('stg_app_details') }} as details
    on details.appid = seen.appid
