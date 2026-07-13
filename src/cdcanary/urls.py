"""Connection URL parsing for `cdcanary scan` — zero-config entry point.

    mysql://user:pass@host:3306/database/schema?  → schema defaults to database
    postgres://user:pass@host/database/schema     → schema defaults to "public"
    bigquery://project/dataset

Passwords in URLs are accepted for quick scans but the generated config
(`init --discover`) always writes env: placeholders instead.
"""

from __future__ import annotations

from urllib.parse import unquote, urlparse

from cdcanary.config import ConfigError


def parse(url: str) -> tuple[dict, str]:
    """→ (connection config dict, namespace to scan)."""
    u = urlparse(url)
    scheme = u.scheme.lower()
    parts = [p for p in (u.path or "").split("/") if p]

    if scheme == "bigquery":
        if not u.hostname or not parts:
            raise ConfigError(f"bigquery URL needs project and dataset: {url}")
        return {"type": "bigquery", "project": u.hostname}, parts[0]

    if scheme in ("mysql", "postgres", "postgresql"):
        if not u.hostname or not parts:
            raise ConfigError(f"{scheme} URL needs host and database: {url}")
        database = parts[0]
        conn = {
            "type": "postgres" if scheme.startswith("postgres") else "mysql",
            "host": u.hostname,
            "database": database,
            "user": unquote(u.username or ""),
            "password": unquote(u.password or ""),
        }
        if u.port:
            conn["port"] = u.port
        default_ns = "public" if conn["type"] == "postgres" else database
        namespace = parts[1] if len(parts) > 1 else default_ns
        return conn, namespace

    raise ConfigError(f"unsupported connection scheme '{u.scheme}' "
                      "(supported: mysql, postgres, bigquery)")
