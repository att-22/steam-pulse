{#
    Hours and days are always cut in UTC.

    The timestamps are instants (timestamptz), and DuckDB cuts an instant into hours and
    days using the time zone of whoever is connected. Left alone, the same snapshot would
    belong to one day on a laptop in Madrid and to another on a server in UTC. Moving to
    UTC first makes the answer the same everywhere.
#}

{% macro utc_hour(column) -%}
    date_trunc('hour', {{ column }} at time zone 'UTC') at time zone 'UTC'
{%- endmacro %}

{% macro utc_date(column) -%}
    cast({{ column }} at time zone 'UTC' as date)
{%- endmacro %}
