"""尖峰B辅助:UIA 程序化导航,触发公众号文章列表接口加载(配合 mitmdump 被动抓包)。

用法(仓库根目录,微信已登录、mitmdump 已在 8888 监听、系统代理已开):
    .venv/Scripts/python.exe tools/spike_navigate.py [--account 中金点睛] [--rounds 10]

流程(依据 docs/spike-findings.md「Task 9 端到端操作流程」):
  1. 定位 WeChatAppEx(Chrome_WidgetWin_0)浏览器窗口,最小化则 SW_RESTORE;
  2. GetChildren 爬树激活 Chromium 无障碍树,按 ClassName 定位
     Chrome_RenderWidgetHostHWND 宿主;
  3. 判断当前页:已是公众号主页(存在 article__item__title)→ 直接滚动;
     在搜索页(存在 weixin-search-input)→ 走完整搜索流程;
  4. 滚动加载:宿主中心 SetCursorPos + WheelDown(10) 多轮,轮询标题数至平台期;
  5. 恢复用户剪贴板。
"""
import argparse
import ctypes
import ctypes.wintypes
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import uiautomation as uia
from spike_uia import (find_browser_window, pick_active_host, find_render_hosts,
                       restore_if_minimized)

log = lambda msg: print(f"[nav] {msg}", flush=True)


def invoke(ctrl):
    """触发按钮:优先 GetInvokePattern().Invoke()(UIA 动作由提供者线程执行,
    不依赖窗口前台/不被 UIPI 拦截,尖峰B''实测是唯一可靠通道),
    失败退化 LegacyIAccessible.DoDefaultAction,再退化坐标点击。"""
    try:
        ctrl.GetInvokePattern().Invoke()
        return True
    except Exception:
        pass
    try:
        ctrl.GetLegacyIAccessiblePattern().DoDefaultAction()
        return True
    except Exception:
        pass
    ctrl.Click(simulateMove=False)
    return False


def reload_active_profile():
    """对当前激活 tab Invoke「重新加载」按钮,强制重取 mp_profile 页面壳。

    尖峰B''实测:窗口不在前台时合成鼠标点击与键盘全部无效
    (SetForegroundWindow 被拒、tab 条点击不切换),唯一可行的是
    UIA InvokePattern —— ReloadButton 控件支持 InvokePattern。
    返回 (ok, 说明)。
    """
    win = find_browser_window()
    if win is None:
        return False, "无浏览器窗口"
    btn = None
    stack = [(win, 0)]
    while stack:
        c, d = stack.pop()
        try:
            if (c.ClassName or "") == "ReloadButton":
                btn = c
                break
            if d < 14:
                stack.extend((k, d + 1) for k in c.GetChildren())
        except Exception:
            continue
    if btn is None:
        return False, "未找到 ReloadButton(按钮区 rect 约 579,96-611,128)"
    ok = invoke(btn)
    return ok, "已 Invoke 重新加载" if ok else "Invoke 失败"


def activate(win):
    """还原+前置窗口,爬树激活无障碍树,返回宿主。"""
    restore_if_minimized(win)
    try:
        win.SetActive()
    except Exception:
        pass
    hosts = find_render_hosts(win)  # 遍历本身即完成激活
    host = pick_active_host(win)
    log(f"宿主: {'None' if host is None else f'aid={host.AutomationId!r}'} "
        f"(render_hosts={len(hosts)})")
    return host


def collect_titles(host, max_depth=30, max_nodes=4000):
    """递归收集文章标题(TextControl class='article__item__title')。"""
    out = []
    stack = [(host, 0)]
    seen = 0
    while stack and seen < max_nodes:
        ctrl, d = stack.pop()
        seen += 1
        try:
            if (ctrl.ClassName or "") == "article__item__title" and ctrl.ControlType == uia.ControlType.TextControl:
                r = ctrl.BoundingRectangle
                out.append(((ctrl.Name or "").strip(), r.bottom))
            if d < max_depth:
                stack.extend((kid, d + 1) for kid in ctrl.GetChildren())
        except Exception:
            continue
    return out


