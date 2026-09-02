"""尖峰A:探测微信 4.1.x 的 UIA 控件树。运行前确保微信已登录。

用法:
    python tools/spike_uia.py                # 定位微信主窗口并 dump(默认深度4)
    python tools/spike_uia.py --list         # 仅枚举桌面顶层窗口
    python tools/spike_uia.py --depth 6      # 指定 dump 深度
    python tools/spike_uia.py --all-children # 不限制每层子控件数量
    python tools/spike_uia.py --browser      # dump 内置浏览器(WeChatAppEx)窗口
    python tools/spike_uia.py --browser --web-only  # 仅 dump 网页内容子树(推荐)

实测要点(详见 docs/spike-findings.md):
  * 4.x 主窗口为 Qt 自绘(Weixin.exe / Qt51514QWindowIcon),UIA 树几乎为空;
  * 公众号搜索/主页/历史消息全部运行在 WeChatAppEx.exe(Chromium)窗口里,
    类名 Chrome_WidgetWin_0,该窗口暴露完整 UIA 树 —— 自动化应锚定此窗口;
  * Chromium 无障碍树需先手动 GetChildren 爬一遍才会激活,FindFirst 才能命中;
  * 网页内容必须锚定在 Chrome_RenderWidgetHostHWND 宿主控件之下检索,
    其 AutomationId 每次导航都会变化,只能按 ClassName 定位。
"""
import argparse
import ctypes
import sys

import uiautomation as uia

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def dump(ctrl, depth=0, max_depth=5, max_children=40):
    try:
        name = (ctrl.Name or "").strip()
        line = (f"{'  ' * depth}{ctrl.ControlType} class={ctrl.ClassName!r} "
                f"name={name!r} auto_id={ctrl.AutomationId!r}")
        rect = ctrl.BoundingRectangle
        line += f" rect=({rect.left},{rect.top},{rect.right},{rect.bottom})"
    except Exception as exc:  # 个别控件可能已失效或拒绝访问
        print(f"{'  ' * depth}<error: {exc!r}>")
        return
    print(line)
    if depth >= max_depth:
        return
    try:
        children = ctrl.GetChildren()
    except Exception as exc:
        print(f"{'  ' * (depth + 1)}<GetChildren error: {exc!r}>")
        return
    for kid in children[:max_children]:
        dump(kid, depth + 1, max_depth, max_children)
    if len(children) > max_children:
        print(f"{'  ' * (depth + 1)}<... {len(children) - max_children} more children truncated>")


def list_top_windows():
    root = uia.GetRootControl()
    for win in root.GetChildren():
        try:
            print(f"Name={win.Name!r} ClassName={win.ClassName!r} "
                  f"pid={win.ProcessId} type={win.ControlType}")
        except Exception as exc:
            print(f"<error: {exc!r}>")


def process_name(pid):
    """返回进程映像名(如 Weixin.exe),失败返回 ''。"""
    k32 = ctypes.windll.kernel32
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
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


def is_wechat_process(pid):
    return process_name(pid).lower() in ("weixin.exe", "wechat.exe")


def restore_if_minimized(ctrl):
    """窗口最小化时 UIA 返回 -32000 坐标且树为空,先还原。返回是否执行了还原。"""
    user32 = ctypes.windll.user32
    GWL_STYLE = -16
    WS_MINIMIZE = 0x20000000
    try:
        hwnd = ctrl.NativeWindowHandle
    except Exception:
        return False
    if not hwnd:
        return False
    style = user32.GetWindowLongW(hwnd, GWL_STYLE)
    if style & WS_MINIMIZE or ctrl.BoundingRectangle.left <= -30000:
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        return True
    return False


def find_browser_window():
    """定位微信内置浏览器窗口(WeChatAppEx.exe / Chrome_WidgetWin_0)。

    公众号搜索、主页与历史消息页都在这个 Chromium 窗口里,而非主窗口。
    """
    root = uia.GetRootControl()
    found = []
    for cand in root.GetChildren():
        try:
            if (cand.ClassName or "") != "Chrome_WidgetWin_0":
                continue
            if process_name(cand.ProcessId).lower() != "wechatappex.exe":
                continue
            found.append(cand)
        except Exception:
            continue
    if not found:
        return None
    # 若有多个,取最大的(可见且面积最大者通常是当前网页窗口)
    def area(c):
        r = c.BoundingRectangle
        return max(0, (r.right - r.left) * (r.bottom - r.top))
    return max(found, key=area)


