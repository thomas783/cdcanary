"""Schema-pair discovery: glob selection, defaults/overrides merge, runner expansion."""

from conftest import FakeAdapter

from cdcanary import runner
from cdcanary.checks.base import Status
from cdcanary.config import from_dict
from cdcanary.runner import select_tables


class TestSelectTables:
    TABLES = ["orders", "users", "tmp_backup", "event_log", "_airbyte_raw"]

    def test_star_selects_all(self):
        assert select_tables(self.TABLES, ["*"]) == self.TABLES

    def test_exclude_wins(self):
        got = select_tables(self.TABLES, ["*", "!tmp_*", "!_airbyte_*"])
        assert got == ["orders", "users", "event_log"]

    def test_explicit_include(self):
        assert select_tables(self.TABLES, ["orders", "users"]) == ["orders", "users"]


class TestChecksFor:
    def _pair(self):
        cfg = from_dict({
            "connections": {"s": {"type": "fake"}, "t": {"type": "fake"}},
            "pairs": [{
                "name": "shop",
                "source": {"connection": "s", "schema": "shop"},
                "target": {"connection": "t", "schema": "raw"},
                "defaults": {"row_delta": {"tolerance_pct": 0.5},
                             "null_rate": {"columns": "auto"}},
                "overrides": [
                    {"match": "orders", "checks": {"row_delta": {"tolerance_pct": 0.1}}},
                    {"match": "event_log", "checks": {"null_rate": False}},
                ],
            }],
        })
        return cfg.pairs[0]

    def test_defaults_apply(self):
        assert self._pair().checks_for("users") == {
            "row_delta": {"tolerance_pct": 0.5}, "null_rate": {"columns": "auto"}}

    def test_override_merges_options(self):
        assert self._pair().checks_for("orders")["row_delta"] == {"tolerance_pct": 0.1}

    def test_override_false_disables_check(self):
        assert "null_rate" not in self._pair().checks_for("event_log")


class TestGlobOverrides:
    def _pair(self, overrides):
        cfg = from_dict({
            "connections": {"s": {"type": "fake"}, "t": {"type": "fake"}},
            "pairs": [{
                "name": "shop",
                "source": {"connection": "s", "schema": "shop"},
                "target": {"connection": "t", "schema": "raw"},
                "defaults": {"row_delta": {"tolerance_pct": 0.5},
                             "null_rate": {"columns": "auto"}},
                "overrides": overrides,
            }],
        })
        return cfg.pairs[0]

    def test_glob_pattern_matches_tables(self):
        pair = self._pair([{"match": "log_*", "checks": {"null_rate": False}}])
        assert "null_rate" not in pair.checks_for("log_events")
        assert "null_rate" in pair.checks_for("orders")

    def test_later_rule_wins(self):
        pair = self._pair([
            {"match": "log_*", "checks": {"row_delta": {"tolerance_pct": 5.0}}},
            {"match": "log_billing", "checks": {"row_delta": {"tolerance_pct": 0.1}}},
        ])
        assert pair.checks_for("log_billing")["row_delta"] == {"tolerance_pct": 0.1}
        assert pair.checks_for("log_events")["row_delta"] == {"tolerance_pct": 5.0}

    def test_match_accepts_list(self):
        pair = self._pair([
            {"match": ["orders", "payments"], "checks": {"row_delta": {"tolerance_pct": 0.1}}},
        ])
        assert pair.checks_for("orders")["row_delta"] == {"tolerance_pct": 0.1}
        assert pair.checks_for("payments")["row_delta"] == {"tolerance_pct": 0.1}
        assert pair.checks_for("users")["row_delta"] == {"tolerance_pct": 0.5}


class TestTargetTable:
    def _pair(self, target_table=None, overrides=()):
        cfg = from_dict({
            "connections": {"s": {"type": "fake"}, "t": {"type": "fake"}},
            "pairs": [{
                "name": "shop",
                "source": {"connection": "s", "schema": "shop"},
                "target": {"connection": "t", "schema": "raw"},
                "defaults": {"row_delta": {}},
                **({"target_table": target_table} if target_table else {}),
                "overrides": list(overrides),
            }],
        })
        return cfg.pairs[0]

    def test_same_name_by_default(self):
        assert self._pair().target_table_for("orders") == "orders"

    def test_pair_level_template(self):
        pair = self._pair(target_table="shop_{table}")
        assert pair.target_table_for("orders") == "shop_orders"

    def test_override_rename_beats_template(self):
        pair = self._pair(target_table="shop_{table}", overrides=[
            {"match": "users", "target_table": "customers"},
        ])
        assert pair.target_table_for("users") == "customers"
        assert pair.target_table_for("orders") == "shop_orders"

    def test_glob_rename_with_placeholder(self):
        pair = self._pair(overrides=[
            {"match": "legacy_*", "target_table": "old_{table}"},
        ])
        assert pair.target_table_for("legacy_users") == "old_legacy_users"

    def test_override_needs_checks_or_rename(self):
        import pytest
        with pytest.raises(Exception, match="checks.*target_table|target_table"):
            self._pair(overrides=[{"match": "users"}])


class TestSchemaPairRun:
    def _run(self, src_tables, tgt_tables, pair_extra=None):
        class Src(FakeAdapter):
            type_name = "fake_src"
            def __init__(self, config): super().__init__(rows=10)
            def list_tables(self, ns): return src_tables

        class Tgt(FakeAdapter):
            type_name = "fake_tgt"
            def __init__(self, config): super().__init__(rows=10)
            def list_tables(self, ns): return tgt_tables

        runner._register_builtins()
        runner.ADAPTERS["fake_src"] = Src
        runner.ADAPTERS["fake_tgt"] = Tgt
        cfg = from_dict({
            "connections": {"s": {"type": "fake_src"}, "t": {"type": "fake_tgt"}},
            "pairs": [{
                "name": "shop",
                "source": {"connection": "s", "schema": "shop"},
                "target": {"connection": "t", "schema": "raw"},
                "defaults": {"row_delta": {"tolerance_pct": 0.5}},
                **(pair_extra or {}),
            }],
        })
        return runner.run(cfg)

    def test_matched_tables_get_default_checks(self):
        results = self._run(["orders", "users"], ["orders", "users"])
        assert {r.pair for r in results} == {"shop.orders", "shop.users"}
        assert all(r.status is Status.OK for r in results)

    def test_missing_table_in_target_fails(self):
        results = self._run(["orders", "coupons"], ["orders"])
        missing = [r for r in results if r.check == "table_presence"]
        assert len(missing) == 1
        assert missing[0].pair == "shop.coupons"
        assert missing[0].status is Status.FAIL

    def test_renamed_table_found_in_target(self):
        results = self._run(["orders"], ["shop_orders"],
                            pair_extra={"target_table": "shop_{table}"})
        assert all(r.status is Status.OK for r in results)
        assert not [r for r in results if r.check == "table_presence"]
