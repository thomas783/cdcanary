import pytest

from cdcanary.config import ConfigError, load

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


def write(tmp_path, text):
    p = tmp_path / "cdcanary.yml"
    p.write_text(text, encoding="utf-8")
    return p


def test_valid_config(tmp_path):
    cfg = load(write(tmp_path, VALID))
    assert cfg.pairs[0].name == "orders"
    assert cfg.pairs[0].source.connection == "src"
    assert cfg.slack_webhook.startswith("https://hooks.slack.com")


def test_env_resolution(tmp_path, monkeypatch):
    monkeypatch.setenv("MY_SECRET", "hunter2")
    cfg = load(write(tmp_path, VALID.replace("user: u", "user: env:MY_SECRET")))
    assert cfg.connections["src"]["user"] == "hunter2"


def test_missing_env_raises(tmp_path):
    with pytest.raises(ConfigError, match="NOPE_NOT_SET"):
        load(write(tmp_path, VALID.replace("user: u", "user: env:NOPE_NOT_SET")))


def test_unknown_connection_raises(tmp_path):
    with pytest.raises(ConfigError, match="unknown connection"):
        load(write(tmp_path, VALID.replace("connection: tgt", "connection: nope")))


def test_pair_without_checks_raises(tmp_path):
    bad = VALID.replace("    checks:\n      row_delta: { tolerance_pct: 0.5 }\n", "")
    with pytest.raises(ConfigError, match="no checks"):
        load(write(tmp_path, bad))
