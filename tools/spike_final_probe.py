"""尖峰B辅助:最终组合探针 —— 回退搜索页、重进主页(触发去缓存抓取)、坐标点击开文章。

用法:
    .venv/Scripts/python.exe tools/spike_final_probe.py
"""
import ctypes
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import uiautomation as uia
from spike_uia import find_browser_window, pick_active_host, find_render_hosts, \
    restore_if_minimized

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
log = lambda m: print(f"[final] {m}", flush=True)


def screenshot(tag):
    path = os.path.join(ROOT, "data", f"spike_final_{tag}.png").replace("/", "\\")
    ps = ("Add-Type -AssemblyName System.Drawing; Add-Type -AssemblyName System.Windows.Forms;"
          "$b=[System.Windows.Forms.SystemInformation]::VirtualScreen;"
          "$bmp=New-Object System.Drawing.Bitmap($b.Width,$b.Height);"
          "$g=[System.Drawing.Graphics]::FromImage($bmp);"
          f"$g.CopyFromScreen($b.Left,$b.Top,0,0,$bmp.Size); $bmp.Save('{path}')")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True)
    log(f"截图: {os.path.basename(path)}")


def invoke(ctrl):
    try:
        ctrl.InvokePattern.Invoke()
    except Exception:
        ctrl.Click(simulateMove=False)


def walk(host, cls, ctrl_type=None, max_depth=30, max_nodes=4000):
    out = []
    stack = [(host, 0)]
    while stack and len(out) < 500:
        ctrl, d = stack.pop()
        try:
            if (ctrl.ClassName or "") == cls and (ctrl_type is None or ctrl.ControlType == ctrl_type):
                out.append(ctrl)
            if d < max_depth:
                stack.extend((k, d + 1) for k in ctrl.GetChildren())
        except Exception:
            continue
    return out


def doc_name(host):
    try:
        d = host.DocumentControl(searchDepth=3)
        if d.Exists(2, 0.5):
            return d.Name or ""
    except Exception:
        pass
    return "?"


def all_appex_docs():
    """枚举所有 WeChatAppEx 顶层窗口及其文档名。"""
    root = uia.GetRootControl()
    rows = []
    for w in root.GetChildren():
        try:
            if (w.ClassName or "") != "Chrome_WidgetWin_0":
                continue
        except Exception:
            continue
        try:
            import spike_uia
            if spike_uia.process_name(w.ProcessId).lower() != "wechatappex.exe":
                continue
        except Exception:
            continue
        find_render_hosts(w)
        h = pick_active_host(w)
        rows.append(f"win pid={w.ProcessId} doc={doc_name(h) if h else '(无宿主)'!r}")
    return rows


def main():
    win = find_browser_window()
    if win is None:
        sys.exit("未找到 WeChatAppEx 窗口")
    restore_if_minimized(win)
    win.SetActive()
    find_render_hosts(win)

    host = pick_active_host(win)
    log(f"起点页: {doc_name(host)!r}")

    # 1) Alt+Left 历史回退,尝试回到搜索结果页
    for attempt in range(2):
        try:
            win.SetFocus()
        except Exception:
            pass
        uia.SendKeys("%{Left}")
        time.sleep(3.5)
        host = pick_active_host(win) or host
        log(f"Alt+Left #{attempt + 1} 后: {doc_name(host)!r}")
        if "搜一搜" in doc_name(host):
            break

    # 2) 重进公众号主页(触发 mp_profile 重新拉取,mitmproxy 已剥缓存头)
    if "搜一搜" in doc_name(host):
        cards = [c for c in walk(host, "header-detail", uia.ControlType.ButtonControl)
                 if "公众号" in (c.Name or "")]
        log(f"公众号卡片数: {len(cards)}")
        if cards:
            invoke(cards[0])
            time.sleep(7)
            host = pick_active_host(win) or host
            log(f"重进主页: {doc_name(host)!r}")
    else:
        log("未回到搜索页,跳过主页重取")

    # 3) 坐标点击打开第一篇文章
    titles = walk(host, "article__item__title", uia.ControlType.TextControl)
    log(f"标题数: {len(titles)}")
    if titles:
        t = titles[0]
        r = t.BoundingRectangle
        log(f"坐标点击文章: {(t.Name or '')[:24]!r} @({(r.left + r.right) // 2},{(r.top + r.bottom) // 2})")
        uia.SetCursorPos((r.left + r.right) // 2, (r.top + r.bottom) // 2)
        time.sleep(0.3)
        uia.Click(x=(r.left + r.right) // 2, y=(r.top + r.bottom) // 2)
        time.sleep(9)

    log("全部 AppEx 窗口文档: " + "; ".join(all_appex_docs()))
    screenshot("end")


if __name__ == "__main__":
    main()
