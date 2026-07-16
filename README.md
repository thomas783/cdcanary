# 🐤 CDCanary

[![CI](https://github.com/thomas783/cdcanary/actions/workflows/ci.yml/badge.svg)](https://github.com/thomas783/cdcanary/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)

**A canary for your CDC pipeline — catches silent replication drift before your analysts do.**

![CDCanary demo — one scan catches four kinds of silent drift](docs/demo.gif)

CDC pipelines fail loudly when they crash — and silently when they don't.
A column added mid-stream lands as `NULL` in your warehouse. A backfill quietly
skips rows. Replication lag creeps from minutes to days. No error, no alert.
Someone finds out three months later, in a business meeting, with a wrong number
on the slide.

CDCanary is a lightweight monitor that runs aggregate consistency checks
between your **source** and **replica** on a schedule, and pings Slack when they
disagree.

```
┌──────────┐   CDC (Datastream / Debezium / Fivetran / ...)   ┌───────────┐
│  MySQL   │ ───────────────────────────────────────────────▶ │ BigQuery  │
└────┬─────┘                                                  └─────┬─────┘
     │                    ┌───────────┐                             │
     └───── aggregate ───▶│  CDCanary │◀──── aggregate queries -────┘
            queries       └─────┬─────┘
                                │  row delta · freshness · null-rate · schema drift
                                ▼
                            Slack alert
```

## Approach

CDCanary deliberately does **not** do row-level diffing. Aggregate signals
(counts, timestamps, null fractions, schemas) catch the failure modes that
actually happen in CDC pipelines, run in seconds on billion-row tables, and
keep the adapter surface small enough to maintain. It monitors continuously —
this is a canary you run from cron, not a one-off migration validator.

The known blind spot: rows whose *values* are wrong while counts and null
rates still match (e.g. lost UPDATE events). A sampled-checksum check is on
the roadmap to cover that probabilistically without paying full-diff costs.

## Checks

| Check | Signal | Catches |
|---|---|---|
| `row_delta` | source vs target row count (windowed) | dropped rows, stalled backfill |
| `freshness` | source vs target `max(updated_at)` lag | replication lag, dead connector |
| `null_rate` | source vs target null fraction per column | **schema-drift NULL corruption** |
| `schema_drift` | column set + coarse types | added/missing columns, type changes |
| `sampled_checksum` | row contents on a sampled key set (newest half + a rotating spread across the whole range) | **value-level corruption** — lossy casts, tz shifts, lost updates |

Aggregate checks are symmetric queries — any supported connector can be a
source *or* a target. 3 connectors = 9 directions, including reverse ETL
(BigQuery → MySQL) and same-engine replicas (PostgreSQL → PostgreSQL).

## Connectors (v0.1)

MySQL · PostgreSQL · BigQuery

## Quickstart

Zero config — point it at both ends and see what disagrees:

```bash
pip install cdcanary[mysql,bigquery]

cdcanary scan \
  --source mysql://readonly:$PW@prod-db/shop \
  --target bigquery://my-project/raw_shop

# ✅ ok    shop.orders    row_delta        rows match: 1,204,331 vs 1,204,318 (Δ0.00%)
# ✅ ok    shop.orders    freshness        replication lag 4m (within limit)
# 🔴 FAIL  shop.products  null_rate        null-rate drift: sale_status (src 0.0% vs tgt 5.9%)
# 🔴 FAIL  shop.products  sampled_checksum row content drift: 6/90 sampled rows differ (cols: price; e.g. id=75)
# 🔴 FAIL  shop.coupons   table_presence   table exists in source but not in target
```

Then make it permanent — generate a config and put `check` on a schedule:

```bash
cdcanary init --discover --source mysql://... --target bigquery://...
cdcanary check -c cdcanary.yml        # cron / GitHub Actions / Airflow
```

### Try the demo locally

The GIF above is a real run. [`examples/demo`](examples/demo) spins up a MySQL
"source" and a PostgreSQL "replica" with four kinds of drift deliberately baked
in — a missing table, stalled replication, schema drift, and the NULL-corruption
case this tool exists for:

```bash
cd examples/demo && docker compose up -d --wait
cdcanary scan \
  --source mysql://root:demo@127.0.0.1:13306/shop \
  --target postgres://demo:demo@127.0.0.1:15432/shop/public
docker compose down -v   # cleanup
```

The same seeds double as the integration suite — `pytest -m integration` boots
them in throwaway [testcontainers](https://testcontainers.com/) and asserts
every seeded drift is caught (runs in CI on each push).

### Run it on a schedule (GitHub Actions)

`cdcanary check` is designed to be a cron job, and GitHub Actions is the
cheapest cron you already have. [`examples/github-actions`](examples/github-actions)
has a copy-paste workflow: scheduled runs, secrets-based credentials, warn/fail
mapped to run status, results kept as artifacts.

## Configuration

Config describes **rules, not snapshots**. A schema pair re-discovers tables
on every run, so tables added or dropped at the source are picked up without
config edits — and a new source table that hasn't reached the target yet is
itself a finding (`table_presence`).

```yaml
connections:
  mysql_prod:
    type: mysql
    host: prod-db.internal
    database: shop
    user: readonly
    password: env:MYSQL_PASSWORD      # secrets always come from the environment

  bq_raw:
    type: bigquery
    project: my-project
    credentials: env:GOOGLE_APPLICATION_CREDENTIALS

pairs:
  - name: shop
    source: { connection: mysql_prod, schema: shop }
    target: { connection: bq_raw, schema: raw_shop }
    tables: ["*", "!tmp_*"]
    # target_table: "shop_{table}"   # uncomment if the CDC tool renames tables           # globs; "!" excludes
    defaults:                         # applied to every discovered table
      row_delta:    { tolerance_pct: 0.5 }
      freshness:    { column: auto, max_lag_minutes: 60 }
      null_rate:    { columns: auto, max_diff_pp: 1.0 }
      schema_drift: { ignore_columns: [datastream_metadata] }
    overrides:                        # applied top-to-bottom; later rules win
      - match: "log_*"                # glob, exact name, or a list of either
        checks:
          null_rate: false            # too big — skip the expensive check
      - match: orders
        checks:
          freshness: { column: paid_at, max_lag_minutes: 30 }
      - match: users
        target_table: customers       # irregular rename, right next to its exceptions

  # Single-table pair — for tables that deserve hand-tuned checks.
  # Can point across schemas/names and coexist with schema pairs above.
  - name: payments
    source: { connection: mysql_prod, table: shop.payments }
    target: { connection: bq_raw, table: raw_shop.payments_v2 }
    checks:
      row_delta: { tolerance_pct: 0.1, where: "created_at < CURRENT_DATE" }
      freshness: { column: paid_at, max_lag_minutes: 15 }
      null_rate: { columns: [amount, status], max_diff_pp: 0.5 }

alerts:
  slack_webhook: env:CDCANARY_SLACK_WEBHOOK
```

`column: auto` picks the first timestamp column by convention
(`updated_at`, `modified_at`, `created_at`, ...); `columns: auto` compares
every column present on both sides. Schema pairs and table pairs can be mixed
freely — broad coverage from discovery, precise thresholds where it counts.

Table names that differ between source and target are handled in two layers:
a pair-level `target_table: "shop_{table}"` template for systematic renames
(CDC tools usually rename uniformly), and per-table `target_table` in
`overrides` for the irregular few. If the mapping has no pattern at all,
single-table pairs are the honest answer.

Exit codes are cron-friendly: `0` all green · `1` warnings · `2` failures.

## Non-goals

- **Full row-level diffing** — comparing every row fits one-off migration
  validation better than scheduled monitoring. CDCanary keeps checks fast and
  cheap enough to run every hour: aggregates for the broad signals, plus
  `sampled_checksum` on a small deterministic sample for value-level
  verification — never a full-table diff.
- **Auto-remediation** — CDCanary detects and alerts; deciding how to fix a
  pipeline is left to a human.
- **Web dashboard** — results are available as terminal output, `--json`, and
  Slack alerts, which integrate with whatever dashboarding you already run.

## Roadmap

- [x] v0.1 — 4 checks, 3 connectors, Slack alerts, CLI
- [x] v0.2 — `sampled_checksum` check: compare row contents on a sampled key
      set (newest half for fresh breakage + a rotating spread that walks the
      whole table across runs, so lost UPDATEs on old rows surface too) —
      value-level corruption that aggregate signals can't see, without the
      cost of a full row diff
- [ ] v0.2 — baseline state (trend-based anomaly instead of fixed thresholds)
- [ ] v0.3 — Snowflake connector, Prometheus exporter

## Releasing

See [RELEASING.md](RELEASING.md) — tags publish to PyPI via trusted publishing.

## License

MIT
