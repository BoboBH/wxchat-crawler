"""配置加载:settings.yaml(参数+钉钉通知)+ accounts.yaml(公众号名单)。"""
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
    notify: "NotifyConfig" = field(default_factory=lambda: NotifyConfig())


@dataclass
class NotifyConfig:
    """钉钉逐篇推送配置(webhook/secret 也在本文件,注意勿外传)。"""

    enabled: bool = False
    webhook: str = ""
    secret: str = ""            # 机器人安全设置选「加签」时的 SEC 密钥
    keyword: str = ""           # 安全设置选「自定义关键词」时每条消息必含
    at_robot_name: str = ""     # 要@的应用机器人「群昵称」(写入正文 @文本)
    at_user_ids: list[str] = field(default_factory=list)  # userId(可@机器人)
    at_mobiles: list[str] = field(default_factory=list)   # 手机号(真@提醒)
    at_all: bool = False


def _require(d: dict, key: str, where: str):
    if key not in d or d[key] is None:
        raise ConfigError(f"{where} 缺少字段 {key!r}")
    return d[key]


def _parse_bool(v, where: str) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, str) and v.strip().lower() in ("true", "false", "yes", "no", "1", "0"):
        return v.strip().lower() in ("true", "yes", "1")
    raise ConfigError(f"{where} 必须是布尔值(true/false),得到 {v!r}")


def _parse_str_list(v, where: str) -> list[str]:
    if v is None:
        return []
    if not isinstance(v, list):
        raise ConfigError(f"{where} 必须是列表(每行 '- 值'),得到 {v!r}")
    return [str(x).strip() for x in v if str(x).strip()]


def _parse_notify(n) -> NotifyConfig:
    if n is not None and not isinstance(n, dict):
        raise ConfigError("settings.notify 必须是键值映射")
    n = n or {}
    return NotifyConfig(
        enabled=_parse_bool(n.get("enabled", False), "notify.enabled"),
        webhook=str(n.get("webhook") or "").strip(),
        secret=str(n.get("secret") or "").strip(),
        keyword=str(n.get("keyword") or "").strip(),
        at_robot_name=str(n.get("at_robot_name") or "").strip(),
        at_user_ids=_parse_str_list(n.get("at_user_ids"), "notify.at_user_ids"),
        at_mobiles=_parse_str_list(n.get("at_mobiles"), "notify.at_mobiles"),
        at_all=_parse_bool(n.get("at_all", False), "notify.at_all"),
    )


def load_config(settings_path=DEFAULT_SETTINGS,
                accounts_path=DEFAULT_ACCOUNTS) -> CrawlConfig:
    settings_path, accounts_path = Path(settings_path), Path(accounts_path)
    if not settings_path.exists():
        raise ConfigError(f"找不到配置文件: {settings_path}")
    if not accounts_path.exists():
        raise ConfigError(f"找不到名单文件: {accounts_path}")
    s = yaml.safe_load(settings_path.read_text(encoding="utf-8")) or {}
    if not isinstance(s, dict):
        raise ConfigError(f"{settings_path} 内容必须是键值映射")
    a = yaml.safe_load(accounts_path.read_text(encoding="utf-8")) or {}
    if not isinstance(a, dict):
        raise ConfigError(f"{accounts_path} 内容必须是键值映射")
    wechat = s.get("wechat") or {}
    crawl = s.get("crawl") or {}
    article = s.get("article") or {}
    run = s.get("run") or {}
    raw_accounts = a.get("accounts")
    if raw_accounts is not None and not isinstance(raw_accounts, list):
        raise ConfigError("accounts.yaml 的 accounts 必须是列表(每行 '- 名称')")
    accounts = [str(x).strip() for x in (raw_accounts or []) if str(x).strip()]
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
    cfg.notify = _parse_notify(s.get("notify"))
    if cfg.notify.enabled and not cfg.notify.webhook:
        raise ConfigError("notify.enabled=true 但 notify.webhook 为空")
    return cfg
