"""Adapter interface — symmetric by design.

Every connector implements the same four aggregate queries, so any connector
can serve as a source *or* a target. This is what makes N connectors cover
N x N replication directions (CDC, reverse ETL, same-engine replicas) without
direction-specific code.

Cross-engine schema comparison works on *coarse* types: each adapter maps its
native column types onto a small shared vocabulary (COARSE_TYPES). Comparing
`BIGINT` (MySQL) to `INT64` (BigQuery) as exact strings would always disagree;
comparing both as "integer" catches real drift without dialect noise.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

# Shared coarse type vocabulary for cross-engine schema comparison.
COARSE_TYPES = ("integer", "float", "decimal", "string", "bool", "timestamp", "date", "json", "binary", "other")


class Adapter(ABC):
    """One live connection to a database, capable of the four canary queries."""

    #: adapter type name used in YAML config (e.g. "mysql")
    type_name: str = ""

    def __init__(self, config: dict):
        self.config = config

    # ── lifecycle ────────────────────────────────────────────────────────

    @abstractmethod
    def connect(self) -> None: ...

    @abstractmethod
    def close(self) -> None: ...

    def __enter__(self) -> "Adapter":
        self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ── the four canary queries ──────────────────────────────────────────

    @abstractmethod
    def row_count(self, table: str, where: str | None = None) -> int:
        """SELECT COUNT(*) FROM table [WHERE where]."""

    @abstractmethod
    def max_timestamp(self, table: str, column: str) -> datetime | None:
        """SELECT MAX(column) FROM table. None if the table is empty."""

    @abstractmethod
    def null_fraction(self, table: str, column: str) -> float:
        """Fraction of NULL values in column, 0.0..1.0. 0.0 for empty tables."""

    @abstractmethod
    def schema(self, table: str) -> dict[str, str]:
        """{column_name: coarse_type} — coarse_type ∈ COARSE_TYPES."""

    @abstractmethod
    def list_tables(self, namespace: str) -> list[str]:
        """Table names in a namespace (MySQL schema / PG schema / BQ dataset).

        Powers runtime discovery: schema-level pairs re-list tables on every
        run, so tables added or dropped at the source enter/leave monitoring
        without config edits.
        """
