"""钉钉逐篇推送测试:加签 / 单篇 payload / send 真值表 / 限流 / 配置加载 / 编排接线。

全部用桩 poster,不发真实网络请求;退避 sleep 注入为收集器以免测试真等;
autouse 夹具把 MIN_GAP 归零,避免限流等待真实睡眠。
编排接线复用桩法(真库 + 桩 bot)。
"""
import base64
import hashlib
import hmac
import logging
import time
import urllib.parse

import pytest

from src import notify as notify_mod
from src import orchestrator
from src.config import CrawlConfig, ConfigError, NotifyConfig, load_config

LOG = logging.getLogger("crawler")
WEBHOOK = "https://oapi.dingtalk.com/robot/send?access_token=abc123"
ROW = {"account": "中金点睛", "title": "构建中国特色新闻学",
       "url": "https://mp.weixin.qq.com/s?__biz=MzA1&mid=100&idx=1&sn=abc123",
       "date_text": "09月01日"}


@pytest.fixture(autouse=True)
def _no_ratelimit(monkeypatch):
    monkeypatch.setattr(notify_mod, "MIN_GAP", 0.0)  # 测试免真实限流等待


def _notify(**kw) -> NotifyConfig:
    base = dict(enabled=True, webhook=WEBHOOK)
    base.update(kw)
    return NotifyConfig(**base)


class _Poster:
    """可编程桩:记录每次 (url, payload),按脚本逐次返回或抛异常。"""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def __call__(self, url, payload):
        self.calls.append((url, payload))
        action = self.script[min(len(self.calls) - 1, len(self.script) - 1)]
        if isinstance(action, Exception):
            raise action
        return action


def _no_sleep(_sec):
    pass


# ------------------------------------------------ sign_webhook 加签

def test_sign_webhook_known_vector():
    ts, secret = 1720000000000, "SECtest"
    string_to_sign = f"{ts}\n{secret}"
    digest = hmac.new(secret.encode(), string_to_sign.encode(),
                      hashlib.sha256).digest()
    expected = (WEBHOOK + f"&timestamp={ts}&sign="
                + urllib.parse.quote_plus(base64.b64encode(digest).decode()))
    assert notify_mod.sign_webhook(WEBHOOK, secret, timestamp_ms=ts) == expected


def test_sign_webhook_empty_secret_unchanged():
    assert notify_mod.sign_webhook(WEBHOOK, "") == WEBHOOK


# ------------------------------------------------ build_payload 单篇消息体

def test_build_payload_single_article_with_mentions():
    p = notify_mod.build_payload(ROW, keyword="研报", at_robot_name="研报机器人",
                                 at_mobiles=["13800000000"])
    assert p["msgtype"] == "markdown"
    assert "研报" in p["markdown"]["title"] and ROW["title"][:10] in p["markdown"]["title"]
    text = p["markdown"]["text"]
    assert "### 新文章发布" in text
    assert f"[{ROW['title']}]({ROW['url']})" in text      # 标题即链接
    assert ROW["account"] in text and ROW["date_text"] in text
    assert "@研报机器人 @13800000000" in text  # markdown 消息 @ 必须写进正文才生效
    assert p["at"] == {"atMobiles": ["13800000000"], "atUserIds": [],
                       "isAtAll": False}


def test_build_payload_user_ids_need_no_text():
    p = notify_mod.build_payload(ROW, at_user_ids=["robot001"])
    assert p["at"] == {"atMobiles": [], "atUserIds": ["robot001"],
                       "isAtAll": False}
    assert "@" not in p["markdown"]["text"]  # userId 由客户端解析,无需正文 @


def test_build_payload_no_at_defaults():
    p = notify_mod.build_payload(ROW)
    assert p["at"] == {"atMobiles": [], "atUserIds": [], "isAtAll": False}
    assert "公众号更新" not in p["markdown"]["title"]  # 逐篇版无「更新N篇」字样


# ------------------------------------------------ send_article 真值表(桩 post)

def test_send_disabled_or_missing_url_never_posts():
    poster = _Poster([(200, {"errcode": 0})])
    assert notify_mod.send_article(_notify(enabled=False), ROW, post=poster,
                                   sleep=_no_sleep) == (False, "通知未启用")
    assert notify_mod.send_article(_notify(webhook=""), ROW, post=poster,
                                   sleep=_no_sleep) == (False, "缺少 webhook")
    assert notify_mod.send_article(_notify(), {**ROW, "url": ""}, post=poster,
                                   sleep=_no_sleep) == (False, "无URL不发送")
    assert poster.calls == []


def test_send_success_posts_once_and_signs_url():
    poster = _Poster([(200, {"errcode": 0})])
    ok, msg = notify_mod.send_article(_notify(secret="SECx"), ROW,
                                      post=poster, sleep=_no_sleep)
    assert ok is True and msg == "ok"
    assert len(poster.calls) == 1
    url, payload = poster.calls[0]
    assert url.startswith(WEBHOOK)
    assert "timestamp=" in url and "sign=" in url  # 配了 secret 必带加签参数
    assert ROW["title"] in payload["markdown"]["text"]


