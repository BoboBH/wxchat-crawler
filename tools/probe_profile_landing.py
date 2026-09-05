"""诊断探针:复现「搜索→打开公众号主页→等列表就绪」,停在失败点抓现场。

用法: .venv/Scripts/python.exe tools/probe_profile_landing.py [账号名]
(默认 中国银河宏观)只读观察,不关任何页签。重点回答:
  1) 搜索结果卡片都有哪些(公众号卡片排第几);
  2) Invoke 卡片后,各 AppEx 渲染宿主的 doc 名是什么;
  3) article__item__title 节点到底出不出现(双门判定卡在哪一道)。
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import uiautomation as uia  # noqa: E402

from src import wechat_bot as bot  # noqa: E402

ACCOUNT = sys.argv[1] if len(sys.argv) > 1 else "中国银河宏观"
CLASS_TITLE = bot.CLASS_TITLE
CLASS_CARD = bot.CLASS_RESULT_CARD


def host_inventory(host, max_nodes=2500):
    """宿主内 ClassName 计数 + 标题样例,压缩成可读清单。"""
    counts: dict[str, int] = {}
    title_samples = []
    for c in bot.walk_ctrls(host, max_nodes=max_nodes):
        try:
            cls = c.ClassName or ""
        except Exception:
            continue
        counts[cls] = counts.get(cls, 0) + 1
        if cls == CLASS_TITLE and len(title_samples) < 5:
            try:
                title_samples.append((c.Name or "")[:30])
            except Exception:
                pass
    return counts, title_samples


def dump_hosts(tag):
    print(f"---- [{tag}] 各渲染宿主现状 ----")
    wins = bot.appex_windows(retries=2)
    if not wins:
        print("  (无 AppEx 窗口)")
        return
    for w in wins:
        hosts = bot.find_render_hosts(w)
        print(f"  窗口 {w.ClassName} 渲染宿主数={len(hosts)}")
        for h in hosts:
            try:
                r = h.BoundingRectangle
                wide = (r.right - r.left) > 100
            except Exception:
                wide = "?"
            doc = bot.host_doc_name(h)
            counts, samples = host_inventory(h)
            n_title = counts.get(CLASS_TITLE, 0)
            print(f"    宿主 wide={wide} doc={doc!r} 标题节点={n_title}")
            if n_title == 0:
                top = sorted(counts.items(), key=lambda kv: -kv[1])[:12]
                print(f"      高频类: {top}")


def main():
    print(f"== 探针目标: {ACCOUNT} ==")
    print("desktop_state =", bot.desktop_state())
    win, host, edit = bot.find_search_entry()
    if edit is None:
        print("!! 未找到搜索页输入框 —— 先在微信里开一次搜一搜再跑")
        return 2
    print("搜索页 OK,开始粘贴…")
    uia.SetClipboardText(ACCOUNT)
    edit.Click(simulateMove=False)
    time.sleep(0.6)
    uia.SendKeys("{Ctrl}a")
    time.sleep(0.2)
    uia.SendKeys("{Ctrl}v")
    time.sleep(1.0)
    print("输入框值 =", bot.control_value(edit).__repr__())

    btn = None
    for c in bot.walk_ctrls(host, max_nodes=3000):
        try:
            if (c.Name or "") == bot.SEARCH_BUTTON_NAME and \
                    c.ControlType == uia.ControlType.ButtonControl:
                btn = c
                break
        except Exception:
            continue
    if btn is not None:
        bot.invoke_control(btn)
        print("已点「搜索」按钮")
    else:
        uia.SendKeys("{Enter}")
        print("未见搜索按钮,已发 Enter")

    # 等结果卡片,把所有卡片名字都打出来
    t0 = time.time()
    cards_seen = []
    while time.time() - t0 < 30:
        cards_seen = []
        _w2, h2 = bot.find_host(
            lambda c: (c.ClassName or "") == CLASS_CARD, max_nodes=3000)
        if h2 is not None:
            for c in bot.walk_ctrls(h2, max_nodes=3000):
                try:
                    if (c.ClassName or "") == CLASS_CARD:
                        mark = bot.RESULT_ACCOUNT_MARK in (c.Name or "")
                        cards_seen.append((c.Name or "")[:40] +
                                          ("  <含公众号标记>" if mark else ""))
                except Exception:
                    continue
            if cards_seen:
                break
        time.sleep(1.0)
    print(f"---- 搜索结果卡片({len(cards_seen)}) ----")
    for i, nm in enumerate(cards_seen):
        print(f"  [{i}] {nm}")

    # Invoke 生产同款判定:前缀同名 + 含「公众号」
    _w3, h3 = bot.find_host(
        lambda c: (c.ClassName or "") == CLASS_CARD
        and (c.Name or "").startswith(ACCOUNT)
        and bot.RESULT_ACCOUNT_MARK in (c.Name or ""), max_nodes=3000)
    if h3 is None:
        print("!! 无匹配的公众号卡片,探针到此为止(页签保持原样)")
        return 3
    card = None
    for c in bot.walk_ctrls(h3, max_nodes=3000):
        try:
            if (c.ClassName or "") == CLASS_CARD \
                    and (c.Name or "").startswith(ACCOUNT) \
                    and bot.RESULT_ACCOUNT_MARK in (c.Name or ""):
                card = c
                break
        except Exception:
            continue
    print("Invoke 公众号卡片:", (card.Name or "")[:50])
    bot.invoke_control(card)

    # 生产同款 45s 等待,但每 5s 打一次宿主现场
    t0 = time.time()
    ok = False
    while time.time() - t0 < 45:
        _w4, h4 = bot.find_profile_host(account=ACCOUNT, kicks=0)
        if h4 is not None:
            print(f"== 主页就绪! doc={bot.host_doc_name(h4)!r} 用时{time.time()-t0:.1f}s ==")
            ok = True
            break
        if int(time.time() - t0) % 5 == 0:
            dump_hosts(f"等就绪 {time.time()-t0:.0f}s")
        time.sleep(1.0)
    if not ok:
        print("== 45s 未就绪 —— 与生产失败一致;页签保持打开供人工查看 ==")
        dump_hosts("最终现场")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
