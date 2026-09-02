"""config 加载测试:正常路径、缺文件、缺字段、非法值。"""
from pathlib import Path

import pytest

from src.config import ConfigError, load_config

SETTINGS = """\
wechat:
  process_name: Weixin.exe
  exe_path: C:\\Program Files\\Tencent\\Weixin\\Weixin.exe
  expected_version_prefix: "4.1"
crawl:
  stop_streak: 3
  overlap_days: 2
  new_account_screens: 2
  scroll_wait_sec: 2.0
  account_gap_min_sec: 20
  account_gap_max_sec: 60
article:
  open_timeout_sec: 40
  url_scan_timeout_sec: 20
  max_tree_nodes: 5000
  close_tab_wait_sec: 2.5
  kick_retry: 2
run:
  db_path: data/crawler.db
  log_dir: logs
"""
ACCOUNTS = "accounts:\n  - 中金点睛\n  - 郭磊宏观茶座\n"


def _write(tmp_path, settings=SETTINGS, accounts=ACCOUNTS):
    sp = tmp_path / "settings.yaml"
    ap = tmp_path / "accounts.yaml"
    sp.write_text(settings, encoding="utf-8")
    ap.write_text(accounts, encoding="utf-8")
    return sp, ap


def test_load_ok(tmp_path):
    sp, ap = _write(tmp_path)
    cfg = load_config(sp, ap)
    assert cfg.process_name == "Weixin.exe"
    assert cfg.stop_streak == 3
    assert cfg.account_gap_min_sec == 20
    assert cfg.article_open_timeout_sec == 40
    assert cfg.db_path == Path("data/crawler.db")
    assert cfg.accounts == ["中金点睛", "郭磊宏观茶座"]


def test_missing_settings_file(tmp_path):
    with pytest.raises(ConfigError, match="找不到配置文件"):
        load_config(tmp_path / "nope.yaml", tmp_path / "a.yaml")


def test_missing_accounts_file(tmp_path):
    sp, _ = _write(tmp_path)
    with pytest.raises(ConfigError, match="找不到名单文件"):
        load_config(sp, tmp_path / "nope.yaml")


def test_empty_accounts(tmp_path):
    sp, ap = _write(tmp_path, accounts="accounts: []\n")
    with pytest.raises(ConfigError, match="未配置任何公众号"):
        load_config(sp, ap)


def test_missing_field(tmp_path):
    bad = SETTINGS.replace("  stop_streak: 3\n", "")
    sp, ap = _write(tmp_path, settings=bad)
    with pytest.raises(ConfigError, match="stop_streak"):
        load_config(sp, ap)


def test_bad_streak(tmp_path):
    sp, ap = _write(tmp_path, settings=SETTINGS.replace("stop_streak: 3", "stop_streak: 0"))
    with pytest.raises(ConfigError, match="stop_streak"):
        load_config(sp, ap)


def test_bad_gap(tmp_path):
    sp, ap = _write(tmp_path, settings=SETTINGS.replace("account_gap_min_sec: 20",
                                                        "account_gap_min_sec: 999"))
    with pytest.raises(ConfigError, match="account_gap"):
        load_config(sp, ap)
