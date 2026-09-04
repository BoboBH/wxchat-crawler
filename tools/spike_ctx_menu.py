"""尖峰E:文章页**右键**菜单「复制链接」剪贴板取 URL 可行性验证。

背景:现有兜底是「··· → 复制链接」(copy_link_via_menu,尖峰D,2/2 成功)。
用户提出(2026-09-04 截图):文章页**右键**菜单也有「复制链接」项,问能否用
它拿文章短链 —— 本探针找出该菜单的触发区域与控件事实。

已实测事实(2026-09-04 多轮探针,复现排查必读):
  * 右键是合成鼠标动作,必须先置 AppEx 前台并校验(ALT 技巧+重试),
    抢不到前台绝不点击;
  * 点在**标题/正文文本/段间空隙**上弹 Chromium 级简易菜单
    (MenuItemView:取消翻译/全文翻译/打印/重新加载/前进/返回/问小微),
    **没有**复制链接;
  * 点在**文章配图**上弹页内 HTML 菜单(class=context-menu__item
    context-menu__item-unified:保存图片/转发/复制),也没有复制链接;
  * a11y 树跨右键保留已实现节点 —— 判断「哪个菜单是新弹的」必须做基线 diff。

用法(仓库根目录,微信已登录,桌面已解锁):
    .venv/Scripts/python.exe tools/spike_ctx_menu.py [--sweep] [--spot X] [--account X]

结论(2026-09-04 实测填写):
  - 用户截图里的菜单(刷新/复制链接/调整文字大小/全文翻译/听全文/查找/打印/
    投诉/转发/分享到朋友圈/收藏)= **「···」(更多)按钮的左键菜单**,不是右键
    菜单 —— 截图只是把顶部几项裁掉了(完整菜单:关闭全部标签页 Ctrl+Shift+W /
    下载内容 / 浏览记录 / 用默认浏览器打开 / 星标 / 收藏 Ctrl+D / 分享到朋友圈 /
    转发… / 投诉 / 打印 / 查找 / 听全文 / 全文翻译 / 调整文字大小 / 复制链接 /
    刷新,全部 class=FlueMenuItemView)。
  - **右键菜单没有「复制链接」**:全区域扫描(标题/作者行/正文段/段间空隙/
    配图/···按钮/页签)证实 —— 文本与空白区弹 Chromium 级简易菜单
    (MenuItemView:取消翻译/全文翻译/打印/重新加载/前进/返回/问小微),
    配图上弹页内图片菜单(context-menu__item:保存图片/转发/复制)。
  - 「···→复制链接」链路当轮复验成功:copy_link_via_menu 返回短链
    https://mp.weixin.qq.com/s/NJXb0U5fQ2k20e-jZiRSJQ,canonical 直接收下
    —— 需求「用复制链接拿文章短链」**本就已实现**(open_article_and_get_url
    的兜底路径),DB 120 篇 0 pending 即其工作证据。
  - 决定:不新增右键路线(本 build 右键菜单根本没有复制链接);需求2由既有
    copy_link_via_menu 满足,实机复验留档。
  - 附带事实:① a11y 树跨右键保留已实现的菜单节点 —— 判断「新弹的菜单」
    必须先取基线再做 diff;② 页面是否被「全文翻译」过不影响右键行为
    (取消翻译/全文翻译两项常驻简易菜单);③ 右键是合成鼠标动作,必须
    ALT 技巧+重试抢前台,抢不到绝不点击。
"""
from __future__ import annotations

import argparse
import ctypes
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import uiautomation as uia  # noqa: E402

from src import canonical  # noqa: E402
from src import wechat_bot as bot  # noqa: E402

WALK_NODES = 8000
U32 = ctypes.windll.user32
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010


def right_click(x: int, y: int) -> None:
    uia.SetCursorPos(x, y)
    time.sleep(0.25)
    U32.mouse_event(MOUSEEVENTF_RIGHTDOWN, 0, 0, 0, 0)
    time.sleep(0.06)
    U32.mouse_event(MOUSEEVENTF_RIGHTUP, 0, 0, 0, 0)
    time.sleep(1.3)


def force_foreground(hwnd, tries: int = 6, gap: float = 1.5) -> bool:
    """置前台并校验:ALT 技巧解锁 SetForegroundWindow 限制,重试 tries 次。"""
    for i in range(tries):
        U32.keybd_event(0x12, 0, 0, 0)   # ALT down(解除前台锁)
        U32.keybd_event(0x12, 0, 2, 0)   # ALT up
        try:
            bot.USER32.SetForegroundWindow(hwnd)
        except Exception:
            pass
        time.sleep(gap)
        if bot.USER32.GetForegroundWindow() == hwnd:
            return True
        print(f"  前台尝试{i + 1}/{tries}失败,重试 …")
    return False


