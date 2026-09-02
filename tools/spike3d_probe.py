"""尖峰3d 探测脚本(过程性工具,沉淀版见 tools/spike_article_url.py)。

子命令:
    state   —— 枚举 AppEx 顶层窗口 / 各窗口宿主 / doc 名 / tab 名
    m1      —— 手段1:Invoke 打开第一篇文章 → 文章页 UIA 树属性 grep URL
    tree    —— dump 指定窗口当前激活页树(深度受限)
"""
import argparse
import ctypes
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import uiautomation as uia
from spike_uia import (find_browser_window, pick_active_host, find_render_hosts,
                       restore_if_minimized, process_name)
from spike_navigate import activate, invoke, doc_name

log = lambda m: print(f"[3d] {m}", flush=True)

URL_RE = re.compile(r"https?://[^\s\"'<>\\]+", re.I)
MP_RE = re.compile(r"mp\.weixin\.qq\.com", re.I)

# ---- 属性 ID 表(沿用 spike_url_extract 的构建方式) ----
PROP_NAME = {}
for _n in dir(uia.PropertyId):
    if not _n.startswith("_"):
        _v = getattr(uia.PropertyId, _n)
        if isinstance(_v, int):
            PROP_NAME[_v] = _n
# 聚焦的字符串型属性(不做全量 173 项穷举,控制 COM 往返)
FOCUS_PROP_NAMES = (
    "Name", "Value", "HelpText", "ItemType", "AriaProperties", "AriaRole",
    "FullDescription", "AutomationId", "ClassName", "ItemStatus",
    "LocalizedControlType", "AcceleratorKey", "Description",
)
FOCUS_PROPS = []
for pid, name in PROP_NAME.items():
    base = name.replace("PropertyId", "")
    if base in FOCUS_PROP_NAMES:
        FOCUS_PROPS.append(pid)

PAT_NAME = {}
for _n in dir(uia.PatternId):
    if not _n.startswith("_"):
        _v = getattr(uia.PatternId, _n)
        if isinstance(_v, int):
            PAT_NAME[_v] = _n
# 想读的模式属性(模式对象上可能有 Url/Value 等)
INTEREST_PATTERNS = ("ValuePattern", "LegacyIAccessiblePattern", "ObjectModelPattern",
                     "TextPattern", "InvokePattern", "HyperlinkPattern")


def all_appex_windows():
    root = uia.GetRootControl()
    out = []
    for cand in root.GetChildren():
        try:
            if (cand.ClassName or "") == "Chrome_WidgetWin_0" and \
                    process_name(cand.ProcessId).lower() == "wechatappex.exe":
                out.append(cand)
        except Exception:
            continue
    return out


def window_hosts(win):
    return find_render_hosts(win)


def window_summary(tag=""):
    wins = all_appex_windows()
    log(f"--- AppEx 顶层窗口 x{len(wins)} {tag}")
    for w in wins:
        try:
            r = w.BoundingRectangle
            hosts = window_hosts(w)
            docs = []
            for h in hosts:
                try:
                    d = h.DocumentControl(searchDepth=3)
                    nm = (d.Name or "") if d.Exists(1, 0.2) else "<no doc>"
                except Exception:
                    nm = "<err>"
                docs.append(nm)
            log(f"win name={w.Name!r} rect=({r.left},{r.top},{r.right},{r.bottom}) "
                f"pid={w.ProcessId} hosts={len(hosts)} docs={docs!r}")
        except Exception as e:
            log(f"win <error {e!r}>")
    return wins


def tab_names(win, max_nodes=3000):
    """tab 条上的 Tab 控件 Name(尖峰A 说无 Name,B'' 后再确认)。"""
    out = []
    stack = [(win, 0)]
    seen = 0
    while stack and seen < max_nodes:
        c, d = stack.pop()
        seen += 1
        try:
            if (c.ClassName or "") in ("Tab", "FlueTabContainer", "TabStrip"):
                out.append((c.ClassName, (c.Name or ""), c.BoundingRectangle))
            if d < 16:
                stack.extend((k, d + 1) for k in c.GetChildren())
        except Exception:
            continue
    return out


