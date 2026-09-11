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
import logging
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


# ---------------------------------------------- 键入中前台复核(B1,加固三)

def test_no_paste_keys_when_foreground_stolen_mid_sequence(monkeypatch):
    # {Ctrl}f 与 {Ctrl}a {Ctrl}v {Enter} 间隔 ~1.2s:此窗口期前台可能被抢,
    # 进入时的校验只保护了第一键,发粘贴前必须再复核一次
    monkeypatch.setattr(bot, "desktop_state", lambda: "ok")
    monkeypatch.setattr(bot, "_focus_main", lambda: (12345, ""))
    fg_seq = [True, False]  # 进入时通过,发粘贴前被抢
    monkeypatch.setattr(bot, "_foreground_is", lambda hwnd: fg_seq.pop(0))
    monkeypatch.setattr(bot.uia, "SetClipboardText", lambda text: None)
    monkeypatch.setattr(bot.time, "sleep", lambda s: None)
    calls = _stub_sendkeys(monkeypatch)
    ok, msg = bot._bootstrap_once("测试", "{Ctrl}f", 2.0)
    assert ok is False
    assert calls == ["{Ctrl}f"]  # 粘贴三连一链都不发
    assert "前台被抢" in msg


# ---------------------------------------------- _focus_main 失败回归(B2,加固一)

class _FakeRect:
    def __init__(self, left, top, right, bottom):
        self.left, self.top, self.right, self.bottom = left, top, right, bottom


class _FakeMainWin:
    """_focus_main 触达的最小面:句柄 + max(..., key) 要读的 BoundingRectangle。"""

    NativeWindowHandle = 12345
    BoundingRectangle = _FakeRect(0, 0, 800, 600)


def test_focus_main_returns_none_when_force_foreground_fails(monkeypatch):
    # 回归锚(23725d2):置前失败必须返回 (None, …未能置前…);旧实现即便
    # SetForegroundWindow 被前台锁拒绝也照样返回句柄,合成键会落进用户前台应用
    monkeypatch.setattr(bot, "_main_window_candidates", lambda: [_FakeMainWin()])
    monkeypatch.setattr(bot, "_force_foreground", lambda hwnd: False)
    monkeypatch.setattr(bot.USER32, "ShowWindow", lambda hwnd, cmd: 1)
    monkeypatch.setattr(bot.time, "sleep", lambda s: None)
    hwnd, msg = bot._focus_main()
    assert hwnd is None
    assert "未能置前" in msg


# ---------------------------------------- search_open_profile 前台闸门(2026-09-11)
#
# 旧实现裸发合成键鼠:edit.Click + {Ctrl}a {Ctrl}v 不做任何前台检查 —— AppEx
# 不在前台时点击/按键落进用户前台应用(全选+替换,破坏性)或被锁屏吞掉,
# 只能靠事后读回校验兜底。现与 _bootstrap_once 同一防线:桌面状态 → 置前 →
# 复核,不过一律不发键、不碰剪贴板,宁跳过不误写。


class _FakeSearchEdit:
    """search_open_profile 触达的最小搜索框:只数 Click。"""

    def __init__(self):
        self.clicked = 0

    def Click(self, simulateMove=False):
        self.clicked += 1


class _FakeNode:
    """walk_ctrls / invoke_control / is_account_card 触达的最小控件。"""

    NativeWindowHandle = 8642  # 前台闸门经 GetAncestor 解析宿主句柄

    def __init__(self, name=None, cls=None, children=(), button=False):
        self.Name = name
        self.ClassName = cls
        self.ControlType = bot.uia.ControlType.ButtonControl if button else None
        self._children = list(children)

    def GetChildren(self):
        return list(self._children)

    def GetPattern(self, pid):
        return self  # 鸭子桩:InvokePattern.Invoke 空操作即成功

    def Invoke(self):
        pass


def _stub_search_entry(monkeypatch, edit, host=None):
    monkeypatch.setattr(
        bot, "find_search_entry",
        lambda: (None, host if host is not None else _FakeNode(), edit))


