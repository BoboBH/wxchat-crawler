"""尖峰B' 路线A:穷举公众号文章条目的 UIA 属性,探测文章 URL 是否经无障碍树暴露。

用法(仓库根目录,微信已登录;「中金点睛」主页若未打开会自动搜索导航):
    .venv/Scripts/python.exe tools/spike_url_extract.py [--account 中金点睛]
        [--rounds 3] [--all-nodes] [--max-nodes 2500]

判定(docs/spike-findings.md 尖峰B'):
  任一节点任一属性/模式取到形如 http(s)://...mp.weixin.qq.com/s?__biz=... 的 URL,
  且滚动加载后的新条目同样取到 → 路线A成立(完全不需要 mitmproxy)。

实现要点:
  * 复用 spike_uia/spike_navigate 的定位与导航函数(先 GetChildren 爬树激活);
  * 对每个候选节点用 IUIAutomationElement.GetSupportedProperties() 穷举全部受支持
    属性(GetCurrentPropertyValueEx(pid, True) 过滤默认值),再对 GetSupportedPatterns()
    的每个模式逐属性读取 —— 重点 ValuePattern.Value(30045)、
    LegacyIAccessiblePattern.Value/Description/Help/Name/DefaultAction、
    AriaProperties(30126)/FullDescription(30159)/ItemType(30021);
  * 每个节点先打印一行摘要,再缩进打印属性,便于 grep 与 diff。
"""
import argparse
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import uiautomation as uia
from spike_uia import find_browser_window, pick_active_host, restore_if_minimized
from spike_navigate import activate, page_state, search_flow, scroll_load, collect_titles

# ---- 属性/模式 ID → 名称映射(来自 uiautomation.PropertyId / PatternId) ----
PROP_NAME = {}
for _n in dir(uia.PropertyId):
    if not _n.startswith("_"):
        _v = getattr(uia.PropertyId, _n)
        if isinstance(_v, int):
            PROP_NAME[_v] = _n
PAT_NAME = {}
for _n in dir(uia.PatternId):
    if not _n.startswith("_"):
        _v = getattr(uia.PatternId, _n)
        if isinstance(_v, int):
            PAT_NAME[_v] = _n

# uiautomation 自带的 comtypes IUIAutomationElement 定义不含 GetSupportedProperties/
# GetSupportedPatterns(AttributeError),故暴力遍历全部已知属性/模式 ID:
#   属性  → elem.GetCurrentPropertyValueEx(pid, True)(不支持即报错,过滤默认值)
#   模式  → elem.GetCurrentPattern(pid)(不支持返回 None/报错)
ALL_PROP_IDS = sorted({getattr(uia.PropertyId, n) for n in dir(uia.PropertyId)
                       if not n.startswith("_") and isinstance(getattr(uia.PropertyId, n), int)})
ALL_PAT_IDS = sorted({getattr(uia.PatternId, n) for n in dir(uia.PatternId)
                      if not n.startswith("_") and isinstance(getattr(uia.PatternId, n), int)})
# 不打噪声:RuntimeId/ProcessId/NativeWindowHandle/PatternAvailable 布尔位单独处理
SKIP_PROPS = {30000, 30002, 30020}
NOISE_PAT_AVAIL = tuple(range(30027, 30063))  # *PatternAvailable 布尔位

URL_RE = re.compile(r"https?://[^\s\"'<>\\]+", re.I)

# 模式对象上值得一读的属性名(uiautomation 2.0 的封装属性,不带 Current 前缀)
PATTERN_ATTRS = (
    "Value", "Name", "Description", "Help", "HelpText", "DefaultAction",
    "Role", "State", "ChildId", "KeyboardShortcut", "Selection",
    "ItemStatus", "ItemType", "AriaProperties", "AriaRole",
    "FullDescription", "Url", "Uri", "Target",
)


def short(val, limit=240):
    s = repr(val)
    return s if len(s) <= limit else s[:limit] + "…"


def find_urls(val):
    """从任意属性值(字符串/元组/列表)里抽出 http(s) URL 列表。"""
    out = []
    if isinstance(val, (str, bytes)):
        out.extend(URL_RE.findall(val.decode("utf-8", "ignore") if isinstance(val, bytes) else val))
    elif isinstance(val, (tuple, list)):
        for v in val:
            out.extend(find_urls(v))
    return out


def node_header(ctrl):
    try:
        t = ctrl.ControlType
        r = ctrl.BoundingRectangle
        rect = f"({r.left},{r.top},{r.right},{r.bottom})"
    except Exception as exc:
        return f"<error {exc!r}>"
    return (f"type={t} class={ctrl.ClassName!r} aid={ctrl.AutomationId!r} "
            f"name={(ctrl.Name or '')[:60]!r} rect={rect}")


