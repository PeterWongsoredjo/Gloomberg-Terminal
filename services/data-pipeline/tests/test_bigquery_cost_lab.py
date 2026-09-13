"""The cost lab compares layouts fairly and never builds from untrusted text."""

from __future__ import annotations

import pytest

from pipeline.bigquery import cost_lab
from pipeline.bigquery.cost_lab import Measurement, render
from pipeline.bigquery.target import BigQueryTarget

TARGET = BigQueryTarget(project="proj", location="asia-southeast2")


def test_render_compares_each_layout_against_flat() -> None:
    table = render(
        [
            Measurement("flat", "one week", 100_000_000, 100_000_000),
            Measurement("partitioned", "one week", 12_700_000, 12_700_000),
            Measurement("clustered_ticker", "one week", 100_000_000, 16_000_000),
        ]
    )
    assert "| one week | partitioned | 12.7 MB | 12.7 MB | 12.7% |" in table
    assert "| one week | clustered_ticker | 100.0 MB | 16.0 MB | 16.0% |" in table


def test_render_without_a_baseline_says_so() -> None:
    table = render([Measurement("partitioned", "one week", 1, 0)])
    assert table.endswith("| n/a |")


def test_every_layout_is_measured_against_a_flat_baseline() -> None:
    assert cost_lab.LAYOUTS["flat"] == ""
    assert "partition by trade_date" in cost_lab.LAYOUTS["partitioned"]


def test_the_ticker_travels_as_a_parameter_not_as_sql() -> None:
    """A ticker spliced into SQL text would be an injection path."""
    one_ticker = cost_lab.QUERIES["one week, one ticker"]
    assert "@ticker" in one_ticker
    assert "{table}" in one_ticker


def test_scaled_select_reads_the_gold_mirror() -> None:
    sql = cost_lab.scaled_select(TARGET, 3)
    assert "`proj.gloomberg_gold.fct_daily_trade`" in sql
    assert "generate_array(1, 3)" in sql


def test_zero_replicas_is_refused_before_touching_bigquery() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        cost_lab.build_lab(None, TARGET, 0)  # type: ignore[arg-type]
