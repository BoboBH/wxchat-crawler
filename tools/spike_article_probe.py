"""尖峰B辅助:综合探针 —— 进程映射 / tab 枚举 / 重取主页 / 打开文章验证 URL 可见性。

用法:
    .venv/Scripts/python.exe tools/spike_article_probe.py
"""
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

log = lambda m: print(f"[probe2] {m}", flush=True)


def sh(cmd):
    return subprocess.run(cmd, capture_output=True).stdout.decode("gbk", errors="replace")


def proxy_client_pids():
    """找出当前连到 127.0.0.1:8888 的本地进程。"""
    out = sh(["netstat", "-ano"])
    pids = set()
    for line in out.splitlines():
        if "ESTABLISHED" in line:
            parts = line.split()
            if len(parts) >= 5 and parts[2].startswith("127.0.0.1:8888"):
                pids.add(parts[-1])
    info = []
    for pid in pids:
        name = sh(["tasklist", "/FI", f"PID eq {pid}"]).splitlines()
        proc = ""
        for ln in name:
            if ln.lower().startswith(("weixin", "wechatappex", "wechat")):
                proc = ln.split()[0]
                break
        info.append(f"pid={pid}({proc or '?'})")
    return info


def walk(host, cls=None, ctrl_type=None, max_depth=30, max_nodes=4000):
    out = []
    stack = [(host, 0)]
    while stack and len(out) < 400:
        ctrl, d = stack.pop()
        try:
            if (cls is None or (ctrl.ClassName or "") == cls) and \
                    (ctrl_type is None or ctrl.ControlType == ctrl_type):
                out.append(ctrl)
            if d < max_depth:
                stack.extend((k, d + 1) for k in ctrl.GetChildren())
        except Exception:
            continue
    return out


def invoke(ctrl):
    try:
        ctrl.InvokePattern.Invoke()
    except Exception:
        ctrl.Click(simulateMove=False)


def tabs_info(win):
    """tab 条上的 Tab 控件(无 Name,按坐标点击)。"""
    tabs = walk(win, cls="Tab", ctrl_type=uia.ControlType.TabItemControl)
    tabs += walk(win, cls="Tab", ctrl_type=None)
    uniq, seen = [], set()
    for t in tabs:
        try:
            r = t.BoundingRectangle
            key = (r.left, r.top)
            if key in seen or r.right - r.left < 10:
                continue
            seen.add(key)
            uniq.append((t, r))
        except Exception:
            continue
    return uniq


def doc_name(host):
    try:
        d = host.DocumentControl(searchDepth=3)
        if d.Exists(2, 0.5):
            return d.Name
    except Exception:
        pass
    return "?"


def main():
    win = find_browser_window()
    if win is None:
        sys.exit("未找到 WeChatAppEx 窗口")
    restore_if_minimized(win)
    win.SetActive()
    find_render_hosts(win)

    log(f"代理客户端进程: {proxy_client_pids()}")

    host = pick_active_host(win)
    log(f"当前页: {doc_name(host)!r}")
    for t, r in tabs_info(win)[:8]:
        log(f"Tab rect=({r.left},{r.top},{r.right},{r.bottom}) name={t.Name!r}")

    # 1) 切到搜索结果页 tab(其 RootWebArea 名含「搜一搜」),重新拉主页
    host = pick_active_host(win)
    if "搜一搜" not in doc_name(host):
        for t, r in tabs_info(win):
            cx, cy = (r.left + r.right) // 2, (r.top + r.bottom) // 2
            uia.SetCursorPos(cx, cy)
            time.sleep(0.5)
            uia.Click(x=cx, y=cy)
            time.sleep(2.5)
            host = pick_active_host(win) or host
            log(f"点击 tab 后页面: {doc_name(host)!r}")
            if "搜一搜" in doc_name(host):
                break
    host = pick_active_host(win)
    cards = [c for c in walk(host, cls="header-detail", ctrl_type=uia.ControlType.ButtonControl)
             if "公众号" in (c.Name or "")]
    log(f"搜索页公众号卡片数: {len(cards)}")
    if cards:
        log(f"重新 Invoke 卡片: {(cards[0].Name or '')[:24]!r}")
        invoke(cards[0])
        time.sleep(6)

    # 2) 打开第一篇文章,验证 /s?__biz= 是否经代理可见
    host = pick_active_host(win)
    log(f"当前页: {doc_name(host)!r}")
    titles = walk(host, cls="article__item__title", ctrl_type=uia.ControlType.TextControl)
    log(f"标题数: {len(titles)}")
    if titles:
        target = titles[-1]  # 列表最末尾(最不可能被缓存过的)文章
        log(f"点击文章: {(target.Name or '')[:24]!r}")
        invoke(target)
        time.sleep(8)
        host = pick_active_host(win) or host
        log(f"打开文章后页面: {doc_name(host)!r}")

    log(f"代理客户端进程(结束时): {proxy_client_pids()}")


if __name__ == "__main__":
    main()
