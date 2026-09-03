"""尖峰D:文章页「··· → 复制链接」菜单剪贴板取 URL 可行性验证。

背景:文章页 UIA 树可能内嵌其他 mp 文章 URL(推荐阅读/原文链接/上一篇),
「树内最短者即主文」的启发式在验收中把上一篇的 URL 记到了本篇头上。
「···」菜单的「复制链接」按定义复制当前页自身 URL,可作兜底。

判定规则:完整链路(找到 ··· 按钮 → Invoke → 弹层含「复制链接」→ Invoke →
剪贴板读到 URL → canonicalize 通过)连续 2 次成功 → 在 wechat_bot 实现
copy_link_via_menu 兜底;菜单不可达或 <2/2 → 不实现,本文件留结论。

用法(仓库根目录,微信已登录,桌面已解锁):
    .venv/Scripts/python.exe tools/spike_copy_link.py [--account 郭磊宏观茶座]
        [--index 1] [--times 2] [--dump]

结论(2026-09-03 实测填写):
  - 尝试1/尝试2:连续 2 次全链路成功(找到 AppMenuButton「更多」→ Click 弹层 →
    FlueMenuItemView「复制链接」→ Invoke → 剪贴板读到 URL → canonicalize 通过)。
  - 入口细节:AppMenuButton(Name=更多,浏览器工具条右上)**没有** InvokePattern,
    ExpandCollapse / LegacyIAccessible.DoDefaultAction 都不弹层,只有合成鼠标
    Click 能打开;菜单不是独立顶层窗口(root 窗口快照无新增),菜单项就落在
    同一 AppEx 窗口树里(class=FlueMenuItemView:收藏 Ctrl+D / 转发… /
    复制链接 / 刷新 Ctrl+R),Invoke 菜单项即可。
  - 树内污染证据:该篇(「沃什Jackson Hole演讲的新信号」)页面树 3647 节点、
    **475 个** mp URL(推荐阅读/目录类内链),最短者是另一篇文章 —— 这就是
    验收时「上一篇 URL 被记到本篇头上」的根因;且标题 aid(activity-name /
    js_name)在树**尾部**(node≈3635/3644),小预算扫描读不到。
  - URL 形态:剪贴板得到 `https://mp.weixin.qq.com/s/MDVUlmL76lg0UsPl8KF86A`
    短链(两次一致,token 即身份、永久有效);canonical 已扩展为接受短链。
  - 决定:路线成立(2/2)→ 已在 wechat_bot 实现 `copy_link_via_menu()` 兜底,
    并在 `open_article_and_get_url` 中按「树内恰 1 个 URL → 直接用;0 个或
    ≥2 个 → 菜单兜底;标题校验失败(-2)→ 不复制直接弃」的策略接线。
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

MORE_NAMES = ("更多", "...", "···", "⋯", "。。。", "•••")
CF_UNICODETEXT = 13
K32 = ctypes.windll.kernel32
U32 = ctypes.windll.user32
U32.OpenClipboard.argtypes = [ctypes.c_void_p]
U32.GetClipboardData.argtypes = [ctypes.c_uint]
U32.GetClipboardData.restype = ctypes.c_void_p
K32.GlobalLock.argtypes = [ctypes.c_void_p]
K32.GlobalLock.restype = ctypes.c_void_p
K32.GlobalUnlock.argtypes = [ctypes.c_void_p]
log = lambda m: print(f"[spike] {m}", flush=True)


def read_clipboard_text() -> str:
    """ctypes 读剪贴板 CF_UNICODETEXT(失败/为空返回 '')。"""
    try:
        if not U32.OpenClipboard(None):
            return ""
        try:
            if not U32.IsClipboardFormatAvailable(CF_UNICODETEXT):
                return ""
            h = U32.GetClipboardData(CF_UNICODETEXT)
            p = K32.GlobalLock(h) if h else None
            if not p:
                return ""
            try:
                return ctypes.wstring_at(p)
            finally:
                K32.GlobalUnlock(h)
        finally:
            U32.CloseClipboard()
    except Exception:
        return ""


def invoke_card(title_ctrl):
    """标题控件向上最多 5 级找可 Invoke 祖先卡片并 Invoke(同产品路径)。"""
    p = title_ctrl
    for _ in range(5):
        try:
            pat = p.GetPattern(uia.PatternId.InvokePattern)
        except Exception:
            pat = None
        if pat is not None:
            try:
                pat.Invoke()
                return True
            except Exception:
                return False
        try:
            p = p.GetParentControl()
        except Exception:
            break
        if p is None:
            break
    return False


def dump_toolbar(win):
    """诊断:打印窗口顶部(rect.top 最小区域)的全部按钮类控件。"""
    cands = []
    for c in bot.walk_ctrls(win, max_nodes=3000):
        try:
            ct = c.ControlType
            r = c.BoundingRectangle
        except Exception:
            continue
        if ct == uia.ControlType.ButtonControl or \
                (c.ClassName or "").lower().find("button") >= 0:
            cands.append((r.top, r.left, c))
    cands.sort(key=lambda x: (x[0], x[1]))
    for top, left, c in cands[:40]:
        log(f"  按钮候选 top={top} left={left} class={c.ClassName!r} "
            f"aid={c.AutomationId!r} name={(c.Name or '')[:30]!r}")


def find_more_button(wins):
    """找浏览器工具条的「更多」菜单按钮(实测 class=AppMenuButton,无 InvokePattern)。"""
    hits = []
    for win in wins:
        for c in bot.walk_ctrls(win, max_nodes=6000):
            try:
                name = (c.Name or "").strip()
                cls = (c.ClassName or "")
            except Exception:
                continue
            if name in MORE_NAMES or "更多" in name or "moremenu" in cls.lower():
                hits.append(c)
    hits.sort(key=lambda c: 0 if (c.ClassName or "") == "AppMenuButton" else 1)
    return hits


def open_more_menu(btn) -> bool:
    """打开「···」菜单。实测:Click(合成鼠标)是唯一有效通道 —— ExpandCollapse、
    LegacyIAccessible.DoDefaultAction 均不弹层(AppMenuButton 无 InvokePattern)。"""
    try:
        btn.Click(simulateMove=False)
        return True
    except Exception:
        return False


def find_copy_link_item(timeout: float = 3.0):
    """弹层出现后,在所有 AppEx 窗口树里找 Name 含「复制链接」的控件。"""
    t0 = time.time()
    while time.time() - t0 < timeout:
        for w in bot.appex_windows(retries=1):
            for c in bot.walk_ctrls(w, max_nodes=6000):
                try:
                    if "复制链接" in (c.Name or ""):
                        return c
                except Exception:
                    continue
        time.sleep(0.4)
    return None


def probe_title_aids(host):
    """诊断:全树定位 aid 锚点的 (节点序号, 深度, Name),校准 read_page_title。"""
    found = []
    stack = [(host, 0)]
    n = 0
    while stack and n < 8000:
        c, d = stack.pop()
        n += 1
        try:
            aid = c.AutomationId or ""
            nm = (c.Name or "")[:50]
        except Exception:
            continue
        if aid in bot.ARTICLE_MARKERS or aid in bot.TITLE_AIDS:
            found.append((n, d, aid, nm))
        if d >= 40:
            continue
        try:
            stack.extend((k, d + 1) for k in c.GetChildren())
        except Exception:
            continue
    log(f"探测: 全树 {n} 节点,锚点 {len(found)} 个")
    for x in found[:20]:
        log(f"  node={x[0]} depth={x[1]} aid={x[2]} name={x[3]!r}")


def run_once(account: str, index: int, dump: bool, probe: bool = False) -> bool:
    """单次:开主页 → Invoke 第 index 篇 → 读标题/树内URL → 菜单复制 → 关 tab。"""
    ok, msg = bot.search_open_profile(account)
    if not ok:
        log(f"主页打开失败: {msg}")
        return False
    w, host = bot.find_profile_host(account=account, kicks=2)
    if host is None:
        log("主页宿主未就绪")
        return False
    titles, _times = bot.scan_list(host, max_nodes=5000)
    if index >= len(titles):
        log(f"列表只有 {len(titles)} 条,无第 {index + 1} 篇")
        return False
    target = titles[index]
    target_name = (target.Name or "").strip()
    log(f"目标第{index + 1}篇: {target_name[:46]!r}")
    if not invoke_card(target):
        log("标题卡片不可 Invoke")
        return False
    win, h2 = bot.find_article_host(timeout=40.0)
    if h2 is None:
        log("文章页未打开")
        return False
    # 与产品路径一致:标题与 URL 一趟扫描拿齐(scan_article_page)
    page_t, urls, n = bot.scan_article_page(h2)
    log(f"页面自身标题: {page_t!r}; 标题校验 = "
        f"{bot._titles_match(target_name, page_t)}")
    if probe:
        probe_title_aids(h2)
    shortest = sorted(urls, key=len)[0] if urls else ""
    log(f"树内 mp URL {len(urls)} 个(树 {n} 节点);最短者 = {shortest!r}")
    log(f"最短者 canonical = {canonical.canonicalize_url(shortest) if shortest else None!r}")
    if not win:
        win = bot.appex_windows(retries=1)[0]
    mores = find_more_button([win])
    log(f"「···」按钮候选 {len(mores)} 个: " + str(
        [(m.ClassName, m.AutomationId, (m.Name or "")[:20]) for m in mores]))
    clip = ""
    try:
        clip = uia.GetClipboardText() or ""
    except Exception:
        pass
    copied = ""
    try:
        opened = False
        for m in mores:
            if open_more_menu(m):
                opened = True
                break
        if not opened:
            log("!! 「更多」按钮不可点击")
        else:
            item = find_copy_link_item(timeout=3.0)
            if item is None:
                log("!! 菜单未出现「复制链接」项")
                try:
                    uia.SendKeys("{Esc}")
                except Exception:
                    pass
            elif bot.invoke_control(item):
                time.sleep(2.0)
                copied = read_clipboard_text()
                log(f"复制链接已 Invoke,剪贴板 = {copied!r}")
            else:
                log("!! 「复制链接」项不可触发")
                try:
                    uia.SendKeys("{Esc}")
                except Exception:
                    pass
    finally:
        time.sleep(1.0)
        try:
            uia.SetClipboardText(clip)
        except Exception:
            pass
    shape = ("full?__biz+sn" if ("__biz=" in copied and "sn=" in copied)
             else ("short /s/" if "/s/" in copied and "?" not in copied else "other/none"))
    canon = canonical.canonicalize_url(copied) if copied else None
    log(f"URL 形态 = {shape}; canonicalize = {canon!r}")
    if not bot.close_active_tab(wait=2.5):
        bot.close_article_tabs(max_close=2, wait=2.5)
    bot.close_profile_tab(account, wait=2.5)
    return bool(copied) and canon is not None


def root_snapshot():
    """全部顶层窗口快照 [(class, name, pid, hwnd)],用于发现弹出的菜单窗口。"""
    out = []
    try:
        for c in uia.GetRootControl().GetChildren():
            try:
                out.append((c.ClassName or "", (c.Name or "")[:24], c.ProcessId,
                            c.NativeWindowHandle))
            except Exception:
                continue
    except Exception:
        pass
    return out


def menu_probe(account: str, index: int):
    """诊断:Invoke 浏览器「更多」菜单,枚举弹出的新顶层窗口及其树。"""
    ok, msg = bot.search_open_profile(account)
    if not ok:
        log(f"主页打开失败: {msg}")
        return
    _w, host = bot.find_profile_host(account=account, kicks=2)
    if host is None:
        log("主页宿主未就绪")
        return
    titles, _t = bot.scan_list(host, max_nodes=5000)
    if index >= len(titles) or not invoke_card(titles[index]):
        log("目标卡片不可用")
        return
    _w2, h2 = bot.find_article_host(timeout=40.0)
    if h2 is None:
        log("文章页未打开")
        return
    time.sleep(1.5)
    before = root_snapshot()
    more = None
    for w in bot.appex_windows():
        for c in bot.walk_ctrls(w, max_nodes=6000):
            try:
                if (c.ClassName or "") == "AppMenuButton" and (c.Name or "") == "更多":
                    more = c
                    break
            except Exception:
                continue
        if more is not None:
            break
    log(f"AppMenuButton(更多): {'找到' if more is not None else '未找到'}")
    if more is None:
        return
    # 逐种动作尝试,每种之后检查弹层(新顶层窗口 / AppEx 树内 复制链接 字样)
    def try_expand():
        try:
            p = more.GetPattern(uia.PatternId.ExpandCollapsePattern)
            if p is None:
                return False
            p.Expand()
            return True
        except Exception:
            return False

    def try_legacy():
        try:
            more.GetPattern(uia.PatternId.LegacyIAccessiblePattern).DoDefaultAction()
            return True
        except Exception:
            return False

    def try_click():
        try:
            more.Click(simulateMove=False)
            return True
        except Exception:
            return False

    def scan_popup(tag):
        hit = False
        t_end = time.time() + 3.0
        while time.time() < t_end:
            for snap in root_snapshot():
                if snap not in before:
                    log(f"  [{tag}] 新顶层窗口: class={snap[0]!r} name={snap[1]!r} "
                        f"pid={snap[2]}")
                    hit = True
            for w in bot.appex_windows(retries=1):
                for c in bot.walk_ctrls(w, max_nodes=8000):
                    try:
                        nm = c.Name or ""
                    except Exception:
                        continue
                    if "复制链接" in nm:
                        log(f"  [{tag}] 命中 复制链接 控件: ct={c.ControlType} "
                            f"class={c.ClassName!r} name={nm[:40]!r}")
                        hit = True
            time.sleep(0.6)
        if not hit:
            log(f"  [{tag}] 3s 内未见弹层/复制链接控件")
        return hit

    for tag, fn in (("ExpandCollapse", try_expand), ("LegacyDoDefault", try_legacy),
                    ("Click", try_click)):
        ok = False
        try:
            ok = fn()
        except Exception as exc:
            log(f"  [{tag}] 动作异常: {exc!r}")
        log(f"动作 {tag}: 发送={ok}")
        if ok and scan_popup(tag):
            break
        try:
            uia.SendKeys("{Esc}")
            time.sleep(0.8)
        except Exception:
            pass
    seen = set()
    for snap in root_snapshot():
        if snap not in before:
            seen.add(snap)
    if not seen:
        log("全程无新顶层窗口(菜单若打开,应为原窗口内自绘面板或 Qt 面板)")
    for snap in seen:
        for c in uia.GetRootControl().GetChildren():
            try:
                if c.NativeWindowHandle != snap[3]:
                    continue
            except Exception:
                continue
            k = 0
            for x in bot.walk_ctrls(c, max_nodes=400):
                k += 1
                try:
                    log(f"    [{k}] ct={x.ControlType} class={x.ClassName!r} "
                        f"aid={x.AutomationId!r} name={(x.Name or '')[:40]!r}")
                except Exception:
                    continue
    # 也扫一遍 AppEx 窗口里是否出现 复制/转发/收藏 字样控件
    for w in bot.appex_windows():
        for c in bot.walk_ctrls(w, max_nodes=8000):
            try:
                nm = c.Name or ""
            except Exception:
                continue
            if any(kw in nm for kw in ("复制", "转发", "收藏", "在浏览器", "刷新")):
                log(f"  AppEx 内候选: ct={c.ControlType} class={c.ClassName!r} "
                    f"name={nm[:40]!r}")
    try:
        uia.SendKeys("{Esc}")
    except Exception:
        pass
    time.sleep(1.0)
    if not bot.close_active_tab(wait=2.5):
        bot.close_article_tabs(max_close=2, wait=2.5)
    bot.close_profile_tab(account, wait=2.5)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", default="郭磊宏观茶座")
    ap.add_argument("--index", type=int, default=1, help="列表第几篇(0 起)")
    ap.add_argument("--times", type=int, default=2)
    ap.add_argument("--dump", action="store_true", help="打印窗口顶部按钮清单")
    ap.add_argument("--probe", action="store_true", help="探测标题 aid 在树中的位置")
    ap.add_argument("--menu-dump", action="store_true",
                    help="只诊断「更多」菜单弹层落在哪个窗口,不做复制")
    args = ap.parse_args()
    if args.menu_dump:
        menu_probe(args.account, args.index)
        return 0
    oks = 0
    for i in range(args.times):
        log(f"===== 第 {i + 1}/{args.times} 次 =====")
        try:
            oks += 1 if run_once(args.account, args.index, args.dump, args.probe) else 0
        except Exception as exc:
            import traceback
            log(f"!! 异常: {exc!r}")
            traceback.print_exc()
            bot.close_article_tabs(max_close=3, wait=2.5)
            bot.close_profile_tab(args.account, wait=2.5)
        time.sleep(2.0)
    log(f"===== 成功 {oks}/{args.times} =====")
    return 0 if oks == args.times else 1


if __name__ == "__main__":
    sys.exit(main())
