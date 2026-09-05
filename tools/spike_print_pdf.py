"""尖峰G:文章详情页「··· → 打印」弹出的打印预览结构与驱动可行性探索。

背景(2026-09-05 用户需求):想用微信文章详情页自身的打印功能把文章存成
PDF(全程在登录会话内,零公网请求,无验证码问题)。本探针只做三件事:
  A. 定位用户已打开的文章页(标题、宿主窗口);
  B. Invoke「···(更多)」按钮 → Invoke「打印」菜单项(尖峰E:菜单项
     FlueMenuItemView 支持 Invoke,后台生效,不需要合成鼠标);
  C. 基线 diff 摸清打印预览的控件结构(新节点全量 dump 到文件),
     为后续「选目的地 → 另存为 → 写路径」的驱动实现提供事实。

不碰原流程代码;不导航、不关页签、不用合成键鼠。预览打开后尽力用
新节点里的关闭/取消控件还原界面,还原不了会明确提示人工 Esc。

用法(仓库根目录,微信已登录,文章详情页已打开):
    .venv/Scripts/python.exe tools/spike_print_pdf.py [--keep-open] [--out D:/temp/print_preview_dump.txt]
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import uiautomation as uia  # noqa: E402

from src import wechat_bot as bot  # noqa: E402

MAX_NODES = 6000
MAX_DEPTH = 45

PATTERNS = [
    ("Invoke", uia.PatternId.InvokePattern),
    ("Value", uia.PatternId.ValuePattern),
    ("ExpandCollapse", uia.PatternId.ExpandCollapsePattern),
    ("SelItem", uia.PatternId.SelectionItemPattern),
    ("Sel", uia.PatternId.SelectionPattern),
    ("Toggle", uia.PatternId.TogglePattern),
    ("Scroll", uia.PatternId.ScrollPattern),
    ("RangeValue", uia.PatternId.RangeValuePattern),
    ("Window", uia.PatternId.WindowPattern),
]


def pat_flags(c) -> str:
    flags = []
    for nm, pid in PATTERNS:
        try:
            if c.GetPattern(pid) is not None:
                flags.append(nm)
        except Exception:
            pass
    return ",".join(flags)


def iter_tree(root, max_nodes=MAX_NODES):
    """带深度的先序遍历(异常节点跳过)。"""
    stack = [(root, 0)]
    n = 0
    while stack and n < max_nodes:
        c, d = stack.pop()
        n += 1
        yield c, d
        if d >= MAX_DEPTH:
            continue
        try:
            kids = c.GetChildren()
        except Exception:
            kids = []
        stack.extend((k, d + 1) for k in reversed(kids))


def node_line(c, d: int) -> str:
    try:
        return (f"{'  ' * d}{c.ControlTypeName}|{c.ClassName}|"
                f"{c.AutomationId}|{(c.Name or '')[:80]}|{pat_flags(c)}")
    except Exception as e:
        return f"{'  ' * d}<err {e}>"


def root_children(retries: int = 4, gap: float = 0.5) -> list:
    """桌面根的子窗口列表。UIA 树剧烈抖动时(预览窗口开/关瞬间)根级
    GetChildren 会抛 _ctypes.COMError(-2146233083),尖峰G run8 因此裸崩
    ——这里重试兜底,绝不向上抛。"""
    for i in range(retries):
        try:
            return uia.GetRootControl().GetChildren()
        except Exception as e:
            if i == retries - 1:
                print(f"   [root_children] 枚举失败×{retries}: {e}")
                return []
            time.sleep(gap)
    return []


def fingerprint(win) -> set[tuple[str, str, str]]:
    """(ControlType, ClassName, AutomationId, Name[:40]) 快照,用于 diff。"""
    out = set()
    for c, _d in iter_tree(win, MAX_NODES * 2):
        try:
            out.add((c.ControlTypeName, c.ClassName or "",
                     c.AutomationId or "", (c.Name or "")[:40]))
        except Exception:
            continue
    return out


def proc_toplevels(pid: int) -> list[str]:
    out = []
    for w in root_children():
        try:
            if w.ProcessId == pid:
                out.append(f"{w.ClassName}|{(w.Name or '')[:60]}")
        except Exception:
            continue
    return out


def toplevel_handles(pid: int) -> dict[int, str]:
    """进程内所有顶层窗口: hwnd -> 'ClassName|Name'。"""
    out = {}
    for w in root_children():
        try:
            if w.ProcessId == pid:
                out[w.NativeWindowHandle] = f"{w.ClassName}|{(w.Name or '')[:60]}"
        except Exception:
            continue
    return out


def window_by_handle(hwnd: int):
    for w in root_children():
        try:
            if w.ProcessId and w.NativeWindowHandle == hwnd:
                return w
        except Exception:
            continue
    return None


def find_control(win, pred, max_nodes=MAX_NODES):
    for c, _d in iter_tree(win, max_nodes):
        try:
            if pred(c):
                return c
        except Exception:
            continue
    return None


def force_foreground(hwnd, tries: int = 6, gap: float = 1.5) -> bool:
    """置前台并校验:ALT 技巧解锁 SetForegroundWindow 限制(同 spike_ctx_menu)。"""
    for i in range(tries):
        bot.USER32.keybd_event(0x12, 0, 0, 0)
        bot.USER32.keybd_event(0x12, 0, 2, 0)
        try:
            bot.USER32.SetForegroundWindow(hwnd)
        except Exception:
            pass
        time.sleep(gap)
        if bot.USER32.GetForegroundWindow() == hwnd:
            return True
        print(f"   前台尝试{i + 1}/{tries}失败,重试 …")
    return False


def find_save_dialog(known: set[int], timeout: float = 8.0):
    """等一个新的 #32770 通用对话框(另存为打印输出)。known=已存在句柄集。"""
    t0 = time.time()
    while time.time() - t0 < timeout:
        for w in root_children():
            try:
                if (w.ClassName or "") == "#32770" and \
                        w.NativeWindowHandle not in known:
                    return w
            except Exception:
                continue
        time.sleep(0.4)
    return None


