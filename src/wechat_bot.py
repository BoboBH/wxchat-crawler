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

USER32 = ctypes.windll.user32
K32 = ctypes.windll.kernel32
# 64 位下句柄是 64 位,必须显式声明 restype/argtypes,否则被截断成 32 位
USER32.OpenClipboard.argtypes = [ctypes.c_void_p]
USER32.GetClipboardData.argtypes = [ctypes.c_uint]
USER32.GetClipboardData.restype = ctypes.c_void_p
K32.GlobalLock.argtypes = [ctypes.c_void_p]
K32.GlobalLock.restype = ctypes.c_void_p
K32.GlobalUnlock.argtypes = [ctypes.c_void_p]


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


def scroll_once(host, wheels: int = 10):
    """在宿主中心滚轮下翻一屏(合成鼠标输入,锁屏下无效;仅新账号扩量用)。"""
    try:
        r = host.BoundingRectangle
        USER32.SetCursorPos(
            int((r.left + r.right) // 2), int(min((r.top + r.bottom) // 2, r.bottom - 60)))
        time.sleep(0.3)
        uia.WheelDown(wheels)
    except Exception:
        pass


def scroll_to_top(host, wheels: int = 30, wait: float = 2.0):
    """滚轮回到页顶并稍候(合成鼠标输入,锁屏下无效)。

    主页日期标签是 sticky 头:页面滚动后其 rect 被视口钳制、不再随分组走,
    在滚动状态下扫描会导致日期与标题错位配对(验收实测:同文一次 08-28
    一次 08-30)。扩量滚动后必须回顶再取最终列表。
    """
    try:
        r = host.BoundingRectangle
        USER32.SetCursorPos(
            int((r.left + r.right) // 2), int(min((r.top + r.bottom) // 2, r.bottom - 60)))
        time.sleep(0.3)
        uia.WheelUp(wheels)
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
        return False, "未找到搜索页(weixin-search-input);请先在微信中打开一次「搜一搜」"

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
    # 唯一候选也要归属证据:控件 Name ≈ 页面标题(无页面标题时用期望标题,
    # 两者皆无 → 不采信)。scan_article_page 存的 Name 截到 40 字,截断保留
    # 前缀,双向包含判断不受影响。
    owner_probe = page_t or (expected_title or "")
    if len(urls) == 1 and not (owner_probe and
                               _titles_match(owner_probe, next(iter(urls.values())))):
        urls = {}  # 唯一候选归属存疑 → 按 0 个可信 URL 处理,走菜单兜底
    if urls:
        if not close_active_tab(wait=close_wait):
            close_article_tabs(max_close=2, wait=close_wait)
        return next(iter(urls)), len(urls)
    # 0 个可信候选:「··· → 复制链接」菜单兜底(copy 的就是本页 URL,
    # 不受内嵌链接污染)。-2 分支已提前返回,不会走到这里。
    menu_url = copy_link_via_menu(close_wait=close_wait, win=win) if title_ok else None
    if not close_active_tab(wait=close_wait):
        close_article_tabs(max_close=2, wait=close_wait)
    if menu_url:
        return menu_url, len(urls) or 1
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