def dump_node(ctrl, idx, findings, miss):
    """穷举一个节点的全部受支持属性与模式属性。返回发现的 URL 列表。

    miss: {(ControlType字符串, id): 未命中次数} —— 同类型控件连续多次不支持的
    属性/模式直接跳过(Chromium 同 role 的支持面一致,省 COM 往返)。
    """
    print(f"\n=== NODE #{idx} {node_header(ctrl)}")
    urls = []
    elem = ctrl.Element
    try:
        ctype = str(ctrl.ControlType)
    except Exception:
        ctype = "?"

    # ---- 元素属性穷举 ----
    for pid in ALL_PROP_IDS:
        if pid in SKIP_PROPS or pid in NOISE_PAT_AVAIL:
            continue
        if miss.get((ctype, pid), 0) >= 3:
            continue
        try:
            val = elem.GetCurrentPropertyValueEx(pid, True)
        except Exception:
            miss[(ctype, pid)] = miss.get((ctype, pid), 0) + 1
            continue
        if val is None:
            miss[(ctype, pid)] = miss.get((ctype, pid), 0) + 1
            continue
        if pid == 30001:  # BoundingRectangle → tagRECT
            try:
                val = (int(val.left), int(val.top), int(val.right), int(val.bottom))
            except Exception:
                pass
        miss[(ctype, pid)] = 0
        us = find_urls(val)
        if us:
            urls.extend(us)
            print(f"  !! URL HIT  PROP {PROP_NAME.get(pid, pid)} = {short(val, 400)}")
            findings.append(("prop", PROP_NAME.get(pid, pid), us))
        else:
            print(f"  PROP {PROP_NAME.get(pid, pid)} = {short(val)}")

    # ---- 模式穷举 ----
    for pid in ALL_PAT_IDS:
        pname = PAT_NAME.get(pid, str(pid))
        if miss.get((ctype, ("pat", pid)), 0) >= 2:
            continue
        try:
            pat = ctrl.GetPattern(pid)
        except Exception:
            miss[(ctype, ("pat", pid))] = miss.get((ctype, ("pat", pid)), 0) + 1
            continue
        if pat is None:
            miss[(ctype, ("pat", pid))] = miss.get((ctype, ("pat", pid)), 0) + 1
            continue
        miss[(ctype, ("pat", pid))] = 0
        print(f"  PATTERN {pname}({pid})")
        for attr in PATTERN_ATTRS:
            try:
                if not hasattr(pat, attr):
                    continue
                val = getattr(pat, attr)
            except Exception as exc:
                print(f"    {attr} -> <error {str(exc)[:60]!r}>")
                continue
            if callable(val) or val in ("", None):
                continue
            us = find_urls(val)
            if us:
                urls.extend(us)
                print(f"    !! URL HIT  {attr} = {short(val, 500)}")
                findings.append((f"pattern:{pname}", attr, us))
            else:
                print(f"    {attr} = {short(val)}")
    return urls


def walk(ctrl, max_depth=30, max_nodes=4000):
    """深度遍历子树(兼做无障碍树激活),产出 (深度, 控件)。"""
    stack = [(ctrl, 0)]
    seen = 0
    while stack and seen < max_nodes:
        node, d = stack.pop()
        seen += 1
        yield d, node
        if d >= max_depth:
            continue
        try:
            stack.extend((kid, d + 1) for kid in node.GetChildren())
        except Exception:
            continue


def find_candidates(host, all_nodes=False, max_nodes=2500):
    """返回值得穷举的节点列表。

    all_nodes=False:文章条目相关节点 —— 标题控件、HyperlinkControl、
    class 含 article/link/url 的任意控件,以及它们的直接父链(链接很可能是整卡 <a>)。
    all_nodes=True:容器下全部节点(兜底,量大)。
    """
    titles = []
    by_y = {}
    extra = []
    n = 0
    for d, node in walk(host, max_nodes=max_nodes):
        n += 1
        try:
            cls = node.ClassName or ""
            ctype = node.ControlType
        except Exception:
            continue
        if cls == "article__item__title":
            titles.append(node)
        if cls == "publish_time":
            try:
                by_y[node.BoundingRectangle.top] = (node.Name or "").strip()
            except Exception:
                pass
        if (not all_nodes) and ("Hyperlink" in str(ctype) or cls and
                                re.search(r"article|link|url|item__|jump", cls, re.I)):
            extra.append(node)
    # 标题的父链(最多 4 级)也是候选
    parents = []
    for t in titles[:60]:
        p = t
        for _ in range(4):
            try:
                p = p.GetParentControl()
            except Exception:
                break
            if p is None or p is host:
                break
            parents.append(p)
    uniq, seen_id = [], set()
    for node in (titles + extra + parents if not all_nodes else [t for _, t in walk(host, max_nodes=max_nodes)]):
        try:
            rid = tuple(node.GetRuntimeId())
        except Exception:
            continue
        if rid not in seen_id:
            seen_id.add(rid)
            uniq.append(node)
    return uniq, titles, by_y, n


