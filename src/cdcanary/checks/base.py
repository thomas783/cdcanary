"""Check interface and result model.

A check receives the pair's source/target adapters plus its own options and
returns a CheckResult. Checks never raise on data problems — a data problem is
a *result* (FAIL), not an exception. Exceptions are reserved for
infrastructure errors (connection refused, table missing, ...) and surface as
ERROR results at the runner level.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum

from cdcanary.adapters.base import Adapter


class Status(str, Enum):
    OK = "ok"
    WARN = "warn"
    FAIL = "fail"
    ERROR = "error"  # infrastructure problem — could not evaluate

    @property
    def exit_code(self) -> int:
        return {"ok": 0, "warn": 1, "fail": 2, "error": 2}[self.value]


@dataclass
class CheckResult:
    check: str                      # e.g. "row_delta"
    pair: str                       # pair name from config
    status: Status
    message: str                    # one-line human summary (goes to Slack)
    metrics: dict = field(default_factory=dict)  # raw numbers for --json output


class Check(ABC):
    """One consistency check over a (source, target) pair."""

    #: check name used in YAML config (e.g. "row_delta")
    name: str = ""

    def __init__(self, options: dict):
        self.options = options or {}

    @abstractmethod
    def run(self, pair_name: str, source: Adapter, source_table: str,
            target: Adapter, target_table: str) -> CheckResult: ...
