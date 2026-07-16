"""CLI.

Three levels of commitment:
    cdcanary scan  --source URL --target URL   # zero config, one-off diagnosis
    cdcanary init  --discover ...              # generate a rule-based config
    cdcanary check -c cdcanary.yml             # the cron job

Exit codes are the cron contract: 0 all green, 1 warnings, 2 failures/errors.
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path

import click

from cdcanary import config as config_mod
from cdcanary import runner, urls
from cdcanary.alert import slack
from cdcanary.checks.base import CheckResult, Status

_STATUS_MARK = {Status.OK: "✅ ok  ", Status.WARN: "🟡 warn",
                Status.FAIL: "🔴 FAIL", Status.ERROR: "⚠️ ERR "}

DEFAULT_CHECKS = {
    "row_delta": {"tolerance_pct": 0.5},
    "freshness": {"column": "auto", "max_lag_minutes": 60},
    "null_rate": {"columns": "auto", "max_diff_pp": 1.0},
    "schema_drift": {},
    # quietly skips tables without a single-column primary key
    "sampled_checksum": {"key": "auto", "sample_size": 100},
}

EXAMPLE_CONFIG = """\
connections:
  mysql_prod:
    type: mysql            # mysql | postgres | bigquery
    host: prod-db.internal
    database: shop
    user: readonly
    password: env:MYSQL_PASSWORD

  bq_raw:
    type: bigquery
    project: my-project
    credentials: env:GOOGLE_APPLICATION_CREDENTIALS

pairs:
  # Schema pair — tables are discovered on every run and get `defaults`;
  # new tables at the source enter monitoring without config edits.
  - name: shop
    source: { connection: mysql_prod, schema: shop }
    target: { connection: bq_raw, schema: raw_shop }
    tables: ["*", "!tmp_*"]
    # target_table: "shop_{table}"   # uncomment if the CDC tool renames tables
    defaults:
      row_delta:        { tolerance_pct: 0.5 }
      freshness:        { column: auto, max_lag_minutes: 60 }
      null_rate:        { columns: auto, max_diff_pp: 1.0 }
      schema_drift:     { ignore_columns: [datastream_metadata] }
      sampled_checksum: { key: auto, sample_size: 100 }   # strategy: mixed — newest half + rotating spread
    overrides:                       # applied top-to-bottom; later rules win
      - match: "log_*"               # glob, exact name, or a list of either
        checks:
          null_rate: false           # too big — skip the expensive check
      - match: orders
        checks:
          freshness: { column: paid_at, max_lag_minutes: 30 }

  # Single-table pair — hand-tuned checks for one table (source/target names
  # may differ). Mixes freely with schema pairs.
  - name: payments
    source: { connection: mysql_prod, table: shop.payments }
    target: { connection: bq_raw, table: raw_shop.payments_v2 }
    checks:
      row_delta: { tolerance_pct: 0.1, where: "created_at < CURRENT_DATE" }
      freshness: { column: paid_at, max_lag_minutes: 15 }
      null_rate: { columns: [amount, status], max_diff_pp: 0.5 }

alerts:
  slack_webhook: env:CDCANARY_SLACK_WEBHOOK
