"""freshness — replication lag between source and target.

Compares MAX(column) on both sides. Lag = source_max - target_max: how far the
replica is behind the origin. Comparing against the source (not wall clock)
means a naturally quiet table doesn't false-alarm at night.

Options:
    column: timestamp column to compare, or "auto" to pick the first
            column of coarse type timestamp/date matching column_candidates
            (discovered schema pairs use "auto" by default)
    column_candidates: candidate names for "auto", in priority order
    max_lag_minutes: FAIL above this (default 60)
    warn_lag_minutes: WARN above this (default: half of max_lag_minutes)
"""

from __future__ import annotations

from cdcanary.adapters.base import Adapter
from cdcanary.checks.base import Check, CheckResult, Status

AUTO_COLUMN_CANDIDATES = ("updated_at", "modified_at", "updated_dt",
                          "created_at", "created_dt", "inserted_at")


class FreshnessCheck(Check):
    name = "freshness"

    def _resolve_column(self, source: Adapter, source_table: str) -> str | None:
        candidates = self.options.get("column_candidates", AUTO_COLUMN_CANDIDATES)
        schema = source.schema(source_table)
        for name in candidates:
            if schema.get(name) in ("timestamp", "date"):
                return name
        return None

    def run(self, pair_name: str, source: Adapter, source_table: str,
            target: Adapter, target_table: str) -> CheckResult:
        column = self.options.get("column", "auto")
        if column == "auto":
            column = self._resolve_column(source, source_table)
            if column is None:
                return CheckResult(self.name, pair_name, Status.OK,
                                   "skipped — no timestamp column found for auto mode",
                                   {"skipped": True})
        max_lag_minutes = float(self.options.get("max_lag_minutes", 60))
        warn_lag_minutes = float(self.options.get("warn_lag_minutes", max_lag_minutes / 2))

        source_max = source.max_timestamp(source_table, column)
        target_max = target.max_timestamp(target_table, column)

        if source_max is None:
            return CheckResult(self.name, pair_name, Status.OK,
                               "source table empty — nothing to replicate", {})
        if target_max is None:
            return CheckResult(self.name, pair_name, Status.FAIL,
                               f"target has no rows but source max({column})={source_max}", {})

        lag_minutes = (source_max - target_max).total_seconds() / 60
        metrics = {"source_max": source_max.isoformat(), "target_max": target_max.isoformat(),
                   "lag_minutes": round(lag_minutes, 1)}

        if lag_minutes > max_lag_minutes:
            status, verdict = Status.FAIL, f"> {max_lag_minutes:.0f}m limit"
        elif lag_minutes > warn_lag_minutes:
            status, verdict = Status.WARN, f"> {warn_lag_minutes:.0f}m warning"
        else:
            status, verdict = Status.OK, "within limit"
        return CheckResult(self.name, pair_name, status,
                           f"replication lag {lag_minutes:.0f}m ({verdict})", metrics)
