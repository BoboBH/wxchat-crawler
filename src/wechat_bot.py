"""wechat_bot: 微信 4.x 内置浏览器(WeChatAppEx.exe)的 UIA 操作封装。

全部常量与操作序列来自 docs/spike-findings.md(尖峰A/B/B'/B''/C):
  * 自动化面只有 AppEx 顶层窗口(Chrome_WidgetWin_0);微信主窗口 Qt 树为空;
  * AppEx 有多个顶层窗口且 Z 序不稳,必须按宿主内容筛选,不能按面积/顺序;
  * Chromium a11y 树懒 realization:先 GetChildren 爬一遍,FindFirst 才能命中;
  * 树不 realization 时 ShowWindow(SW_MINIMIZE)→SW_RESTORE kick 一次即恢复;
  * 搜索输入必须剪贴板粘贴(SetValue 不触发页面事件),依赖桌面已解锁;
  * 文章 URL 只在文章页的 ValuePattern.Value 出现,列表页永远拿不到;
  * 激活 tab 的「关闭」按钮 rect 完整落在 Tab rect 内(非激活 tab 是错位悬停位);
  * 文章页可内嵌大量**别人的** mp URL(尖峰D 实测一篇 475 个),页自身 URL
    用「··· → 复制链接」菜单兜底(AppMenuButton 只能 Click 弹层,剪贴板取链)。

微信大版本更新后:用 tools/spike_uia.py --browser --web-only 重新校准以下常量。
"""
from __future__ import annotations

import ctypes
import logging
import re
import time

import uiautomation as uia

# ---------------- 经验常量(微信更新后在此重新校准) ----------------
APP_CLASS = "Chrome_WidgetWin_0"          # AppEx 顶层窗口类名
APP_PROCESS = "wechatappex.exe"           # AppEx 进程映像名
HOST_CLASS = "Chrome_RenderWidgetHostHWND"  # 网页内容宿主(aid 每次导航都变)
CLASS_TITLE = "article__item__title"      # 主页列表条目标题
CLASS_TIME = "publish_time"               # 主页日期分组标签
CLASS_CARD = "js_article_card"            # 列表条目卡片(带 InvokePattern)
SEARCH_INPUT_ID = "weixin-search-input"   # 搜索页输入框 AutomationId
SEARCH_BUTTON_NAME = "搜索"                # 搜索触发按钮
CLASS_RESULT_CARD = "header-detail"       # 搜索结果卡片(公众号/小程序等同名前缀,需再筛)
RESULT_ACCOUNT_MARK = "公众号"             # 公众号卡片 Name 含此标记(小程序卡片没有)
CLOSE_BUTTON_NAME = "关闭"                 # tab 关闭按钮(ImageButton)
CLASS_TAB = "Tab"                         # tab 条上的单个 tab
ARTICLE_MARKERS = ("activity-name", "js_content", "js_name")  # 文章页 aid 锚点
TITLE_AIDS = ("activity-name", "js_name")  # 文章页自身标题控件 aid(校验用)
URL_RE = re.compile(r"https?://mp\.weixin\.qq\.com/s\?\S+")
# 空白 + 常见零宽不可见字符(微信标题里偶见),标题比对前全部剔除
INVISIBLE_RE = re.compile("[\\s\\u200b\\u200c\\u200d\\u2060\\ufeff]+")
# 「···」菜单(尖峰D):按钮在浏览器工具条,菜单项是页内 FlueMenuItemView
MORE_BUTTON_CLASS = "AppMenuButton"
MORE_BUTTON_NAME = "更多"
MENU_ITEM_CLASS = "FlueMenuItemView"
COPY_LINK_NAME = "复制链接"
CF_UNICODETEXT = 13
WM_MOUSEWHEEL = 0x020A                # 滚轮消息(_post_wheel 直投窗口队列)
WHEEL_DELTA = 120                     # Windows 标准滚轮齿距

# 与 orchestrator 同名 logger(诊断日志走同一文件;自愈层只在异常时发声)
_log = logging.getLogger("crawler")

USER32 = ctypes.windll.user32
K32 = ctypes.windll.kernel32
# 64 位下句柄是 64 位,必须显式声明 restype/argtypes,否则被截断成 32 位
USER32.OpenClipboard.argtypes = [ctypes.c_void_p]
USER32.GetClipboardData.argtypes = [ctypes.c_uint]
USER32.GetClipboardData.restype = ctypes.c_void_p
USER32.GetForegroundWindow.restype = ctypes.c_void_p  # 前台句柄比较必须完整 64 位
USER32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
K32.GlobalLock.argtypes = [ctypes.c_void_p]
K32.GlobalLock.restype = ctypes.c_void_p
K32.GlobalUnlock.argtypes = [ctypes.c_void_p]
# 搜索页引导(主窗热键):4.1.12.55 实证 {Ctrl}f 有效,{Ctrl}k 为备选
BOOTSTRAP_HOTKEYS = ("{Ctrl}f", "{Ctrl}k")
# 自愈触发串:本模块发出、orchestrator 以子串匹配 —— 措辞必须走本常量,
# 直接改字符串会静默禁用自愈(回归锚点见 tests/test_wechat_bot_bootstrap.py)
SEARCH_PAGE_MISSING = "未找到搜索页"
MAIN_WINDOW_PROCESS = "weixin.exe"               # 微信主进程映像名
LOCK_PROCESSES = ("lockapp.exe", "logonui.exe")  # 锁屏/登录界面 → 合成键鼠不可达


# ---------------------------------------------------------------- 基础工具

def process_name(pid: int) -> str:
    """进程映像名(如 Weixin.exe);失败返回 ''。"""
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(512)
        size = ctypes.c_ulong(ctypes.sizeof(buf))
        if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value.rsplit("\\", 1)[-1]
    finally:
        k32.CloseHandle(h)
    return ""


