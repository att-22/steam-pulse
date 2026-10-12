-- One row per game and UTC day in which the game was in the ranking at least once.

with hourly as (

    select
        {{ utc_date('hour_at') }} as day,
        *
    from {{ ref('fct_game_hourly') }}

),

-- How many hours of the day have data at all: without it, "12 hours in the ranking"
-- cannot be told apart from "in the ranking every hour that was collected".
coverage as (

    select
        {{ utc_date('hour_at') }} as day,
        count(*) as hours_collected
    from {{ ref('int_hourly_snapshots') }}
    group by 1

)

select
    hourly.day,
    hourly.appid,
    count(*) as hours_in_ranking,
    coverage.hours_collected,
    max(hourly.concurrent_players) as peak_players,
    arg_max(hourly.hour_at, hourly.concurrent_players) as peak_hour_at,
    -- Only over the hours it was in the ranking: its quiet hours are not in the data.
    avg(hourly.concurrent_players) as avg_players_while_ranked,
    min(hourly.rank) as best_rank
from hourly
inner join coverage
    on coverage.day = hourly.day
group by hourly.day, hourly.appid, coverage.hours_collected
