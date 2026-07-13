"""BigQuery adapter (google-cloud-bigquery). Install with: pip install cdcanary[bigquery]"""

from __future__ import annotations

import os
from datetime import datetime

from cdcanary.adapters.base import Adapter

_COARSE = {
    "INT64": "integer", "INTEGER": "integer",
    "FLOAT64": "float", "FLOAT": "float",
    "NUMERIC": "decimal", "BIGNUMERIC": "decimal",
    "STRING": "string",
    "BOOL": "bool", "BOOLEAN": "bool",
    "TIMESTAMP": "timestamp", "DATETIME": "timestamp",
    "DATE": "date",
    "JSON": "json",
    "BYTES": "binary",
}


class BigQueryAdapter(Adapter):
    type_name = "bigquery"

    def connect(self) -> None:
        from google.cloud import bigquery  # optional dependency

        creds = self.config.get("credentials")
        if creds:
            os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", creds)
        self._client = bigquery.Client(project=self.config["project"])

    def close(self) -> None:
        self._client.close()

    def _one(self, sql: str) -> tuple:
        rows = list(self._client.query(sql).result())
        return tuple(rows[0]) if rows else (None,)

    def _fq(self, table: str) -> str:
        """dataset.table → `project.dataset.table`"""
        return f"`{self.config['project']}.{table}`"

    def row_count(self, table: str, where: str | None = None) -> int:
        sql = f"SELECT COUNT(*) FROM {self._fq(table)}"
        if where:
            sql += f" WHERE {where}"
        return int(self._one(sql)[0])

    def max_timestamp(self, table: str, column: str) -> datetime | None:
        value = self._one(f"SELECT MAX({column}) FROM {self._fq(table)}")[0]
        # BigQuery TIMESTAMP comes back tz-aware; DATETIME comes back naive.
        # Normalize to naive UTC so lag math against other engines works.
        if value is not None and getattr(value, "tzinfo", None) is not None:
            value = value.replace(tzinfo=None)
        return value

    def null_fraction(self, table: str, column: str) -> float:
        row = self._one(
            f"SELECT COUNT(*), COUNTIF({column} IS NULL) FROM {self._fq(table)}")
        total, nulls = int(row[0] or 0), int(row[1] or 0)
        return nulls / total if total else 0.0

    def schema(self, table: str) -> dict[str, str]:
        dataset, _, tbl = table.rpartition(".")
        sql = (f"SELECT column_name, data_type "
               f"FROM `{self.config['project']}.{dataset}`.INFORMATION_SCHEMA.COLUMNS "
               f"WHERE table_name = '{tbl}'")
        rows = self._client.query(sql).result()
        out = {}
        for r in rows:
            dtype = r[1].split("<")[0].split("(")[0].upper()  # STRUCT<...>, NUMERIC(p,s) → base
            out[r[0]] = _COARSE.get(dtype, "other")
        return out

    def list_tables(self, namespace: str) -> list[str]:
        sql = (f"SELECT table_name "
               f"FROM `{self.config['project']}.{namespace}`.INFORMATION_SCHEMA.TABLES "
               f"WHERE table_type = 'BASE TABLE'")
        return sorted(r[0] for r in self._client.query(sql).result())
