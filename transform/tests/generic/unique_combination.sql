{#
    Fails when two rows share the same values in the given columns.
    dbt's own `unique` test looks at one column; the rows here are identified by two.
#}

{% test unique_combination(model, columns) %}

select
    {{ columns | join(', ') }},
    count(*) as times
from {{ model }}
group by {{ columns | join(', ') }}
having count(*) > 1

{% endtest %}
