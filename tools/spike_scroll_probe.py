"""尖峰B辅助:滚动有效性探针 —— 验证滚轮是否真的驱动列表翻页。

每轮打印:文章标题数、日期标签数、tab 状态;截图辅助人工核验。
用法:
    .venv/Scripts/python.exe tools/spike_scroll_probe.py [--rounds 6] [--tab 文章]
"""
import argparse
import ctypes
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import uiautomation as uia
from spike_uia import (find_browser_window, pick_active_host, find_render_hosts,
                       restore_if_minimized)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
log = lambda m: print(f"[probe] {m}", flush=True)


def screenshot(tag):
    path = os.path.join(ROOT, "data", f"spike_shot_{tag}.png").replace("/", "\\")
    ps = (
        "Add-Type -AssemblyName System.Drawing; Add-Type -AssemblyName System.Windows.Forms;"
        "$b=[System.Windows.Forms.SystemInformation]::VirtualScreen;"
        "$bmp=New-Object System.Drawing.Bitmap($b.Width,$b.Height);"
        "$g=[System.Drawing.Graphics]::FromImage($bmp);"
        f"$g.CopyFromScreen($b.Left,$b.Top,0,0,$bmp.Size); $bmp.Save('{path}')"
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True)
    log(f"截图: {path}")


def collect(host, cls, ctrl_type=None, max_depth=30, max_nodes=4000):
    out = []
    stack = [(host, 0)]
    seen = 0
    while stack and seen < max_nodes:
        ctrl, d = stack.pop()
        seen += 1
        try:
            if (ctrl.ClassName or "") == cls and (ctrl_type is None or ctrl.ControlType == ctrl_type):
                out.append((ctrl.Name or "").strip())
            if d < max_depth:
                stack.extend((k, d + 1) for k in ctrl.GetChildren())
        except Exception:
            continue
    return out


def stats(host):
    titles = collect(host, "article__item__title", uia.ControlType.TextControl)
    times = collect(host, "publish_time")
    tabs = collect(host, "profile_details__tabs-item")
    return titles, times, tabs


def invoke(ctrl):
    try:
        ctrl.InvokePattern.Invoke()
    except Exception:
        ctrl.Click(simulateMove=False)


def find_by_class(host, cls, name_contains=None, ctrl_type=None, max_depth=35):
    stack = [(host, 0)]
    while stack:
        ctrl, d = stack.pop()
        try:
            if (ctrl.ClassName or "") == cls and (ctrl_type is None or ctrl.ControlType == ctrl_type):
                if name_contains is None or name_contains in (ctrl.Name or ""):
                    return ctrl
            if d < max_depth:
                stack.extend((k, d + 1) for k in ctrl.GetChildren())
        except Exception:
            continue
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=6)
    ap.add_argument("--tab", default="")
    args = ap.parse_args()

    win = find_browser_window()
    if win is None:
        sys.exit("未找到 WeChatAppEx 窗口")
    restore_if_minimized(win)
    win.SetActive()
    find_render_hosts(win)
    host = pick_active_host(win)
    log(f"宿主 aid={host.AutomationId!r}")
    screenshot("before")

    if args.tab:
        tab = find_by_class(host, "profile_details__tabs-item", args.tab,
                            ctrl_type=uia.ControlType.HyperlinkControl)
        if tab is not None:
            log(f"点击 tab「{args.tab}」: {(tab.Name or '')[:20]!r}")
            invoke(tab)
            time.sleep(4)
            host = pick_active_host(win) or host
        else:
            log(f"未找到 tab「{args.tab}」")

    r = host.BoundingRectangle
    cx, cy = (r.left + r.right) // 2, (r.top + r.bottom) // 2
    ctypes.windll.user32.SetCursorPos(int(cx), int(cy))
    time.sleep(0.3)
    prev, plateau = -1, 0
    for i in range(args.rounds):
        uia.WheelDown(10)
        time.sleep(3.0)
        host = pick_active_host(win) or host
        titles, times, tabs = stats(host)
        log(f"轮{i + 1}: 标题数={len(titles)} 日期标签数={len(times)} tabs={tabs[:5]} "
            f"最新日期={times[:2]} 首标题={(titles[0][:16] if titles else '')!r}")
        if len(titles) == prev:
            plateau += 1
            if plateau >= 2:
                log("平台期,停止")
                break
        else:
            plateau = 0
        prev = len(titles)
    screenshot("after")


if __name__ == "__main__":
    main()