def grep_tree_urls(host, max_nodes=3000, max_depth=34, hit_stop=False):
    """聚焦属性 grep:返回 (命中列表, 遍历节点数)。"""
    hits = []
    n = 0
    stack = [(host, 0)]
    while stack and n < max_nodes:
        ctrl, d = stack.pop()
        n += 1
        try:
            header = (f"class={ctrl.ClassName!r} aid={ctrl.AutomationId!r} "
                      f"name={(ctrl.Name or '')[:48]!r} type={ctrl.ControlType}")
            blob_parts = [header]
            elem = ctrl.Element
            for pid in FOCUS_PROPS:
                try:
                    val = elem.GetCurrentPropertyValueEx(pid, True)
                except Exception:
                    continue
                if val is None or val == "":
                    continue
                blob_parts.append(f"{PROP_NAME.get(pid, pid)}={val}")
            # 关键模式属性:LegacyIAccessible 的 Description/Value、ValuePattern.Value
            try:
                lia = ctrl.GetPattern(uia.PatternId.LegacyIAccessiblePatternId)
                if lia is not None:
                    for attr in ("Value", "Description", "DefaultAction"):
                        try:
                            v = getattr(lia, attr)
                        except Exception:
                            v = None
                        if v:
                            blob_parts.append(f"LIA.{attr}={v}")
            except Exception:
                pass
            try:
                vp = ctrl.GetPattern(uia.PatternId.ValuePatternId)
                if vp is not None:
                    blob_parts.append(f"ValuePattern.Value={vp.Value}")
            except Exception:
                pass
            blob = " | ".join(str(p) for p in blob_parts)
            for m in URL_RE.finditer(blob):
                u = m.group(0)
                hits.append((u, header[:160], blob[max(0, m.start() - 60):m.end() + 40]))
                if hit_stop:
                    return hits, n
            if MP_RE.search(blob) and not URL_RE.search(blob):
                hits.append(("<mp-weixin-no-url>", header[:160], blob[:300]))
        except Exception:
            pass
        try:
            if d < max_depth:
                stack.extend((k, d + 1) for k in ctrl.GetChildren())
        except Exception:
            continue
    return hits, n


def pattern_probe(host, max_nodes=2000):
    """检查文章页宿主树里各模式的存在性(ValuePattern/Url 之类)。"""
    got = {}
    stack = [(host, 0)]
    n = 0
    while stack and n < max_nodes:
        c, d = stack.pop()
        n += 1
        try:
            for pid, pname in ((uia.PatternId.ValuePattern, "Value"),
                               (uia.PatternId.LegacyIAccessiblePattern, "LIA"),
                               (uia.PatternId.ObjectModelPattern, "ObjectModel"),
                               (uia.PatternId.InvokePattern, "Invoke")):
                try:
                    pat = c.GetPattern(pid)
                except Exception:
                    pat = None
                if pat is not None:
                    got.setdefault(pname, 0)
                    got[pname] += 1
                    if pname == "Value":
                        try:
                            v = pat.Value
                        except Exception:
                            v = "<err>"
                        if v and MP_RE.search(str(v)):
                            log(f"  ValuePattern.Value URL HIT: {v!r} on class={c.ClassName!r}")
        except Exception:
            pass
        if d < 30:
            try:
                stack.extend((k, d + 1) for k in c.GetChildren())
            except Exception:
                continue
    return got


def find_profile_host():
    """在所有 AppEx 窗口中找「中金点睛」主页(有 article__item__title)。"""
    for w in all_appex_windows():
        try:
            restore_if_minimized(w)
        except Exception:
            pass
        hosts = find_render_hosts(w)
        for h in hosts:
            try:
                if h.BoundingRectangle.right - h.BoundingRectangle.left < 100:
                    continue
            except Exception:
                continue
            titles = collect_titles_quick(h)
            if titles:
                return w, h, titles
    return None, None, []


def collect_titles_quick(host, max_nodes=2500):
    out = []
    stack = [(host, 0)]
    n = 0
    while stack and n < max_nodes:
        c, d = stack.pop()
        n += 1
        try:
            if (c.ClassName or "") == "article__item__title":
                out.append(c)
            if d < 30:
                stack.extend((k, d + 1) for k in c.GetChildren())
        except Exception:
            continue
    return out


def find_article_host(markers=("activity-name", "js_content", "js_name")):
    """找当前打开了文章页的宿主(含 aid=activity-name 等正文锚点)。"""
    for w in all_appex_windows():
        for h in find_render_hosts(w):
            try:
                if h.BoundingRectangle.right - h.BoundingRectangle.left < 100:
                    continue
            except Exception:
                continue
            found = 0
            stack = [(h, 0)]
            n = 0
            while stack and n < 2500:
                c, d = stack.pop()
                n += 1
                try:
                    if (c.AutomationId or "") in markers:
                        found += 1
                        if found >= 1:
                            break
                    if d < 30:
                        stack.extend((k, d + 1) for k in c.GetChildren())
                except Exception:
                    continue
            if found:
                return w, h
    return None, None