def page_state(host):
    """返回 ('profile'|'search'|'other', 说明)。"""
    if host is None:
        return "other", "无宿主"
    edit = host.EditControl(AutomationId="weixin-search-input", searchDepth=30)
    has_edit = edit.Exists(2, 0.5)
    titles = collect_titles(host)
    if titles:
        return "profile", f"已在公众号主页,标题数={len(titles)}"
    if has_edit:
        return "search", "在搜索页(weixin-search-input 存在)"
    return "other", f"未知页面,标题数={len(titles)},搜索框存在={has_edit}"


def search_flow(win, account):
    """搜索页 → 搜索 → header-detail 卡片 → 公众号主页。"""
    host = pick_active_host(win)
    clip = ""
    try:
        clip = uia.GetClipboardText() or ""
    except Exception:
        pass
    ok = False
    try:
        edit = host.EditControl(AutomationId="weixin-search-input", searchDepth=30)
        if not edit.Exists(4, 0.5):
            return False, "未找到搜索输入框"
        edit.Click(simulateMove=False)
        time.sleep(0.6)
        uia.SetClipboardText(account)
        uia.SendKeys("{Ctrl}a")
        time.sleep(0.2)
        uia.SendKeys("{Ctrl}v")
        time.sleep(1.0)
        try:
            val = edit.GetValuePattern().Value
        except Exception:
            val = edit.Name
        log(f"搜索框读回: {val!r}")
        btn = host.ButtonControl(Name="搜索", searchDepth=30)
        if btn.Exists(3, 0.5):
            invoke(btn)
            log("已 Invoke「搜索」按钮")
        else:
            uia.SendKeys("{Enter}")
            log("未找到搜索按钮,改发 {Enter}")
        time.sleep(3.5)

        win = find_browser_window() or win
        activate(win)
        host = pick_active_host(win)
        card = host.ButtonControl(ClassName="header-detail", searchDepth=35)
        if not card.Exists(5, 0.5):
            return False, "未找到 header-detail 公众号卡片"
        log(f"公众号卡片: {((card.Name or '')[:40])!r}…")
        invoke(card)
        time.sleep(4.0)
        ok = True
    finally:
        try:
            uia.SetClipboardText(clip)
        except Exception:
            pass
    return ok, "搜索流程完成"


def scroll_load(win, rounds, label=""):
    """滚轮多轮加载,返回最后一轮标题数。"""
    host = pick_active_host(win)
    if host is None:
        return -1
    r = host.BoundingRectangle
    cx, cy = (r.left + r.right) // 2, min((r.top + r.bottom) // 2, r.bottom - 60)
    ctypes.windll.user32.SetCursorPos(int(cx), int(cy))
    time.sleep(0.3)
    prev, plateau = -1, 0
    last = -1
    for i in range(rounds):
        uia.WheelDown(10)
        time.sleep(2.0)
        host = pick_active_host(win) or host
        titles = collect_titles(host)
        last = len(titles)
        sample = [t[0] for t in titles[:3]]
        log(f"轮{i + 1}: 标题数={last} 样本={[s[:18] for s in sample]} ({label})")
        if last == prev:
            plateau += 1
            if plateau >= 2:
                log("标题数连续两轮无增长,停止滚动")
                break
        else:
            plateau = 0
        prev = last
    return last


def doc_name(host):
    """返回当前网页 RootWebArea 的 Name(公众号主页时即账号名)。"""
    try:
        d = host.DocumentControl(searchDepth=3)
        if d.Exists(2, 0.5):
            return (d.Name or "").strip()
    except Exception:
        pass
    return ""


def close_tab(win):
    """对浏览器窗口发 Ctrl+W 关闭当前 tab(用于从主页退回搜索页)。"""
    try:
        restore_if_minimized(win)
        win.SetActive()
    except Exception:
        pass
    time.sleep(0.3)
    uia.SendKeys("{Ctrl}w")
    time.sleep(1.8)


def find_search_input(win):
    win = find_browser_window() or win
    if win is None:
        return None, None
    host = pick_active_host(win)
    if host is None:
        return win, None
    edit = host.EditControl(AutomationId="weixin-search-input", searchDepth=30)
    return win, (edit if edit.Exists(2, 0.5) else None)


