-- Nobody can be playing without being connected, and Steam is never empty.
-- Returns the snapshots with totals that cannot be true.

select *
from {{ ref('stg_steam_users') }}
where users_online <= 0
   or users_in_game < 0
   or users_in_game > users_online
