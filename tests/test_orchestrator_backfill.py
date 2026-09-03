"""老账号按截止日深度滚动回填测试(_covers_cutoff / _collect_list 回填模式)。

背景(2026-09-04 用户诉求):overlap_days=10 要「真正回填 10 天」,老账号
不能再只读首屏 —— 最旧可见日期未早于截止日就继续下滚(至多
deep_scroll_screens 屏),首屏已覆盖则零滚动,触底提前停,滚后回顶重扫。
全部 monkeypatch 桩,不触真实 UIA。
"""
import logging
import sqlite3

import pytest

from src import orchestrator
from src.config import CrawlConfig

LOG = logging.getLogger("crawler")


class _FakeCtrl:
    def __init__(self, name, top):
        self.Name = name
        self.BoundingRectangle = type("R", (), {"top": top})()


def _ctrls(specs):
    """[(标题, ISO日期或None)] → (ctrls, times);top 按序递增,time 略高于标题。"""
    ctrls, times = [], []
    for i, (name, iso) in enumerate(specs):
        ctrls.append(_FakeCtrl(name, 100.0 * (i + 1)))
        if iso:
            times.append((iso, 100.0 * (i + 1) - 50))
    return ctrls, times


def _cfg(tmp_path, **over):
    kw = dict(process_name="WeChat.exe", exe_path="C:/x/WeChat.exe",
              expected_version_prefix="3.9", stop_streak=3, overlap_days=10,
              new_account_screens=2, deep_scroll_screens=40,
              scroll_wait_sec=0.0, account_gap_min_sec=0, account_gap_max_sec=0,
              article_open_timeout_sec=1.0, url_scan_timeout_sec=1.0,
              max_tree_nodes=500, close_tab_wait_sec=0.0, kick_retry=1,
              db_path=tmp_path / "db.sqlite", log_dir=tmp_path / "logs",
              accounts=["测试号"])
    kw.update(over)
    return CrawlConfig(**kw)


class _Recorder:
    """记录 scroll/scan 调用;scan 按预置屏序出数据,超出后重复末屏。"""

    def __init__(self, screens):
        self.screens = screens
        self.scans = 0
        self.scrolls = 0
        self.tops = 0

    def scan_list(self, host, max_nodes=0):
        i = min(self.scans, len(self.screens) - 1)
        self.scans += 1
        return _ctrls(self.screens[i])

    def scroll_once(self, host, wheels=10):
        self.scrolls += 1

    def scroll_to_top(self, host, wheels=30, wait=0.0):
        self.tops += 1


@pytest.fixture
def rec(monkeypatch):
    def _install(screens):
        r = _Recorder(screens)
        monkeypatch.setattr(orchestrator.bot, "scan_list", r.scan_list)
        monkeypatch.setattr(orchestrator.bot, "scroll_once", r.scroll_once)
        monkeypatch.setattr(orchestrator.bot, "scroll_to_top", r.scroll_to_top)
        return r
    return _install


# ------------------------------------------------ _covers_cutoff 真值表

@pytest.mark.parametrize("dates,cutoff,expected", [
    (["2026-09-03", "2026-08-25"], "2026-08-28", True),   # 最旧已早于截止日
    (["2026-09-03", "2026-09-01"], "2026-08-28", False),  # 尚未滚到窗口底
    ([None, None], "2026-08-28", False),                  # 全不可解析 → 继续滚
    ([], "2026-08-28", False),                            # 空 → 继续滚
])
def test_covers_cutoff_truth_table(dates, cutoff, expected):
    assert orchestrator._covers_cutoff(dates, cutoff) is expected


# ------------------------------------------------ _collect_list 回填模式

def test_first_screen_already_covers_no_scroll(tmp_path, rec):
    r = rec([[("新文", "2026-09-03"), ("旧文", "2026-08-25")]])
    _titles, pairs, _dates = orchestrator._collect_list(
        _cfg(tmp_path), "host", "2026-08-28")
    assert (r.scans, r.scrolls, r.tops) == (1, 0, 0)  # 首屏已覆盖 → 零滚动
    assert len(pairs) == 2


def test_deep_scrolls_until_covered_then_rescan_on_top(tmp_path, rec):
    r = rec(screens=[
        [("新文", "2026-09-03"), ("中", "2026-09-01")],   # 首屏未覆盖
        [("新文", "2026-09-03"), ("中", "2026-09-01"), ("旧", "2026-08-25")],
    ])
    _titles, pairs, _dates = orchestrator._collect_list(
        _cfg(tmp_path), "host", "2026-08-28")
    assert r.scrolls == 1              # 滚一屏即覆盖
    assert r.tops == 1                 # 滚过 → 回顶重扫
    assert r.scans == 3                # 首扫 + 滚后扫 + 回顶重扫
    assert [t for t, _d in pairs] == ["新文", "中", "旧"]


