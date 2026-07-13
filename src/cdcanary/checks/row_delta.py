"""row_delta — source vs target row count.

Options:
    tolerance_pct: allowed relative difference in percent (default 0.0)
    where: optional SQL predicate applied to BOTH sides to bound the
           comparison window (e.g. "created_at < CURRENT_DATE" to exclude
           rows still in flight). Must be valid in both dialects — keep it
           to ANSI basics.
"""

from __future__ import annotations

from cdcanary.adapters.base import Adapter
from cdcanary.checks.base import Check, CheckResult, Status


class RowDeltaCheck(Check):
    name = "row_delta"

    def run(self, pair_name: str, source: Adapter, source_table: str,
            target: Adapter, target_table: str) -> CheckResult:
        where = self.options.get("where")
        tolerance_pct = float(self.options.get("tolerance_pct", 0.0))

        src_n = source.row_count(source_table, where)
        tgt_n = target.row_count(target_table, where)
        delta = tgt_n - src_n
        base = max(src_n, 1)
        delta_pct = abs(delta) / base * 100

        metrics = {"source_rows": src_n, "target_rows": tgt_n,
                   "delta": delta, "delta_pct": round(delta_pct, 4)}

        if delta_pct <= tolerance_pct:
            return CheckResult(self.name, pair_name, Status.OK,
                               f"rows match: {src_n:,} vs {tgt_n:,} (Δ{delta_pct:.2f}%)", metrics)
        return CheckResult(self.name, pair_name, Status.FAIL,
                           f"row count drift: source {src_n:,} vs target {tgt_n:,} "
                           f"(Δ{delta:+,} / {delta_pct:.2f}% > {tolerance_pct}%)", metrics)
