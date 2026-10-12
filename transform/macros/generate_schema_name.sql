{#
    dbt would name the schema of the staging models `main_staging`: the schema of the
    connection, then the one asked for. There is one warehouse per environment here, so
    the prefix separates nothing: use the schema as written.
#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {{ custom_schema_name | trim if custom_schema_name is not none else target.schema }}
{%- endmacro %}
