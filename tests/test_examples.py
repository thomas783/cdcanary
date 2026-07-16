"""Drift guards for examples/github-actions/.

The workflow is a copy-paste template — nobody runs it in this repo's CI, so
nothing would catch it referencing a flag, extra, or exit code that no longer
exists. These tests pin the example to the real CLI surface instead.
"""

from __future__ import annotations

import re
from importlib.metadata import metadata
from pathlib import Path

import yaml

from cdcanary.checks.base import Status
from cdcanary.cli import main as cli_main

WORKFLOW = Path(__file__).parent.parent / "examples" / "github-actions" / "cdcanary-check.yml"


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _triggers(wf: dict) -> dict:
    # YAML 1.1 parses a bare `on:` key as boolean True
    return wf.get("on", wf.get(True))


def _run_scripts(wf: dict) -> str:
    return "\n".join(
        step.get("run", "")
        for job in wf["jobs"].values()
        for step in job["steps"]
    )


class TestScheduledWorkflow:
    def test_has_valid_cron_schedule(self):
        crons = [entry["cron"] for entry in _triggers(_workflow())["schedule"]]
        assert crons, "example must actually schedule something"
        for cron in crons:
            assert len(cron.split()) == 5, f"malformed cron: {cron!r}"

    def test_manual_dispatch_enabled(self):
        assert "workflow_dispatch" in _triggers(_workflow())

    def test_single_flight_concurrency(self):
        # overlapping checks race the next tick and produce confusing diffs
        wf = _workflow()
        assert wf["concurrency"]["cancel-in-progress"] is False

    def test_check_flags_exist_on_cli(self):
        script = _run_scripts(_workflow())
        check_lines = [ln for ln in script.splitlines() if "cdcanary check" in ln]
        assert check_lines, "workflow must invoke `cdcanary check`"
        valid = {
            opt
            for param in cli_main.commands["check"].params
            for opt in (*param.opts, *param.secondary_opts)
        }
        used = {f for ln in check_lines for f in re.findall(r"(?<!-)(-{1,2}[a-z][a-z-]*)", ln)}
        assert used <= valid, f"workflow uses unknown flags: {used - valid}"

    def test_installed_extras_exist(self):
        script = _run_scripts(_workflow())
        m = re.search(r"pip install \"cdcanary\[([a-z,]+)\]\"", script)
        assert m, "install step must pin extras explicitly"
        provided = set(metadata("cdcanary").get_all("Provides-Extra"))
        assert set(m.group(1).split(",")) <= provided

    def test_exit_code_contract_matches_cli(self):
        # the workflow special-cases exit 1 (warn) and forwards the rest —
        # only meaningful while the CLI contract is exactly {0: ok, 1: warn, 2: fail}
        assert {s.exit_code for s in Status} == {0, 1, 2}
        assert Status.WARN.exit_code == 1
        script = _run_scripts(_workflow())
        assert '"$rc" -eq 1' in script, "warn-handling must reference exit code 1"
