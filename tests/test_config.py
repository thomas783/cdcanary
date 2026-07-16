import pytest

from cdcanary.config import ConfigError, load

# Kept as YAML text on purpose: these tests cover load() itself — file read,
# parse, env resolution. Tests that only need a parsed config use from_dict
# with plain dicts instead (see test_discovery.py).
VALID = """
connections:
  src: { type: mysql, host: h, database: d, user: u }
  tgt: { type: bigquery, project: p }
pairs:
  - name: orders
    source: { connection: src, table: orders }
    target: { connection: tgt, table: raw.orders }
    checks:
      row_delta: { tolerance_pct: 0.5 }
alerts:
  slack_webhook: https://hooks.slack.com/services/x
"""


@pytest.fixture
def load_yaml(tmp_path):
    """Factory: write YAML text to a temp config file and load() it."""
    def _load(text):
        p = tmp_path / "cdcanary.yml"
        p.write_text(text, encoding="utf-8")
        return load(p)
    return _load


def mutate(old, new):
    """VALID with one edit. Asserting the anchor exists keeps a reworded VALID
    from turning a should-fail test into a silent pass on the unedited config."""
    assert old in VALID, f"mutation anchor not found in VALID: {old!r}"
    return VALID.replace(old, new)


def test_valid_config(load_yaml):
    cfg = load_yaml(VALID)
    assert cfg.pairs[0].name == "orders"
    assert cfg.pairs[0].source.connection == "src"
    assert cfg.slack_webhook.startswith("https://hooks.slack.com")


def test_env_resolution(load_yaml, monkeypatch):
    monkeypatch.setenv("MY_SECRET", "hunter2")
    cfg = load_yaml(mutate("user: u", "user: env:MY_SECRET"))
    assert cfg.connections["src"]["user"] == "hunter2"


def test_missing_env_raises(load_yaml):
    with pytest.raises(ConfigError, match="NOPE_NOT_SET"):
        load_yaml(mutate("user: u", "user: env:NOPE_NOT_SET"))


def test_unknown_connection_raises(load_yaml):
    with pytest.raises(ConfigError, match="unknown connection"):
        load_yaml(mutate("connection: tgt", "connection: nope"))


def test_pair_without_checks_raises(load_yaml):
    with pytest.raises(ConfigError, match="no checks"):
        load_yaml(mutate("    checks:\n      row_delta: { tolerance_pct: 0.5 }\n", ""))
