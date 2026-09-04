"""wechat_bot 滚轮通道测试:WM_MOUSEWHEEL 直投窗口消息队列。

2026-09-04 实测:合成滚轮(SendInput)在计划任务/后台进程形态下被
系统输入路由丢弃(交互式进程同样代码正常);改向窗口投递
WM_MOUSEWHEEL 绕过全局路由,后台进程同样生效,且不再移动用户鼠标。
命中点必须取列表下部 —— 页头/sticky 区不滚(变体矩阵实测)。
"""
import pytest

from src import wechat_bot as bot


class _Rect:
    left, top, right, bottom = 0, 0, 800, 900


class _Host:
    BoundingRectangle = _Rect()
    NativeWindowHandle = 4242            # 子窗句柄(如 RenderWidgetHostHWND)


@pytest.fixture
def posted(monkeypatch):
    msgs = []
    monkeypatch.setattr(bot.USER32, "PostMessageW",
                        lambda h, m, w, l: msgs.append((h, m, w, l)) or 1)
    monkeypatch.setattr(bot.USER32, "GetAncestor", lambda h, flag: 99)
    monkeypatch.setattr(bot.time, "sleep", lambda s: None)
    return msgs


def test_wheel_point_targets_lower_list_zone():
    assert bot._wheel_point(_Host()) == (600, 750)  # (right-200, bottom-150)


def test_wheel_point_clamped_for_small_hosts():
    small = type("H", (), {})()
    small.BoundingRectangle = type("R", (), {
        "left": 0, "top": 0, "right": 100, "bottom": 80})()
    x, y = bot._wheel_point(small)
    assert 10 <= x <= 90 and 10 <= y <= 20     # 钳回矩形内


def test_scroll_once_posts_down_notches_to_root(posted):
    """必须投 GA_ROOT 顶层主窗:投 Chromium 子窗会被忽略(2026-09-04 实测)。"""
    bot.scroll_once(_Host(), wheels=7)
    assert len(posted) == 7
    hwnd, msg, wparam, lparam = posted[0]
    assert (hwnd, msg) == (99, bot.WM_MOUSEWHEEL)
    assert (wparam >> 16) == (-bot.WHEEL_DELTA & 0xFFFF)  # 负齿距 = 下滚
    assert lparam == (750 << 16) | 600            # 低字 x=600, 高字 y=750


def test_scroll_to_top_posts_up_notches_then_waits(posted):
    bot.scroll_to_top(_Host(), wheels=30, wait=0.0)
    assert len(posted) == 30
    assert (posted[0][2] >> 16) == bot.WHEEL_DELTA        # 正齿距 = 上滚


def test_delta_high_word_is_u16(posted):
    """wParam 高字是 16 位有符号齿距:上滚 +120、下滚 -120(0xFF88)。"""
    bot.scroll_once(_Host(), wheels=1)
    bot.scroll_to_top(_Host(), wheels=1, wait=0.0)
    assert (posted[0][2] >> 16) == 0xFF88                 # -120 的补码
    assert (posted[1][2] >> 16) == 120


def test_no_native_handle_falls_back_to_zero(monkeypatch):
    """NativeWindowHandle 缺失时按句柄 0 投递(GetAncestor(0)=0 兜底),
    不因取不到根句柄而抛异常。"""
    msgs = []
    monkeypatch.setattr(bot.USER32, "PostMessageW",
                        lambda h, m, w, l: msgs.append(h) or 1)
    monkeypatch.setattr(bot.USER32, "GetAncestor", lambda h, flag: 0)
    monkeypatch.setattr(bot.time, "sleep", lambda s: None)
    host = type("H", (), {})()
    host.BoundingRectangle = _Rect()
    host.NativeWindowHandle = 0
    bot.scroll_once(host, wheels=1)
    assert msgs == [0]


def test_scroll_swallows_exceptions(monkeypatch):
    def boom(*a):
        raise OSError("boom")

    monkeypatch.setattr(bot.USER32, "PostMessageW", boom)
    bot.scroll_once(_Host())                      # 不抛出即通过
    bot.scroll_to_top(_Host())