def _stub_clipboard(monkeypatch):
    """替换共享 uia 模块剪贴板读写为记录器,返回 (reads, writes)。"""
    reads, writes = [], []

    def fake_read():
        reads.append(1)
        return ""

    monkeypatch.setattr(bot.uia, "GetClipboardText", fake_read)
    monkeypatch.setattr(bot.uia, "SetClipboardText", lambda text: writes.append(text))
    return reads, writes


def _stub_force_foreground(monkeypatch, result):
    calls = []
    monkeypatch.setattr(
        bot, "_force_foreground", lambda hwnd: calls.append(hwnd) or result)
    return calls


def test_search_no_input_when_desktop_locked(monkeypatch):
    # 锁屏/无前台:合成键鼠必落空 → 直接跳过,零按键零点击,剪贴板分毫未动
    monkeypatch.setattr(bot, "desktop_state", lambda: "locked")
    edit = _FakeSearchEdit()
    _stub_search_entry(monkeypatch, edit)
    calls = _stub_sendkeys(monkeypatch)
    reads, writes = _stub_clipboard(monkeypatch)
    ok, msg = bot.search_open_profile("中金点睛")
    assert ok is False
    assert "locked" in msg and "不发合成键" in msg
    assert calls == []
    assert edit.clicked == 0
    assert reads == [] and writes == []


def test_search_no_input_when_force_foreground_fails(monkeypatch):
    # 置前被拒(前台锁):绝不发键 —— 这是与旧实现分界的核心回归锚
    monkeypatch.setattr(bot, "desktop_state", lambda: "ok")
    edit = _FakeSearchEdit()
    _stub_search_entry(monkeypatch, edit)
    monkeypatch.setattr(bot.USER32, "GetAncestor", lambda hwnd, flag: hwnd or 8642)
    monkeypatch.setattr(bot, "_foreground_is", lambda hwnd: False)
    force_calls = _stub_force_foreground(monkeypatch, False)
    calls = _stub_sendkeys(monkeypatch)
    _reads, writes = _stub_clipboard(monkeypatch)
    ok, msg = bot.search_open_profile("中金点睛")
    assert ok is False
    assert "未置前" in msg
    assert calls == [] and edit.clicked == 0 and writes == []
    assert force_calls == [8642]


def test_search_forces_foreground_then_pastes(monkeypatch):
    # AppEx 不在前台:置前成功后照常粘贴,流程不受影响(功能无损面)
    monkeypatch.setattr(bot, "desktop_state", lambda: "ok")
    edit = _FakeSearchEdit()
    host = _FakeNode(children=[_FakeNode(name="搜索", button=True)])
    _stub_search_entry(monkeypatch, edit, host)
    monkeypatch.setattr(bot.USER32, "GetAncestor", lambda hwnd, flag: hwnd or 8642)
    fg_seq = [False, True, True]  # 初始不在前台→置前→复核过;循环首次复核仍过
    monkeypatch.setattr(bot, "_foreground_is", lambda hwnd: fg_seq.pop(0))
    force_calls = _stub_force_foreground(monkeypatch, True)
    monkeypatch.setattr(bot, "control_value", lambda c: "中金点睛")
    card = _FakeNode(name="中金点睛公众号", cls=bot.CLASS_RESULT_CARD)
    monkeypatch.setattr(bot, "find_host",
                        lambda pred, max_nodes=2500: (None, _FakeNode(children=[card])))
    monkeypatch.setattr(bot, "find_profile_host",
                        lambda account=None, kicks=0: (None, _FakeNode()))
    monkeypatch.setattr(bot.uia, "SetClipboardText", lambda text: None)
    monkeypatch.setattr(bot.time, "sleep", lambda s: None)
    calls = _stub_sendkeys(monkeypatch)
    ok, msg = bot.search_open_profile("中金点睛", nav_timeout=5.0)
    assert ok is True
    assert calls == ["{Ctrl}a", "{Ctrl}v"]  # 按钮路径,无需回车兜底
    assert edit.clicked == 1
    assert force_calls == [8642]


