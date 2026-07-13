"""null_rate — source vs target NULL fraction per column.

This is the schema-drift-corruption catcher. When a column is added to the
source mid-stream and the CDC connector picks up the schema late, rows
replicated in that window land with NULLs the source never had. The table
looks healthy — counts match, freshness is fine — but a column is silently
rotten. Comparing null fractions on both sides exposes exactly that.

(Motivating incident: a Datastream column backfill left 80 rows NULL in the
replica while the source had none. Nobody noticed for three months.)

Options:
    columns: list of columns to compare, or "auto" to compare every column
             that exists on both sides (discovered schema pairs default to
             "auto"; costs one aggregate query per column — exclude very
             large tables via overrides if that matters)
    max_diff_pp: FAIL when |target% - source%| exceeds this, in percentage
                 points (default 1.0)
"""

from __future__ import annotations

from cdcanary.adapters.base import Adapter
from cdcanary.checks.base import Check, CheckResult, Status


class NullRateCheck(Check):
    name = "null_rate"

    def run(self, pair_name: str, source: Adapter, source_table: str,
            target: Adapter, target_table: str) -> CheckResult:
        columns = self.options.get("columns", "auto")
        if columns == "auto":
            shared = set(source.schema(source_table)) & set(target.schema(target_table))
            columns = sorted(shared)
            if not columns:
                return CheckResult(self.name, pair_name, Status.OK,
                                   "skipped — no shared columns", {"skipped": True})
        max_diff_pp = float(self.options.get("max_diff_pp", 1.0))

        offenders, metrics = [], {}
        for col in columns:
            src_pct = source.null_fraction(source_table, col) * 100
            tgt_pct = target.null_fraction(target_table, col) * 100
            diff_pp = tgt_pct - src_pct
            metrics[col] = {"source_null_pct": round(src_pct, 3),
                            "target_null_pct": round(tgt_pct, 3),
                            "diff_pp": round(diff_pp, 3)}
            if abs(diff_pp) > max_diff_pp:
                offenders.append(f"{col} (src {src_pct:.1f}% vs tgt {tgt_pct:.1f}%)")

        if not offenders:
            return CheckResult(self.name, pair_name, Status.OK,
                               f"null rates match on {len(columns)} column(s)", metrics)
        return CheckResult(self.name, pair_name, Status.FAIL,
                           "null-rate drift: " + ", ".join(offenders), metrics)