def restore_if_minimized(ctrl) -> bool:
    """最小化窗口还原(UIA 返回 -32000 坐标且树为空);返回是否执行了还原。"""
    GWL_STYLE, WS_MINIMIZE = -16, 0x20000000
    try:
        hwnd = ctrl.NativeWindowHandle
    except Exception:
        return False
    if not hwnd:
        return False
    if USER32.GetWindowLongW(hwnd, GWL_STYLE) & WS_MINIMIZE or \
            ctrl.BoundingRectangle.left <= -30000:
        USER32.ShowWindow(hwnd, 9)  # SW_RESTORE
        return True
    return False


def kick_window(win):
    """最小化→还原,强制 Chromium 重新暴露无障碍树(尖峰C:一次即恢复)。

    新开 tab 后内容树经常不 realization(全窗 ~122 节点、0 渲染宿主)。
    ShowWindow 是直发消息,锁屏下也有效,不属合成输入。
    """
    try:
        hwnd = win.NativeWindowHandle
        if not hwnd:
            return
        USER32.ShowWindow(hwnd, 6)  # SW_MINIMIZE
        time.sleep(1.2)
        USER32.ShowWindow(hwnd, 9)  # SW_RESTORE
        time.sleep(1.5)
    except Exception:
        pass


# ---------------------------------------------------------------- 窗口/宿主定位

def appex_windows(retries: int = 4) -> list:
    """枚举 AppEx 顶层窗口(顺序不稳,Z 序会变;调用方必须按内容筛选)。"""
    for _ in range(retries):
        out = []
        try:
            for cand in uia.GetRootControl().GetChildren():
                try:
                    if (cand.ClassName or "") == APP_CLASS and \
                            process_name(cand.ProcessId).lower() == APP_PROCESS:
                        out.append(cand)
                except Exception:
                    continue
        except Exception:
            pass
        if out:
            return out
        time.sleep(1.0)
    return []


def walk_ctrls(root, max_nodes: int = 4000, max_depth: int = 32):
    """深度遍历(遍历本身即完成无障碍树激活)。"""
    stack = [(root, 0)]
    n = 0
    while stack and n < max_nodes:
        c, d = stack.pop()
        n += 1
        yield c
        if d >= max_depth:
            continue
        try:
            stack.extend((k, d + 1) for k in c.GetChildren())
        except Exception:
            continue


def find_render_hosts(root_ctrl) -> list:
    """收集网页内容宿主(按 ClassName 定位;遍历即激活 a11y 树)。"""
    hosts = []

    def walk(ctrl):
        try:
            if (ctrl.ClassName or "") == HOST_CLASS:
                hosts.append(ctrl)
                return
        except Exception:
            return
        try:
            children = ctrl.GetChildren()
        except Exception:
            return
        for kid in children:
            walk(kid)

    walk(root_ctrl)
    return hosts


def host_doc_name(host) -> str:
    """当前网页 RootWebArea 的 Name(公众号主页时即账号名)。"""
    try:
        d = host.DocumentControl(searchDepth=3)
        if d.Exists(1, 0.2):
            return (d.Name or "").strip()
    except Exception:
        pass
    return ""


def find_host(pred, max_nodes: int = 2500):
    """按内容谓词在所有 AppEx 窗口里找宿主,返回 (win, host) 或 (None, None)。"""
    for w in appex_windows():
        try:
            restore_if_minimized(w)
        except Exception:
            pass
        for h in find_render_hosts(w):
            try:
                if h.BoundingRectangle.right - h.BoundingRectangle.left <= 100:
                    continue
            except Exception:
                continue
            for c in walk_ctrls(h, max_nodes=max_nodes):
                try:
                    if pred(c):
                        return w, h
                except Exception:
                    continue
    return None, None


def find_profile_host(account: str | None = None, kicks: int = 2):
    """找公众号主页宿主:树里有 article__item__title;account 给定时
    还要求 doc 名含账号名。树折叠时 kick 后重找。返回 (win, host)。

    两段式筛选(尖峰C):谓词阶段无法同时拿到宿主与 doc 名,先在每窗口
    找「有标题」的宿主,再校验其 doc 名。
    """
    for i in range(kicks + 1):
        found = None
        for w in appex_windows():
            try:
                restore_if_minimized(w)
            except Exception:
                pass
            for h in find_render_hosts(w):
                try:
                    if h.BoundingRectangle.right - h.BoundingRectangle.left <= 100:
                        continue
                except Exception:
                    continue
                hit = any((c.ClassName or "") == CLASS_TITLE
                          for c in walk_ctrls(h, max_nodes=2500))
                if hit:
                    doc = host_doc_name(h)
                    if account is None or account in doc:
                        found = (w, h)
                        break
            if found:
                break
        if found:
            return found
        if i < kicks:
            for win in appex_windows():
                kick_window(win)
    return None, None


def find_search_entry():
    """找搜索页输入框,返回 (win, host, edit|None)。"""
    win, host = find_host(
        lambda c: (c.AutomationId or "") == SEARCH_INPUT_ID, max_nodes=3000)
    if host is None:
        return None, None, None
    edit = None
    for c in walk_ctrls(host, max_nodes=3000):
        try:
            if (c.AutomationId or "") == SEARCH_INPUT_ID:
                edit = c
                break
        except Exception:
            continue
    return win, host, edit


def invoke_control(ctrl) -> bool:
    """触发控件:InvokePattern → LegacyIAccessible.DoDefaultAction → 坐标点击。

    UIA Pattern 动作由提供者线程执行,不依赖窗口前台/不被 UIPI 拦截(尖峰B'')。
    """
    try:
        pat = ctrl.GetPattern(uia.PatternId.InvokePattern)
        if pat is not None:
            pat.Invoke()
            return True
    except Exception:
        pass
    try:
        ctrl.GetLegacyIAccessiblePattern().DoDefaultAction()
        return True
    except Exception:
        pass
    try:
        ctrl.Click(simulateMove=False)
        return True
    except Exception:
        return False