def test_search_aborts_when_foreground_stolen_before_paste(monkeypatch):
    # 进入校验通过后、发键前被抢且置不回:中止,粘贴三连一链不发
    monkeypatch.setattr(bot, "desktop_state", lambda: "ok")
    edit = _FakeSearchEdit()
    _stub_search_entry(monkeypatch, edit)
    monkeypatch.setattr(bot.USER32, "GetAncestor", lambda hwnd, flag: hwnd or 8642)
    fg_seq = [True, False]  # 进入过,发键前被抢
    monkeypatch.setattr(bot, "_foreground_is", lambda hwnd: fg_seq.pop(0))
    force_calls = _stub_force_foreground(monkeypatch, False)
    monkeypatch.setattr(bot.time, "sleep", lambda s: None)
    calls = _stub_sendkeys(monkeypatch)
    ok, msg = bot.search_open_profile("中金点睛")
    assert ok is False
    assert "前台被抢" in msg
    assert calls == []
    assert force_calls == [8642]


def test_search_no_enter_when_foreground_lost_at_button_fallback(monkeypatch):
    # 页面无「搜索」按钮需回车兜底,此刻前台被抢且置不回 → 宁可不回车
    monkeypatch.setattr(bot, "desktop_state", lambda: "ok")
    edit = _FakeSearchEdit()
    _stub_search_entry(monkeypatch, edit, host=_FakeNode())  # 无按钮子控件
    monkeypatch.setattr(bot.USER32, "GetAncestor", lambda hwnd, flag: hwnd or 8642)
    fg_seq = [True, True, False]  # 进入过、循环复核过,回车兜底前被抢
    monkeypatch.setattr(bot, "_foreground_is", lambda hwnd: fg_seq.pop(0))
    _stub_force_foreground(monkeypatch, False)
    monkeypatch.setattr(bot, "control_value", lambda c: "中金点睛")
    monkeypatch.setattr(bot.uia, "SetClipboardText", lambda text: None)
    monkeypatch.setattr(bot.time, "sleep", lambda s: None)
    calls = _stub_sendkeys(monkeypatch)
    # nav_timeout 压到 0.01:RED 期旧实现会带真实 find_host 进结果轮询,必须即出
    ok, msg = bot.search_open_profile("中金点睛", nav_timeout=0.01)
    assert ok is False
    assert "回车" in msg
    assert calls == ["{Ctrl}a", "{Ctrl}v"]  # 回车兜底必须缺席


# ---------------------------------------------- 身份过滤诊断(B3,加固三)

def test_main_window_candidates_accepts_stripped_name(monkeypatch):
    # UIA 读回的 Name 偶见尾部空白:strip 后比对,不能把真主窗一刀切拒掉
    main = _FakeCtrl(100, "Qt51514QWindowIcon", "微信 ")
    monkeypatch.setattr(bot, "process_name", lambda pid: "weixin.exe")
    monkeypatch.setattr(bot.uia, "GetRootControl", lambda: _FakeRoot([main]))
    assert bot._main_window_candidates() == [main]


def test_main_window_candidates_warns_on_name_mismatch(monkeypatch, caplog):
    # 未读数变体(微信(3))/非中文 UI 会被 Name 过滤拒掉 → 自愈永久静默失败;
    # 拒的时候必须留下 WARNING 说明看见了什么,否则巡检无从下手
    odd = _FakeCtrl(100, "Qt51514QWindowIcon", "微信(3)")
    monkeypatch.setattr(bot, "process_name", lambda pid: "weixin.exe")
    monkeypatch.setattr(bot.uia, "GetRootControl", lambda: _FakeRoot([odd]))
    with caplog.at_level(logging.INFO, logger="crawler"):
        assert bot._main_window_candidates() == []
    warns = [r for r in caplog.records
             if r.levelno == logging.WARNING and "身份不匹配" in r.getMessage()]
    assert len(warns) == 1
    assert "微信(3)" in warns[0].getMessage()
    assert "Qt51514QWindowIcon" in warns[0].getMessage()
