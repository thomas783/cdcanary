"""Fake adapter — lets every check run without a real database."""

from __future__ import annotations

import os
from datetime import datetime

from cdcanary.adapters.base import Adapter

# Docker Desktop (macOS) reports its socket as ~/.docker/run/docker.sock, a
# path that exists only on the host — Ryuk (testcontainers' cleanup container)
# then fails to bind-mount it. Point the mount at /var/run/docker.sock, which
# is valid inside the Docker Desktop VM and already the socket path on Linux
# runners, so this is a no-op in CI. Harmless for the unit suite.
os.environ.setdefault("TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE", "/var/run/docker.sock")


class FakeAdapter(Adapter):
    type_name = "fake"

    def __init__(self, rows: int = 0, max_ts: datetime | None = None,
                 null_fractions: dict[str, float] | None = None,
                 columns: dict[str, str] | None = None):
        super().__init__({})
        self._rows = rows
        self._max_ts = max_ts
        self._null_fractions = null_fractions or {}
        self._columns = columns or {}

    def connect(self) -> None: ...
    def close(self) -> None: ...

    def row_count(self, table, where=None):
        return self._rows

    def max_timestamp(self, table, column):
        return self._max_ts

    def null_fraction(self, table, column):
        return self._null_fractions.get(column, 0.0)

    def schema(self, table):
        return dict(self._columns)

    def list_tables(self, namespace):
        return []
