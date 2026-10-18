-- One row per game and hour in which the game was in the ranking.
--
-- The changes compare with exactly one hour before. If the game was not in the ranking
-- then, or that hour was not collected, the change is unknown (null), never measured
-- against whatever older row happens to exist.
--
-- Incremental: a run only rebuilds the most recent hours, not the whole history.
--   * Whole hours are replaced (unique_key is the hour, not hour and game). A later
--     snapshot can replace the one chosen for an hour, and its ranking may lack a game the
--     first one had; replacing row by row would leave that game behind as a ghost row.
--   * The last `lookback_hours` hours already built are rebuilt as well: that is where a
--     second snapshot of an hour, or a run that came late, can still change things.
--   * One more hour than that is read, so that the first hour rebuilt still finds the
--     hour before it.
-- Anything older than the window is never revisited: to change history, run with
-- --full-refresh (the Build workflow does it whenever the models change).

{{
    config(
        materialized='incremental',
        incremental_strategy='delete+insert',
        unique_key='hour_at',
        on_schema_change='fail',
    )
}}

{% set lookback %}interval {{ var('lookback_hours') }} hour{% endset %}

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
    {% if is_incremental() %}
    where hours.hour_at >= (select max(hour_at) from {{ this }}) - {{ lookback }} - interval 1 hour
    {% endif %}

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
{% if is_incremental() %}
where this_hour.hour_at >= (select max(hour_at) from {{ this }}) - {{ lookback }}
{% endif %}