"""


def _print_results(results: list[CheckResult], as_json: bool) -> None:
    if as_json:
        click.echo(json.dumps([asdict(r) for r in results], default=str, ensure_ascii=False))
        return
    width = max((len(r.pair) for r in results), default=4)
    cwidth = max((len(r.check) for r in results), default=10)
    for r in results:
        click.echo(f"{_STATUS_MARK[r.status]}  {r.pair:<{width}}  {r.check:<{cwidth}}  {r.message}")


def _scan_config(source: str, target: str, tables: tuple[str, ...]) -> config_mod.Config:
    src_conn, src_ns = urls.parse(source)
    tgt_conn, tgt_ns = urls.parse(target)
    return config_mod.from_dict({
        "connections": {"source": src_conn, "target": tgt_conn},
        "pairs": [{
            "name": src_ns,
            "source": {"connection": "source", "schema": src_ns},
            "target": {"connection": "target", "schema": tgt_ns},
            "tables": list(tables) or ["*"],
            "defaults": DEFAULT_CHECKS,
        }],
    })


@click.group()
@click.version_option(package_name="cdcanary")
def main() -> None:
    """🐤 CDCanary — catches silent CDC replication drift."""


@main.command()
@click.option("--source", required=True, help="e.g. mysql://user:pass@host/shop")
@click.option("--target", required=True, help="e.g. bigquery://my-project/raw_shop")
@click.option("-t", "--tables", multiple=True, help='glob filter, "!" excludes (repeatable)')
@click.option("--json", "as_json", is_flag=True)
def scan(source: str, target: str, tables: tuple[str, ...], as_json: bool) -> None:
    """One-off diagnosis with zero config: discover common tables, run default
    checks, print what disagrees."""
    try:
        cfg = _scan_config(source, target, tables)
    except config_mod.ConfigError as e:
        raise click.ClickException(str(e)) from e
    results = runner.run(cfg)
    _print_results(results, as_json)
    sys.exit(runner.worst_status(results).exit_code)


@main.command()
@click.option("-o", "--output", default="cdcanary.yml", show_default=True)
@click.option("--discover", is_flag=True,
              help="introspect --source/--target and generate a config for them")
@click.option("--source", help="required with --discover")
@click.option("--target", help="required with --discover")
def init(output: str, discover: bool, source: str | None, target: str | None) -> None:
    """Write a config file — a commented example, or one generated from your
    actual databases with --discover."""
    path = Path(output)
    if path.exists():
        raise click.ClickException(f"{output} already exists — not overwriting")

    if not discover:
        path.write_text(EXAMPLE_CONFIG, encoding="utf-8")
        click.echo(f"wrote {output} — edit it, then run: cdcanary check -c {output}")
        return

    if not (source and target):
        raise click.ClickException("--discover needs --source and --target URLs")
    import yaml

    src_conn, src_ns = urls.parse(source)
    tgt_conn, tgt_ns = urls.parse(target)
    for conn, var in ((src_conn, "CDCANARY_SOURCE_PASSWORD"),
                      (tgt_conn, "CDCANARY_TARGET_PASSWORD")):
        if conn.pop("password", ""):
            conn["password"] = f"env:{var}"  # never write secrets to disk

    generated = {
        "connections": {"source": src_conn, "target": tgt_conn},
        "pairs": [{
            "name": src_ns,
            "source": {"connection": "source", "schema": src_ns},
            "target": {"connection": "target", "schema": tgt_ns},
            "tables": ["*"],
            "defaults": DEFAULT_CHECKS,
            "overrides": [],
        }],
        "alerts": {"slack_webhook": "env:CDCANARY_SLACK_WEBHOOK"},
    }
    path.write_text(yaml.safe_dump(generated, sort_keys=False, allow_unicode=True),
                    encoding="utf-8")
    click.echo(f"wrote {output} (rule-based: tables are re-discovered on every run)\n"
               f"next: cdcanary check -c {output}")


@main.command()
@click.option("-c", "--config", "config_path", default="cdcanary.yml", show_default=True)
@click.option("--json", "as_json", is_flag=True, help="machine-readable output")
@click.option("--no-alert", is_flag=True, help="skip Slack alert even if configured")
def check(config_path: str, as_json: bool, no_alert: bool) -> None:
    """Run all configured checks once and exit (cron-friendly)."""
    try:
        cfg = config_mod.load(config_path)
    except (config_mod.ConfigError, FileNotFoundError) as e:
        raise click.ClickException(str(e)) from e

    results = runner.run(cfg)
    _print_results(results, as_json)

    if cfg.slack_webhook and not no_alert:
        try:
            if slack.send(cfg.slack_webhook, results):
                click.echo("→ slack alert sent", err=True)
        except Exception as e:  # alert failure must not mask check results
            click.echo(f"→ slack alert failed: {e}", err=True)

    sys.exit(runner.worst_status(results).exit_code)


if __name__ == "__main__":
    main()
