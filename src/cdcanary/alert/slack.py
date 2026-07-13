"""Slack webhook alert. Standard library only — no requests dependency.

Alerting policy: send only when something is wrong (WARN or worse). A canary
that sings every hour when everything is fine trains people to ignore it.
"""

from __future__ import annotations

import json
import urllib.request

from cdcanary.checks.base import CheckResult, Status

_EMOJI = {Status.OK: "✅", Status.WARN: "🟡", Status.FAIL: "🔴", Status.ERROR: "⚠️"}


def format_message(results: list[CheckResult]) -> str:
    bad = [r for r in results if r.status is not Status.OK]
    lines = [f"🐤 *CDCanary* — {len(bad)} problem(s) in {len(results)} check(s)"]
    for r in bad:
        lines.append(f"{_EMOJI[r.status]} `{r.pair}` {r.check}: {r.message}")
    return "\n".join(lines)


def send(webhook_url: str, results: list[CheckResult]) -> bool:
    """Send an alert if anything is not OK. Returns True if a message was sent."""
    if all(r.status is Status.OK for r in results):
        return False
    payload = json.dumps({"text": format_message(results)}).encode("utf-8")
    req = urllib.request.Request(webhook_url, data=payload,
                                 headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=10)
    return True
