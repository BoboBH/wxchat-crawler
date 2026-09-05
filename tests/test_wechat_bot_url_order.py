"""open_article_and_get_url 的「短链优先」次序回归(2026-09-05 用户需求)。

下游 playwright 消费 canonical 全参链接会被微信拦去人工验证,短链才能全
自动 —— 「··· → 复制链接」菜单必须是主路径,树内提取退居兜底。本文件用
假控件钉死四条次序/防线:
  1. 树内有唯一可信候选时也先走菜单(短链优先);
  2. 菜单失败才用树内唯一可信候选(兜底仍有效);
  3. 污染页(多候选)菜单成功照样拿短链、菜单失败则 pending 不猜树序;
  4. 标题不符(-2)先于菜单,绝不给别人的页复制链接。
"""
from types import SimpleNamespace

import pytest

from src import wechat_bot as bot

OWN_FULL = ("https://mp.weixin.qq.com/s?__biz=b&mid=m&idx=1&sn=s"
            "&chksm=zzz")
SHORT = "https://mp.weixin.qq.com/s/ABCtoken"
TITLE = "测试文章标题"


class FakePattern:
    def __init__(self):
        self.invoked = 0

    def Invoke(self):
        self.invoked += 1


class FakeCtrl:
    """标题卡片桩:有 InvokePattern、无父级(函数只用到这两个接口)。"""

    def __init__(self):
        self.pattern = FakePattern()

    def GetPattern(self, _pid):
        return self.pattern

    def GetParentControl(self):
        return None


@pytest.fixture
def patched(monkeypatch):
    """文章页已打开的世界:win/h 恒定,scan 返回唯一可信候选,菜单成功。"""
    win = SimpleNamespace(NativeWindowHandle=111)
    calls = {"menu": 0, "scan": 0}

    def find_article_host(timeout=40.0):
        # 调用序:残留防护×1(无残留即过)+ 打开后定位×1
        find_article_host.n += 1
        if find_article_host.n == 1:
            return None, None
        return win, 111
    find_article_host.n = 0

    def scan_article_page(h, max_nodes=0):
        calls["scan"] += 1
        return (TITLE, {OWN_FULL: TITLE}, 100)

    def copy_link_via_menu(close_wait=2.5, win=None):
        calls["menu"] += 1
        return SHORT

    monkeypatch.setattr(bot, "find_article_host", find_article_host)
    monkeypatch.setattr(bot, "scan_article_page", scan_article_page)
    monkeypatch.setattr(bot, "copy_link_via_menu", copy_link_via_menu)
    monkeypatch.setattr(bot, "close_active_tab", lambda wait: True)
    monkeypatch.setattr(bot, "close_article_tabs", lambda **kw: True)
    return calls


def test_menu_first_even_with_trusted_tree_candidate(patched):
    url, n = bot.open_article_and_get_url(FakeCtrl(), expected_title=TITLE)
    assert (url, n) == (SHORT, 1)
    assert patched["menu"] == 1  # 树内虽有唯一可信候选,菜单仍先行


def test_falls_back_to_tree_only_when_menu_fails(monkeypatch, patched):
    monkeypatch.setattr(bot, "copy_link_via_menu",
                        lambda close_wait=2.5, win=None: None)
    url, n = bot.open_article_and_get_url(FakeCtrl(), expected_title=TITLE)
    assert (url, n) == (OWN_FULL, 1)  # 兜底返回的是树内 raw(编排层再 canonical 化)


def test_polluted_page_menu_still_wins(monkeypatch, patched):
    monkeypatch.setattr(
        bot, "scan_article_page",
        lambda h, max_nodes=0: (TITLE, {OWN_FULL: "别人的文章",
                                        "u2": "x", "u3": "y"}, 100))
    url, n = bot.open_article_and_get_url(FakeCtrl(), expected_title=TITLE)
    assert (url, n) == (SHORT, 1)  # 多候选不可信,主路径菜单照样拿短链


def test_polluted_page_pending_when_menu_fails(monkeypatch, patched):
    monkeypatch.setattr(bot, "copy_link_via_menu",
                        lambda close_wait=2.5, win=None: None)
    monkeypatch.setattr(
        bot, "scan_article_page",
        lambda h, max_nodes=0: (TITLE, {"u1": "别人", "u2": "x"}, 100))
    assert bot.open_article_and_get_url(
        FakeCtrl(), expected_title=TITLE) == (None, 0)  # 不猜树序,留待下轮


def test_title_mismatch_skips_menu(patched):
    url, n = bot.open_article_and_get_url(FakeCtrl(), expected_title="另一个标题")
    assert (url, n) == (None, -2)
    assert patched["menu"] == 0  # -2 先于菜单:绝不给别人的页复制链接
