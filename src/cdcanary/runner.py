"""Runner — wires config, adapters and checks together.

Schema pairs are expanded here at run time: list tables on both sides, apply
the pair's glob patterns, then run the pair's default checks (plus per-table
overrides) on every matched table. A table that matches the patterns but is
missing from the target is itself a FAIL — that's the table-level cousin of
schema drift, and it's how newly created source tables get noticed.

Adapter/check registries are plain dicts on purpose: adding a connector or a
check is "write the class, add one line here". No plugin framework until
someone actually needs one.
"""

from __future__ import annotations

from fnmatch import fnmatch

from cdcanary.adapters.base import Adapter
from cdcanary.checks.base import Check, CheckResult, Status
from cdcanary.config import Config, ConfigError, Pair

ADAPTERS: dict[str, type[Adapter]] = {}
CHECKS: dict[str, type[Check]] = {}


def _register_builtins() -> None:
    # Imported lazily so missing optional drivers only fail when actually used.
    from cdcanary.checks.freshness import FreshnessCheck
    from cdcanary.checks.null_rate import NullRateCheck
    from cdcanary.checks.row_delta import RowDeltaCheck
    from cdcanary.checks.sampled_checksum import SampledChecksumCheck
    from cdcanary.checks.schema_drift import SchemaDriftCheck

    for check in (RowDeltaCheck, FreshnessCheck, NullRateCheck, SchemaDriftCheck,
                  SampledChecksumCheck):
        CHECKS[check.name] = check

    from cdcanary.adapters.bigquery import BigQueryAdapter
    from cdcanary.adapters.mysql import MySQLAdapter
    from cdcanary.adapters.postgres import PostgresAdapter

    for adapter in (MySQLAdapter, PostgresAdapter, BigQueryAdapter):
        ADAPTERS[adapter.type_name] = adapter


def _make_adapter(config: Config, connection_name: str) -> Adapter:
    conn_cfg = config.connections[connection_name]
    type_name = conn_cfg.get("type")
    if type_name not in ADAPTERS:
        raise ConfigError(f"connection '{connection_name}': unknown type '{type_name}' "
                          f"(supported: {', '.join(sorted(ADAPTERS))})")
    return ADAPTERS[type_name](conn_cfg)


def select_tables(tables: list[str], patterns: list[str]) -> list[str]:
    """Glob include/exclude. "!" prefix excludes; excludes win over includes."""
    includes = [p for p in patterns if not p.startswith("!")] or ["*"]
    excludes = [p[1:] for p in patterns if p.startswith("!")]
    return [t for t in tables
            if any(fnmatch(t, p) for p in includes)
            and not any(fnmatch(t, p) for p in excludes)]


def _run_checks(pair_name: str, checks: dict[str, dict],
                src: Adapter, source_table: str,
                tgt: Adapter, target_table: str) -> list[CheckResult]:
    results = []
    for check_name, options in checks.items():
        if check_name not in CHECKS:
            results.append(CheckResult(check_name, pair_name, Status.ERROR,
                                       f"unknown check '{check_name}'"))
            continue
        check = CHECKS[check_name](options)
        try:
            results.append(check.run(pair_name, src, source_table, tgt, target_table))
        except Exception as e:  # data problems are results; this is infra
            results.append(CheckResult(check_name, pair_name, Status.ERROR,
                                       f"{type(e).__name__}: {e}"))
    return results


def _run_schema_pair(pair: Pair, src: Adapter, tgt: Adapter) -> list[CheckResult]:
    results: list[CheckResult] = []
    src_tables = select_tables(src.list_tables(pair.source.schema), pair.tables)
    tgt_tables = set(tgt.list_tables(pair.target.schema))

    for table in src_tables:
        label = f"{pair.name}.{table}"
        target_table = pair.target_table_for(table)
        if target_table not in tgt_tables:
            suffix = f" (looked for '{target_table}')" if target_table != table else ""
            results.append(CheckResult("table_presence", label, Status.FAIL,
                                       "table exists in source but not in target "
                                       f"(new table not yet replicated?){suffix}"))
            continue
        results.extend(_run_checks(
            label, pair.checks_for(table),
            src, f"{pair.source.schema}.{table}",
            tgt, f"{pair.target.schema}.{target_table}"))
    if not src_tables:
        results.append(CheckResult("table_presence", pair.name, Status.WARN,
                                   "no source tables matched the configured patterns"))
    return results


def run_pair(config: Config, pair: Pair) -> list[CheckResult]:
    try:
        with _make_adapter(config, pair.source.connection) as src, \
             _make_adapter(config, pair.target.connection) as tgt:
            if pair.is_schema_pair:
                return _run_schema_pair(pair, src, tgt)
            return _run_checks(pair.name, pair.checks,
                               src, pair.source.table, tgt, pair.target.table)
    except Exception as e:  # connection-level failure fails the whole pair
        failed = list(pair.checks) or ["connection"]
        return [CheckResult(name, pair.name, Status.ERROR,
                            f"connection failed: {type(e).__name__}: {e}")
                for name in failed]


def run(config: Config) -> list[CheckResult]:
    if not CHECKS:
        _register_builtins()
    results: list[CheckResult] = []
    for pair in config.pairs:
        results.extend(run_pair(config, pair))
    return results


def worst_status(results: list[CheckResult]) -> Status:
    order = [Status.OK, Status.WARN, Status.FAIL, Status.ERROR]
    return max((r.status for r in results), key=order.index, default=Status.OK)