def active_doc(win) -> str:
    """窗口当前激活页的 doc 名(取宽度>100 的最大渲染宿主)。"""
    try:
        hosts = []
        for h in find_render_hosts(win):
            r = h.BoundingRectangle
            if r.right - r.left > 100:
                hosts.append((h, (r.right - r.left) * (r.bottom - r.top)))
        if not hosts:
            return ""
        return host_doc_name(max(hosts, key=lambda x: x[1])[0])
    except Exception:
        return ""


# ---------------------------------------------------------------- 主页列表

def scan_list(host, max_nodes: int = 4000):
    """读主页列表,返回 (title_ctrls, time_pairs):
    title_ctrls 为标题控件列表(按纵序),time_pairs 为 [(日期文本, top)]。"""
    titles, times = [], []
    for c in walk_ctrls(host, max_nodes=max_nodes):
        cls = ""
        try:
            cls = c.ClassName or ""
        except Exception:
            continue
        if cls == CLASS_TITLE:
            try:
                titles.append((c, c.BoundingRectangle.top))
            except Exception:
                continue
        elif cls == CLASS_TIME:
            try:
                label = (c.Name or "").strip()
                if not label:  # 新版 UI:日期文本挂在子 TextControl 上(容器 Name 为空)
                    for kid in walk_ctrls(c, max_nodes=8):
                        t = (kid.Name or "").strip()
                        if t:
                            label = t
                            break
                times.append((label, c.BoundingRectangle.top))
            except Exception:
                continue
    titles.sort(key=lambda x: x[1])
    return [t[0] for t in titles], times


def _wheel_point(host):
    """WM_MOUSEWHEEL 消息携带的命中点:主页列表区下部(右缘内缩 200、
    底缘上移 150)。

    2026-09-04 变体矩阵实测:主页是 Chromium 内嵌页,滚轮只认命中点的
    DOM 容器 —— 页头/列表首行(sticky 日期头一带)不滚,列表下部四种
    前台状态下全部正常滚动。不再锚定标题控件(滚动恢复/视口外钳制
    会让标题 rect 不可靠)。
    """
    r = host.BoundingRectangle
    x = max(r.left + 10, min(r.right - 200, r.right - 10))
    y = max(r.top + 10, min(r.bottom - 150, r.bottom - 60))
    return int(x), int(y)


def _post_wheel(host, notches: int):
    """向微信主窗消息队列投递 WM_MOUSEWHEEL(notches>0 下滚,<0 上滚)。

    2026-09-04 实测:合成滚轮(SendInput,即 uia.WheelDown)在计划任务/
    后台进程形态下被系统输入路由丢弃,时灵时不灵;直投消息则稳定生效,
    但命中窗口有讲究 —— 投 Chromium 子窗(RenderWidgetHostHWND)被忽略,
    投顶层主窗才被 Chromium 按命中点路由到列表视口(真机验证:
    投主窗连续滚动 -442→-2692,同坐标投子窗纹丝不动)。故取
    GA_ROOT 顶层句柄投递。附带收益:不移动用户鼠标、与前台状态无关。
    锁屏下无效(与合成滚轮一致)。
    每次投递打一行诊断(句柄/矩形/落点/返回值):该通道失效模式隐蔽,
    现场数据是唯一可靠的排查依据。
    """
    hwnd = host.NativeWindowHandle or 0
    root = USER32.GetAncestor(hwnd, 2) or hwnd   # 2 = GA_ROOT
    x, y = _wheel_point(host)
    lparam = ((y & 0xFFFF) << 16) | (x & 0xFFFF)
    delta = -WHEEL_DELTA if notches > 0 else WHEEL_DELTA
    wparam = ((delta & 0xFFFF) << 16)          # 低字为按键状态标志(0)
    rv = 0
    for _ in range(abs(notches)):
        rv = USER32.PostMessageW(root, WM_MOUSEWHEEL, wparam, lparam)
        time.sleep(0.03)
    try:
        r = host.BoundingRectangle
        rect = f"({r.left},{r.top},{r.right},{r.bottom})"
    except Exception:
        rect = "?"
    _log.info("    [滚轮] %+d齿 root=%s host=%s rect=%s 点=(%d,%d) rv=%s",
              notches, root, hwnd, rect, x, y, rv)


def scroll_once(host, wheels: int = 10):
    """在宿主下翻一屏(投递 WM_MOUSEWHEEL,锁屏下无效;见 _post_wheel)。"""
    try:
        _post_wheel(host, wheels)
    except Exception:
        pass


def scroll_to_top(host, wheels: int = 30, wait: float = 2.0):
    """滚回页顶并稍候(投递 WM_MOUSEWHEEL,锁屏下无效;见 _post_wheel)。

    主页日期标签是 sticky 头:页面滚动后其 rect 被视口钳制、不再随分组走,
    在滚动状态下扫描会导致日期与标题错位配对(验收实测:同文一次 08-28
    一次 08-30)。主页打开时的滚动状态不可知 —— 所以每次收采开始
    也必须先回顶再扫。
    """
    try:
        _post_wheel(host, -wheels)
        time.sleep(wait)
    except Exception:
        pass


# ---------------------------------------------------------------- 搜索导航

def control_value(ctrl) -> str:
    """读控件 ValuePattern.Value(搜索框回显校验用);不可读返回 ''。"""
    try:
        vp = ctrl.GetPattern(uia.PatternId.ValuePattern)
        if vp is not None:
            return (vp.Value or "").strip()
    except Exception:
        pass
    return ""


