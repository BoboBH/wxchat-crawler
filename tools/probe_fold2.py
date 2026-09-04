"""「余下N篇」探针 v2:祖先 InvokePattern / 真点击 两路展开 + 持久性验证。

用法(仓库根目录,微信已登录;勿与计划任务 08:05/19:05 并发):
    .venv/Scripts/python.exe tools/probe_fold2.py [--account 中金点睛]

步骤:
  1. 开主页回顶首扫(标题数/折叠数)
  2. 对首个折叠条:逐级向上找 InvokePattern → 有则 Invoke(后台安全通道)
  3. 未生效 → 取视口内首个折叠条 uia.Click 真点击(合成鼠标)
  4. 每次尝试后重扫:标题数/日期/折叠条数/doc 名
  5. 持久性:下滚 2 屏再回顶重扫,看展开是否保留
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


def snapshot(host, tag):
    """回顶扫一遍:标题数/名样本/日期/折叠条(带视口内标记)。"""
    titles, times = bot.scan_list(host, max_nodes=8000)
    win_r = host.BoundingRectangle
    folds = []
    for c in bot.walk_ctrls(host, max_nodes=8000):
        try:
            name = (c.Name or "").strip()
        except Exception:
            continue
        if "余下" in name and "篇" in name:
            r = c.BoundingRectangle
            folds.append((name, r.top, r.left,
                          win_r.top <= r.top <= win_r.bottom))
    P(f"[{tag}] doc={bot.host_doc_name(host)!r} 标题{len(titles)} "
      f"日期{len(times)} 折叠{len(folds)}")
    P(f"[{tag}] 标题: {[ (c.Name or '')[:16] for c in titles ]}")
    P(f"[{tag}] 日期: {[(d, int(t)) for d, t in times]}")
    P(f"[{tag}] 折叠(name,top,left,视口内): "
      f"{[(n, int(t), int(l), vis) for n, t, l, vis in folds]}")
    return titles, folds, win_r


def fold_target(host):
    """首个折叠条 + 其祖先链(带各层 InvokePattern 有无)。"""
    for c in bot.walk_ctrls(host, max_nodes=8000):
        try:
            name = (c.Name or "").strip()
        except Exception:
            continue
        if "余下" in name and "篇" in name:
            chain, cur = [], c
            for _ in range(8):
                try:
                    cur = cur.GetParentControl()
                except Exception:
                    break
                if cur is None:
                    break
                has = False
                try:
                    has = cur.GetPattern(uia.PatternId.InvokePattern) is not None
                except Exception:
                    pass
                chain.append((cur, (cur.ClassName or "")[:40], has))
            return c, chain
    return None, []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", default="中金点睛")
    args = ap.parse_args()
    acc = args.account

    ok, msg = bot.search_open_profile(acc)
    if not ok and bot.SEARCH_PAGE_MISSING in (msg or ""):
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

    bot.scroll_to_top(host, wait=2.0)
    time.sleep(1.0)
    host = bot.find_profile_host(acc)[1] or host

    titles, folds, win_r = snapshot(host, "1首扫")

    # ---- 2. 祖先 InvokePattern 通道
    bar, chain = fold_target(host)
    if bar is None:
        P("无折叠条,结束")
        bot.close_profile_tab(acc)
        return
    P("[2祖先] 折叠条祖先链 InvokePattern:")
    for anc, cls, has in chain:
        P(f"    class={cls!r} invoke={has}")
    invokable = next((a for a, _c, has in chain if has), None)
    grew = False
    if invokable is not None:
        P("[2祖先] Invoke 祖先卡片 …")
        try:
            invokable.GetPattern(uia.PatternId.InvokePattern).Invoke()
        except Exception as e:
            P(f"[2祖先] Invoke 异常: {e}")
        time.sleep(3.0)
        t2, f2, _ = snapshot(host, "2Invoke后")
        grew = len(t2) > len(titles) or len(f2) < len(folds)
        P(f"[2祖先] 生效={grew}")
    else:
        P("[2祖先] 祖先均无 InvokePattern")

    # ---- 3. 真点击视口内首个折叠条
    if not grew:
        vis = [f for f in folds if f[3]]
        if not vis:
            P("[3点击] 无视口内折叠条,先下滚半屏再试")
            bot.scroll_once(host, wheels=5)
            time.sleep(1.5)
            titles, folds, win_r = snapshot(host, "3滚后")
            vis = [f for f in folds if f[3]]
        if vis:
            name, top, left, _ = vis[0]
            x, y = int(left + 37), int(top + 11)  # 条中心(宽74高23)
            P(f"[3点击] Click({x},{y}) on {name!r}")
            try:
                uia.SetCursorPos(x, y)
                time.sleep(0.3)
                uia.Click(x, y)
            except Exception as e:
                P(f"[3点击] 点击异常: {e}")
            time.sleep(3.0)
            t3, f3, _ = snapshot(host, "3点击后")
            grew = len(t3) > len(titles) or len(f3) < len(folds)
            P(f"[3点击] 生效={grew}")
        else:
            P("[3点击] 仍无视口内折叠条")

    # ---- 4. 持久性:滚走再滚回
    if grew:
        bot.scroll_once(host, wheels=10)
        time.sleep(2.0)
        bot.scroll_to_top(host, wait=2.0)
        time.sleep(1.0)
        host = bot.find_profile_host(acc)[1] or host
        snapshot(host, "4回顶(持久性)")

    # ---- 5. 清理
    bot.close_article_tabs(max_close=2, wait=1.5)
    bot.close_profile_tab(acc, wait=1.5)
    P("[5清理] 完成")


if __name__ == "__main__":
    main()