def dialog_handles() -> set[int]:
    out = set()
    for w in root_children():
        try:
            if (w.ClassName or "") == "#32770":
                out.add(w.NativeWindowHandle)
        except Exception:
            continue
    return out


def find_dialog_in_tree(hwnd: int, max_depth: int = 4):
    """保存对话框可能是预览窗口的**子 HWND**(尖峰G 实测:不在桌面根下)——
    限深扫描找 #32770(原生子窗口在前几层,全树遍历太慢);兜底根级扫描。"""
    root = window_by_handle(hwnd)
    if root is not None:
        stack = [(root, 0)]
        while stack:
            c, d = stack.pop()
            try:
                if (c.ClassName or "") == "#32770":
                    return c
                if d < max_depth:
                    stack.extend((k, d + 1) for k in c.GetChildren())
            except Exception:
                continue
    for w in root_children():
        try:
            if (w.ClassName or "") == "#32770":
                return w
        except Exception:
            continue
    return None


def find_filename_edit(dlg):
    """保存对话框的文件名输入框:优先「文件名」组合框内的 Edit
    (对话框里还有搜索框等别的输入框,不能抓第一个)。"""
    cbs = []
    for c, _d in iter_tree(dlg, 2500):
        try:
            if c.ControlTypeName == "ComboBoxControl":
                cbs.append(c)
        except Exception:
            continue
    for cb in cbs:
        nm = cb.Name or ""
        if "文件名" in nm or "File name" in nm:
            e = find_control(cb, lambda x: x.ControlTypeName == "EditControl")
            if e is not None:
                return e
    for cb in cbs:
        e = find_control(cb, lambda x: x.ControlTypeName == "EditControl"
                         and (x.AutomationId or "") == "Edit")
        if e is not None:
            return e
    return None


