"""sampled_checksum — row *content* comparison on a deterministic sample.

The other checks count, aggregate, or diff structure; none of them notice a
row whose values are wrong: a lossy DECIMAL→FLOAT cast, a timezone shift, a
truncated string, a lost UPDATE. This check picks the newest N rows by key at
the source (CDC drift shows up in recent rows first), fetches the same keys on
both sides, and compares values column by column.

Comparison happens in Python, not SQL: with a small sample the transfer cost
is negligible, normalization lives in one place instead of a per-dialect
CAST/format matrix, and a mismatch can name the exact rows and columns —
"3/100 rows differ (cols: price; e.g. id=57)" instead of "checksums disagree".

Cross-engine value normalization (the value-level cousin of coarse types):
int/Decimal unify as Decimal, floats compare with tolerance, tz-aware
timestamps normalize to naive UTC, MySQL tinyint(1) equals a real bool,
bytes compare as bytes. Strings compare exactly — a changed string IS drift.

Sampling strategy — honest names for what each one is:
    recent  newest N by key. An index-cheap *window*, not a statistical
            sample: it never revisits old rows, so a lost UPDATE on last
            year's row stays invisible forever.
    spread  MOD(key, B) = r with r rotated per run — a slice across the whole
            key range; repeated runs walk the entire table. Numeric keys only
            (falls back to recent otherwise). This is the statistical half.
    mixed   half recent + half spread (default): recent rows are where CDC
            breakage concentrates, spread covers everything else eventually.

The sample is only ever drawn at the *source*; the target is fetched by
explicit key list, so run-to-run variation in the sample never breaks
source/target comparability.

Options:
    key: single-column key for sampling, or "auto" to use the source's
         primary key (BigQuery has no enforced keys — set explicitly;
         auto quietly skips when no usable key is found)
    strategy: recent | spread | mixed (default mixed)
    columns: list to compare, or "auto" for every column on both sides
    ignore_columns: columns to skip (CDC bookkeeping etc.)
    sample_size: rows to sample (default 100)
    max_mismatch_pct: FAIL when (differing + missing) rows exceed this share
                      of the sample (default 0 — content drift is never fine)
"""

from __future__ import annotations

import math
import random
from datetime import datetime, timezone
from decimal import Decimal

from cdcanary.adapters.base import Adapter
from cdcanary.checks.base import Check, CheckResult, Status


def _normalize(value):
    if isinstance(value, (bool, int)):  # bool first-class: tinyint(1) ↔ real bool
        return Decimal(int(value))
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(timezone.utc).replace(tzinfo=None)
        return value
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value)
    return value


def values_equal(a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, float) or isinstance(b, float):
        try:
            return math.isclose(float(a), float(b), rel_tol=1e-9, abs_tol=1e-9)
        except (TypeError, ValueError):
            return False  # float vs non-numeric — genuinely different
    a, b = _normalize(a), _normalize(b)
    if type(a) is not type(b):
        return False  # after normalization a type mismatch is drift, not noise
    return a == b


class SampledChecksumCheck(Check):
    name = "sampled_checksum"

    def _draw_sample(self, source: Adapter, source_table: str, key: str,
                     n: int, strategy: str) -> tuple[list, bool]:
        """Key sample per strategy. Returns (keys, spread_fell_back_to_recent)."""
        spread_ok = source.schema(source_table).get(key) == "integer"
        if strategy == "recent" or not spread_ok:
            return source.sample_keys(source_table, key, n), strategy != "recent"

        n_recent = n // 2 if strategy == "mixed" else 0
        n_spread = n - n_recent
        # bucket count ≈ rows/sample so one remainder class is one sample's
        # worth; the rotating remainder makes successive runs walk the table
        modulus = max(1, source.row_count(source_table) // max(1, n_spread))
        remainder = random.randrange(modulus)
        keys = list(source.sample_keys(source_table, key, n_recent)) if n_recent else []
        seen = set(keys)
        for k in source.sample_keys_spread(source_table, key, n_spread, modulus, remainder):
            if k not in seen:
                keys.append(k)
                seen.add(k)
        return keys, False

    def run(self, pair_name: str, source: Adapter, source_table: str,
            target: Adapter, target_table: str) -> CheckResult:
        key = self.options.get("key", "auto")
        if key == "auto":
            key = source.primary_key(source_table)
            if key is None:
                return CheckResult(self.name, pair_name, Status.OK,
                                   "skipped — no single-column primary key at source "
                                   "(set `key:` explicitly)", {"skipped": True})

        columns = self.options.get("columns", "auto")
        if columns == "auto":
            columns = sorted(set(source.schema(source_table)) & set(target.schema(target_table)))
        ignore = set(self.options.get("ignore_columns", ())) | {key}
        columns = [c for c in columns if c not in ignore]
        if not columns:
            return CheckResult(self.name, pair_name, Status.OK,
                               "skipped — no shared columns to compare", {"skipped": True})

        sample_size = int(self.options.get("sample_size", 100))
        max_mismatch_pct = float(self.options.get("max_mismatch_pct", 0))
        strategy = self.options.get("strategy", "mixed")
        if strategy not in ("recent", "spread", "mixed"):
            return CheckResult(self.name, pair_name, Status.ERROR,
                               f"unknown strategy '{strategy}' (recent | spread | mixed)", {})

        keys, spread_fallback = self._draw_sample(
            source, source_table, key, sample_size, strategy)
        if not keys:
            return CheckResult(self.name, pair_name, Status.OK,
                               "source table empty — nothing to compare", {})
        src_rows = source.fetch_rows(source_table, key, keys, columns)
        tgt_rows = target.fetch_rows(target_table, key, keys, columns)

        missing = [k for k in keys if k in src_rows and k not in tgt_rows]
        differing: dict = {}  # key -> [column, ...]
        for k in keys:
            if k not in src_rows or k not in tgt_rows:
                continue
            bad = [c for c in columns
                   if not values_equal(src_rows[k].get(c), tgt_rows[k].get(c))]
            if bad:
                differing[k] = bad

        mismatch_pct = (len(differing) + len(missing)) / len(keys) * 100
        drifted_columns = sorted({c for cols in differing.values() for c in cols})
        metrics = {"sampled": len(keys), "key": key, "strategy": strategy,
                   "spread_fallback": spread_fallback,
                   "differing_rows": len(differing), "missing_rows": len(missing),
                   "mismatch_pct": round(mismatch_pct, 2),
                   "drifted_columns": drifted_columns,
                   "example_keys": [str(k) for k in list(differing)[:5]]}

        if mismatch_pct <= max_mismatch_pct:
            return CheckResult(self.name, pair_name, Status.OK,
                               f"row contents match on {len(keys)} sampled rows (by {key})",
                               metrics)
        problems = []
        if differing:
            example = next(iter(differing))
            problems.append(f"{len(differing)}/{len(keys)} sampled rows differ "
                            f"(cols: {', '.join(drifted_columns)}; e.g. {key}={example})")
        if missing:
            problems.append(f"{len(missing)}/{len(keys)} sampled rows missing at target")
        return CheckResult(self.name, pair_name, Status.FAIL,
                           "row content drift: " + "; ".join(problems), metrics)
