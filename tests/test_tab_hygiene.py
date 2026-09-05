"""页签卫生(2026-09-04 需求「打开公众号/文章抓完即关页签」)的三个清理点。

  * process_account 开主页前:清上次崩溃/强杀遗留的残留文章 tab 与同账号
    主页 tab(旧主页 tab 列表永不刷新,find_profile_host 命中它会把水位线
    冻结在旧状态);
  * _attempt_account 异常轮:文章 tab + 主页 tab 都要尽力收(旧实现只收主页
    tab,异常瞬间开着的文章 tab 会泄漏);
  * run() 轮末 finally:兜底清扫残留文章 tab(任何退出路径都执行)。

全部桩件不触真实 UIA;流程桩复用 test_notify 的做法(真库 + 桩 bot)。
"""
import logging

from src import orchestrator
from src.config import CrawlConfig, MysqlConfig

LOG = logging.getLogger("tab-hygiene")

# 独立测试库表名(与根 conftest.py 约定一致);真正连库的用例经
# mysql_ready/store 夹具注入带连接参数的配置。
_TEST_DB = MysqlConfig(database="wxchat_crawler_test",
                       table_accounts="wt_accounts",
                       table_articles="wt_articles", table_runs="wt_runs")


def _cfg(tmp_path, **over):
    kw = dict(process_name="WeChat.exe", exe_path="C:/x/WeChat.exe",
              expected_version_prefix="3.9", stop_streak=3, overlap_days=10,
              new_account_screens=2, deep_scroll_screens=40,
              scroll_wait_sec=0.0, account_gap_min_sec=0, account_gap_max_sec=0,
              article_open_timeout_sec=1.0, url_scan_timeout_sec=1.0,
              max_tree_nodes=500, close_tab_wait_sec=0.0, kick_retry=1,
              log_dir=tmp_path / "logs", mysql=_TEST_DB,
              accounts=["测试号"])
    kw.update(over)
    return CrawlConfig(**kw)


class _FakeCtrl:
    def __init__(self, name, top):
        self.Name = name
        self.BoundingRectangle = type("R", (), {"top": top})()


class _Recorder:
    """bot 导航/清理桩:按序记录调用名,便于断言页签清理时机。"""

    def __init__(self, monkeypatch):
        self.seq: list[str] = []
        b = orchestrator.bot
        monkeypatch.setattr(b, "close_article_tabs",
                            lambda **kw:
                            self.seq.append("close_article_tabs") or True)
        monkeypatch.setattr(b, "close_profile_tab",
                            lambda name, wait=0:
                            self.seq.append("close_profile_tab") or True)
        monkeypatch.setattr(b, "search_open_profile",
                            lambda name:
                            self.seq.append("search") or (True, "doc"))
        monkeypatch.setattr(b, "find_profile_host",
                            lambda account=None, kicks=0: (100, object()))
        monkeypatch.setattr(b, "scroll_once",
                            lambda host, wheels=10, anchor=None: None)
        monkeypatch.setattr(b, "scroll_to_top",
                            lambda host, wheels=30, wait=0, anchor=None: None)
        monkeypatch.setattr(b, "scan_list", lambda host, max_nodes=0:
                            ([_FakeCtrl("构建中国特色新闻学", 100.0)],
                             [("昨天", 0.0)]))
        monkeypatch.setattr(b, "expand_fold_bars", lambda host, max_nodes=0: 0)
        monkeypatch.setattr(b, "open_article_and_get_url",
                            lambda ctrl, **kw:
                            ("https://mp.weixin.qq.com/s?__biz=MzA1"
                             "&mid=1&idx=1&sn=aa", 1))


def test_process_account_cleans_stale_tabs_before_search(tmp_path, monkeypatch,
                                                         mysql_ready, store,
                                                         make_name):
    rec = _Recorder(monkeypatch)
    name = make_name("测试号")
    cfg = _cfg(tmp_path, mysql=mysql_ready, accounts=[name])
    st = orchestrator.process_account(store, cfg, name, LOG)
    assert st["ok"] is True
    # 开主页前先清残留文章 tab + 同账号主页 tab;收尾再关主页 tab
    assert rec.seq[:3] == ["close_article_tabs", "close_profile_tab", "search"]
    assert rec.seq[-1] == "close_profile_tab"


def test_search_failure_still_cleans(tmp_path, monkeypatch, mysql_ready, store,
                                     make_name):
    rec = _Recorder(monkeypatch)
    monkeypatch.setattr(orchestrator.bot, "search_open_profile",
                        lambda name:
                        rec.seq.append("search") or (False, "搜索失败"))
    name = make_name("测试号")
    cfg = _cfg(tmp_path, mysql=mysql_ready, accounts=[name])
    st = orchestrator.process_account(store, cfg, name, LOG)
    assert st["ok"] is False
    # 搜索失败的早退路径:前置清理照做,失败后也尽力收主页 tab
    assert rec.seq == ["close_article_tabs", "close_profile_tab", "search",
                       "close_profile_tab"]


def test_attempt_account_exception_closes_article_tabs(tmp_path, monkeypatch):
    rec = _Recorder(monkeypatch)

    def boom(store, cfg, name, log, position=None, push=None):
        raise RuntimeError("UIA COM 崩了")

    monkeypatch.setattr(orchestrator, "process_account", boom)
    cfg = _cfg(tmp_path, fail_retry=0)
    st, attempts = orchestrator._attempt_account(
        object(), cfg, "测试号", LOG, position=None, push=None)
    assert attempts == 1 and st["ok"] is False
    assert "异常" in st["message"]
    # 异常轮也要收文章 tab(只收主页 tab 会泄漏开着的文章页)
    assert rec.seq == ["close_article_tabs", "close_profile_tab"]


def test_run_end_of_round_sweeps_tabs(tmp_path, monkeypatch):
    rec = _Recorder(monkeypatch)

    def fake(store, cfg, name, log, position=None, push=None):
        return {"ok": True, "new": 1, "upgraded": 0, "pending": 0,
                "message": "扫描1条,新增1"}

    monkeypatch.setattr(orchestrator, "process_account", fake)
    monkeypatch.setattr(orchestrator, "setup_logging", lambda d: LOG)
    monkeypatch.setattr(orchestrator.version_check, "check_environment",
                        lambda *a, **k: {"ok": True, "message": "env-ok"})

    class _Store:
        conn = object()

        def start_run(self):
            return 1

        def finish_run(self, *a, **k):
            pass

        def close(self):
            pass

    monkeypatch.setattr(orchestrator, "Store", lambda cfg: _Store())
    monkeypatch.setattr(orchestrator.random, "shuffle", lambda x: None)
    monkeypatch.setattr(orchestrator.time, "sleep", lambda s: None)
    monkeypatch.setattr(orchestrator.notify_mod, "send_markdown",
                        lambda *a, **k: (True, "ok"))
    assert orchestrator.run(_cfg(tmp_path)) == 0
    # 轮末兜底清扫:即使每账号都正常收尾,run 收尾也要再扫一次残留文章页
    assert rec.seq.count("close_article_tabs") == 1