def save_via_dialog(dlg, path: Path, hwnd: int | None = None) -> tuple[bool, str]:
    """在「另存为打印输出」对话框里写路径并点保存,等待文件落盘。"""
    edit = find_filename_edit(dlg)
    if edit is None:
        return False, "对话框里没找到文件名输入框"
    want = str(path)

    def read_value():
        try:
            return edit.GetPattern(uia.PatternId.ValuePattern).Value or ""
        except Exception:
            return "<读取失败>"

    def cancel_dialog() -> None:
        cancel = find_control(dlg, lambda c: (c.Name or "").strip() in
                              ("取消(&N)", "取消", "Cancel")
                              and "Invoke" in pat_flags(c))
        if cancel is not None:
            bot.invoke_control(cancel)

    # 写入路径:SetValue 优先,失败降级剪贴板粘贴;每步读回校验
    ok_set = False
    for attempt in range(3):
        if attempt == 0:
            try:
                edit.GetPattern(uia.PatternId.ValuePattern).SetValue(want)
            except Exception as e:
                print(f"   SetValue 异常({e})→ 降级剪贴板粘贴")
                clip = ""
                try:
                    clip = uia.GetClipboardText() or ""
                except Exception:
                    pass
                try:
                    uia.SetClipboardText(want)
                    edit.SetFocus()
                    time.sleep(0.3)
                    uia.SendKeys("{Ctrl}a{Ctrl}v", waitInterval=0.05)
                except Exception as e2:
                    return False, f"剪贴板粘贴失败: {e2}", None
                finally:
                    time.sleep(0.4)
                    try:
                        uia.SetClipboardText(clip)
                    except Exception:
                        pass
        elif attempt == 1:
            # 剪贴板粘贴也不行 → 键入(无特殊字符的兜底)
            try:
                edit.SetFocus()
                uia.SendKeys("{Ctrl}a", waitInterval=0.05)
                uia.SendKeys(want.replace("[", "{[}").replace("]", "{]}")
                             .replace("(", "{(}").replace(")", "{)}")
                             .replace("{", "{{").replace("}", "}}")
                             .replace("+", "{+}").replace("^", "{^}")
                             .replace("%", "{%}").replace("~", "{~}"),
                             interval=0.01)
            except Exception as e:
                print(f"   键入路径失败: {e}")
        time.sleep(0.5)
        cur = read_value()
        if want in cur:
            ok_set = True
            break
        print(f"   路径未生效(当前={cur[:60]!r})→ 换法重试 {attempt + 1}/3")
    if not ok_set:
        cancel_dialog()
        return False, "文件名无法确认为目标全路径 → 已取消保存(防错误地址落盘)"

    btn = find_control(dlg, lambda c: "保存" in (c.Name or "")
                       and "Invoke" in pat_flags(c))
    if btn is None:
        return False, "没找到「保存」按钮"
    bot.invoke_control(btn)
    # 等文件落盘;若弹出「确认另存为」覆盖框(根级或树内 #32770)则点是
    t0 = time.time()
    while time.time() - t0 < 30.0:
        if path.exists() and path.stat().st_size > 0:
            return True, f"{path.stat().st_size}B"
        conf = None
        if hwnd is not None:
            d2 = find_dialog_in_tree(hwnd)
            if d2 is not None and (d2.Name or "") != (dlg.Name or ""):
                conf = find_control(d2, lambda c: (c.Name or "").strip() in
                                    ("是(&Y)", "是", "Yes")
                                    and "Invoke" in pat_flags(c))
        if conf is not None:
            print("   [覆盖确认] → 是")
            bot.invoke_control(conf)
            time.sleep(0.8)
        time.sleep(0.6)
    return False, "30s 内文件未生成"


def find_retry(hwnd: int, pred, tries: int = 6, gap: float = 1.0):
    """带重试的查找:每次重新按句柄取窗口根(旧包装对象会失效),
    控件找不到时重试 —— 预览树有渲染延迟与「冷却剪枝」现象。"""
    for _i in range(tries):
        w = window_by_handle(hwnd)
        if w is not None:
            c = find_control(w, pred)
            if c is not None:
                return c
        time.sleep(gap)
    return None


def newest_pdfs(seconds: float = 120.0) -> list[str]:
    """近 N 秒内落在 文档/下载 的新 PDF(排查「无对话框直打」落盘位置)。"""
    out = []
    now = time.time()
    for d in (Path.home() / "Documents", Path.home() / "Downloads"):
        try:
            for p in d.glob("*.pdf"):
                if now - p.stat().st_mtime <= seconds:
                    out.append(f"{d.name}/{p.name}")
        except OSError:
            continue
    return out