def test_stop_at_bottom_without_top_rescan(tmp_path, rec):
    r = rec(screens=[
        [("新文", "2026-09-03")],
        [("新文", "2026-09-03")],      # 触底:条数不增
    ])
    _titles, pairs, _dates = orchestrator._collect_list(
        _cfg(tmp_path), "host", "2026-08-28")
    assert r.scrolls == 1              # 试滚一屏
    assert r.tops == 0                 # 未增 → 判触底,不再回顶重扫
    assert r.scans == 2
    assert len(pairs) == 1


def test_screen_cap_bounds_scrolling(tmp_path, rec):
    # 每屏条数严格递增(2,3,4,…),永不触发「触底」,验证屏数上限兜底
    growing = [[("新文", "2026-09-03")]
               + [(f"文{i}_{j}", "2026-09-02") for j in range(i + 1)]
               for i in range(10)]
    r = rec(screens=[[("新文", "2026-09-03")]] + growing)
    _titles, pairs, _dates = orchestrator._collect_list(
        _cfg(tmp_path, deep_scroll_screens=3), "host", "2026-08-28")
    assert r.scrolls == 3              # 屏数上限兜底
    assert r.tops == 1
    assert r.scans == 1 + 3 + 1
    assert len(pairs) == 5             # 回顶重扫取 growing[3] 屏(1+4 条)


def test_deep_scroll_disabled_zero_keeps_first_screen_only(tmp_path, rec):
    r = rec([[("新文", "2026-09-03")]])
    _titles, _pairs, _dates = orchestrator._collect_list(
        _cfg(tmp_path, deep_scroll_screens=0), "host", "2026-08-28")
    assert (r.scans, r.scrolls, r.tops) == (1, 0, 0)


def test_new_account_path_unchanged(tmp_path, rec):
    r = rec(screens=[
        [("新文", "2026-09-03")],
        [("新文", "2026-09-03"), ("文2", "2026-09-02")],
        [("新文", "2026-09-03"), ("文2", "2026-09-02"), ("文3", "2026-09-01")],
    ])
    _titles, pairs, _dates = orchestrator._collect_list(
        _cfg(tmp_path), "host", None)  # 无水位线 → 新账号固定扩量
    assert r.scrolls == 2              # new_account_screens=2
    assert r.tops == 1
    assert len(pairs) == 3


# --------------------------------- process_account 集成:真把漏网旧文补回来

def test_process_account_backfills_missed_old_article(tmp_path, rec, monkeypatch):
    monkeypatch.setattr(orchestrator.bot, "search_open_profile",
                        lambda name: (True, ""))
    monkeypatch.setattr(orchestrator.bot, "find_profile_host",
                        lambda account=None, kicks=0: (100, object()))
    monkeypatch.setattr(orchestrator.bot, "close_article_tabs",
                        lambda **kw: True)
    monkeypatch.setattr(orchestrator.bot, "close_profile_tab",
                        lambda name, wait=0: True)
    urls = iter([
        ("https://mp.weixin.qq.com/s?__biz=MzA1&mid=1&idx=1&sn=aaa", 3),
        ("https://mp.weixin.qq.com/s?__biz=MzA1&mid=1&idx=2&sn=bbb", 3),
    ])
    monkeypatch.setattr(orchestrator.bot, "open_article_and_get_url",
                        lambda ctrl, **kw: next(urls))
    # 首屏只有新文;滚一屏露出 08-20 的漏网旧文(≤ 截止日 08-22 → 停滚回顶)
    r = rec(screens=[
        [("新文", "2026-09-03")],
        [("新文", "2026-09-03"), ("漏网旧文", "2026-08-20")],
    ])

    cfg = _cfg(tmp_path, overlap_days=10)  # 截止日 = 水位线09-01 - 10 = 08-22
    from src.db import Store
    store = Store(cfg.db_path)
    acc_id = store.get_or_create_account("测试号")
    store.set_watermark(acc_id, "2026-09-01")
    st = orchestrator.process_account(store, cfg, "测试号", LOG)
    with sqlite3.connect(cfg.db_path) as con:
        titles_in_db = {t for (t,) in con.execute(
            "SELECT title FROM articles WHERE account_id=?", (acc_id,))}
    store.close()

    assert st["ok"] and st["new"] == 2     # 新文 + 补回的漏网旧文
    assert titles_in_db == {"新文", "漏网旧文"}
    assert (r.scrolls, r.tops) == (1, 1)