def nearest_date(y, dates):
    """返回 y 上方最近(含相等,容差 40px)的日期分组标签文本。"""
    best, best_dy = "", 10 ** 9
    for dy_off, label in dates.items():
        dy = y - dy_off
        if -40 <= dy < best_dy:
            best, best_dy = label, dy
    return best


def probe(host, all_nodes=False, max_nodes=2500):
    findings, results, miss = [], [], {}
    uniq, titles, dates, walked = find_candidates(host, all_nodes, max_nodes)
    print(f"\n##### 本次遍历 {walked} 节点,候选 {len(uniq)} 个"
          f"(标题 {len(titles)},日期标签 {len(dates)})#####")
    for i, node in enumerate(uniq):
        try:
            urls = dump_node(node, i, findings, miss)
        except Exception as exc:
            print(f"=== NODE #{i} <dump error {exc!r}>")
            continue
        try:
            r = node.BoundingRectangle
            y = r.top
        except Exception:
            y = -1
        title = ""
        cls = ""
        try:
            cls = node.ClassName or ""
            if cls == "article__item__title":
                title = (node.Name or "").strip()
        except Exception:
            pass
        results.append({"idx": i, "class": cls, "name": (node.Name or "")[:80],
                        "y": y, "date": nearest_date(y, dates),
                        "urls": sorted(set(urls)), "title": title})
    return results, findings, titles, dates


def summarize(results):
    hits = [r for r in results if r["urls"]]
    print(f"\n##### SUMMARY: {len(hits)}/{len(results)} 个候选节点携带 URL #####")
    for r in hits[:20]:
        print(f"  #{r['idx']} class={r['class']!r} name={r['name']!r} urls={r['urls']}")
    wechat = [u for r in hits for u in r["urls"] if "mp.weixin.qq.com/s" in u or "mp.weixin.qq.com" in u]
    print(f"##### 含 mp.weixin.qq.com 的 URL 共 {len(wechat)} 个")
    for u in sorted(set(wechat))[:10]:
        print(f"    {u}")
    return hits, sorted(set(wechat))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", default="中金点睛")
    ap.add_argument("--rounds", type=int, default=3, help="批次1之后滚动加载轮数")
    ap.add_argument("--all-nodes", action="store_true", help="穷举容器下全部节点(默认只穷举条目相关)")
    ap.add_argument("--max-nodes", type=int, default=2500)
    ap.add_argument("--skip-scroll", action="store_true")
    args = ap.parse_args()

    win = find_browser_window()
    if win is None:
        sys.exit("未找到 WeChatAppEx 浏览器窗口")
    print(f"[url] 浏览器窗口 pid={win.ProcessId} name={win.Name!r}")
    host = activate(win)
    state, why = page_state(host)
    print(f"[url] 页面状态: {state} ({why})")
    if state == "search":
        ok, msg = search_flow(win, args.account)
        print(f"[url] 搜索流程: {msg}")
        if not ok:
            sys.exit(2)
        win = find_browser_window() or win
        host = activate(win)
    elif state == "other":
        sys.exit("当前页面既不是搜索页也不是公众号主页,请手动打开后重试")

    # ---- 批次1:首屏 ----
    results, findings, titles, dates = probe(host, args.all_nodes, args.max_nodes)
    hits, urls = summarize(results)
    before_titles = {(t.Name or "").strip() for t in titles}

    # ---- 滚动加载后批次2(验证新条目同样携带 URL) ----
    if hits and not args.skip_scroll:
        print(f"\n##### 滚动加载 {args.rounds} 轮,验证新条目 #####")
        scroll_load(win, args.rounds, "批次2前滚动")
        win2 = find_browser_window() or win
        host2 = pick_active_host(win2) or host
        results2, findings2, titles2, dates2 = probe(host2, args.all_nodes, args.max_nodes)
        new_titles = {(t.Name or "").strip() for t in titles2} - before_titles
        print(f"[url] 新增标题 {len(new_titles)} 个: {[t[:20] for t in sorted(new_titles)[:5]]}")
        hits2, urls2 = summarize(results2)
        new_hits = [r for r in hits2 if r["name"] in new_titles]
        print(f"[url] 新增标题中携带 URL 的节点数: {len(new_hits)}")
        for r in new_hits[:5]:
            print(f"    NEW class={r['class']!r} name={r['name']!r} date={r['date']!r} urls={r['urls']}")

    # ---- 判定 ----
    ok = bool([u for u in urls if "mp.weixin.qq.com/s" in u])
    print(f"\n##### VERDICT: {'ROUTE_A_HIT' if ok else 'ROUTE_A_MISS'} #####")


if __name__ == "__main__":
    main()