def search_open_profile(account: str, nav_timeout: float = 45.0):
    """从搜索页搜索账号并打开其主页,返回 (ok, message)。

    前置:任一 AppEx 窗口有含 weixin-search-input 的搜索页 tab(部署时人工
    打开一次「搜一搜」即可,之后复用)。剪贴板临时占用并在 finally 恢复;
    粘贴是合成键鼠,锁屏下会失败。失败时可能残留已打开的主页 tab
    (由编排层负责清理,见 close_profile_tab)。
    """
    win, host, edit = find_search_entry()
    if edit is None:
        return False, f"{SEARCH_PAGE_MISSING}(weixin-search-input);将尝试自动引导「搜一搜」"

    def is_account_card(c) -> bool:
        """结果页公众号卡片:同名前缀的小程序/文章卡片(实测同名开头)必须排除,
        否则会 Invoke 到小程序卡片 → 打开空白小程序壳而非主页。"""
        name = c.Name or ""
        return ((c.ClassName or "") == CLASS_RESULT_CARD
                and name.startswith(account) and RESULT_ACCOUNT_MARK in name)

    clip = ""
    try:
        clip = uia.GetClipboardText() or ""
    except Exception:
        pass
    try:
        edit.Click(simulateMove=False)
        time.sleep(0.6)
        uia.SetClipboardText(account)
        pasted = False
        for _ in range(3):  # 前台被用户抢占时合成粘贴会落空:读回校验,失败重聚焦重贴
            uia.SendKeys("{Ctrl}a")
            time.sleep(0.2)
            uia.SendKeys("{Ctrl}v")
            time.sleep(1.0)
            if control_value(edit) == account:
                pasted = True
                break
            edit.Click(simulateMove=False)
            time.sleep(0.6)
        if not pasted:
            return False, "搜索框粘贴未生效(前台被占用或桌面锁定),本轮跳过"
        btn = None
        for c in walk_ctrls(host, max_nodes=3000):
            try:
                if (c.Name or "") == SEARCH_BUTTON_NAME and \
                        c.ControlType == uia.ControlType.ButtonControl:
                    btn = c
                    break
            except Exception:
                continue
        if btn is not None:
            invoke_control(btn)
        else:
            uia.SendKeys("{Enter}")
        # 等搜索结果卡片并 Invoke 打开主页
        t0 = time.time()
        opened = False
        while time.time() - t0 < nav_timeout:
            _w2, h2 = find_host(is_account_card, max_nodes=3000)
            if h2 is not None:
                card = None
                for c in walk_ctrls(h2, max_nodes=3000):
                    try:
                        if is_account_card(c):
                            card = c
                            break
                    except Exception:
                        continue
                if card is not None and invoke_control(card):
                    opened = True
                    break
            time.sleep(1.0)
        if not opened:
            return False, f"未出现[{account}]的公众号结果卡片(搜索可能无结果)"
        t0 = time.time()
        kicked = 0
        while time.time() - t0 < nav_timeout:
            _w3, h3 = find_profile_host(account=account, kicks=0)
            if h3 is not None:
                return True, f"主页已打开 doc={host_doc_name(h3)!r}"
            wins = appex_windows(retries=1)
            if wins:
                kick_window(wins[kicked % len(wins)])
                kicked += 1
            time.sleep(1.0)
        return False, "主页未就绪(未找到 article__item__title)"
    finally:
        try:
            uia.SetClipboardText(clip)
        except Exception:
            pass


# ---------------------------------------------------------------- 搜索页引导(自愈)

def desktop_state() -> str:
    """'locked' | 'no-foreground' | 'ok' | 'unknown'(探测异常):
    合成键鼠只在前台正常('ok')时有效。"""
    try:
        fg = USER32.GetForegroundWindow()
        if not fg:
            return "no-foreground"
        pid = ctypes.c_uint()
        USER32.GetWindowThreadProcessId(ctypes.c_void_p(fg), ctypes.byref(pid))
        return "locked" if process_name(pid.value).lower() in LOCK_PROCESSES else "ok"
    except Exception:
        return "unknown"


def _force_foreground(hwnd: int) -> bool:
    """SetForegroundWindow 受前台锁限制(后台进程调用常被静默拒绝);
    被拒时先 AttachThreadInput 借前台线程输入权限,再不行就 tap Alt 解锁重试。"""
    try:
        USER32.SetForegroundWindow(ctypes.c_void_p(hwnd))
        if USER32.GetForegroundWindow() == hwnd:
            return True
        fg = USER32.GetForegroundWindow()
        cur = K32.GetCurrentThreadId()
        other = USER32.GetWindowThreadProcessId(ctypes.c_void_p(fg), None) if fg else 0
        attached = False
        if other and other != cur:
            attached = bool(
                USER32.AttachThreadInput(ctypes.c_uint(cur), ctypes.c_uint(other), 1))
            USER32.BringWindowToTop(ctypes.c_void_p(hwnd))
            USER32.SetForegroundWindow(ctypes.c_void_p(hwnd))
            if attached:
                USER32.AttachThreadInput(ctypes.c_uint(cur), ctypes.c_uint(other), 0)
        if USER32.GetForegroundWindow() != hwnd:
            uia.SendKeys("{Alt}")  # 一次无害 Alt tap 解除前台锁
            USER32.SetForegroundWindow(ctypes.c_void_p(hwnd))
        return USER32.GetForegroundWindow() == hwnd
    except Exception:
        return False


