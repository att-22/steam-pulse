-- Games that have been in the ranking for more than a day and still have no details.
--
-- The details of a new game are asked for in the same run that sees it, so a day without
-- them means the store keeps refusing, or that step of the collection keeps failing. It
-- warns rather than fails: the figures are still right, only a name is missing.
--
-- Measured against the latest snapshot, not the clock, so that old data gives the same
-- answer whenever it is built.

{{ config(severity='warn') }}

select
    appid,
    first_seen_at
from {{ ref('dim_games') }}
where not has_details
  and first_seen_at < (select max(last_seen_at) from {{ ref('dim_games') }}) - interval 1 day
