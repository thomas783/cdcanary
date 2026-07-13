from datetime import datetime

from conftest import FakeAdapter

from cdcanary.checks.base import Status
from cdcanary.checks.freshness import FreshnessCheck
from cdcanary.checks.null_rate import NullRateCheck
from cdcanary.checks.row_delta import RowDeltaCheck
from cdcanary.checks.schema_drift import SchemaDriftCheck


def run(check, src, tgt):
    return check.run("pair", src, "src.t", tgt, "tgt.t")


class TestRowDelta:
    def test_match_within_tolerance(self):
        r = run(RowDeltaCheck({"tolerance_pct": 0.5}),
                FakeAdapter(rows=1000), FakeAdapter(rows=996))
        assert r.status is Status.OK

    def test_drift_beyond_tolerance(self):
        r = run(RowDeltaCheck({"tolerance_pct": 0.5}),
                FakeAdapter(rows=1000), FakeAdapter(rows=900))
        assert r.status is Status.FAIL
        assert r.metrics["delta"] == -100


class TestFreshness:
    def test_within_limit(self):
        r = run(FreshnessCheck({"column": "updated_at", "max_lag_minutes": 60}),
                FakeAdapter(max_ts=datetime(2026, 7, 14, 12, 20)),
                FakeAdapter(max_ts=datetime(2026, 7, 14, 12, 0)))
        assert r.status is Status.OK

    def test_lag_beyond_limit(self):
        r = run(FreshnessCheck({"column": "updated_at", "max_lag_minutes": 60}),
                FakeAdapter(max_ts=datetime(2026, 7, 14, 12, 0)),
                FakeAdapter(max_ts=datetime(2026, 7, 13, 12, 0)))
        assert r.status is Status.FAIL
        assert r.metrics["lag_minutes"] == 1440.0

    def test_empty_target_with_data_at_source(self):
        r = run(FreshnessCheck({"column": "updated_at"}),
                FakeAdapter(max_ts=datetime(2026, 7, 14)), FakeAdapter(max_ts=None))
        assert r.status is Status.FAIL


class TestNullRate:
    def test_datastream_drift_incident(self):
        """The motivating bug: source has no NULLs, replica grew 5.9% NULLs."""
        r = run(NullRateCheck({"columns": ["sale_status"], "max_diff_pp": 1.0}),
                FakeAdapter(null_fractions={"sale_status": 0.0}),
                FakeAdapter(null_fractions={"sale_status": 0.059}))
        assert r.status is Status.FAIL
        assert "sale_status" in r.message

    def test_equal_null_rates_pass(self):
        r = run(NullRateCheck({"columns": ["status"], "max_diff_pp": 1.0}),
                FakeAdapter(null_fractions={"status": 0.02}),
                FakeAdapter(null_fractions={"status": 0.021}))
        assert r.status is Status.OK


class TestSchemaDrift:
    def test_coarse_types_agree_across_engines(self):
        r = run(SchemaDriftCheck({}),
                FakeAdapter(columns={"id": "integer", "name": "string"}),
                FakeAdapter(columns={"id": "integer", "name": "string"}))
        assert r.status is Status.OK

    def test_missing_column_in_target_fails(self):
        r = run(SchemaDriftCheck({}),
                FakeAdapter(columns={"id": "integer", "sale_status": "string"}),
                FakeAdapter(columns={"id": "integer"}))
        assert r.status is Status.FAIL
        assert "sale_status" in r.message

    def test_cdc_bookkeeping_columns_ignored(self):
        r = run(SchemaDriftCheck({"ignore_columns": ["datastream_metadata"]}),
                FakeAdapter(columns={"id": "integer"}),
                FakeAdapter(columns={"id": "integer", "datastream_metadata": "json"}))
        assert r.status is Status.OK

    def test_type_change_fails(self):
        r = run(SchemaDriftCheck({}),
                FakeAdapter(columns={"id": "integer"}),
                FakeAdapter(columns={"id": "string"}))
        assert r.status is Status.FAIL
