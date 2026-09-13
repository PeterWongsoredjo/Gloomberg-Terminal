"""
Measures what partitioning and clustering save on a scaled copy of Gold.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass

from google.cloud import bigquery

from pipeline.bigquery.target import GOLD_DATASET, LAB_DATASET, BigQueryTarget, target_from_env

# same rows three ways, so only the layout differs
LAYOUTS: dict[str, str] = {
    "flat": "",
    "clustered_ticker": "cluster by ticker",
    "clustered_date_ticker": "cluster by trade_date, ticker",
    "partitioned": "partition by trade_date",
    "partitioned_clustered": "partition by trade_date cluster by ticker",
}

_WEEK = (
    "trade_date between date_sub(current_date('Asia/Jakarta'), interval 6 day) "
    "and current_date('Asia/Jakarta')"
)

QUERIES: dict[str, str] = {
    "one week, every ticker": (
        f"select ticker, avg(close_idr) as avg_close_idr from `{{table}}` where {_WEEK} group by ticker"
    ),
    "one week, one ticker": (
        f"select avg(close_idr) as avg_close_idr from `{{table}}` where {_WEEK} and ticker = @ticker"
    ),
}


@dataclass(frozen=True)
class Measurement:
    """What one query cost on one layout, estimated and actual."""

    layout: str
    query: str
    estimated_bytes: int
    scanned_bytes: int


def scaled_select(target: BigQueryTarget, replicas: int) -> str:
    """Every listed ticker, every day of the last 55, repeated per replica."""
    gold = target.dataset_id(GOLD_DATASET)
    return f"""
        with latest as (
            select security_id, close_idr, volume_shares, value_idr, net_foreign_idr
            from `{gold}.fct_daily_trade`
            qualify row_number() over (partition by security_id order by trade_date desc) = 1
        ),
        listed as (
            select s.ticker, l.*
            from latest l
            join `{gold}.dim_security` s using (security_id)
            where s.is_current
        )
        select
            day as trade_date,
            listed.ticker,
            listed.security_id,
            replica,
            listed.close_idr + mod(abs(farm_fingerprint(format('%s|%t|%d', listed.ticker, day, replica))), 100)
                as close_idr,
            listed.volume_shares,
            listed.value_idr,
            listed.net_foreign_idr
        from listed
        cross join unnest(generate_date_array(
            date_sub(current_date('Asia/Jakarta'), interval 54 day), current_date('Asia/Jakarta')
        )) as day
        cross join unnest(generate_array(1, {int(replicas)})) as replica
    """


def build_lab(client: bigquery.Client, target: BigQueryTarget, replicas: int) -> int:
    """Creates the three lab tables and returns how many rows each holds."""
    if replicas < 1:
        raise ValueError("replicas must be at least 1")
    dataset = bigquery.Dataset(target.dataset_id(LAB_DATASET))
    dataset.location = target.location
    client.create_dataset(dataset, exists_ok=True)
    lab = target.dataset_id(LAB_DATASET)
    client.query(f"create or replace table `{lab}.trades_flat` as {scaled_select(target, replicas)}").result()
    for layout, clause in LAYOUTS.items():
        if layout == "flat":
            continue
        client.query(
            f"create or replace table `{lab}.trades_{layout}` {clause} as select * from `{lab}.trades_flat`"
        ).result()
    return int(client.get_table(f"{lab}.trades_flat").num_rows or 0)


def _job_config(ticker: str, dry_run: bool) -> bigquery.QueryJobConfig:
    """Uncached, so every run pays its own scan and the numbers are honest."""
    return bigquery.QueryJobConfig(
        dry_run=dry_run,
        use_query_cache=False,
        query_parameters=[bigquery.ScalarQueryParameter("ticker", "STRING", ticker)],
    )


def measure(client: bigquery.Client, target: BigQueryTarget, ticker: str) -> list[Measurement]:
    """Runs each query on each layout, keeping the estimate beside the actual."""
    lab = target.dataset_id(LAB_DATASET)
    results: list[Measurement] = []
    for label, template in QUERIES.items():
        for layout in LAYOUTS:
            sql = template.format(table=f"{lab}.trades_{layout}")
            estimate = client.query(sql, job_config=_job_config(ticker, dry_run=True))
            job = client.query(sql, job_config=_job_config(ticker, dry_run=False))
            job.result()
            results.append(
                Measurement(
                    layout=layout,
                    query=label,
                    estimated_bytes=int(estimate.total_bytes_processed or 0),
                    scanned_bytes=int(job.total_bytes_processed or 0),
                )
            )
    return results


def render(measurements: list[Measurement]) -> str:
    """A markdown table, each layout compared against the flat baseline."""
    baseline = {m.query: m.scanned_bytes for m in measurements if m.layout == "flat"}
    lines = [
        "| query | layout | dry run estimate | actually scanned | vs flat |",
        "|---|---|---:|---:|---:|",
    ]
    for m in measurements:
        flat = baseline.get(m.query) or 0
        saved = f"{m.scanned_bytes / flat:.1%}" if flat else "n/a"
        lines.append(
            f"| {m.query} | {m.layout} | {m.estimated_bytes / 1e6:,.1f} MB "
            f"| {m.scanned_bytes / 1e6:,.1f} MB | {saved} |"
        )
    return "\n".join(lines)


def drop_lab(client: bigquery.Client, target: BigQueryTarget) -> None:
    """Removes the lab tables once measured."""
    client.delete_dataset(target.dataset_id(LAB_DATASET), delete_contents=True, not_found_ok=True)


def main() -> None:
    """CLI: build the scaled lab, measure it, print the table, clean up."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replicas", type=int, default=40, help="copies per ticker per day")
    parser.add_argument("--ticker", default="BBRI", help="ticker for the single-ticker query")
    parser.add_argument("--keep", action="store_true", help="leave the lab tables in place")
    args = parser.parse_args()

    target = target_from_env()
    if target is None:
        raise SystemExit("BQ_PROJECT is not set, BigQuery is off")
    client = target.client()
    try:
        rows = build_lab(client, target, args.replicas)
        print(f"lab rows per layout: {rows:,}\n")
        print(render(measure(client, target, args.ticker)))
    finally:
        if not args.keep:
            drop_lab(client, target)


if __name__ == "__main__":
    main()