def goto_search(win, max_close=2):
    """确保当前页带搜索框;没有则 Ctrl+W 逐个关 tab,直到找到或放弃。"""
    for i in range(max_close + 1):
        win, edit = find_search_input(win)
        if edit is not None:
            if i:
                log(f"第{i}次 Ctrl+W 后搜索框可用")
            return win, True
        if i == max_close:
            break
        log(f"当前页(doc={doc_name(pick_active_host(win) or win)!r})无搜索框,"
            f"Ctrl+W 关 tab ({i + 1}/{max_close})")
        close_tab(win)
        if find_browser_window() is None:
            log("Ctrl+W 后浏览器窗口消失,停止关闭")
            break
    return win, False


def open_account(win, account, dwell):
    """搜索并打开指定公众号主页,停留 dwell 秒(不滚动)。返回 (win, ok, 说明)。"""
    win = find_browser_window() or win
    host = pick_active_host(win)
    name = doc_name(host) if host is not None else ""
    n_titles = len(collect_titles(host)) if host is not None else 0
    if account and account in name and n_titles:
        log(f"[{account}] 已在该公众号主页(doc={name!r},标题数={n_titles}),跳过搜索")
    else:
        win, ok = goto_search(win)
        if not ok:
            return win, False, "无法回到带搜索框的页面"
        ok, msg = search_flow(win, account)
        log(f"[{account}] 搜索流程: {msg}")
        if not ok:
            return win, False, msg
    win = find_browser_window() or win
    host = activate(win)
    time.sleep(dwell)
    host = pick_active_host(win) or host
    titles = collect_titles(host)
    name = doc_name(host)
    log(f"[{account}] 主页就绪 doc={name!r} 标题数={len(titles)} "
        f"样本={[t[0][:16] for t in titles[:3]]} (停留{dwell:.0f}s,未滚动)")
    return win, True, f"titles={len(titles)} doc={name!r}"


def scroll_test(win, wheels=(10, 10)):
    """滚动对照实验:WheelDown 若干屏,观察标题数变化(mitmdump 侧看是否新增请求)。"""
    win = find_browser_window() or win
    if win is None:
        return
    try:
        win.SetActive()
    except Exception:
        pass
    host = pick_active_host(win)
    if host is None:
        log("滚动对照: 无宿主,跳过")
        return
    r = win.BoundingRectangle
    cx, cy = (r.left + r.right) // 2, (r.top + r.bottom) // 2
    ctypes.windll.user32.SetCursorPos(int(cx), int(cy))
    time.sleep(0.4)
    pt = ctypes.wintypes.POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    log(f"滚动对照: 光标=({pt.x},{pt.y}) 窗口中心=({cx},{cy})")
    before = len(collect_titles(host))
    log(f"滚动对照: 滚动前标题数={before}")
    for k, w in enumerate(wheels, 1):
        uia.WheelDown(w)
        time.sleep(2.5)
        host = pick_active_host(find_browser_window() or win) or host
        titles = collect_titles(host)
        log(f"滚动对照: 第{k}次 WheelDown({w}) 后标题数={len(titles)} "
            f"样本={[t[0][:16] for t in titles[:3]]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", default=None, help="单个账号(向后兼容)")
    ap.add_argument("--accounts", default="中金点睛,郭磊宏观茶座",
                    help="逗号分隔,依次打开的公众号")
    ap.add_argument("--rounds", type=int, default=0,
                    help=">0 时恢复旧行为:对每个主页做滚轮无限加载")
    ap.add_argument("--dwell", type=float, default=8.0, help="每个主页停留秒数")
    ap.add_argument("--no-scroll-test", dest="scroll_test", action="store_false",
                    help="最后一个主页不做 WheelDown 2 屏对照")
    args = ap.parse_args()

    accounts = [a for a in (args.accounts or "").split(",") if a]
    if args.account:
        accounts = [args.account]

    win = find_browser_window()
    if win is None:
        sys.exit("未找到 WeChatAppEx 浏览器窗口")
    log(f"浏览器窗口 pid={win.ProcessId} name={win.Name!r}")

    for i, account in enumerate(accounts):
        win, ok, info = open_account(win, account, args.dwell)
        if not ok:
            log(f"[{account}] 打开失败: {info}")
            continue
        if args.rounds > 0:
            scroll_load(win, args.rounds, label=account)
        elif args.scroll_test and i == len(accounts) - 1:
            scroll_test(win)
    log("结束")


if __name__ == "__main__":
    main()
