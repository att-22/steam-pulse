-- Within a snapshot the positions are 1, 2, 3... with no gaps and no repeats.
-- Returns the snapshots where that does not hold.

select
    snapshot_id,
    count(*) as games,
    min(rank) as first_rank,
    max(rank) as last_rank,
    count(distinct rank) as different_ranks
from {{ ref('stg_concurrent_players') }}
group by snapshot_id
having min(rank) <> 1
    or max(rank) <> count(*)
    or count(distinct rank) <> count(*)