def save_flow(hwnd: int, pid: int, title: str, save_dir: str):
    """预览窗口内:校验目的地 → 「打印」→ 另存为对话框写路径保存。

    返回 (ok, info, pdf_path)。必须在预览打开后**立即**调用:预览的
    设置侧栏控件会在树里被剪枝(尖峰G 实测),拖延就找不到 action-button。
    """
    # 目的地 = 第1个 weui-popup-button-text(顺序固定:打印机/彩色/页数/纸张/方向)
    dest = None
    for _i in range(6):
        root = window_by_handle(hwnd)
        if root is None:
            return False, "预览窗口已消失", None
        txt = find_control(root, lambda c:
                           (c.ClassName or "") == "weui-popup-button-text")
        if txt is not None:
            t = find_control(txt, lambda c: c.ControlTypeName == "TextControl")
            if t is not None:
                dest = (t.Name or "").strip()
                break
        time.sleep(1.0)
    print(f"   目的地: {dest!r}")
    if not dest or "PDF" not in dest:
        return False, f"目的地不是 Print to PDF({dest!r}),不冒进", None
    action = find_retry(hwnd, lambda c: (c.ClassName or "") == "action-button"
                        and "Invoke" in pat_flags(c))
    if action is None:
        return False, "没找到「打印」执行按钮(action-button)", None
    safe = "".join(ch if ch not in '\\/:*?"<>|\r\n\t' else "_"
                   for ch in (title or "article"))
    safe = " ".join(safe.split()).strip(" ._")[:60] or "article"
    out_pdf = Path(save_dir) / f"{safe}.pdf"
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    if out_pdf.exists():
        out_pdf = out_pdf.with_name(f"{out_pdf.stem}_1.pdf")
    def wait_dialog(seconds: float):
        t0 = time.time()
        while time.time() - t0 < seconds:
            d = find_dialog_in_tree(hwnd)
            if d is not None:
                return d
            time.sleep(0.4)
        return None

    if not bot.invoke_control(action):
        return False, "action-button Invoke 失败", None
    dlg = wait_dialog(20)  # 打印 spool 需要时间(12 页图文)
    if dlg is None and window_by_handle(hwnd) is not None:
        print("   Invoke 无反应 → 合成鼠标 Click(校验前台后)")
        if bot.USER32.GetForegroundWindow() != hwnd:
            force_foreground(hwnd, tries=4, gap=1.0)
        root = window_by_handle(hwnd)
        act2 = find_control(root, lambda c:
                            (c.ClassName or "") == "action-button") \
            if root is not None else None
        if act2 is not None:
            r = act2.BoundingRectangle
            uia.SetCursorPos((r.left + r.right) // 2, (r.top + r.bottom) // 2)
            time.sleep(0.25)
            act2.Click(simulateMove=False)
            dlg = wait_dialog(20)
    if dlg is None:
        gone = window_by_handle(hwnd) is None
        hints = newest_pdfs()
        return False, (f"保存对话框未出现(预览已关={gone},"
                       f"近期新PDF={hints or '无'})"), None
    print(f"   对话框已出现: {(dlg.Name or '')[:40]!r}")
    ok, info = save_via_dialog(dlg, out_pdf, hwnd=hwnd)
    return ok, info, out_pdf


def win32_preclean(pid: int) -> None:
    """UIA 之前的 Win32 清尸:残留的模态 #32770(另存为/重命名等)会让
    本进程 UIA 消息泵卡死,树遍历无限阻塞(尖峰G run9 教训:A 段 145s
    无进展)。WM_CLOSE 对保存对话框=取消,不会落盘。"""
    import ctypes
    import ctypes.wintypes as wt
    u32 = ctypes.windll.user32
    hits: list[int] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def cb(h, _l):
        wpid = wt.DWORD()
        u32.GetWindowThreadProcessId(h, ctypes.byref(wpid))
        if wpid.value == pid and u32.IsWindowVisible(h):
            cls = ctypes.create_unicode_buffer(256)
            u32.GetClassNameW(h, cls, 256)
            if (cls.value or "") == "#32770":
                hits.append(h)
        return True

    u32.EnumWindows(cb, 0)
    for h in hits:
        u32.SendMessageW(wt.HWND(h), 0x0010, 0, 0)  # WM_CLOSE = 取消
        print(f"   [Win32清尸] WM_CLOSE #32770 hwnd={h}")
        time.sleep(1.0)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep-open", action="store_true",
                    help="结束时不尝试关闭打印预览(留给人检查)")
    ap.add_argument("--save", default=None, metavar="DIR",
                    help="保存目录(如 D:/temp);给出则走完整保存流程")
    ap.add_argument("--out", default=r"D:\temp\print_preview_dump.txt")
    args = ap.parse_args()

    print("== A. 定位文章页 ==")
    for w in bot.appex_windows():
        try:
            bot.restore_if_minimized(w)
        except Exception:
            pass
    win, h = bot.find_article_host(timeout=3.0)
    if h is None:
        print("   结论: 没找到已打开的文章详情页 —— 请先打开一篇再跑")
        return 1
    title = None
    tc = find_control(h, lambda c: (c.AutomationId or "") == "activity-name"
                      and (c.Name or "").strip())
    if tc is not None:
        title = (tc.Name or "").strip()
    print(f"   文章: {title!r}")
    print(f"   宿主: {win.ClassName} | pid={win.ProcessId}")
    win32_preclean(win.ProcessId)

    # 上轮可能遗留的模态保存对话框(停在错误地址那个)→ 先取消掉
    for w in root_children():
        try:
            if (w.ClassName or "") != "#32770":
                continue
        except Exception:
            continue
        cancel = find_control(w, lambda c: (c.Name or "").strip() in
                              ("取消(&N)", "取消", "Cancel")
                              and "Invoke" in pat_flags(c))
        if cancel is not None:
            print(f"   [清理] 收掉残留对话框 {(w.Name or '')[:30]!r}")
            bot.invoke_control(cancel)
            time.sleep(1.0)

    # 上轮可能残留未关闭的预览(同进程额外顶层窗口且无文章特征)→ 先收掉
    for h2, label in list(toplevel_handles(win.ProcessId).items()):
        if h2 == win.NativeWindowHandle:
            continue
        w2 = window_by_handle(h2)
        if w2 is None:
            continue
        if find_control(w2, lambda c: (c.AutomationId or "") == "activity-name") \
                is not None:
            continue  # 别的文章页窗口,不动
        print(f"   [清理] 残留预览窗口 hwnd={h2} {label}")
        closer = find_control(
            w2, lambda c: (c.Name or "").strip() in
            ("关闭", "取消", "Close", "Cancel", "×", "✕")
            and "Invoke" in pat_flags(c))
        if closer is not None:
            bot.invoke_control(closer)
            time.sleep(1.5)
        if window_by_handle(h2) is not None:
            try:
                w2.SetFocus()
                time.sleep(0.4)
                if bot.USER32.GetForegroundWindow() == h2:
                    uia.SendKeys("{Esc}")
                    time.sleep(1.0)
            except Exception:
                pass
        print(f"   [清理] 已关闭: {window_by_handle(h2) is None}")

    toplevels_before = proc_toplevels(win.ProcessId)
    tops_before = toplevel_handles(win.ProcessId)
    baseline = fingerprint(win)
    print(f"   基线: {len(baseline)} 种控件;进程顶层窗口 {len(toplevels_before)} 个")

    print("== B. 前台 + Click「···」→ Invoke「打印」 ==")
    # 尖峰D 实测:「更多」按钮无 InvokePattern,ExpandCollapse/DoDefaultAction
    # 都不弹层,只有合成鼠标 Click 能打开菜单 → 必须先抢到前台。
    hwnd = win.NativeWindowHandle
    if not force_foreground(hwnd):
        print("   结论: AppEx 多次抢不到前台(用户正在用电脑)→ 不点击,中止")
        return 2
    more = find_control(win, lambda c: (c.ClassName or "") == bot.MORE_BUTTON_CLASS
                        and (c.Name or "") == bot.MORE_BUTTON_NAME)
    if more is None:
        print("   结论: 没找到「···」(更多)按钮")
        return 1
    try:
        more.Click(simulateMove=False)
    except Exception as e:
        print(f"   结论: 「···」Click 失败: {e}")
        return 1
    item = None
    t0 = time.time()
    while time.time() - t0 < 3.0 and item is None:
        item = find_control(win, lambda c: "FlueMenuItemView" in (c.ClassName or "")
                            and "打印" in (c.Name or ""))
        if item is None:
            time.sleep(0.4)
    if item is None:
        print("   结论: 菜单里没出现「打印」项(新项 diff:)")
        for c, d in iter_tree(win, MAX_NODES):
            try:
                key = (c.ControlTypeName, c.ClassName or "", c.AutomationId or "",
                       (c.Name or "")[:40])
            except Exception:
                continue
            if key not in baseline and (c.Name or ""):
                print("   +", node_line(c, 1))
        bot._dismiss_menu()
        return 1
    print(f"   菜单项: {node_line(item, 1)}")
    if not bot.invoke_control(item):
        print("   结论: 「打印」Invoke 失败")
        bot._dismiss_menu()
        return 1
    print("   已 Invoke「打印」,等预览弹出 …")
    time.sleep(4.0)

    print("== C. 预览结构(顶层窗口句柄 diff)==")
    tops_after = toplevel_handles(win.ProcessId)
    new_handles = [h2 for h2 in tops_after if h2 not in tops_before]
    print(f"   进程顶层窗口: {len(tops_after)} 个(新增 {len(new_handles)}):")
    for h2 in new_handles:
        print(f"     + hwnd={h2} {tops_after[h2]}")

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not new_handles:
        print("   结论: 没有新顶层窗口,树内也无新节点(打印没生效?)")
        return 1
    preview_root = None
    save_ok = False
    saved_pdf = None
    for h2 in new_handles:
        w2 = window_by_handle(h2)
        if w2 is None:
            print(f"   hwnd={h2} 已消失(闪退的过渡窗口)")
            continue
        preview_root = w2
        if args.save:
            print(f"== E. 保存流程 → {args.save}(打开后立即执行,防控件剪枝)==")
            save_ok, save_msg, saved_pdf = save_flow(h2, win.ProcessId,
                                                     title, args.save)
            if save_ok:
                print(f"   ★ PDF 已保存: {saved_pdf}({save_msg})")
            else:
                print(f"   保存失败: {save_msg}(保留 dump 供排查)")
        if args.save and save_ok:
            continue
        fp = out_path.with_name(f"{out_path.stem}_win{h2}.txt")
        n = 0
        with fp.open("w", encoding="utf-8") as f:
            f.write(f"# 尖峰G 预览窗口 dump hwnd={h2} "
                    f"{time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"# 文章: {title!r}\n\n")
            for c, d in iter_tree(w2, MAX_NODES):
                f.write(node_line(c, d) + "\n")
                n += 1
        print(f"   预览窗口树 {n} 节点 → {fp}")
        shown = 0
        with fp.open("r", encoding="utf-8") as f:
            for ln in f:
                if ln.startswith("#"):
                    continue
                if shown >= 70:
                    print("   … 其余见 dump 文件")
                    break
                print("   " + ln.rstrip())
                shown += 1

    if args.keep_open:
        print("== 结束: 预览保持打开(--keep-open)==")
        return 0

    print("== D. 还原(关闭对话框/预览)==")
    # 先收残留的「另存为打印输出」对话框(模态,不收掉预览也关不掉)
    for w in root_children():
        try:
            if (w.ClassName or "") != "#32770":
                continue
        except Exception:
            continue
        cancel = find_control(w, lambda c: (c.Name or "").strip() in
                              ("取消(&N)", "取消", "Cancel")
                              and "Invoke" in pat_flags(c))
        if cancel is not None:
            print(f"   收掉残留对话框: {(w.Name or '')[:30]!r}")
            bot.invoke_control(cancel)
            time.sleep(1.0)
    if preview_root is None:
        print("   预览窗口已不在,无需还原")
        return 0
    close_names = ("关闭", "取消", "Close", "Cancel", "×", "✕")
    closer = None
    for c, _d in iter_tree(preview_root, MAX_NODES):
        try:  # 保存成功后预览会自行销毁,遍历中随时可能读到失效元素
            nm = (c.Name or "").strip()
            if nm in close_names and "Invoke" in pat_flags(c):
                closer = c
                break
        except Exception:
            continue
    if closer is not None:
        print(f"   Invoke 关闭控件: {node_line(closer, 1)}")
        bot.invoke_control(closer)
        time.sleep(1.8)
    closed = window_by_handle(preview_root.NativeWindowHandle) is None
    if not closed and preview_root is not None:
        # 兜底:预览拿焦点后发 Esc(SendInput 落在焦点窗口)
        try:
            preview_root.SetFocus()
            time.sleep(0.4)
            fg = bot.USER32.GetForegroundWindow()
            if fg == preview_root.NativeWindowHandle:
                uia.SendKeys("{Esc}")
                time.sleep(1.2)
                closed = window_by_handle(
                    preview_root.NativeWindowHandle) is None
                print(f"   Esc 兜底 → 关闭: {closed}")
            else:
                print("   预览没拿到前台,不乱发 Esc(避免落进别的窗口)")
        except Exception as e:
            print(f"   Esc 兜底失败: {e}")
    print(f"   预览已关闭: {closed}")
    tc2 = find_control(h, lambda c: (c.AutomationId or "") == "activity-name"
                       and (c.Name or "").strip())
    try:
        print(f"   文章页仍在: {bool(tc2)}" +
              (f"(标题 {(tc2.Name or '')[:30]!r})" if tc2 else ""))
    except Exception:
        print("   文章页仍在: True(标题读取失败,元素已失效)")
    print("== 完成 ==")
    return 0


if __name__ == "__main__":
    try:
        _code = main()
    except Exception as _e:  # COMError 等 UIA 瞬时异常:留结论行,不留裸栈
        import traceback
        print(f"== 异常退出: {type(_e).__name__}: {_e}")
        traceback.print_exc()
        _code = 3
    raise SystemExit(_code)