def _main_window_candidates() -> list:
    """微信主窗口候选:weixin.exe 顶层窗口中类名以 Qt 开头且 Name=微信 者。

    只按「进程名 + 非 AppEx 类」筛不够:最大化的独立聊天窗(同为 weixin.exe
    的 Qt 窗)面积可压过主窗,合成热键/粘贴会落进聊天。主窗的文档化身份即
    类名形如 Qt51514QWindowIcon、Name=微信,三者必须同时满足;一个都不满足
    时自愈安全失败(宁不引导,不误键入)。
    Name 先 strip 再比对(UIA 文本偶见尾部空白);进程+类名已中而 Name 不符
    (未读数变体「微信(3)」/非中文 UI)的候选打一条 WARNING 再拒 —— 否则
    自愈会永久静默失败,巡检时无从得知为何找不到主窗。过滤本身不放宽。"""
    out, mismatched = [], []
    try:
        for c in uia.GetRootControl().GetChildren():
            try:
                if process_name(c.ProcessId).lower() != MAIN_WINDOW_PROCESS or \
                        not (c.ClassName or "").startswith("Qt"):
                    continue
                if (c.Name or "").strip() == "微信":
                    out.append(c)
                else:  # 只在真的拒掉时记(一次自愈至多两条),不额外去重
                    mismatched.append(c)
            except Exception:
                continue
        if mismatched:
            _log.warning("微信主窗身份不匹配: %s(按 Name=='微信' 过滤)",
                         ";".join(f"Name='{(c.Name or '').strip()}' "
                                  f"ClassName='{c.ClassName}'" for c in mismatched))
    except Exception:
        pass
    return out


def _foreground_is(hwnd: int) -> bool:
    """前台窗口是否恰为目标句柄(合成键发送前的最后校验)。"""
    try:
        fg = USER32.GetForegroundWindow()
        return bool(fg) and int(fg) == int(hwnd)
    except Exception:
        return False


def _focus_main() -> tuple[int | None, str]:
    """激活微信主窗口(_main_window_candidates 候选中面积最大者,
    类名形如 Qt51514QWindowIcon、Name=微信)。返回 (主窗句柄, 说明):
    句柄仅在 _force_foreground 实际置前成功时非 None,失败/异常一律 None ——
    主窗 Qt 树为空无法读回粘贴目标,前台比对是合成键入的唯一防线,
    置前失败时绝不能发键(Ctrl+A/V/Enter 落进用户前台应用是破坏性的)。"""
    cands = _main_window_candidates()
    if not cands:
        return None, f"未找到微信主进程({MAIN_WINDOW_PROCESS})的顶层主窗口"
    try:
        win = max(cands, key=lambda c: max(
            (c.BoundingRectangle.right - c.BoundingRectangle.left) *
            (c.BoundingRectangle.bottom - c.BoundingRectangle.top), 0))
        USER32.ShowWindow(win.NativeWindowHandle, 9)  # SW_RESTORE
        time.sleep(0.5)
        if not _force_foreground(win.NativeWindowHandle):
            return None, "主窗口未能置前"
        time.sleep(1.0)  # 置前 settle;此窗口期用户仍可能抢回焦点,发键前再复核
        return win.NativeWindowHandle, ""
    except Exception as exc:
        return None, f"主窗口聚焦异常({exc})"


def _bootstrap_once(account: str, shortcut: str, poll_sec: float) -> tuple[bool, str]:
    """单次引导:主窗聚焦并复核前台 → 合成热键聚焦微信搜索 → 粘贴账号名 →
    Enter → 轮询 AppEx 里出现搜索页输入框(即「搜一搜」tab 被重新打开)。
    聚焦失败/前台被抢占的一律不发键,宁自愈失败不误写用户前台应用。"""
    try:
        state = desktop_state()
        if state != "ok":
            return False, f"桌面状态={state}(锁屏/无前台窗口),合成键鼠不可达"
        hwnd, focus_msg = _focus_main()
        # _focus_main 置前后留了 settle 窗口期,用户可能已抢回焦点:主窗
        # UIA 树为空无粘贴目标可读回,发键前最后一次前台比对是唯一防线。
        if not hwnd or not _foreground_is(hwnd):
            tail = f";{focus_msg}" if focus_msg else ""
            return False, f"主窗口未置前,跳过合成键(避免误入其他应用){tail}"
        uia.SendKeys(shortcut)
        time.sleep(1.0)
        # 前台校验只保护了 {Ctrl}f:到粘贴三连还有 ~1.2s,此窗口期前台可能
        # 被抢(Ctrl+A/V/Enter 落进用户应用是破坏性的),发前必须再复核。
        # 此处中止无害:搜索框刚聚焦且为空,微信停在搜一搜页。
        if not _foreground_is(hwnd):
            return False, "键入中前台被抢,跳过本次引导(避免误入其他应用)"
        uia.SetClipboardText(account)
        uia.SendKeys("{Ctrl}a")
        uia.SendKeys("{Ctrl}v")
        time.sleep(1.0)
        uia.SendKeys("{Enter}")
        t0 = time.time()
        while time.time() - t0 < poll_sec:
            _w, _h, edit = find_search_entry()
            if edit is not None:
                return True, f"搜索页已由 {shortcut} 打开"
            time.sleep(2.0)
        tail = f";{focus_msg}" if focus_msg else ""
        return False, f"{shortcut} 后 {poll_sec:.0f}s 内未出现搜索页输入框{tail}"
    except Exception as exc:
        return False, f"引导异常({shortcut}): {exc}"


def ensure_search_page(account: str, attempts: int = 2,
                       poll_sec: float = 30.0) -> tuple[bool, str]:
    """确保 AppEx 里有搜一搜页:激活微信主窗→Ctrl+F→粘贴账号名→回车。
    返回 (是否可用, 说明)。失败自动在 {Ctrl}f/{Ctrl}k 与重试间轮换。

    AppEx 搜索页 tab 会被微信在数小时内回收,编排层在
    search_open_profile 报「未找到搜索页」时调用本函数自愈,不再要求
    人工部署。规则:绝不抛异常;剪贴板先存后还在 finally 恢复;尝试间
    静默暂停 3s(避开前台焦点争抢);说明里写清走向(哪个热键成功 /
    锁屏不可达 / 各尝试为何落空)。"""
    clip = ""
    try:
        clip = uia.GetClipboardText() or ""
    except Exception:
        pass
    msgs: list[str] = []
    try:
        _w, _h, edit = find_search_entry()
        if edit is not None:
            return True, "搜索页已可用(无需引导)"
        for i in range(max(1, attempts)):
            if i:
                time.sleep(3.0)
            shortcut = BOOTSTRAP_HOTKEYS[i % len(BOOTSTRAP_HOTKEYS)]
            ok, why = _bootstrap_once(account, shortcut, poll_sec)
            msgs.append(why)
            if ok:
                return True, why
        return False, ";".join(msgs) if msgs else "未执行任何引导尝试"
    except Exception as exc:  # 任何异常都不得打断抓取主流程
        msgs.append(f"引导异常: {exc}")
        return False, ";".join(msgs)
    finally:
        try:
            uia.SetClipboardText(clip)
        except Exception:
            pass