def open_first_article(profile_host, title_index=0):
    """Invoke 第 title_index 篇文章标题(退化:卡片 InvokePattern / LIA DoDefaultAction)。"""
    titles = collect_titles_quick(profile_host)
    if not titles:
        return False, "无标题"
    titles.sort(key=lambda c: c.BoundingRectangle.top)
    t = titles[title_index]
    name = (t.Name or "").strip()
    log(f"目标标题: {name[:40]!r}")
    invoke(t)
    time.sleep(6)
    w, h = find_article_host()
    if h is not None:
        return True, f"已打开文章 doc={doc_name(h)!r}"
    # 退化:向上找带 InvokePattern 的卡片
    p = t
    for _ in range(6):
        try:
            p = p.GetParentControl()
        except Exception:
            break
        if p is None:
            break
        try:
            pat = p.GetInvokePattern()
        except Exception:
            continue
        if pat is not None:
            log(f"退化: Invoke 父卡片 class={p.ClassName!r}")
            try:
                pat.Invoke()
            except Exception as e:
                log(f"  Invoke 失败 {e!r}")
                continue
            time.sleep(6)
            w, h = find_article_host()
            if h is not None:
                return True, f"已打开文章(卡片Invoke) doc={doc_name(h)!r}"
    return False, "Invoke 后未检测到文章页"


def cmd_state(args):
    window_summary("state")
    for w in all_appex_windows():
        tn = tab_names(w)
        log(f"win={w.Name!r} tabs: {[(c, n, (r.left, r.top)) for c, n, r in tn[:12]]}")


def cmd_m1(args):
    wins = all_appex_windows()
    log(f"打开前 AppEx 窗口数={len(wins)}")
    w, h, titles = find_profile_host()
    if h is None:
        log("未找到已打开的中金点睛主页 —— 请先运行 tools/spike_navigate.py 或 spike_article_url.py 导航")
        return 2
    log(f"主页窗口 name={w.Name!r} 标题数={len(titles)} doc={doc_name(h)!r}")
    before_wins = {x.Name for x in all_appex_windows()}
    before_hosts = {(x.Name, len(find_render_hosts(x))) for x in all_appex_windows()}

    ok, msg = open_first_article(h, args.index)
    log(f"打开文章: {msg}")
    if not ok:
        return 3

    log("打开后窗口对照:")
    for x in all_appex_windows():
        nm = x.Name
        mark = "  <== NEW" if nm not in before_wins else ""
        log(f"  win name={nm!r}{mark}")

    aw, ah = find_article_host()
    if ah is None:
        log("未找到文章宿主")
        return 4
    log(f"文章宿主窗口 name={aw.Name!r} doc={doc_name(ah)!r}")
    log(f"窗口标题(title) = {aw.Name!r}")
    log(f"tab 控件: {[(c, n, (r.left, r.top)) for c, n, r in tab_names(aw)[:12]]}")

    # 手段1 核心:grep 文章页树
    hits, n = grep_tree_urls(ah)
    log(f"文章树遍历 {n} 节点,URL/微信域命中 {len(hits)} 处")
    for u, header, ctx in hits[:25]:
        log(f"  HIT url={u!r}\n      node={header}\n      ctx={ctx!r}")
    # 窗口标题/Tab name 里找
    blob = f"{aw.Name}"
    for c, nm, r in tab_names(aw):
        blob += f" {nm}"
    log(f"窗口标题+tab 名 grep: {URL_RE.findall(blob) or '无'}")

    pats = pattern_probe(ah)
    log(f"模式统计(文章树): {pats}")
    return 0


def cmd_tree(args):
    from spike_uia import dump
    for w in all_appex_windows():
        if args.win_name and args.win_name not in (w.Name or ""):
            continue
        h = pick_active_host(w)
        if h is None:
            continue
        log(f"=== win={w.Name!r} doc={doc_name(h)!r}")
        dump(h, max_depth=args.depth, max_children=args.max_children)
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("state")
    p1 = sub.add_parser("m1")
    p1.add_argument("--index", type=int, default=0)
    pt = sub.add_parser("tree")
    pt.add_argument("--win-name", default=None)
    pt.add_argument("--depth", type=int, default=5)
    pt.add_argument("--max-children", type=int, default=40)
    args = ap.parse_args()
    if args.cmd == "state":
        sys.exit(cmd_state(args))
    if args.cmd == "m1":
        sys.exit(cmd_m1(args))
    if args.cmd == "tree":
        sys.exit(cmd_tree(args))


if __name__ == "__main__":
    main()