def test_send_errcode_nonzero_retries_three_times():
    poster = _Poster([(200, {"errcode": 310000, "errmsg": "sign not match"})])
    sleeps = []
    ok, msg = notify_mod.send_article(_notify(), ROW, post=poster,
                                      sleep=sleeps.append)
    assert ok is False
    assert len(poster.calls) == 3          # 首次 + 2 次重试
    assert sleeps == [1.0, 3.0]            # 退避 1s / 3s
    assert "310000" in msg                 # 失败原因可读


def test_send_post_raises_never_escapes():
    poster = _Poster([RuntimeError("网络抖动")])
    ok, msg = notify_mod.send_article(_notify(), ROW, post=poster,
                                      sleep=_no_sleep)
    assert ok is False
    assert len(poster.calls) == 3
    assert "网络抖动" in msg


def test_send_http_non_200_fails():
    poster = _Poster([(502, {"errcode": 0})])  # 非 200 即使 errcode=0 也算失败
    ok, _msg = notify_mod.send_article(_notify(), ROW, post=poster,
                                       sleep=_no_sleep)
    assert ok is False
    assert len(poster.calls) == 3


def test_min_gap_throttle_between_sends(monkeypatch):
    monkeypatch.setattr(notify_mod, "MIN_GAP", 5.0)
    monkeypatch.setattr(notify_mod, "_last_send", time.monotonic())
    poster = _Poster([(200, {"errcode": 0})])
    sleeps = []
    ok, _msg = notify_mod.send_article(_notify(), ROW, post=poster,
                                       sleep=sleeps.append)
    assert ok is True
    assert sleeps and abs(sleeps[0] - 5.0) < 0.5  # 距上次发送不足 MIN_GAP → 先等


# ------------------------------------------------ notify 配置加载

SETTINGS_TMPL = """\
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
{notify}"""
ACCOUNTS = "accounts:\n  - 中金点睛\n"
NOTIFY_ON = f"""\
notify:
  enabled: true
  webhook: "{WEBHOOK}"
  secret: SECsec
  keyword: 研报
  at_robot_name: 研报机器人
  at_user_ids:
    - robot001
  at_mobiles:
    - "13800000000"
    - 13900000000
  at_all: false
"""


def _write(tmp_path, notify_yaml=""):
    sp, ap = tmp_path / "settings.yaml", tmp_path / "accounts.yaml"
    sp.write_text(SETTINGS_TMPL.format(notify=notify_yaml), encoding="utf-8")
    ap.write_text(ACCOUNTS, encoding="utf-8")
    return sp, ap


def test_config_without_notify_section_defaults_disabled(tmp_path):
    sp, ap = _write(tmp_path)
    cfg = load_config(sp, ap)
    assert cfg.notify == NotifyConfig()  # 全默认:关、无 webhook、无 @ 目标


def test_config_notify_loaded(tmp_path):
    sp, ap = _write(tmp_path, NOTIFY_ON)
    cfg = load_config(sp, ap)
    n = cfg.notify
    assert n.enabled is True and n.webhook == WEBHOOK
    assert n.secret == "SECsec" and n.keyword == "研报"
    assert n.at_robot_name == "研报机器人"
    assert n.at_user_ids == ["robot001"]
    assert n.at_mobiles == ["13800000000", "13900000000"]  # int 也归一成 str
    assert n.at_all is False


def test_config_enabled_without_webhook_fails_fast(tmp_path):
    sp, ap = _write(tmp_path, NOTIFY_ON.replace(
        f'  webhook: "{WEBHOOK}"\n', '  webhook: ""\n'))
    with pytest.raises(ConfigError, match="webhook"):
        load_config(sp, ap)


def test_config_notify_wrong_type_raises(tmp_path):
    sp, ap = _write(tmp_path, NOTIFY_ON.replace(
        '  at_mobiles:\n    - "13800000000"\n    - 13900000000\n',
        "  at_mobiles: 研报\n"))
    with pytest.raises(ConfigError, match="at_mobiles"):
        load_config(sp, ap)
    sp2, ap2 = _write(tmp_path, NOTIFY_ON.replace(
        "  at_user_ids:\n    - robot001\n", "  at_user_ids: robot001\n"))
    with pytest.raises(ConfigError, match="at_user_ids"):
        load_config(sp2, ap2)
    sp3, ap3 = _write(tmp_path, NOTIFY_ON.replace("enabled: true", "enabled: yesstr"))
    with pytest.raises(ConfigError, match="enabled"):
        load_config(sp3, ap3)


# ------------------------------------------------ 编排接线(桩 bot + 桩 post)

class _FakeCtrl:
    def __init__(self, name, top):
        self.Name = name
        self.BoundingRectangle = type("R", (), {"top": top})()