# ---------------------------------------------------------------- 文章页 URL 提取

def find_article_host(timeout: float = 40.0):
    """轮询等待文章页宿主(含正文 aid 锚点);每轮落空 kick 一个窗口。"""
    t0 = time.time()
    kicked = 0
    while time.time() - t0 < timeout:
        w, h = find_host(lambda c: (c.AutomationId or "") in ARTICLE_MARKERS,
                         max_nodes=1200)
        if h is not None:
            return w, h
        wins = []
        try:
            wins = sorted(appex_windows(),
                          key=lambda x: -((x.BoundingRectangle.right - x.BoundingRectangle.left) *
                                          (x.BoundingRectangle.bottom - x.BoundingRectangle.top))
                          if x.BoundingRectangle.right > x.BoundingRectangle.left else 0)
        except Exception:
            pass
        if kicked < len(wins):
            kick_window(wins[kicked])
            kicked += 1
        time.sleep(0.8)
    return None, None


def close_active_tab(wait: float = 2.5) -> bool:
    """关闭当前激活 tab:激活 tab = 其子「关闭」按钮 rect 完整落在 Tab rect 内
    (非激活 tab 的关闭按钮 rect 是错位的悬停位)。"""
    for w in appex_windows():
        stack = [(w, 0)]
        while stack:
            c, d = stack.pop()
            try:
                if (c.ClassName or "") == CLASS_TAB:
                    tr = c.BoundingRectangle
                    for k in c.GetChildren():
                        if (k.Name or "") != CLOSE_BUTTON_NAME:
                            continue
                        kr = k.BoundingRectangle
                        if tr.left <= kr.left and kr.right <= tr.right and \
                                tr.top <= kr.top and kr.bottom <= kr.bottom:
                            if invoke_control(k):
                                time.sleep(wait)
                                return True
                if d < 18:
                    stack.extend((kid, d + 1) for kid in c.GetChildren())
            except Exception:
                continue
    return False


def close_article_tabs(max_close: int = 3, wait: float = 2.5) -> bool:
    """确保没有残留文章页(aid 锚点命中即视为文章页开着);残留会导致
    下一次提取拿到上一篇的 URL。"""
    for _ in range(max_close):
        w, h = find_host(lambda c: (c.AutomationId or "") in ARTICLE_MARKERS,
                         max_nodes=1200)
        if h is None:
            return True
        close_active_tab(wait=wait)
        time.sleep(1.0)
    w, h = find_host(lambda c: (c.AutomationId or "") in ARTICLE_MARKERS, max_nodes=1200)
    return h is None


def close_profile_tab(account: str, wait: float = 2.5, max_try: int = 3) -> bool:
    """若某 AppEx 窗口当前激活页为该账号主页,关闭该 tab(退回搜索页)。"""
    for _ in range(max_try):
        w, h = find_profile_host(account=account, kicks=0)
        if h is None:
            return True
        if account not in active_doc(w):
            return False  # 主页不是激活页,不冒险关 tab
        if not close_active_tab(wait=wait):
            time.sleep(1.0)
    return find_profile_host(account=account, kicks=0)[1] is None


def scan_article_page(host, max_nodes: int = 12000, max_depth: int = 40):
    """文章页单趟扫描:同时收集「自身标题」与树内 mp URL。

    返回 (title, {url: 控件名}, 遍历节点数)。标题取 aid ∈ TITLE_AIDS 控件的
    非空 Name,activity-name(文章 <h1>)优先于 js_name —— js_name 是
    **公众号名**而非标题,且遍历序里常先出现(尖峰D 实测 node 3635 vs 3644),
    拿它当标题会把每篇都判成「标题不符」;URL 为 ValuePattern 命中 URL_RE 者。

    两个实测事实决定了本函数的形态(尖峰D,2026-09-03,郭磊宏观茶座
    「沃什Jackson Hole演讲」一篇):
      * 标题 aid 落在 a11y 树**尾部**(node≈3635/3647,而 js_content 在
        node≈74)—— 按正文锚点的节点预算(1200)扫标题必然落空;
      * 页内嵌入链接可把树撑得很大并带来大量**别人的** mp URL(该篇
        475 个,推荐阅读/目录类内链),「最短者即主文」不再成立。
    因此标题与 URL 必须一趟拿齐且预算给足(max_depth 40:标题 aid 实测
    深度仅 7~9,但嵌入链接子树更深,34 会截断)。
    """
    title = ""
    account_name = ""
    urls = {}
    n = 0
    for c in walk_ctrls(host, max_nodes=max_nodes, max_depth=max_depth):
        n += 1
        try:
            aid = c.AutomationId or ""
        except Exception:
            aid = ""
        if (not title or not account_name) and aid in TITLE_AIDS:
            try:
                t = (c.Name or "").strip()
            except Exception:
                t = ""
            if t:
                if aid == "activity-name":
                    title = t
                elif not account_name:
                    account_name = t
        try:
            vp = c.GetPattern(uia.PatternId.ValuePattern)
            if vp is None:
                continue
            v = vp.Value
        except Exception:
            continue
        if not v or "http" not in str(v):
            continue
        m = URL_RE.match(str(v))
        if m:
            urls.setdefault(m.group(0), (c.Name or "")[:40])
    # 页面缺 activity-name 时退回 js_name(校验多半仍会 False → -2,宁缺毋错)
    return title or account_name, urls, n


