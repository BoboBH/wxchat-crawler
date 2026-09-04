"""「余下N篇」折叠探针:中金点睛主页找折叠条目,展开并解剖子页结构。

用法(仓库根目录,微信已登录;勿与计划任务 08:05/19:05 并发):
    .venv/Scripts/python.exe tools/probe_fold.py [--account 中金点睛]

输出四段:
  A. 主页扫描:CLASS_TITLE/CLASS_TIME 全量 + 含「余下」控件的解剖
     (类名/ControlType/rect/InvokePattern/父链)
  B. Invoke 第一个「余下」控件后:doc 名变化 + 新页标题/日期全量
  C. 子页返回手段:含「返回」控件的解剖 + 尝试返回
  D. 清理 tab
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import uiautomation as uia  # noqa: E402

from src import wechat_bot as bot  # noqa: E402

P = lambda msg: print(msg, flush=True)


def rect_str(c):
    try:
        r = c.BoundingRectangle
        return f"({r.left},{r.top},{r.right},{r.bottom})"
    except Exception:
        return "?"


def parent_chain(c, depth=6):
    out = []
    cur = c
    for _ in range(depth):
        try:
            cur = cur.GetParentControl()
        except Exception:
            break
        if cur is None:
            break
        try:
            out.append(f"{cur.ClassName}|{cur.ControlType}|{(cur.Name or '')[:24]!r}")
        except Exception:
            out.append("<err>")
    return " <- ".join(out)


def has_invoke(c):
    try:
        return c.GetPattern(uia.PatternId.InvokePattern) is not None
    except Exception:
        return False


def dump_fold_hits(host, tag):
    """全树解剖含「余下」的控件 + 卡片名样本。"""
    hits, cards = [], []
    for c in bot.walk_ctrls(host, max_nodes=6000):
        try:
            name = (c.Name or "").strip()
            cls = c.ClassName or ""
        except Exception:
            continue
        if "余下" in name:
            hits.append((c, name, cls))
        if cls == bot.CLASS_CARD and len(cards) < 40:
            cards.append(name[:28])
    P(f"[{tag}] 含「余下」控件数={len(hits)}")
    for i, (c, name, cls) in enumerate(hits[:8]):
        try:
            ct = str(c.ControlType).split(".")[-1] if c.ControlType else "?"
        except Exception:
            ct = "?"
        P(f"  #{i} name={name!r} class={cls!r} type={ct} "
          f"rect={rect_str(c)} invoke={has_invoke(c)}")
        P(f"      父链: {parent_chain(c)}")
    P(f"[{tag}] js_article_card 卡片 {len(cards)} 个, 名样本: {cards[:12]}")
    return hits


def dump_titles(host, tag, limit=30):
    titles, times = bot.scan_list(host, max_nodes=8000)
    P(f"[{tag}] scan_list: 标题{len(titles)} 日期{len(times)}")
    tmap = {}
    for c in titles:
        try:
            tmap[c.BoundingRectangle.top] = (c.Name or "").strip()
        except Exception:
            pass
    P(f"[{tag}] 标题(前{limit}): {[t[:20] for t in list(tmap.values())[:limit]]}")
    P(f"[{tag}] 日期标签: {[(d, int(t)) for d, t in times[:limit]]}")
    # 标题类名之外,凡 Name 非空且像标题的文本控件也抽样(防折叠标题换了类名)
    return titles, times


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", default="中金点睛")
    args = ap.parse_args()
    acc = args.account

    ok, msg = bot.search_open_profile(acc)
    if not ok and bot.SEARCH_PAGE_MISSING in (msg or ""):
        P(f"[开主页] 搜索页丢失,自动引导: …")
        healed, heal_msg = bot.ensure_search_page(acc)
        P(f"[开主页] 引导 ok={healed} {heal_msg}")
        if healed:
            ok, msg = bot.search_open_profile(acc)
    P(f"[开主页] ok={ok} {msg}")
    if not ok:
        sys.exit(1)
    win, host = bot.find_profile_host(acc)
    if host is None:
        P("[开主页] 找不到主页宿主")
        sys.exit(1)
    P(f"[开主页] doc={bot.host_doc_name(host)!r}")

    bot.scroll_to_top(host, wait=2.0)
    time.sleep(1.0)
    host2 = bot.find_profile_host(acc)[1] or host
    dump_titles(host2, "A主页")
    hits = dump_fold_hits(host2, "A主页")

    if not hits:
        P("该主页无「余下」折叠,结束")
        bot.close_profile_tab(acc)
        return

    # ---- B. 展开第一个
    target = hits[0][0]
    P(f"[B展开] Invoke: {hits[0][1]!r} @ {rect_str(target)}")
    ok = bot.invoke_control(target)
    P(f"[B展开] invoke_control={ok}")
    time.sleep(4.0)

    win2, host3 = bot.find_profile_host(acc)
    host3 = host3 or host2
    P(f"[B展开后] doc={bot.host_doc_name(host3) if host3 else '?'!r} "
      f"active_doc={bot.active_doc(win2) if win2 else '?'!r}")
    dump_titles(host3, "B子页")
    dump_fold_hits(host3, "B子页")

    # ---- C. 返回手段:找含「返回」的控件
    backs = []
    for c in bot.walk_ctrls(host3, max_nodes=6000):
        try:
            name = (c.Name or "").strip()
        except Exception:
            continue
        if name and ("返回" in name or name.lower() == "back"):
            backs.append(c)
    P(f"[C返回] 含「返回」控件 {len(backs)} 个")
    for i, c in enumerate(backs[:6]):
        try:
            ct = str(c.ControlType).split(".")[-1] if c.ControlType else "?"
        except Exception:
            ct = "?"
        P(f"  #{i} name={c.Name!r} class={c.ClassName!r} type={ct} "
          f"rect={rect_str(c)} invoke={has_invoke(c)}")

    # ---- D. 清理:先关文章类 tab,再关主页 tab
    bot.close_article_tabs(max_close=3, wait=1.5)
    bot.close_profile_tab(acc, wait=1.5)
    P("[D清理] 完成")


if __name__ == "__main__":
    main()
