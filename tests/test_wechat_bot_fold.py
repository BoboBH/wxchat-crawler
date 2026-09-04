"""「余下N篇」折叠条展开测试(find_fold_bars/expand_fold_bars)。

背景(2026-09-04 真机定律,中金点睛):
* 多图文推送组卡只显示头条,余篇收在 article-list__more-bar 折叠条里,
  不展开就漏抓(首屏 4 组折叠=漏 4 篇);
* 条自带 InvokePattern,UIA Invoke 后台生效(视口外亦可),展开后
  条从树中消失 → 逐轮「找条→Invoke→等 settle」到找不到条即收;
* GetPattern 抛异常/返回 None 的条跳过,轮数上限兜底(树延迟激活)。
全部桩件,不触真实 UIA。
"""
import pytest

from src import wechat_bot as bot


class _Pat:
    def __init__(self):
        self.invokes = 0

    def Invoke(self):
        self.invokes += 1


class _Bar:
    """mode: 'ok'=有 InvokePattern;'none'=无;'raise'=GetPattern 抛异常。"""

    def __init__(self, mode="ok"):
        self.mode = mode
        self.pat = _Pat() if mode == "ok" else None

    def GetPattern(self, pid):
        if self.mode == "raise":
            raise RuntimeError("boom")
        return self.pat


@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr(bot.time, "sleep", lambda _s: None)


def test_find_fold_bars_filters_by_class(monkeypatch):
    class _C:
        def __init__(self, cls):
            self.ClassName = cls

    made = [_C(bot.CLASS_MORE_BAR), _C("article__item__title"),
            _C(""), _C(bot.CLASS_MORE_BAR)]
    monkeypatch.setattr(bot, "walk_ctrls",
                        lambda root, max_nodes=0: iter(list(made)))
    bars = bot.find_fold_bars("host", max_nodes=99)
    assert len(bars) == 2


def test_expand_invokes_all_bars_then_stops(monkeypatch, no_sleep):
    b1, b2, b3 = _Bar(), _Bar(), _Bar()
    rounds = [[b1, b2, b3], []]           # 第二轮条已消失(展开后从树中移除)
    monkeypatch.setattr(bot, "find_fold_bars",
                        lambda host, max_nodes=0: rounds.pop(0))
    assert bot.expand_fold_bars("host") == 3
    assert [b.pat.invokes for b in (b1, b2, b3)] == [1, 1, 1]


def test_expand_swallows_pattern_failures(monkeypatch, no_sleep):
    ok_bar, none_bar, raise_bar = _Bar(), _Bar("none"), _Bar("raise")
    screen = [ok_bar, none_bar, raise_bar]
    monkeypatch.setattr(bot, "find_fold_bars",
                        lambda host, max_nodes=0: list(screen))
    assert bot.expand_fold_bars("host", rounds=3) == 3  # 每轮只有 ok 条成功
    assert ok_bar.pat.invokes == 3


def test_expand_rounds_cap(monkeypatch, no_sleep):
    calls = []

    def _find(host, max_nodes=0):
        calls.append(1)
        return [_Bar("none")]             # 永不消失、永不成功

    monkeypatch.setattr(bot, "find_fold_bars", _find)
    assert bot.expand_fold_bars("host", rounds=2) == 0
    assert len(calls) == 2                # 轮数上限兜底,不死循环
