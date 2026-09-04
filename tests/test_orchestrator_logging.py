"""orchestrator 分步计时日志测试(fmt_duration / log_step / 逐篇用时行)。

背景(2026-09-03 用户诉求):「每次反馈结果,都打印日期和时间,我要知道
每一步的进展和时耗」—— 日志行带完整日期(datefmt)+ 每步(含逐篇)用时。
只测编排层日志形状,不触真实 UIA(全部 monkeypatch 桩),store 用 tmp_path
真库(与 test_db.py 同前提)。
"""
import logging
import re

import pytest

from src import orchestrator
from src.config import CrawlConfig

LOG = logging.getLogger("crawler")


# ------------------------------------------------ fmt_duration 真值表

@pytest.mark.parametrize("seconds,expected", [
    (0, "0.0s"),
    (38.24, "38.2s"),
    (59.96, "60.0s"),     # 60s 边界前仍按秒显示(进位到 60.0s 可接受)
    (60, "1分0.0s"),
    (272.34, "4分32.3s"),
])
def test_fmt_duration_truth_table(seconds, expected):
    assert orchestrator.fmt_duration(seconds) == expected


# ------------------------------------------------ log_step 上下文管理器

def test_log_step_start_done_lines_with_duration(caplog, monkeypatch):
    ticks = iter([100.0, 112.4])  # 固定 t0/t1 → 用时恒为 12.4s
    monkeypatch.setattr(orchestrator.time, "perf_counter", lambda: next(ticks))
    with caplog.at_level(logging.INFO, logger="crawler"):
        with orchestrator.log_step(LOG, "[%s] 列表扫描", "测试号") as done:
            done.append("9条")
    msgs = [r.getMessage() for r in caplog.records]
    assert msgs == ["[测试号] 列表扫描 …", "[测试号] 列表扫描 完成(9条) 用时12.4s"]


def test_log_step_failure_line_then_reraise(caplog):
    with caplog.at_level(logging.INFO, logger="crawler"):
        with pytest.raises(RuntimeError, match="boom"):
            with orchestrator.log_step(LOG, "步骤%s", "甲"):
                raise RuntimeError("boom")
    last = caplog.records[-1].getMessage()
    assert re.fullmatch(r"步骤甲 失败 用时\d+\.\d+s", last)


def test_setup_logging_writes_full_datetime(tmp_path):
    root = logging.getLogger()
    saved = list(root.handlers)
    try:
        orchestrator.setup_logging(tmp_path)
        fmts = [h.formatter for h in root.handlers if h.formatter]
        assert all(f.datefmt == "%Y-%m-%d %H:%M:%S" for f in fmts)
        rec = logging.LogRecord("crawler", logging.INFO, "f", 1, "你好 %s", ("x",), None)
        assert re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} INFO 你好 x$", fmts[0].format(rec))
    finally:
        for h in root.handlers:
            if h not in saved:
                h.close()
        root.handlers[:] = saved


# --------------------------------------------- process_account 逐篇计时行(集成)

class _FakeCtrl:
    def __init__(self, name, top):
        self.Name = name
        self.BoundingRectangle = type("R", (), {"top": top})()


def _cfg(tmp_path) -> CrawlConfig:
    return CrawlConfig(
        process_name="WeChat.exe", exe_path="C:/x/WeChat.exe",
        expected_version_prefix="3.9", stop_streak=3, overlap_days=3,
        new_account_screens=2, deep_scroll_screens=40,
        scroll_wait_sec=0.0, account_gap_min_sec=0,
        account_gap_max_sec=0, article_open_timeout_sec=1.0,
        url_scan_timeout_sec=1.0, max_tree_nodes=500, close_tab_wait_sec=0.0,
        kick_retry=1, db_path=tmp_path / "db.sqlite", log_dir=tmp_path / "logs",
        accounts=["测试号"])


def test_process_account_logs_per_article_step_line(tmp_path, caplog, monkeypatch):
    from src.db import Store
    from src import wechat_bot as bot

    monkeypatch.setattr(bot, "search_open_profile", lambda name: (True, ""))
    monkeypatch.setattr(bot, "find_profile_host",
                        lambda account=None, kicks=0: (100, object()))
    monkeypatch.setattr(bot, "close_article_tabs", lambda **kw: True)
    monkeypatch.setattr(bot, "scroll_once",
                        lambda host, wheels=10, anchor=None: None)
    monkeypatch.setattr(bot, "scroll_to_top",
                        lambda host, wheels=30, wait=0, anchor=None: None)
    monkeypatch.setattr(bot, "scan_list",
                        lambda host, max_nodes=0:
                        ([_FakeCtrl("构建中国特色新闻学", 100.0)], [("昨天", 0.0)]))
    monkeypatch.setattr(bot, "close_profile_tab", lambda name, wait=0: True)
    monkeypatch.setattr(bot, "open_article_and_get_url",
                        lambda ctrl, **kw:
                        ("https://mp.weixin.qq.com/s?__biz=MzA1&mid=100&idx=1&sn=abc123", 3))

    cfg = _cfg(tmp_path)
    store = Store(cfg.db_path)
    acc_id = store.get_or_create_account("测试号")
    store.set_watermark(acc_id, "2026-09-01")  # 有水位线 → 走单次扫描,不扩量

    with caplog.at_level(logging.INFO, logger="crawler"):
        st = orchestrator.process_account(store, cfg, "测试号", LOG, position="1/1")
    store.close()

    msgs = [r.getMessage() for r in caplog.records]
    assert any(re.fullmatch(r"\[测试号\] 开始处理账号\(1/1\)", m) for m in msgs)
    assert any(re.fullmatch(r"\[测试号\] 第1/1篇《构建中国特色新闻学》打开文章页提取URL …", m)
               for m in msgs)
    # 既有「+ 标题 (日期) url=状态」行保持原文,仅追加 结果(用时Xs)
    assert any(re.fullmatch(r"\[测试号\] \+ 构建中国特色新闻学 \(昨天\) url=ok → 新增\(用时\d+\.\d+s\)", m)
               for m in msgs)
    assert st["message"] == "扫描1条,新增1,补URL0,待补0"  # 既有汇总文案不变
