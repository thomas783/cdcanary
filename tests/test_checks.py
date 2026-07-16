from datetime import datetime, timezone
from decimal import Decimal

from conftest import FakeAdapter

from cdcanary.checks.base import Status
from cdcanary.checks.freshness import FreshnessCheck
from cdcanary.checks.null_rate import NullRateCheck
from cdcanary.checks.row_delta import RowDeltaCheck
from cdcanary.checks.sampled_checksum import SampledChecksumCheck
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


class TestSampledChecksum:
    OPTS = {"key": "auto", "columns": ["amount", "status"]}

    def _fake(self, rows_by_key, **kw):
        return FakeAdapter(rows_by_key=rows_by_key, **kw)

    def test_identical_rows_pass(self):
        rows = {n: {"amount": Decimal("10.00"), "status": "paid"} for n in range(1, 6)}
        r = run(SampledChecksumCheck(self.OPTS), self._fake(rows), self._fake(dict(rows)))
        assert r.status is Status.OK
        assert r.metrics["sampled"] == 5

    def test_value_drift_names_row_and_column(self):
        src = {n: {"amount": Decimal("10.00"), "status": "paid"} for n in range(1, 6)}
        tgt = {n: dict(v) for n, v in src.items()}
        tgt[3]["amount"] = Decimal("10.07")  # lossy-cast style corruption
        r = run(SampledChecksumCheck(self.OPTS), self._fake(src), self._fake(tgt))
        assert r.status is Status.FAIL
        assert "amount" in r.message and "id=3" in r.message
        assert r.metrics["differing_rows"] == 1

    def test_missing_rows_at_target(self):
        src = {n: {"amount": Decimal("1"), "status": "paid"} for n in range(1, 6)}
        tgt = {n: v for n, v in src.items() if n <= 3}  # newest rows never arrived
        r = run(SampledChecksumCheck(self.OPTS), self._fake(src), self._fake(tgt))
        assert r.status is Status.FAIL
        assert "missing at target" in r.message
        assert r.metrics["missing_rows"] == 2

    def test_cross_engine_value_normalization(self):
        # int vs Decimal, tinyint(1) vs bool, tz-aware UTC vs naive — all equal
        src = {1: {"amount": 10, "flag": 1,
                   "at": datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)}}
        tgt = {1: {"amount": Decimal("10.00"), "flag": True,
                   "at": datetime(2026, 1, 1, 12, 0)}}
        opts = {"key": "auto", "columns": ["amount", "flag", "at"]}
        r = run(SampledChecksumCheck(opts), self._fake(src), self._fake(tgt))
        assert r.status is Status.OK

    def test_float_tolerance(self):
        src = {1: {"amount": 0.1 + 0.2, "status": "x"}}
        tgt = {1: {"amount": 0.3, "status": "x"}}
        r = run(SampledChecksumCheck(self.OPTS), self._fake(src), self._fake(tgt))
        assert r.status is Status.OK

    def test_no_primary_key_skips_quietly(self):
        r = run(SampledChecksumCheck({"key": "auto", "columns": ["amount"]}),
                self._fake({}, pk=None), self._fake({}))
        assert r.status is Status.OK
        assert r.metrics.get("skipped") is True


class TestSamplingStrategy:
    def _fake(self, n_rows, rows=0):
        data = {k: {"v": k * 10} for k in range(1, n_rows + 1)}
        return FakeAdapter(rows_by_key=data, rows=rows, columns={"id": "integer", "v": "integer"})

    def test_recent_is_a_window(self):
        src, tgt = self._fake(20), self._fake(20)
        opts = {"key": "auto", "columns": ["v"], "sample_size": 5, "strategy": "recent"}
        r = run(SampledChecksumCheck(opts), src, tgt)
        assert r.metrics["sampled"] == 5 and r.metrics["strategy"] == "recent"

    def test_mixed_reaches_old_rows(self):
        # recent half alone would only see keys 16..20 — mixed must dip lower
        src = self._fake(20, rows=20)
        opts = {"key": "auto", "columns": ["v"], "sample_size": 10, "strategy": "mixed"}
        check = SampledChecksumCheck(opts)
        keys, fallback = check._draw_sample(src, "src.t", "id", 10, "mixed")
        assert not fallback
        assert {16, 17, 18, 19, 20} <= set(keys)          # recent half always present
        assert min(keys) <= 12                             # spread half reaches the old zone

    def test_spread_falls_back_without_numeric_key(self):
        data = {f"k{i}": {"v": i} for i in range(5)}
        src = FakeAdapter(rows_by_key=data, columns={"id": "string", "v": "integer"})
        tgt = FakeAdapter(rows_by_key=dict(data), columns={"id": "string", "v": "integer"})
        opts = {"key": "auto", "columns": ["v"], "strategy": "spread"}
        r = run(SampledChecksumCheck(opts), src, tgt)
        assert r.status is Status.OK
        assert r.metrics["spread_fallback"] is True

    def test_unknown_strategy_errors(self):
        r = run(SampledChecksumCheck({"key": "auto", "strategy": "yolo", "columns": ["v"]}),
                self._fake(3), self._fake(3))
        assert r.status is Status.ERROR