def find_render_hosts(root_ctrl):
    """收集 Chrome_RenderWidgetHostHWND 宿主控件(网页内容锚点)。

    必须先 GetChildren 爬一遍才能激活 Chromium 的无障碍树,随后 FindFirst
    才能命中 —— 本函数的遍历本身即完成了激活。
    """
    hosts = []

    def walk(ctrl):
        try:
            if (ctrl.ClassName or "") == "Chrome_RenderWidgetHostHWND":
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


def pick_active_host(root_ctrl):
    """返回当前激活网页的宿主(取宽度>100 的最大者)。"""
    hosts = [h for h in find_render_hosts(root_ctrl)
             if h.BoundingRectangle.right - h.BoundingRectangle.left > 100]
    if not hosts:
        return None
    def area(c):
        r = c.BoundingRectangle
        return (r.right - r.left) * (r.bottom - r.top)
    return max(hosts, key=area)


def find_wechat_window():
    """定位微信主窗口:按标题「微信」+ 进程名匹配;失败再按已知 ClassName 兜底。"""
    root = uia.GetRootControl()
    candidates = []
    for cand in root.GetChildren():
        try:
            if (cand.Name or "").strip() == "微信" and is_wechat_process(cand.ProcessId):
                candidates.append((cand, f"Name=微信+进程{process_name(cand.ProcessId)}"))
        except Exception:
            continue
    if not candidates:
        # 兜底:已知类名(3.x Duilib / 4.x Qt / mmui)
        known = ("WeChatMainWndForPC", "Qt51514QWindowIcon")
        for cand in root.GetChildren():
            try:
                cls = cand.ClassName or ""
            except Exception:
                continue
            if cls in known or cls.startswith("mmui::"):
                candidates.append((cand, f"ClassName={cls}"))
    for cand, how in candidates:
        if cand.Exists(1, 0.5):
            return cand, how
    return None, None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="微信 UIA 控件树探测")
    parser.add_argument("--list", action="store_true", help="仅枚举桌面顶层窗口")
    parser.add_argument("--browser", action="store_true",
                        help="dump 内置浏览器(WeChatAppEx.exe)窗口而非主窗口")
    parser.add_argument("--web-only", action="store_true",
                        help="配合 --browser,仅 dump 激活网页内容子树")
    parser.add_argument("--depth", type=int, default=4, help="dump 最大深度")
    parser.add_argument("--max-children", type=int, default=40, help="每层最多子控件数")
    parser.add_argument("--all-children", action="store_true", help="不限制每层子控件数量")
    args = parser.parse_args()

    if args.list:
        list_top_windows()
        sys.exit(0)

    max_children = 10 ** 9 if args.all_children else args.max_children

    if args.browser:
        win = find_browser_window()
        if win is None:
            sys.exit("未找到 WeChatAppEx.exe 浏览器窗口;请先在微信里打开任一公众号/搜一搜页面")
        restore_if_minimized(win)
        print(f"浏览器窗口: Name={win.Name!r} ClassName={win.ClassName!r} pid={win.ProcessId}")
        if args.web_only:
            host = pick_active_host(win)
            if host is None:
                sys.exit("未找到 Chrome_RenderWidgetHostHWND 宿主(网页可能尚未加载)")
            print(f"网页宿主: aid={host.AutomationId!r} rect={host.BoundingRectangle}")
            doc = host.DocumentControl(searchDepth=3)
            if doc.Exists(2, 1):
                dump(doc, max_depth=args.depth, max_children=max_children)
            else:
                dump(host, max_depth=args.depth, max_children=max_children)
        else:
            dump(win, max_depth=args.depth, max_children=max_children)
        sys.exit(0)

    win, how = find_wechat_window()
    if win is None:
        sys.exit("未找到微信主窗口(标题含'微信'或 mmui:: 类名),请确认已登录。可用 --list 查看顶层窗口")
    restore_if_minimized(win)
    print(f"主窗口(经{how}定位): Name={win.Name!r} ClassName={win.ClassName!r} "
          f"pid={win.ProcessId}")
    dump(win, max_depth=args.depth, max_children=max_children)
