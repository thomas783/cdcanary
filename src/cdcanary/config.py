"""YAML config loading.

Two kinds of pairs:

* **table pair** — source/target each name one table. Precise, verbose.
* **schema pair** — source/target name a namespace (MySQL schema / PG schema /
  BQ dataset). Tables are discovered at *runtime* on every check run, matched
  by name, filtered by glob patterns, and given the pair's `defaults` checks
  (with per-table `overrides`). Config describes a rule, not a snapshot — so
  tables added or dropped at the source are picked up without config edits.

Secrets never live in the YAML: any string value of the form "env:VAR_NAME"
is resolved from the environment at load time, so the config file is safe to
commit next to the cron job that runs it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path

import yaml


class ConfigError(Exception):
    pass


@dataclass
class Endpoint:
    connection: str
    table: str | None = None    # table pair
    schema: str | None = None   # schema pair


@dataclass
class Override:
    """One override rule: which tables it matches, and what it changes."""
    match: list[str]                 # table names or globs
    checks: dict[str, dict] = field(default_factory=dict)  # check -> options, or False
    target_table: str | None = None  # rename; "{table}" expands to the source name

    def matches(self, table: str) -> bool:
        return any(fnmatch(table, pattern) for pattern in self.match)


@dataclass
class Pair:
    name: str
    source: Endpoint
    target: Endpoint
    checks: dict[str, dict] = field(default_factory=dict)     # table pair
    tables: list[str] = field(default_factory=lambda: ["*"])  # schema pair: globs, "!" = exclude
    defaults: dict[str, dict] = field(default_factory=dict)   # schema pair
    overrides: list[Override] = field(default_factory=list)   # schema pair
    target_table: str | None = None                            # schema pair: rename template

    @property
    def is_schema_pair(self) -> bool:
        return self.source.schema is not None

    def checks_for(self, table: str) -> dict[str, dict]:
        """Effective checks for one discovered table.

        defaults first, then every matching override rule in declaration
        order — later rules win, so put broad globs before specific names.
        A check value of False disables that check for the table.
        """
        merged: dict[str, dict] = {k: dict(v or {}) for k, v in self.defaults.items()}
        for rule in self.overrides:
            if not rule.matches(table):
                continue
            for check_name, opts in rule.checks.items():
                if opts is False:
                    merged.pop(check_name, None)
                else:
                    merged[check_name] = {**merged.get(check_name, {}), **(opts or {})}
        return merged

    def target_table_for(self, table: str) -> str:
        """Target table name for one discovered source table.

        Resolution order: the last matching override with a `target_table`
        wins → else the pair-level `target_table` template → else same name.
        "{table}" in either expands to the source table name, so systematic
        renames (e.g. Datastream's "shop_{table}") stay one line while
        irregular ones live next to the table's other exceptions.
        """
        template = self.target_table
        for rule in self.overrides:
            if rule.target_table is not None and rule.matches(table):
                template = rule.target_table
        if template is None:
            return table
        return template.replace("{table}", table)


@dataclass
class Config:
    connections: dict[str, dict]
    pairs: list[Pair]
    slack_webhook: str | None = None
    raw: dict = field(default_factory=dict)


def _resolve_env(value):
    """'env:FOO' → os.environ['FOO'], recursively through dicts/lists."""
    if isinstance(value, str) and value.startswith("env:"):
        var = value[4:]
        if var not in os.environ:
            raise ConfigError(f"environment variable not set: {var}")
        return os.environ[var]
    if isinstance(value, dict):
        return {k: _resolve_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_env(v) for v in value]
    return value


def _parse_pair(i: int, p: dict) -> Pair:
    try:
        source = Endpoint(**p["source"])
        target = Endpoint(**p["target"])
    except (KeyError, TypeError) as e:
        raise ConfigError(f"pairs[{i}]: invalid source/target — {e}") from e

    name = p.get("name") or f"pair_{i}"
    src_kind = "schema" if source.schema else "table" if source.table else None
    tgt_kind = "schema" if target.schema else "table" if target.table else None
    if src_kind is None or src_kind != tgt_kind:
        raise ConfigError(
            f"pair '{name}': source and target must both use 'table' or both use 'schema'")

    overrides = []
    for j, o in enumerate(p.get("overrides") or []):
        if not isinstance(o, dict) or "match" not in o:
            raise ConfigError(f"pair '{name}': overrides[{j}] needs 'match'")
        if "checks" not in o and "target_table" not in o:
            raise ConfigError(
                f"pair '{name}': overrides[{j}] needs 'checks' and/or 'target_table'")
        match = o["match"] if isinstance(o["match"], list) else [o["match"]]
        overrides.append(Override(match=match, checks=o.get("checks") or {},
                                  target_table=o.get("target_table")))

    pair = Pair(
        name=name, source=source, target=target,
        checks=p.get("checks") or {},
        tables=p.get("tables") or ["*"],
        defaults=p.get("defaults") or {},
        overrides=overrides,
        target_table=p.get("target_table"),
    )
    if pair.is_schema_pair and not pair.defaults:
        raise ConfigError(f"pair '{name}': schema pair needs 'defaults' with at least one check")
    if not pair.is_schema_pair and not pair.checks:
        raise ConfigError(f"pair '{name}': no checks configured")
    return pair


def load(path: str | Path) -> Config:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: expected a YAML mapping at top level")
    raw = _resolve_env(raw)
    return from_dict(raw)


def from_dict(raw: dict) -> Config:
    """Build a Config from an already-parsed dict (used by `cdcanary scan`)."""
    connections = raw.get("connections") or {}
    if not connections:
        raise ConfigError("config needs at least one entry under 'connections'")

    pairs = [_parse_pair(i, p) for i, p in enumerate(raw.get("pairs") or [])]
    if not pairs:
        raise ConfigError("config needs at least one entry under 'pairs'")
    for pair in pairs:
        for side in (pair.source, pair.target):
            if side.connection not in connections:
                raise ConfigError(f"pair '{pair.name}': unknown connection '{side.connection}'")

    return Config(
        connections=connections,
        pairs=pairs,
        slack_webhook=(raw.get("alerts") or {}).get("slack_webhook"),
        raw=raw,
    )
