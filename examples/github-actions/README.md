# Run cdcanary on a GitHub Actions schedule

`cdcanary check` is built to be someone's cron job — and the cheapest cron you
already have is GitHub Actions. No server, no scheduler, secrets managed by the
repo, results kept as artifacts.

## Setup

1. Copy [`cdcanary-check.yml`](./cdcanary-check.yml) to
   `.github/workflows/cdcanary-check.yml` in the repo that holds your
   `cdcanary.yml`.
2. Add the secrets your config references (`env:VAR_NAME` values) under
   repo **Settings → Secrets and variables → Actions**.
3. Commit. The check runs every 30 minutes and can be fired manually from the
   Actions tab (`workflow_dispatch`).

## How exit codes map to run status

`cdcanary check` exits `0` (all green), `1` (warnings), `2` (failures/errors).
The example maps them to Actions semantics:

| exit | run result | why |
|------|-----------|-----|
| 0 | ✅ green | nothing to see |
| 1 | ✅ green + ⚠️ annotation | a warning shouldn't page anyone at 3am; the annotation and Slack alert are enough |
| 2 | ❌ failed | replication drifted — the red X is the point |

If you'd rather have warnings fail the run too, delete the three `rc=0` lines
in the "Run checks" step.

## Notes

- **Schedule granularity**: GitHub cron is best-effort — runs can lag a few
  minutes, and `*/5` or tighter tends to be throttled on busy runners. If you
  need minute-precision monitoring, run the same config from a real cron.
- **Overlap**: the `concurrency` block guarantees one check at a time, so a
  slow run never races the next tick.
- **Database access**: GitHub-hosted runners need network reach to your
  databases. For DBs inside a VPC, use a
  [self-hosted runner](https://docs.github.com/en/actions/hosting-your-own-runners)
  in the same network — the workflow itself doesn't change.
- **Slack**: set `CDCANARY_SLACK_WEBHOOK` and reference it from `cdcanary.yml`
  (`alerts: slack_webhook: env:CDCANARY_SLACK_WEBHOOK`) to get alerts on
  warn/fail regardless of who looks at the Actions tab.
