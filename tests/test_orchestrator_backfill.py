"""老账号按截止日深度滚动回填测试(_covers_cutoff/_screen_fingerprint/_collect_list)。

背景(2026-09-04 用户诉求):overlap_days=10 要「真正回填 10 天」,老账号
不能再只读首屏 —— 最旧可见日期未早于截止日就继续下滚(至多
deep_scroll_screens 屏),首屏已覆盖则零滚动,触底提前停,滚后回顶重扫。
触底判定 = 条数不增 且 首标题 top 不动(滚半屏条数常不变,2026-09-04 实测)。
收采一律先回顶(主页打开时滚动状态不可知);
首次「未移动」先重试一屏再判触底。全部 monkeypatch 桩,不触真实 UIA。
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


def _ctrls(specs, base=0.0):
    """[(标题, ISO日期或None)] → (ctrls, times);top 自 base 起按序递增 100。"""
    ctrls, times = [], []
    for i, (name, iso) in enumerate(specs):
        ctrls.append(_FakeCtrl(name, base + 100.0 * (i + 1)))
        if iso:
            times.append((iso, base + 100.0 * (i + 1) - 50))
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
    """记录 scroll/scan 调用;scan 按预置屏序出数据,超出后重复末屏。

    bases 为各屏视口基准 top(默认全 0);模拟「滚动了但条数不变」用位移。
    """

    def __init__(self, screens, bases=None):
        self.screens = screens
        self.bases = bases
        self.scans = 0
        self.scrolls = 0
        self.tops = 0

    def scan_list(self, host, max_nodes=0):
        i = min(self.scans, len(self.screens) - 1)
        base = self.bases[i] if self.bases else 0.0
        self.scans += 1
        return _ctrls(self.screens[i], base=base)

    def scroll_once(self, host, wheels=10, anchor=None):
        self.scrolls += 1

    def scroll_to_top(self, host, wheels=30, wait=0.0, anchor=None):
        self.tops += 1


@pytest.fixture
def rec(monkeypatch):
    def _install(screens, bases=None, expanded=0):
        r = _Recorder(screens, bases)
        monkeypatch.setattr(orchestrator.bot, "scan_list", r.scan_list)
        monkeypatch.setattr(orchestrator.bot, "scroll_once", r.scroll_once)
        monkeypatch.setattr(orchestrator.bot, "scroll_to_top", r.scroll_to_top)
        monkeypatch.setattr(orchestrator.bot, "expand_fold_bars",
                            lambda host, max_nodes=0: expanded)
        return r
    return _install


# ------------------------------------------------ 判定函数真值表

@pytest.mark.parametrize("dates,cutoff,expected", [
    (["2026-09-03", "2026-08-25"], "2026-08-28", True),   # 最旧已早于截止日
    (["2026-09-03", "2026-09-01"], "2026-08-28", False),  # 尚未滚到窗口底
    ([None, None], "2026-08-28", False),                  # 全不可解析 → 继续滚
    ([], "2026-08-28", False),                            # 空 → 继续滚
])
def test_covers_cutoff_truth_table(dates, cutoff, expected):
    assert orchestrator._covers_cutoff(dates, cutoff) is expected


def test_same_screen_truth_table():
    old = orchestrator._screen_fingerprint(
        _ctrls([("甲", "2026-09-03"), ("乙", "2026-09-01")])[0])
    same = orchestrator._screen_fingerprint(
        _ctrls([("甲", "2026-09-03"), ("乙", "2026-09-01")])[0])
    shifted = orchestrator._screen_fingerprint(
        _ctrls([("甲", "2026-09-03"), ("乙", "2026-09-01")], base=150.0)[0])
    grew = orchestrator._screen_fingerprint(
        _ctrls([("甲", "2026-09-03"), ("乙", "2026-09-01"),
                ("丙", "2026-08-25")])[0])
    assert orchestrator._same_screen(old, same)      # 条数同 top 同 → 未动
    assert not orchestrator._same_screen(old, shifted)  # 条数同但视口动了
    assert not orchestrator._same_screen(old, grew)     # 条数增 → 没到底


def test_fingerprint_is_snapshot_not_live_query():
    """UIA 控件 rect 是活查询:指纹必须取扫描当时数值。模拟首读 100、
    之后被滚走的控件 —— 快照后仍按 100 判;滚动后的新屏指纹(首读即
    -1692)与快照不同 → 判「已移动」,而不是拿漂移值误判「未移动」。"""

    class _TopCtrl:
        def __init__(self, top):
            self.Name = "甲"
            self._top = top
            self.reads = 0

        @property
        def BoundingRectangle(self):
            self.reads += 1
            return type("R", (), {"top": self._top})()

    c = _TopCtrl(100.0)
    fp = orchestrator._screen_fingerprint([c])
    assert fp == (1, 100.0)          # 快照:扫描当时的值
    c.BoundingRectangle              # 后续任何重读(即使值漂移)不影响快照
    fp_moved = orchestrator._screen_fingerprint([_TopCtrl(-1692.0)])
    assert not orchestrator._same_screen(fp, fp_moved)  # 漂移 ≠ 未动
    assert orchestrator._same_screen(fp, orchestrator._screen_fingerprint(
        [_TopCtrl(100.5)]))          # ±1.5px 内算未动


# ------------------------------------------------ _collect_list 回填模式

def test_first_screen_already_covers_no_scroll(tmp_path, rec):
    r = rec([[("新文", "2026-09-03"), ("旧文", "2026-08-25")]])
    _titles, pairs, _dates = orchestrator._collect_list(
        _cfg(tmp_path), "host", "2026-08-28")
    # 起始回顶(1 top)+首扫,首屏已覆盖 → 零扩量滚动
    assert (r.scans, r.scrolls, r.tops) == (1, 0, 1)
    assert len(pairs) == 2


def test_deep_scrolls_until_covered_then_rescan_on_top(tmp_path, rec):
    r = rec(screens=[
        [("新文", "2026-09-03"), ("中", "2026-09-01")],   # 首屏未覆盖
        [("新文", "2026-09-03"), ("中", "2026-09-01"), ("旧", "2026-08-25")],
    ])
    _titles, pairs, _dates = orchestrator._collect_list(
        _cfg(tmp_path), "host", "2026-08-28")
    assert r.scrolls == 1              # 滚一屏即覆盖
    assert r.tops == 2                 # 起始回顶 + 滚后回顶重扫
    assert r.scans == 3                # 首扫 + 滚后扫 + 回顶重扫
    assert [t for t, _d in pairs] == ["新文", "中", "旧"]


def test_same_count_but_viewport_shifted_keeps_scrolling(tmp_path, rec):
    """滚半屏:条数不变但视口已动 → 不得误判触底(2026-09-04 真机回归)。"""
    r = rec(screens=[
        [("新文", "2026-09-03"), ("中", "2026-09-01")],
        [("新文", "2026-09-03"), ("中", "2026-09-01")],   # 同条数,top 位移
        [("新文", "2026-09-03"), ("中", "2026-09-01"), ("旧", "2026-08-25")],
    ], bases=[0.0, 150.0, 300.0])
    _titles, pairs, _dates = orchestrator._collect_list(
        _cfg(tmp_path), "host", "2026-08-28")
    assert r.scrolls == 2              # 位移屏不算触底,继续滚
    assert r.tops == 2
    assert r.scans == 4                # 首扫 + 2 滚后扫 + 回顶重扫
    assert [t for t, _d in pairs] == ["新文", "中", "旧"]


def test_frozen_first_scroll_retries_then_finds_coverage(tmp_path, rec):
    """首次视口未移动可能是落点/前台问题(2026-09-04 终验实测):重试一屏
    后若视口动了,继续回填而不是误判触底。"""
    r = rec(screens=[
        [("新文", "2026-09-03"), ("中", "2026-09-01")],
        [("新文", "2026-09-03"), ("中", "2026-09-01")],   # 冻结(同条数同top)
        [("新文", "2026-09-03"), ("中", "2026-09-01"), ("旧", "2026-08-25")],
    ], bases=[0.0, 0.0, 300.0])
    _titles, pairs, _dates = orchestrator._collect_list(
        _cfg(tmp_path), "host", "2026-08-28")
    assert r.scrolls == 2              # 冻结 → 重试一屏
    assert r.tops == 2
    assert [t for t, _d in pairs] == ["新文", "中", "旧"]


def test_frozen_twice_is_really_bottom(tmp_path, rec):
    r = rec(screens=[
        [("新文", "2026-09-03"), ("中", "2026-09-01")],
        [("新文", "2026-09-03"), ("中", "2026-09-01")],   # 第一次冻结
        [("新文", "2026-09-03"), ("中", "2026-09-01")],   # 重试仍冻结 → 触底
    ], bases=[0.0, 0.0, 0.0])
    _titles, pairs, _dates = orchestrator._collect_list(
        _cfg(tmp_path), "host", "2026-08-28")
    assert r.scrolls == 2              # 只重试一次
    assert r.tops == 1                 # 触底 → 不再回顶(仅起始回顶)
    assert len(pairs) == 2


def test_screen_cap_bounds_scrolling(tmp_path, rec):
    # 每屏条数严格递增(2,3,4,…),永不触发「触底」,验证屏数上限兜底
    growing = [[("新文", "2026-09-03")]
               + [(f"文{i}_{j}", "2026-09-02") for j in range(i + 1)]
               for i in range(10)]
    r = rec(screens=[[("新文", "2026-09-03")]] + growing)
    _titles, pairs, _dates = orchestrator._collect_list(
        _cfg(tmp_path, deep_scroll_screens=3), "host", "2026-08-28")
    assert r.scrolls == 3              # 屏数上限兜底
    assert r.tops == 2                 # 起始回顶 + 屏满回顶
    assert r.scans == 1 + 3 + 1        # 首扫 + 3 次滚后扫 + 回顶重扫
    assert len(pairs) == 5             # 回顶重扫取 growing[3] 屏(1+4 条)


def test_deep_scroll_disabled_zero_keeps_first_screen_only(tmp_path, rec):
    r = rec([[("新文", "2026-09-03")]])
    _titles, _pairs, _dates = orchestrator._collect_list(
        _cfg(tmp_path, deep_scroll_screens=0), "host", "2026-08-28")
    assert (r.scans, r.scrolls, r.tops) == (1, 0, 1)   # 仍先回顶再首扫


def test_new_account_path_unchanged(tmp_path, rec):
    r = rec(screens=[
        [("新文", "2026-09-03")],
        [("新文", "2026-09-03"), ("文2", "2026-09-02")],
        [("新文", "2026-09-03"), ("文2", "2026-09-02"), ("文3", "2026-09-01")],
    ])
    _titles, pairs, _dates = orchestrator._collect_list(
        _cfg(tmp_path), "host", None)  # 无水位线 → 新账号固定扩量
    assert r.scrolls == 2              # new_account_screens=2
    assert r.tops == 2                 # 起始回顶 + 收尾回顶
    assert len(pairs) == 3


# ------------------------------------------------ 「余下N篇」折叠组展开

def test_expand_fold_bars_reveals_hidden_article(tmp_path, rec):
    """多图文折叠:折叠条 Invoke 展开后重扫,余篇文章继承组日期进采集
    (2026-09-04 真机:中金点睛首屏 4 组折叠,不展开就漏抓)。"""
    r = rec(screens=[
        [("头条", "2026-09-03"), ("旧文", "2026-08-25")],  # 首扫(已覆盖,停滚)
        # 展开后重扫:余篇就地插在头条之下,继承组日期;旧文仍在更下
        [("头条", "2026-09-03"), ("余篇", "2026-09-03"), ("旧文", "2026-08-25")],
    ], expanded=1)
    _titles, pairs, dates = orchestrator._collect_list(
        _cfg(tmp_path), "host", "2026-08-28")
    assert (r.scans, r.scrolls, r.tops) == (2, 0, 1)  # 首扫 + 展开后重扫
    assert [t for t, _d in pairs] == ["头条", "余篇", "旧文"]
    assert dates == ["2026-09-03", "2026-09-03", "2026-08-25"]  # 余篇继承组日期


def test_no_fold_bars_no_extra_scan(tmp_path, rec):
    r = rec([[("新文", "2026-09-03"), ("旧文", "2026-08-25")]])
    _titles, pairs, _dates = orchestrator._collect_list(
        _cfg(tmp_path), "host", "2026-08-28")
    assert (r.scans, r.scrolls, r.tops) == (1, 0, 1)   # 无折叠零开销
    assert len(pairs) == 2


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
    assert (r.scrolls, r.tops) == (1, 2)   # 滚一屏;起始回顶+滚后回顶