def menu_snapshot(win) -> set[tuple[str, str]]:
    """树内菜单样控件快照(有 Name 的 + menu/context 类)。"""
    out = set()
    for c in bot.walk_ctrls(win, max_nodes=WALK_NODES):
        try:
            cls = c.ClassName or ""
            name = (c.Name or "")[:40]
        except Exception:
            continue
        if name or "menu" in cls.lower() or "context" in cls.lower():
            out.add((cls, name))
    return out


def find_title_ctrl(host):
    for c in bot.walk_ctrls(host, max_nodes=12000):
        try:
            if (c.AutomationId or "") == "activity-name":
                t = (c.Name or "").strip()
                if t:
                    return t, c
        except Exception:
            continue
    return None, None


def find_body_anchor(host, mode: str):
    """正文区右键锚点 → (说明, (x, y)|None)。"""
    body = None
    for c in bot.walk_ctrls(host, max_nodes=12000):
        try:
            if (c.AutomationId or "") == "js_content":
                body = c
                break
        except Exception:
            continue
    if body is None:
        return "未找到 js_content", None
    r = body.BoundingRectangle
    screen_h = U32.GetSystemMetrics(1)
    if mode == "image":
        for c in bot.walk_ctrls(body, max_nodes=8000):
            try:
                cls = c.ClassName or ""
            except Exception:
                continue
            if "img" in cls.lower() or "image" in cls.lower():
                try:
                    cr = c.BoundingRectangle
                except Exception:
                    continue
                if 0 <= cr.top and cr.bottom <= screen_h and \
                        cr.right - cr.left > 60:
                    cx, cy = (cr.left + cr.right) // 2, (cr.top + cr.bottom) // 2
                    return f"配图 class={cls[:30]!r} ({cx},{cy})", (cx, cy)
        return "正文里没找到可见配图", None
    cands = []
    for c in bot.walk_ctrls(body, max_nodes=6000):
        try:
            t = (c.Name or "").strip()
            if len(t) <= 10:
                continue
            cr = c.BoundingRectangle
        except Exception:
            continue
        cands.append((cr.top, t, cr))
    cands.sort(key=lambda x: x[0])
    visible = [(top, t, cr) for top, t, cr in cands
               if 0 <= cr.top and cr.bottom <= screen_h
               and cr.bottom - cr.top <= 300 and cr.right - cr.left >= 80]
    if not visible:
        return f"没有可见文本段(候选{len(cands)}个,屏高{screen_h})", None
    if mode == "para":  # 最顶上的可见正文段
        top, t, cr = visible[0]
        return f"首段 {t[:16]!r}", ((cr.left + cr.right) // 2,
                                    (cr.top + cr.bottom) // 2)
    # gap:中部可见段下沿的空隙
    mid = screen_h / 2
    top, t, cr = min(visible, key=lambda x: abs((x[2].top + x[2].bottom) / 2 - mid))
    return f"空隙({t[:16]!r} bottom={cr.bottom})", \
        ((cr.left + cr.right) // 2, cr.bottom + 8)


def find_meta_ctrl(host):
    """文章头部作者行(aid=js_name,公众号名)。"""
    for c in bot.walk_ctrls(host, max_nodes=12000):
        try:
            if (c.AutomationId or "") == "js_name":
                t = (c.Name or "").strip()
                if t:
                    return t, c
        except Exception:
            continue
    return None, None


def bootstrap_article(account: str) -> bool:
    """AppEx 全关时走爬虫同款路径:搜一搜引导 → 开主页 → 点开第一篇。"""
    ok, msg = bot.search_open_profile(account)
    if not ok and bot.SEARCH_PAGE_MISSING in (msg or ""):
        print(f"  搜索页缺失 → 自动引导: {account}")
        healed, hmsg = bot.ensure_search_page(account)
        if not healed:
            print(f"  引导失败: {hmsg}")
            return False
        ok, msg = bot.search_open_profile(account)
    if not ok:
        print(f"  打开主页失败: {msg}")
        return False
    print(f"  主页已打开: {msg}")
    # Invoke 首个标题卡片
    _w, h = bot.find_profile_host(account=account, kicks=1)
    if h is None:
        return False
    for c in bot.walk_ctrls(h, max_nodes=2500):
        try:
            if (c.ClassName or "") == bot.CLASS_TITLE:
                p = c
                for _ in range(5):
                    try:
                        pat = p.GetPattern(uia.PatternId.InvokePattern)
                    except Exception:
                        pat = None
                    if pat is not None:
                        pat.Invoke()
                        return True
                    p = p.GetParentControl()
                    if p is None:
                        break
                break
        except Exception:
            continue
    return False


def untranslate(win, h) -> bool:
    """页面若被「全文翻译」过(菜单含取消翻译),先还原再实验。"""
    find_title_ctrl(h)
    t, c = find_title_ctrl(h)
    if c is None:
        return False
    r = c.BoundingRectangle
    right_click((r.left + r.right) // 2, (r.top + r.bottom) // 2)
    target = None
    for c2 in bot.walk_ctrls(win, max_nodes=WALK_NODES):
        try:
            if "MenuItemView" in (c2.ClassName or "") and \
                    "取消翻译" in (c2.Name or ""):
                target = c2
                break
        except Exception:
            continue
    if target is None:
        print("  页面未处于翻译态(菜单无取消翻译)")
        bot._dismiss_menu()
        return False
    bot.invoke_control(target)
    time.sleep(3.0)
    print("  已取消翻译,页面还原中 …")
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bootstrap", action="store_true",
                    help="AppEx 全关时走完整引导(搜索页→主页→第一篇)")
    ap.add_argument("--account", default="留富兵法", help="账号名(引导用)")
    ap.add_argument("--spot", default=None,
                    choices=["title", "meta", "para", "gap", "image",
                             "more_btn", "tab"],
                    help="只试单个区域;不给则跑全区域扫描")
    ap.add_argument("--untranslate", action="store_true",
                    help="扫描前先还原「全文翻译」(翻译态可能改变右键行为)")
    args = ap.parse_args()

    print("== A. 页签盘点 ==")
    wins = bot.appex_windows()
    print(f"AppEx 顶层窗口: {len(wins)} 个")
    for i, w in enumerate(wins):
        try:
            bot.restore_if_minimized(w)
        except Exception:
            pass
        print(f"  窗口{i}: 激活doc={bot.active_doc(w)!r}")

    print("== B. 定位文章宿主 ==")
    win, h = bot.find_article_host(timeout=2.0)
    if h is None and args.bootstrap:
        print(f"  AppEx 无窗口 → 完整引导[{args.account}]")
        if not bootstrap_article(args.account):
            print("  结论: 引导失败,探针中止")
            return 1
        win, h = bot.find_article_host(timeout=20.0)
    if h is None:
        print("  结论: 没有打开的文章页(可加 --bootstrap)")
        return 1
    title, _ = find_title_ctrl(h)
    print(f"  文章: {title!r}")

    print("== C. 置前台 ==")
    hwnd = win.NativeWindowHandle
    if not force_foreground(hwnd):
        print("  结论: AppEx 多次抢不到前台(用户正在用电脑)→ 不点击,探针中止")
        return 2

    # 收集实验区域
    regions: list[tuple[str, tuple[int, int]]] = []
    if args.spot:
        if args.spot == "more_btn":
            pt = None
            for c in bot.walk_ctrls(win, max_nodes=WALK_NODES):
                try:
                    if (c.ClassName or "") == bot.MORE_BUTTON_CLASS and \
                            (c.Name or "") == bot.MORE_BUTTON_NAME:
                        r = c.BoundingRectangle
                        pt = ((r.left + r.right) // 2, (r.top + r.bottom) // 2)
                        break
                except Exception:
                    continue
            if pt is None:
                print("  结论: 没找到「···」按钮")
                return 1
            regions.append(("more_btn", pt))
        elif args.spot == "tab":
            pt = None
            for w in bot.appex_windows():
                stack = [(w, 0)]
                while stack and pt is None:
                    c, d = stack.pop()
                    try:
                        if (c.ClassName or "") == bot.CLASS_TAB and \
                                (c.Name or ""):
                            r = c.BoundingRectangle
                            pt = ((r.left + r.right) // 2,
                                  (r.top + r.bottom) // 2)
                            break
                        if d < 12:
                            stack.extend((k, d + 1) for k in c.GetChildren())
                    except Exception:
                        continue
            if pt is None:
                print("  结论: 没找到页签控件")
                return 1
            regions.append(("tab", pt))
        elif args.spot == "title":
            t, c = find_title_ctrl(h)
            r = c.BoundingRectangle
            regions.append(("title", ((r.left + r.right) // 2,
                                      (r.top + r.bottom) // 2)))
        elif args.spot == "meta":
            t, c = find_meta_ctrl(h)
            if c is None:
                print("  结论: 没找到 js_name 作者行")
                return 1
            r = c.BoundingRectangle
            regions.append(("meta", ((r.left + r.right) // 2,
                                     (r.top + r.bottom) // 2)))
        else:
            desc, pt = find_body_anchor(h, args.spot)
            if pt is None:
                print(f"  结论: {desc}")
                return 1
            regions.append((args.spot, pt))
    else:
        t, c = find_title_ctrl(h)
        if c is not None:
            r = c.BoundingRectangle
            regions.append(("title", ((r.left + r.right) // 2,
                                      (r.top + r.bottom) // 2)))
        t, c = find_meta_ctrl(h)
        if c is not None:
            r = c.BoundingRectangle
            regions.append(("meta", ((r.left + r.right) // 2,
                                     (r.top + r.bottom) // 2)))
        for mode in ("para", "gap", "image"):
            desc, pt = find_body_anchor(h, mode)
            if pt is not None:
                regions.append((mode, pt))
        # 追加:「···」按钮与页签本身(用户截图富菜单可能来自工具条区)
        for c in bot.walk_ctrls(win, max_nodes=WALK_NODES):
            try:
                if (c.ClassName or "") == bot.MORE_BUTTON_CLASS and \
                        (c.Name or "") == bot.MORE_BUTTON_NAME:
                    r = c.BoundingRectangle
                    regions.append(("more_btn", ((r.left + r.right) // 2,
                                                 (r.top + r.bottom) // 2)))
                    break
            except Exception:
                continue
        for w in bot.appex_windows():
            stack = [(w, 0)]
            while stack:
                c, d = stack.pop()
                try:
                    if (c.ClassName or "") == bot.CLASS_TAB and (c.Name or ""):
                        r = c.BoundingRectangle
                        regions.append(("tab", ((r.left + r.right) // 2,
                                                (r.top + r.bottom) // 2)))
                        stack = []
                        break
                    if d < 12:
                        stack.extend((k, d + 1) for k in c.GetChildren())
                except Exception:
                    continue
            if any(lb == "tab" for lb, _ in regions):
                break

    if args.untranslate:
        print("== C2. 还原翻译态 ==")
        untranslate(win, h)

    baseline = menu_snapshot(win)
    print(f"== D. 区域扫描(基线菜单项 {len(baseline)} 个) ==")
    clip = ""
    try:
        clip = uia.GetClipboardText() or ""
    except Exception:
        pass
    hit_region = None
    hit_item = None
    for label, (cx, cy) in regions:
        print(f"-- 区域[{label}] 点击=({cx},{cy})")
        right_click(cx, cy)
        snap = menu_snapshot(win)
        new = sorted(snap - baseline)
        hit = None
        for cls, name in snap:
            if bot.COPY_LINK_NAME in name:
                hit = (cls, name)
                break
        for cls, name in new[:14]:
            print(f"   + [{cls}] {name!r}")
        if len(new) > 14:
            print(f"   … 共 {len(new)} 个新项")
        if hit is not None:
            print(f"   ★ 复制链接出现: [{hit[0]}] {hit[1]!r}")
            hit_region, hit_item = label, hit
            break
        bot._dismiss_menu()
        time.sleep(0.9)
    if hit_region is None:
        print("== 结论: 所有区域右键都没有「复制链接」 ==")
        try:
            uia.SetClipboardText(clip)
        except Exception:
            pass
        return 1

    print(f"== E. 区域[{hit_region}] Invoke「复制链接」 → 读剪贴板 ==")
    ok = False
    url = None
    for c in bot.walk_ctrls(win, max_nodes=WALK_NODES):
        try:
            if (c.ClassName or "") == hit_item[0] and \
                    bot.COPY_LINK_NAME in (c.Name or ""):
                if bot.invoke_control(c):
                    time.sleep(2.0)
                    raw = bot._read_clipboard_text().strip()
                    print(f"  剪贴板: {raw[:120]!r}")
                    if raw.startswith("http") and "mp.weixin.qq.com/s" in raw:
                        url = raw
                        ok = True
                break
        except Exception:
            continue
    try:
        uia.SetClipboardText(clip)
    except Exception:
        pass
    bot._dismiss_menu()
    if url:
        print(f"  canonical: {canonical.canonicalize_url(url)}")
        print(f"  结论: 右键「复制链接」路线成立(区域={hit_region})")
        return 0
    print(f"  结论: 菜单项点了但剪贴板没有合法 mp 链接(区域={hit_region})")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
