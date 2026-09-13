-- a float price column here would break the integer rupiah rule
select column_name, data_type
from `{{ target.project }}.{{ target.schema }}.INFORMATION_SCHEMA.COLUMNS`
where table_name = 'mart_foreign_flow_daily'
    and ends_with(column_name, '_idr')
    and data_type != 'INT64'
