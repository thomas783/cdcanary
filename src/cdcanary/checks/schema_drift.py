"""schema_drift — column set and coarse-type comparison.

Cross-engine schema comparison uses each adapter's coarse-type mapping
(see adapters.base.COARSE_TYPES), so MySQL BIGINT vs BigQuery INT64 agree as
"integer" while a genuine INT → STRING migration still trips the wire.

Missing-in-target columns are the early warning for NULL corruption: the
column exists at the source but the connector hasn't propagated it yet —
every row replicated from now on is silently NULL. That's a FAIL, not a WARN.

Options:
    ignore_columns: columns to skip (e.g. CDC bookkeeping like
                    datastream_metadata, _fivetran_synced)
"""

from __future__ import annotations

from cdcanary.adapters.base import Adapter
from cdcanary.checks.base import Check, CheckResult, Status


class SchemaDriftCheck(Check):
    name = "schema_drift"

    def run(self, pair_name: str, source: Adapter, source_table: str,
            target: Adapter, target_table: str) -> CheckResult:
        ignore = set(self.options.get("ignore_columns", []))
        src = {c: t for c, t in source.schema(source_table).items() if c not in ignore}
        tgt = {c: t for c, t in target.schema(target_table).items() if c not in ignore}

        missing_in_target = sorted(set(src) - set(tgt))
        extra_in_target = sorted(set(tgt) - set(src))
        type_changed = sorted(c for c in set(src) & set(tgt) if src[c] != tgt[c])

        metrics = {
            "missing_in_target": missing_in_target,
            "extra_in_target": extra_in_target,
            "type_changed": {c: {"source": src[c], "target": tgt[c]} for c in type_changed},
        }

        problems = []
        if missing_in_target:
            problems.append(f"missing in target: {', '.join(missing_in_target)} "
                            "(rows replicating as NULL right now)")
        if type_changed:
            problems.append("type drift: " + ", ".join(
                f"{c} {src[c]}→{tgt[c]}" for c in type_changed))

        if problems:
            return CheckResult(self.name, pair_name, Status.FAIL, "; ".join(problems), metrics)
        if extra_in_target:
            return CheckResult(self.name, pair_name, Status.WARN,
                               f"extra columns in target: {', '.join(extra_in_target)}", metrics)
        return CheckResult(self.name, pair_name, Status.OK,
                           f"schemas agree ({len(src)} columns)", metrics)
