"""wechat_bot: 微信 4.x 内置浏览器(WeChatAppEx.exe)的 UIA 操作封装。

全部常量与操作序列来自 docs/spike-findings.md(尖峰A/B/B'/B''/C):
  * 自动化面只有 AppEx 顶层窗口(Chrome_WidgetWin_0);微信主窗口 Qt 树为空;
  * AppEx 有多个顶层窗口且 Z 序不稳,必须按宿主内容筛选,不能按面积/顺序;
  * Chromium a11y 树懒 realization:先 GetChildren 爬一遍,FindFirst 才能命中;
  * 树不 realization 时 ShowWindow(SW_MINIMIZE)→SW_RESTORE kick 一次即恢复;
  * 搜索输入必须剪贴板粘贴(SetValue 不触发页面事件),依赖桌面已解锁;
  * 文章 URL 只在文章页的 ValuePattern.Value 出现,列表页永远拿不到;
  * 激活 tab 的「关闭」按钮 rect 完整落在 Tab rect 内(非激活 tab 是错位悬停位)。

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
URL_RE = re.compile(r"https?://mp\.weixin\.qq\.com/s\?\S+")

USER32 = ctypes.windll.user32


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
        uia.SendKeys("{Ctrl}a")
        time.sleep(0.2)
        uia.SendKeys("{Ctrl}v")
        time.sleep(1.0)
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


def extract_article_urls(host, max_nodes: int = 5000):
    """文章宿主全树扫 ValuePattern.Value,返回 ({url: 控件名}, 遍历节点数)。

    尖峰C:833~1218 节点 / 1.8~2.2s;每篇实测恰 1 个 mp 文章 URL
    (偶有页内小程序链接,取最短者即文章自身,见 open_article_and_get_url)。
    """
    urls = {}
    n = 0
    for c in walk_ctrls(host, max_nodes=max_nodes, max_depth=34):
        n += 1
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
    return urls, n


def open_article_and_get_url(title_ctrl, open_timeout: float = 40.0,
                             scan_timeout: float = 20.0, max_nodes: int = 5000,
                             close_wait: float = 2.5):
    """打开标题所在文章,提取其 raw URL,关闭文章 tab。返回 (raw_url|None, url数)。

    url数 哨兵:-1 = 文章页未打开/无可 Invoke 卡片/残留文章页未清干净
    (此时本篇有意不打开);0 = 已打开但未提取到 URL。
    标题 TextControl 自身无 InvokePattern,向上最多 5 级找可 Invoke 的祖先卡片
    (尖峰C:class js_article_card…)。
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
    _w, h = find_article_host(timeout=open_timeout)
    if h is None:
        close_article_tabs(max_close=2, wait=close_wait)
        return None, -1
    deadline = time.time() + scan_timeout
    urls = {}
    while time.time() < deadline:
        urls, _n = extract_article_urls(h, max_nodes=max_nodes)
        if urls:
            break
        time.sleep(1.0)
    if not close_active_tab(wait=close_wait):
        close_article_tabs(max_close=2, wait=close_wait)
    if not urls:
        return None, 0
    # 每篇通常恰 1 个;偶含页内小程序链接时,最短者为主文 URL(尖峰C 经验)
    return sorted(urls, key=len)[0], len(urls)


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
