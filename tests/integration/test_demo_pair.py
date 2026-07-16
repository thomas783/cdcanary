"""Real-database integration: the demo pair, promoted to a test.

examples/demo seeds a MySQL "source" and a Postgres "replica" with five kinds
of drift deliberately baked in. Here the same seed files boot in throwaway
testcontainers, the real adapters connect (dialect SQL, coarse-type
normalization — code the unit suite never touches), and every seeded drift
must surface as exactly the finding the demo promises. Doubles as a guard
that the README demo stays honest.

Needs a Docker daemon. Excluded from the default run — `pytest -m integration`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cdcanary import runner
from cdcanary.checks.base import Status
from cdcanary.cli import DEFAULT_CHECKS
from cdcanary.config import from_dict

pytestmark = pytest.mark.integration

pytest.importorskip("testcontainers", reason="dev extras not installed")
from testcontainers.mysql import MySqlContainer  # noqa: E402
from testcontainers.postgres import PostgresContainer  # noqa: E402

DEMO = Path(__file__).parent.parent.parent / "examples" / "demo"


def _connection(container, type_name: str, port: int) -> dict:
    return {
        "type": type_name,
        "host": container.get_container_host_ip(),
        "port": container.get_exposed_port(port),
        "user": "demo",
        "password": "demo",
        "database": "shop",
    }


@pytest.fixture(scope="session")
def results():
    """Boot the demo pair once, run every default check, index by (pair, check)."""
    source = (
        MySqlContainer("mysql:8.4", username="demo", password="demo", dbname="shop")
        .with_volume_mapping(str(DEMO / "seed-source.sql"), "/docker-entrypoint-initdb.d/seed.sql")
    )
    replica = (
        PostgresContainer("postgres:16", username="demo", password="demo", dbname="shop")
        .with_volume_mapping(str(DEMO / "seed-replica.sql"), "/docker-entrypoint-initdb.d/seed.sql")
    )
    with source, replica:
        cfg = from_dict({
            "connections": {
                "src": _connection(source, "mysql", 3306),
                "tgt": _connection(replica, "postgres", 5432),
            },
            "pairs": [{
                "name": "shop",
                "source": {"connection": "src", "schema": "shop"},
                "target": {"connection": "tgt", "schema": "public"},
                "defaults": DEFAULT_CHECKS,
            }],
        })
        yield {(r.pair, r.check): r for r in runner.run(cfg)}


def _statuses(results, table: str) -> dict[str, Status]:
    return {check: r.status for (pair, check), r in results.items() if pair == f"shop.{table}"}


class TestSeededDrift:
    def test_nothing_errored(self, results):
        # ERROR means an adapter blew up (bad SQL, driver issue) — never expected
        errors = [r for r in results.values() if r.status is Status.ERROR]
        assert not errors, [r.message for r in errors]

    def test_healthy_table_all_green(self, results):
        # negative control: a table without drift must not produce noise
        statuses = _statuses(results, "orders")
        assert statuses and set(statuses.values()) == {Status.OK}

    def test_unreplicated_table_fails_presence(self, results):
        assert results[("shop.coupons", "table_presence")].status is Status.FAIL

    def test_null_corruption_detected(self, results):
        r = results[("shop.products", "null_rate")]
        assert r.status is Status.FAIL
        assert "sale_status" in r.message

    def test_lost_column_detected(self, results):
        r = results[("shop.users", "schema_drift")]
        assert r.status is Status.FAIL
        assert "phone" in r.message

    def test_stalled_connector_detected(self, results):
        assert results[("shop.events", "freshness")].status is Status.FAIL
        assert results[("shop.events", "row_delta")].status is Status.FAIL
        # the newest source rows never arrived — content sampling names that too
        r = results[("shop.events", "sampled_checksum")]
        assert r.status is Status.FAIL
        assert "missing at target" in r.message

    def test_lossy_cast_detected_only_by_checksum(self, results):
        # six replica rows have price off by 7 cents: counts, nulls, schema and
        # freshness all pass — this drift exists purely at the value level
        r = results[("shop.products", "sampled_checksum")]
        assert r.status is Status.FAIL
        assert "price" in r.message
        assert r.metrics["differing_rows"] >= 6
        assert results[("shop.products", "row_delta")].status is Status.OK
