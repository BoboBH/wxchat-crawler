"""run() 轮级统计功能测试:失败重试 / 失败即刻钉钉告警 / 轮末总结(日志+钉钉)。

全部桩件不触真实 UIA/网络:process_account 按脚本返回,send_markdown 用
收集器记录,重试/账号间隔 sleep 注入为收集器。
"""
import logging

import pytest

from src import orchestrator
from src.config import CrawlConfig

LOG = logging.getLogger("run-test")


def _cfg(tmp_path, **over):
    kw = dict(process_name="WeChat.exe", exe_path="C:/x/WeChat.exe",
              expected_version_prefix="3.9", stop_streak=3, overlap_days=10,
              new_account_screens=2, deep_scroll_screens=40,
              scroll_wait_sec=0.0, account_gap_min_sec=0, account_gap_max_sec=0,
              article_open_timeout_sec=1.0, url_scan_timeout_sec=1.0,
              max_tree_nodes=500, close_tab_wait_sec=0.0, kick_retry=1,
              db_path=tmp_path / "db.sqlite", log_dir=tmp_path / "logs",
              accounts=["甲", "乙"])
    kw.update(over)
    return CrawlConfig(**kw)


class _Store:
    conn = object()  # sync_mysql 只读该属性(mysql 默认 disabled → 直接跳过)

    def __init__(self):
        self.finished = None
        self.closed = False

    def start_run(self):
        return 1

    def finish_run(self, run_id, ok_count, fail_count, new_count):
        self.finished = (ok_count, fail_count, new_count)

    def close(self):
        self.closed = True


class _Md:
    """send_markdown 收集器。"""

    def __init__(self):
        self.calls = []

    def __call__(self, notify, title, text, mention=True, **kw):
        self.calls.append((title, text, mention))
        return True, "ok"


@pytest.fixture
def env(monkeypatch):
    """run() 全套桩:环境 OK/假 Store/顺序保持/sleep 收集/send_markdown 收集。"""
    store = _Store()
    sleeps: list[float] = []
    md = _Md()
    monkeypatch.setattr(orchestrator, "setup_logging", lambda d: LOG)
    monkeypatch.setattr(orchestrator.version_check, "check_environment",
                        lambda *a, **k: {"ok": True, "message": "env-ok"})
    monkeypatch.setattr(orchestrator, "Store", lambda path: store)
    monkeypatch.setattr(orchestrator.random, "shuffle", lambda x: None)
    monkeypatch.setattr(orchestrator.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(orchestrator.notify_mod, "send_markdown", md)
    # run() 轮末页签清扫(2026-09-04)会调 bot,桩掉防触真实 UIA
    monkeypatch.setattr(orchestrator.bot, "close_article_tabs",
                        lambda **kw: True)
    return store, sleeps, md


def _install(monkeypatch, results):
    """[(ok, new, message), …] → process_account 桩,返回调用序记录。"""
    calls: list[str] = []
    seq = list(results)

    def fake(store, cfg, name, log, position=None, push=None):
        calls.append(name)
        ok, new, msg = seq[min(len(calls) - 1, len(seq) - 1)]
        return {"ok": ok, "new": new, "upgraded": 0, "pending": 0,
                "message": msg}

    monkeypatch.setattr(orchestrator, "process_account", fake)
    return calls


def test_retry_then_success(tmp_path, env, monkeypatch):
    store, sleeps, md = env
    calls = _install(monkeypatch,
                     [(False, 0, "主页未就绪"), (True, 4, "扫描10条,新增4")])
    rc = orchestrator.run(_cfg(tmp_path), only_account="甲")
    assert calls == ["甲", "甲"]            # 失败后重试了一次
    assert sleeps == [10.0]                 # 重试前暂停 10s
    assert store.finished == (1, 0, 4)
    assert rc == 0
    assert len(md.calls) == 1               # 仅轮末总结,无失败告警
    assert md.calls[0][2] is False          # 总结不带 @


def test_retry_exhausted_alert_then_summary(tmp_path, env, monkeypatch):
    store, sleeps, md = env
    calls = _install(monkeypatch, [(False, 0, "主页未就绪")])
    rc = orchestrator.run(_cfg(tmp_path, fail_retry=3), only_account="甲")
    assert calls == ["甲"] * 4              # 首次 + 3 次重试
    assert sleeps == [10.0, 10.0, 10.0]
    assert store.finished == (0, 1, 0)
    assert rc == 1
    assert len(md.calls) == 2               # 即刻告警 + 轮末总结
    alert_title, alert_text, alert_mention = md.calls[0]
    assert "甲" in alert_title and alert_mention is True     # 告警带 @
    assert "主页未就绪" in alert_text and "共尝试 4 次" in alert_text
    sum_title, sum_text, sum_mention = md.calls[1]
    assert sum_mention is False
    assert "失败: 甲 —— 主页未就绪" in sum_text


def test_fail_retry_zero_no_retry(tmp_path, env, monkeypatch):
    store, sleeps, md = env
    calls = _install(monkeypatch, [(False, 0, "搜索失败")])
    orchestrator.run(_cfg(tmp_path, fail_retry=0), only_account="甲")
    assert calls == ["甲"]                  # 不重试
    assert sleeps == []                     # 也没有重试等待
    assert len(md.calls) == 2               # 仍即刻告警 + 总结


def test_summary_counts_and_lists(tmp_path, env, monkeypatch):
    store, sleeps, md = env
    calls = _install(monkeypatch, [(True, 5, "扫描31条,新增5"),
                                   (False, 0, "主页未就绪")])
    orchestrator.run(_cfg(tmp_path))        # 全量轮:甲、乙(已禁 shuffle)
    assert calls == ["甲", "乙", "乙", "乙", "乙"]  # 乙 默认重试3次共4尝试
    assert store.finished == (1, 1, 5)
    assert len(md.calls) == 2               # 乙的即刻告警 + 轮末总结
    title, text, mention = md.calls[-1]
    assert "成功账号: **1/2**" in text
    assert "新增文章: **5** 篇" in text
    assert "失败账号: **1** 个" in text
    assert "失败: 乙 —— 主页未就绪" in text
    assert "成功列表: 甲" in text
    alert_title, _at, alert_mention = md.calls[0]
    assert "乙" in alert_title and alert_mention is True


def test_run_summary_text_pure():
    t = orchestrator._run_summary_text(
        ["甲", "乙"], [("丙", "异常(见日志)")], 7, 3)
    assert "- 成功账号: **2/3** 个" in t
    assert "- 新增文章: **7** 篇" in t
    assert "- 失败: 丙 —— 异常(见日志)" in t
    assert t.endswith("成功列表: 甲、乙")
    t2 = orchestrator._run_summary_text(["甲"], [], 0, 1)
    assert "失败账号: **0** 个" in t2
    assert "失败: " not in t2               # 全成功无失败行
