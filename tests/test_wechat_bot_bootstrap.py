"""wechat_bot 搜索页自愈引导测试(触发串共享常量 + 合成键的前台闸门)。

背景(2026-09-03 评审 I1/M1/M2):
  * 置前失败/聚焦异常时旧实现仍发送 {Ctrl}a {Ctrl}v {Enter} —— 而置前被拒
    恰恰发生在用户正在用机器(第三方应用持前台)时,合成键会落进用户应用
    (全选+替换+提交),属破坏性输入。现在聚焦失败或发键前前台复核不过的
    一律不发键;
  * orchestrator 以子串匹配「未找到搜索页」触发自愈,措辞一旦改动自愈即被
    静默禁用 —— 触发串收敛到 bot.SEARCH_PAGE_MISSING,并对 orchestrator
    源码做引用级 pin;
  * 主窗候选加身份过滤(进程 + Qt 类 + Name=微信),防独立聊天窗按面积
    压过主窗后吃下热键。

全部 monkeypatch 桩,不触真实 UIA 窗口(uiautomation 模块级导入无副作用,
与既有 test_wechat_bot_*.py 同前提)。
"""
from pathlib import Path

from src import orchestrator
from src import wechat_bot as bot


def _stub_sendkeys(monkeypatch) -> list:
    """替换共享 uia 模块的 SendKeys 为记录器,返回 calls 列表。"""
    calls: list = []
    monkeypatch.setattr(bot.uia, "SendKeys", lambda keys, **kw: calls.append(keys))
    return calls


# ------------------------------------------------ 触发串常量(M2 回归锚点)

def test_trigger_constant_nonempty():
    assert isinstance(bot.SEARCH_PAGE_MISSING, str) and bot.SEARCH_PAGE_MISSING


def test_search_open_profile_emits_trigger_constant(monkeypatch):
    # 搜索页缺失路径必须携常量子串,否则 orchestrator 的自愈分支永远不触发
    monkeypatch.setattr(bot, "find_search_entry", lambda: (None, None, None))
    ok, msg = bot.search_open_profile("某账号")
    assert ok is False
    assert bot.SEARCH_PAGE_MISSING in msg


def test_orchestrator_pins_trigger_constant():
    # 源码级 pin(有意为之):消费方必须引用常量而非裸字符串
    src = Path(orchestrator.__file__).read_text(encoding="utf-8")
    assert "SEARCH_PAGE_MISSING" in src


# ------------------------------------------------ 前台闸门(I1:不发破坏性键)

def test_no_keys_when_focus_fails(monkeypatch):
    monkeypatch.setattr(bot, "desktop_state", lambda: "ok")
    monkeypatch.setattr(bot, "_focus_main", lambda: (None, "主窗口未能置前"))
    calls = _stub_sendkeys(monkeypatch)
    ok, msg = bot._bootstrap_once("测试", "{Ctrl}f", 2.0)
    assert ok is False
    assert calls == []  # 一次键都不能发


def test_no_keys_when_foreground_recheck_fails(monkeypatch):
    # _focus_main 声称置前成功,但 settle 窗口期焦点被抢回 → 发键前复核必须拦下
    monkeypatch.setattr(bot, "desktop_state", lambda: "ok")
    monkeypatch.setattr(bot, "_focus_main", lambda: (12345, ""))
    monkeypatch.setattr(bot, "_foreground_is", lambda hwnd: False)
    calls = _stub_sendkeys(monkeypatch)
    ok, msg = bot._bootstrap_once("测试", "{Ctrl}f", 2.0)
    assert ok is False
    assert calls == []


def test_happy_path_sends_expected_sequence(monkeypatch):
    monkeypatch.setattr(bot, "desktop_state", lambda: "ok")
    monkeypatch.setattr(bot, "_focus_main", lambda: (12345, ""))
    monkeypatch.setattr(bot, "_foreground_is", lambda hwnd: True)
    monkeypatch.setattr(bot, "find_search_entry",
                        lambda: (0, 0, object()))  # 立即命中,轮询即退出
    monkeypatch.setattr(bot.uia, "SetClipboardText", lambda text: None)
    monkeypatch.setattr(bot.time, "sleep", lambda s: None)  # 免 2s settle,monkeypatch 会还原
    calls = _stub_sendkeys(monkeypatch)
    ok, msg = bot._bootstrap_once("测试", "{Ctrl}f", 2.0)
    assert ok is True
    assert calls == ["{Ctrl}f", "{Ctrl}a", "{Ctrl}v", "{Enter}"]


def test_foreground_is_truth_table(monkeypatch):
    class FakeUser32:
        def __init__(self, fg):
            self._fg = fg

        def GetForegroundWindow(self):
            return self._fg

    monkeypatch.setattr(bot, "USER32", FakeUser32(12345))
    assert bot._foreground_is(12345) is True
    monkeypatch.setattr(bot, "USER32", FakeUser32(999))
    assert bot._foreground_is(12345) is False
    monkeypatch.setattr(bot, "USER32", FakeUser32(None))
    assert bot._foreground_is(12345) is False


# ------------------------------------------------ 主窗候选身份过滤(M1)

class _FakeCtrl:
    def __init__(self, pid, cls, name):
        self.ProcessId = pid
        self.ClassName = cls
        self.Name = name


class _FakeRoot:
    def __init__(self, children):
        self._children = children

    def GetChildren(self):
        return list(self._children)


def test_main_window_candidates_identity_filter(monkeypatch):
    main = _FakeCtrl(100, "Qt51514QWindowIcon", "微信")
    detached = _FakeCtrl(100, "Qt51514QWindowIcon", "其他")  # 同进程同类的独立聊天窗
    appex = _FakeCtrl(200, "Chrome_WidgetWin_0", "微信")     # 内置浏览器窗口
    monkeypatch.setattr(
        bot, "process_name",
        lambda pid: "weixin.exe" if pid == 100 else "wechatappex.exe")

    monkeypatch.setattr(bot.uia, "GetRootControl",
                        lambda: _FakeRoot([detached, appex, main]))
    assert bot._main_window_candidates() == [main]

    # 只有非主窗身份的候选 → 空列表(自愈安全失败,无 legacy 兜底)
    monkeypatch.setattr(bot.uia, "GetRootControl",
                        lambda: _FakeRoot([detached, appex]))
    assert bot._main_window_candidates() == []