def _cfg(tmp_path, notify: NotifyConfig) -> CrawlConfig:
    return CrawlConfig(
        process_name="WeChat.exe", exe_path="C:/x/WeChat.exe",
        expected_version_prefix="3.9", stop_streak=3, overlap_days=3,
        new_account_screens=2, scroll_wait_sec=0.0, account_gap_min_sec=0,
        account_gap_max_sec=0, article_open_timeout_sec=1.0,
        url_scan_timeout_sec=1.0, max_tree_nodes=500, close_tab_wait_sec=0.0,
        kick_retry=1, db_path=tmp_path / "db.sqlite", log_dir=tmp_path / "logs",
        accounts=["测试号"], notify=notify)


def _stub_round(monkeypatch, url_result):
    """桩一轮可跑通的抓取:开主页 → 扫到一篇 → 按 url_result 给 (raw, nodes)。"""
    from src import wechat_bot as bot
    monkeypatch.setattr(orchestrator.version_check, "check_environment",
                        lambda *a, **kw: {"ok": True, "message": "环境OK"})
    monkeypatch.setattr(orchestrator, "setup_logging", lambda log_dir: LOG)
    monkeypatch.setattr(bot, "search_open_profile", lambda name: (True, ""))
    monkeypatch.setattr(bot, "find_profile_host",
                        lambda account=None, kicks=0: (100, object()))
    monkeypatch.setattr(bot, "close_article_tabs", lambda **kw: True)
    monkeypatch.setattr(bot, "scan_list", lambda host, max_nodes=0:
                        ([_FakeCtrl("构建中国特色新闻学", 100.0)], [("昨天", 0.0)]))
    monkeypatch.setattr(bot, "close_profile_tab", lambda name, wait=0: True)
    monkeypatch.setattr(bot, "open_article_and_get_url", lambda ctrl, **kw: url_result)


def _seed_watermark(cfg, name="测试号"):
    from src.db import Store
    store = Store(cfg.db_path)
    acc_id = store.get_or_create_account(name)
    store.set_watermark(acc_id, "2026-09-01")  # 有水位线 → 单次扫描不扩量
    store.close()


def test_process_account_pushes_each_new_article(tmp_path, monkeypatch):
    from src.db import Store
    poster = _Poster([(200, {"errcode": 0})])
    monkeypatch.setattr(notify_mod, "_default_post", poster)
    _stub_round(monkeypatch,
                ("https://mp.weixin.qq.com/s?__biz=MzA1&mid=1&idx=1&sn=aa", 3))
    cfg = _cfg(tmp_path, _notify())
    _seed_watermark(cfg)
    store = Store(cfg.db_path)
    st = orchestrator.process_account(
        store, cfg, "测试号", LOG,
        push=orchestrator._make_pusher(cfg.notify, LOG))
    store.close()
    assert st["new"] == 1
    assert len(poster.calls) == 1              # 逐篇即推:一篇一条
    url, payload = poster.calls[0]
    assert url == WEBHOOK                      # 无 secret 不加签
    assert "构建中国特色新闻学" in payload["markdown"]["text"]


def test_process_account_pending_not_pushed(tmp_path, monkeypatch):
    from src.db import Store
    poster = _Poster([(200, {"errcode": 0})])
    monkeypatch.setattr(notify_mod, "_default_post", poster)
    _stub_round(monkeypatch, (None, -2))  # 标题不符 → pending,无 URL 可推
    cfg = _cfg(tmp_path, _notify())
    _seed_watermark(cfg)
    store = Store(cfg.db_path)
    st = orchestrator.process_account(
        store, cfg, "测试号", LOG,
        push=orchestrator._make_pusher(cfg.notify, LOG))
    store.close()
    assert st["pending"] == 1 and poster.calls == []


def test_run_pushes_per_article_when_enabled(tmp_path, monkeypatch):
    poster = _Poster([(200, {"errcode": 0})])
    monkeypatch.setattr(notify_mod, "_default_post", poster)
    _stub_round(monkeypatch,
                ("https://mp.weixin.qq.com/s?__biz=MzA1&mid=1&idx=1&sn=aa", 3))
    cfg = _cfg(tmp_path, _notify())
    _seed_watermark(cfg)
    assert orchestrator.run(cfg) == 0
    assert len(poster.calls) == 1              # run 内部自建 pusher 并逐篇推


def test_run_disabled_notify_never_posts(tmp_path, monkeypatch):
    poster = _Poster([(200, {"errcode": 0})])
    monkeypatch.setattr(notify_mod, "_default_post", poster)
    _stub_round(monkeypatch,
                ("https://mp.weixin.qq.com/s?__biz=MzA1&mid=1&idx=1&sn=aa", 3))
    cfg = _cfg(tmp_path, NotifyConfig())  # 默认关闭
    _seed_watermark(cfg)
    assert orchestrator.run(cfg) == 0
    assert poster.calls == []
