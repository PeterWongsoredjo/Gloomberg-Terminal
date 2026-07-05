from datetime import date

from pipeline.bronze import paths


def test_object_key_is_hive_partitioned() -> None:
    key = paths.object_key("idx_summary", "daily_trade", date(2026, 7, 2), "v3", "RUN123", 0)
    assert key == (
        "idx_summary/daily_trade/"
        "ingest_date=2026-07-02/source_version=v3/part-RUN123-0000.json.zst"
    )


def test_manifest_key_lives_under_manifests_prefix() -> None:
    key = paths.manifest_key("idx_summary", "daily_trade", date(2026, 7, 2), "RUN123")
    assert key == "_manifests/idx_summary/daily_trade/ingest_date=2026-07-02/RUN123.json"


def test_partition_uses_wib_trade_date_not_utc() -> None:
    # an instant at 23:30 UTC on the 1st is WIB the 2nd; the key must show the WIB date
    key = paths.object_key("idx_summary", "daily_trade", date(2026, 7, 2), "v1", "R", 3)
    assert "ingest_date=2026-07-02" in key
