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
    """uiautomation 2.0 无 Control.Invoke(),走 InvokePattern,失败退化为鼠标点击。"""
    try:
        ctrl.InvokePattern.Invoke()
    except Exception:
        ctrl.Click(simulateMove=False)


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", default="中金点睛")
    ap.add_argument("--rounds", type=int, default=10)
    args = ap.parse_args()

    win = find_browser_window()
    if win is None:
        sys.exit("未找到 WeChatAppEx 浏览器窗口")
    log(f"浏览器窗口 pid={win.ProcessId} name={win.Name!r}")
    host = activate(win)
    state, why = page_state(host)
    log(f"当前页面状态: {state} ({why})")

    if state == "search":
        ok, msg = search_flow(win, args.account)
        log(f"搜索流程: {msg}")
        if not ok:
            sys.exit(2)
        win = find_browser_window() or win
        activate(win)
    elif state == "other":
        doc = pick_active_host(win)
        names = []
        try:
            d = doc.DocumentControl(searchDepth=3)
            d = d if d.Exists(2, 0.5) else doc
            names = [(c.Name or "")[:40] for c in d.GetChildren()][:15]
        except Exception as exc:
            names = [f"<{exc!r}>"]
        sys.exit(f"未知页面,RootWebArea 子节点: {names}")

    n = scroll_load(win, args.rounds)
    log(f"结束,最终标题数={n}")


if __name__ == "__main__":
    main()
