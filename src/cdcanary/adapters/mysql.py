"""MySQL adapter (pymysql). Install with: pip install cdcanary[mysql]"""

from __future__ import annotations

from datetime import datetime

from cdcanary.adapters.base import Adapter

_COARSE = {
    "tinyint": "integer", "smallint": "integer", "mediumint": "integer",
    "int": "integer", "bigint": "integer",
    "float": "float", "double": "float", "real": "float",
    "decimal": "decimal", "numeric": "decimal",
    "char": "string", "varchar": "string", "text": "string",
    "tinytext": "string", "mediumtext": "string", "longtext": "string", "enum": "string",
    "bool": "bool", "boolean": "bool",
    "datetime": "timestamp", "timestamp": "timestamp",
    "date": "date",
    "json": "json",
    "binary": "binary", "varbinary": "binary", "blob": "binary",
}


class MySQLAdapter(Adapter):
    type_name = "mysql"

    def connect(self) -> None:
        import pymysql  # optional dependency

        self._conn = pymysql.connect(
            host=self.config["host"],
            port=int(self.config.get("port", 3306)),
            user=self.config["user"],
            password=self.config.get("password", ""),
            database=self.config["database"],
            read_timeout=int(self.config.get("timeout_s", 60)),
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
            f"SELECT COUNT(*), SUM(CASE WHEN {column} IS NULL THEN 1 ELSE 0 END) FROM {table}")
        total, nulls = int(row[0]), int(row[1] or 0)
        return nulls / total if total else 0.0

    def schema(self, table: str) -> dict[str, str]:
        # table may be "db.table" or bare "table" (falls back to connection db)
        db, _, tbl = table.rpartition(".")
        db = db or self.config["database"]
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_schema = %s AND table_name = %s", (db, tbl))
            rows = cur.fetchall()
        return {name: _COARSE.get(dtype.lower(), "other") for name, dtype in rows}

    def list_tables(self, namespace: str) -> list[str]:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = %s AND table_type = 'BASE TABLE'", (namespace,))
            return sorted(r[0] for r in cur.fetchall())