def extract_article_urls(host, max_nodes: int = 5000):
    """文章宿主树扫 ValuePattern.Value,返回 ({url: 控件名}, 遍历节点数)。

    兼容包装(见 scan_article_page)。注意:多 URL 页面的树内集合**可能
    全是页内嵌入链接**,不含页面自身 URL —— 取舍逻辑在
    open_article_and_get_url(单 URL 才直接用,多 URL 走「复制链接」兜底)。
    """
    _t, urls, n = scan_article_page(host, max_nodes=max_nodes)
    return urls, n


def read_page_title(host, max_nodes: int = 12000) -> str:
    """读文章页「自身」标题;读不到返回 ''(调用方视为无法校验)。"""
    return scan_article_page(host, max_nodes=max_nodes)[0]


def _titles_match(expected: str | None, actual: str | None) -> bool:
    """标题校验:剔除全部空白/零宽字符后,任一方向包含即算同一篇。

    expected 来自主页列表卡片,actual 是文章页自身标题;两者都可能带排版
    空白或前后缀差异,故用双向包含而非全等。任一侧为空(含 actual 为空 =
    无法校验)返回 False。
    """
    if not expected or not actual:
        return False
    e = INVISIBLE_RE.sub("", str(expected))
    a = INVISIBLE_RE.sub("", str(actual))
    if not e or not a:
        return False
    return e in a or a in e


def select_trusted_url(urls: dict, page_title: str | None,
                       expected_title: str | None) -> str | None:
    """树内 URL 取用策略(纯函数,单测锚点):唯一候选且归属可验证才采信。

    采信 = 恰好 1 个候选,且「页面标题(读不到时退回期望标题)」与该 URL
    控件的 Name 双向包含(尖峰C:文章自身 URL 的控件 Name 就是文章标题)。
    0 个、≥2 个、或归属验证不过、或两个标题都拿不到 → 一律返回 None,
    调用方走「··· → 复制链接」菜单兜底 —— 树序首个候选在污染页(内嵌 475 个
    mp URL)上几乎必是别人的文章,绝不能按遍历顺序猜。
    """
    if len(urls) != 1:
        return None
    url = next(iter(urls))
    probe = page_title or (expected_title or "")
    if not (probe and _titles_match(probe, urls[url])):
        return None
    return url


def open_article_and_get_url(title_ctrl, open_timeout: float = 40.0,
                             scan_timeout: float = 20.0, max_nodes: int = 5000,
                             close_wait: float = 2.5,
                             expected_title: str | None = None):
    """打开标题所在文章,提取其 raw URL,关闭文章 tab。返回 (raw_url|None, url数)。

    url数 哨兵:-1 = 文章页未打开/无可 Invoke 卡片/残留文章页未清干净
    (此时本篇有意不打开);0 = 已打开但未提取到 URL;
    -2 = 标题校验未通过(打开的页不是 expected_title 对应的文章;此时本页
    URL 一律不上报 —— 既防残留页错配,也防把内嵌链接记到本篇头上)。
    expected_title 为 None 时不出现 -2。
    标题 TextControl 自身无 InvokePattern,向上最多 5 级找可 Invoke 的祖先卡片
    (尖峰C:class js_article_card…)。

    URL 取用策略(尖峰D 实测后):
      * 树内恰 1 个 mp URL **且归属有据** → 直接用(中金点睛 13 篇全为此形态,
        主路径不变)。归属证据 = URL 控件 Name ≈ 页面标题(尖峰C:文章自身
        URL 的控件 Name 就是文章标题;标题只证明「页面身份」,不证明
        「URL 归属」—— 页面恰嵌 1 个他人链接时计数信任同样会被骗);
      * 树内 0 个、≥2 个、或唯一候选归属存疑 → 用「··· → 复制链接」菜单
        兜底(复制的是页面自身 URL,见 copy_link_via_menu);菜单也拿不到时
        返回 (None, 0) 留待下轮,**不再**用「最短者」猜 —— 验收实测最短者
        是别人的文章(本篇 475 个内链里最短者为主文外的一篇)。
      * 所有成功路径都必须先关文章 tab 再返回(泄漏的 tab 会拖慢下一篇的
        残留防护,收尾时还会让 close_profile_tab 拒关主页 tab)。
    """
    # 残留文章页防护:上一篇文章 tab 未关成功时,提取会拿到上一篇文章的 URL。
    if find_article_host(timeout=0.5)[1] is not None:
        close_article_tabs(max_close=3, wait=close_wait)
        if find_article_host(timeout=1.0)[1] is not None:
            return None, -1  # 清不掉残留,宁可本篇 pending 也不冒错配风险
    p = title_ctrl
    pat = None
    for _ in range(5):
        try:
            pat = p.GetPattern(uia.PatternId.InvokePattern)
        except Exception:
            pat = None
        if pat is not None:
            break
        try:
            p = p.GetParentControl()
        except Exception:
            break
        if p is None:
            break
    if pat is None:
        return None, -1
    try:
        pat.Invoke()
    except Exception:
        return None, -1
    win, h = find_article_host(timeout=open_timeout)
    if h is None:
        close_article_tabs(max_close=2, wait=close_wait)
        return None, -1
    # 标题与 URL 一趟扫描拿齐(标题 aid 在树尾部,见 scan_article_page);
    # expected_title 给定时标题是硬校验,读不到就多扫几轮再下结论。
    deadline = time.time() + scan_timeout
    page_t, urls = "", {}
    while True:
        page_t, urls, _n = scan_article_page(h, max_nodes=max(max_nodes, 12000))
        if urls and (page_t or expected_title is None):
            break
        if time.time() >= deadline:
            break
        time.sleep(1.0)
    title_ok = bool(expected_title) and _titles_match(expected_title, page_t)
    if expected_title is not None and not title_ok:
        if not close_active_tab(wait=close_wait):
            close_article_tabs(max_close=2, wait=close_wait)
        return None, -2
    # 树内 0 个、≥2 个、或唯一候选归属存疑 → 全部丢弃,走「··· → 复制链接」
    # 菜单兜底(策略集中在 select_trusted_url,可单测)。≥2 时若按树序取首,
    # 污染页(内嵌 475 个 mp URL)几乎必取到别人的文章 —— 且入库后是终态
    # ok 行,永不重试,比最短者启发式更糟。scan_article_page 存的 Name 截到
    # 40 字,截断保留前缀,双向包含判断不受影响。
    own = select_trusted_url(urls, page_t, expected_title)
    if own is not None:  # 此处 urls 只可能是「唯一且归属已验证」的候选
        if not close_active_tab(wait=close_wait):
            close_article_tabs(max_close=2, wait=close_wait)
        return own, 1
    # 0 个可信候选:「··· → 复制链接」菜单兜底(copy 的就是本页 URL,
    # 不受内嵌链接污染)。-2 分支已提前返回,不会走到这里。
    menu_url = copy_link_via_menu(close_wait=close_wait, win=win) if title_ok else None
    if not close_active_tab(wait=close_wait):
        close_article_tabs(max_close=2, wait=close_wait)
    if menu_url:
        return menu_url, 1  # 菜单拿的是本页自身 URL,按 1 个可信候选上报
    return None, 0


