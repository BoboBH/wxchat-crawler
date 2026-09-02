"""配置加载:settings.yaml(参数)+ accounts.yaml(公众号名单)。"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SETTINGS = PROJECT_ROOT / "config" / "settings.yaml"
DEFAULT_ACCOUNTS = PROJECT_ROOT / "config" / "accounts.yaml"


class ConfigError(Exception):
    """配置缺失或字段非法。"""


@dataclass
class CrawlConfig:
    process_name: str
    exe_path: str
    expected_version_prefix: str
    stop_streak: int
    overlap_days: int
    new_account_screens: int
    scroll_wait_sec: float
    account_gap_min_sec: int
    account_gap_max_sec: int
    article_open_timeout_sec: float
    url_scan_timeout_sec: float
    max_tree_nodes: int
    close_tab_wait_sec: float
    kick_retry: int
    db_path: Path
    log_dir: Path
    accounts: list[str] = field(default_factory=list)


def _require(d: dict, key: str, where: str):
    if key not in d or d[key] is None:
        raise ConfigError(f"{where} 缺少字段 {key!r}")
    return d[key]


def load_config(settings_path=DEFAULT_SETTINGS,
                accounts_path=DEFAULT_ACCOUNTS) -> CrawlConfig:
    settings_path, accounts_path = Path(settings_path), Path(accounts_path)
    if not settings_path.exists():
        raise ConfigError(f"找不到配置文件: {settings_path}")
    if not accounts_path.exists():
        raise ConfigError(f"找不到名单文件: {accounts_path}")
    s = yaml.safe_load(settings_path.read_text(encoding="utf-8")) or {}
    a = yaml.safe_load(accounts_path.read_text(encoding="utf-8")) or {}
    wechat = s.get("wechat") or {}
    crawl = s.get("crawl") or {}
    article = s.get("article") or {}
    run = s.get("run") or {}
    accounts = [str(x).strip() for x in (a.get("accounts") or []) if str(x).strip()]
    if not accounts:
        raise ConfigError("accounts.yaml 未配置任何公众号")
    cfg = CrawlConfig(
        process_name=str(_require(wechat, "process_name", "settings.wechat")),
        exe_path=str(_require(wechat, "exe_path", "settings.wechat")),
        expected_version_prefix=str(_require(wechat, "expected_version_prefix", "settings.wechat")),
        stop_streak=int(_require(crawl, "stop_streak", "settings.crawl")),
        overlap_days=int(_require(crawl, "overlap_days", "settings.crawl")),
        new_account_screens=int(_require(crawl, "new_account_screens", "settings.crawl")),
        scroll_wait_sec=float(_require(crawl, "scroll_wait_sec", "settings.crawl")),
        account_gap_min_sec=int(_require(crawl, "account_gap_min_sec", "settings.crawl")),
        account_gap_max_sec=int(_require(crawl, "account_gap_max_sec", "settings.crawl")),
        article_open_timeout_sec=float(_require(article, "open_timeout_sec", "settings.article")),
        url_scan_timeout_sec=float(_require(article, "url_scan_timeout_sec", "settings.article")),
        max_tree_nodes=int(_require(article, "max_tree_nodes", "settings.article")),
        close_tab_wait_sec=float(_require(article, "close_tab_wait_sec", "settings.article")),
        kick_retry=int(_require(article, "kick_retry", "settings.article")),
        db_path=Path(str(_require(run, "db_path", "settings.run"))),
        log_dir=Path(str(_require(run, "log_dir", "settings.run"))),
        accounts=accounts,
    )
    if cfg.stop_streak < 1:
        raise ConfigError("crawl.stop_streak 必须 >= 1")
    if cfg.overlap_days < 0:
        raise ConfigError("crawl.overlap_days 不能为负")
    if cfg.account_gap_min_sec > cfg.account_gap_max_sec:
        raise ConfigError("crawl.account_gap_min_sec 不能大于 account_gap_max_sec")
    return cfg
