-- The games in the ranking are part of everything being played, so together they cannot
-- have more players than Steam has people in a game. The two figures are a few minutes
-- apart, so a small excess is possible: this warns, it does not fail the build.

{{ config(severity='warn') }}

select *
from {{ ref('fct_platform_hourly') }}
where ranked_players > users_in_game