def _read_clipboard_text() -> str:
    """ctypes 读剪贴板 CF_UNICODETEXT;失败/为空返回 ''。"""
    try:
        if not USER32.OpenClipboard(None):
            return ""
        try:
            if not USER32.IsClipboardFormatAvailable(CF_UNICODETEXT):
                return ""
            handle = USER32.GetClipboardData(CF_UNICODETEXT)
            ptr = K32.GlobalLock(handle) if handle else None
            if not ptr:
                return ""
            try:
                return ctypes.wstring_at(ptr)
            finally:
                K32.GlobalUnlock(ptr)
        finally:
            USER32.CloseClipboard()
    except Exception:
        return ""


def _dismiss_menu():
    """尽力关掉仍开着的「···」菜单(Esc 合成键,锁屏下无效但不抛错)。"""
    try:
        uia.SendKeys("{Esc}")
        time.sleep(0.6)
    except Exception:
        pass


def copy_link_via_menu(close_wait: float = 2.5, win=None) -> str | None:
    """「··· → 复制链接」菜单兜底:返回页面自身 raw URL,失败返回 None。

    尖峰D 实测(2026-09-03,郭磊宏观茶座,连续 2 次成功):
      * 入口是浏览器工具条 `AppMenuButton Name=更多`(无 InvokePattern;
        ExpandCollapse / LegacyIAccessible.DoDefaultAction 均不弹层),
        只有合成鼠标 `Click` 能打开菜单 —— 因此锁屏下不可用;
      * 菜单项为 `FlueMenuItemView Name=复制链接`(无独立顶层窗口,
        就在本 AppEx 窗口树里),Invoke 后剪贴板得到
        `https://mp.weixin.qq.com/s/<token>` 短链(永久,canonical 直接用);
      * 菜单复制的是**当前页自身** URL,不受页内嵌入链接污染。
    剪贴板先存后还在 finally 恢复;任何异常都吞掉(调用方还有返回 None
    后的 pending 路径)。win 给定时只在该窗口找「更多」按钮(多窗口时
    避免点到别的窗口菜单)。
    """
    clip = ""
    try:
        clip = uia.GetClipboardText() or ""
    except Exception:
        pass
    url = None
    try:
        targets = [win] if win is not None else appex_windows()
        if not targets or targets[0] is None:
            return None
        btn = None
        for t in targets:
            for c in walk_ctrls(t, max_nodes=6000):
                try:
                    if (c.ClassName or "") == MORE_BUTTON_CLASS and \
                            (c.Name or "") == MORE_BUTTON_NAME:
                        btn = c
                        break
                except Exception:
                    continue
            if btn is not None:
                break
        if btn is None:
            return None
        try:
            btn.Click(simulateMove=False)
        except Exception:
            return None
        item = None
        t0 = time.time()
        # 只在本窗口找菜单项:别的窗口残留同名菜单/正文文字绝不能 Invoke
        while time.time() - t0 < 3.0:
            for t in targets:
                for c in walk_ctrls(t, max_nodes=8000):
                    try:
                        if (c.ClassName or "") == MENU_ITEM_CLASS and \
                                COPY_LINK_NAME in (c.Name or ""):
                            item = c
                            break
                    except Exception:
                        continue
                if item is not None:
                    break
            if item is not None:
                break
            time.sleep(0.4)
        if item is None:
            _dismiss_menu()
            return None
        if not invoke_control(item):
            _dismiss_menu()
            return None
        time.sleep(2.0)
        raw = _read_clipboard_text().strip()
        if raw.startswith("http") and "mp.weixin.qq.com/s" in raw:
            url = raw
    except Exception:
        url = None
    finally:
        time.sleep(0.3)
        try:
            uia.SetClipboardText(clip)
        except Exception:
            pass
    return url


if __name__ == "__main__":
    # 冒烟:python -m src.wechat_bot
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    wins = appex_windows()
    print(f"AppEx 窗口: {len(wins)} 个")
    _w, _h = find_profile_host(kicks=1)
    print(f"公众号主页: {host_doc_name(_h) if _h else '无'}")
    _w2, _h2, _e = find_search_entry()
    print(f"搜索页: {'可用' if _e else '未找到(请人工打开一次「搜一搜」)'}")
