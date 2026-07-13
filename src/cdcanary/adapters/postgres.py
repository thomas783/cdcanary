"""PostgreSQL adapter (psycopg 3). Install with: pip install cdcanary[postgres]"""

from __future__ import annotations

from datetime import datetime

from cdcanary.adapters.base import Adapter

_COARSE = {
    "smallint": "integer", "integer": "integer", "bigint": "integer",
    "smallserial": "integer", "serial": "integer", "bigserial": "integer",
    "real": "float", "double precision": "float",
    "numeric": "decimal", "money": "decimal",
    "character varying": "string", "character": "string", "text": "string", "uuid": "string",
    "boolean": "bool",
    "timestamp without time zone": "timestamp", "timestamp with time zone": "timestamp",
    "date": "date",
    "json": "json", "jsonb": "json",
    "bytea": "binary",
}


class PostgresAdapter(Adapter):
    type_name = "postgres"

    def connect(self) -> None:
        import psycopg  # optional dependency

        self._conn = psycopg.connect(
            host=self.config["host"],
            port=int(self.config.get("port", 5432)),
            user=self.config["user"],
            password=self.config.get("password", ""),
            dbname=self.config["database"],
            connect_timeout=int(self.config.get("timeout_s", 60)),
        )

    def close(self) -> None:
        self._conn.close()

    def _one(self, sql: str, params: tuple = ()) -> tuple:
        with self._conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()

    def row_count(self, table: str, where: str | None = None) -> int:
        sql = f"SELECT COUNT(*) FROM {table}"
        if where:
            sql += f" WHERE {where}"
        return int(self._one(sql)[0])

    def max_timestamp(self, table: str, column: str) -> datetime | None:
        return self._one(f"SELECT MAX({column}) FROM {table}")[0]

    def null_fraction(self, table: str, column: str) -> float:
        row = self._one(
            f"SELECT COUNT(*), COUNT(*) FILTER (WHERE {column} IS NULL) FROM {table}")
        total, nulls = int(row[0]), int(row[1] or 0)
        return nulls / total if total else 0.0

    def schema(self, table: str) -> dict[str, str]:
        schema, _, tbl = table.rpartition(".")
        schema = schema or "public"
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_schema = %s AND table_name = %s", (schema, tbl))
            rows = cur.fetchall()
        return {name: _COARSE.get(dtype.lower(), "other") for name, dtype in rows}

    def list_tables(self, namespace: str) -> list[str]:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = %s AND table_type = 'BASE TABLE'", (namespace,))
            return sorted(r[0] for r in cur.fetchall())
