"""bootstrap_search —— 编程化打开微信「搜一搜」页,替代一次性人工部署步骤。

per-account 流程(search_open_profile)总依赖 AppEx 搜索页输入框
(AutomationId weixin-search-input)已存在;部署文档原要求人工在微信里
打开一次「搜一搜」。本脚本以合成输入替代:激活微信主窗口(Weixin.exe 的
顶层非 AppEx 窗口)→ Ctrl+F 聚焦搜索 → 剪贴板粘贴测试账号名 → Enter →
轮询 find_search_entry。仅部署/验收时运行一次;桌面须已解锁(合成键鼠)。

用法:python tools/bootstrap_search.py [账号名,默认取 config/accounts.yaml 首个]
"""
from __future__ import annotations

import ctypes
import sys
import time
from pathlib import Path

import uiautomation as uia

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.wechat_bot import find_search_entry, process_name  # noqa: E402

MAIN_PROCESS = "weixin.exe"
USER32 = ctypes.windll.user32
USER32.GetForegroundWindow.restype = ctypes.c_void_p
KERNEL32 = ctypes.windll.kernel32
KERNEL32.GetCurrentThreadId.restype = ctypes.c_uint


def force_foreground(hwnd: int) -> bool:
    """SetForegroundWindow 受前台锁限制(后台进程调用常被静默拒绝);
    被拒时先 AttachThreadInput 借前台线程输入权限,再不行就 tap Alt 解锁重试。"""
    USER32.SetForegroundWindow(ctypes.c_void_p(hwnd))
    if USER32.GetForegroundWindow() == hwnd:
        return True
    fg = USER32.GetForegroundWindow()
    cur = KERNEL32.GetCurrentThreadId()
    other = USER32.GetWindowThreadProcessId(ctypes.c_void_p(fg), None) if fg else 0
    attached = False
    if other and other != cur:
        attached = bool(USER32.AttachThreadInput(ctypes.c_uint(cur), ctypes.c_uint(other), 1))
        USER32.BringWindowToTop(ctypes.c_void_p(hwnd))
        USER32.SetForegroundWindow(ctypes.c_void_p(hwnd))
        if attached:
            USER32.AttachThreadInput(ctypes.c_uint(cur), ctypes.c_uint(other), 0)
    if USER32.GetForegroundWindow() != hwnd:
        uia.SendKeys("{Alt}")  # 一次无害 Alt tap 解除前台锁
        USER32.SetForegroundWindow(ctypes.c_void_p(hwnd))
    return USER32.GetForegroundWindow() == hwnd


def desktop_state() -> str:
    """'locked' | 'no-foreground' | 'ok':合成键鼠只在前台正常时有效。"""
    fg = USER32.GetForegroundWindow()
    if not fg:
        return "no-foreground"
    pid = ctypes.c_uint()
    USER32.GetWindowThreadProcessId(ctypes.c_void_p(fg), ctypes.byref(pid))
    return "locked" if process_name(pid.value).lower() == "lockapp.exe" else "ok"


def main_window_candidates() -> list:
    """微信主进程的顶层窗口(AppEx 的 Chrome_WidgetWin_0 除外)。"""
    out = []
    for c in uia.GetRootControl().GetChildren():
        try:
            if process_name(c.ProcessId).lower() == MAIN_PROCESS and \
                    (c.ClassName or "") != "Chrome_WidgetWin_0":
                out.append(c)
        except Exception:
            continue
    return out


def focus_main() -> bool:
    cands = main_window_candidates()
    if not cands:
        print(f"未找到 {MAIN_PROCESS} 的顶层主窗口", file=sys.stderr)
        return False
    for c in cands:
        r = c.BoundingRectangle
        print(f"  候选主窗口: class={c.ClassName} name={c.Name!r} "
              f"rect=({r.left},{r.top},{r.right},{r.bottom})")
    win = max(cands, key=lambda c: max(
        (c.BoundingRectangle.right - c.BoundingRectangle.left) *
        (c.BoundingRectangle.bottom - c.BoundingRectangle.top), 0))
    USER32.ShowWindow(win.NativeWindowHandle, 9)  # SW_RESTORE
    time.sleep(0.5)
    ok = force_foreground(win.NativeWindowHandle)
    print(f"  主窗口已聚焦: {ok} (hwnd=0x{win.NativeWindowHandle:X})")
    time.sleep(1.0)
    return True  # 聚焦失败不中止:仍尝试快捷键(日志可判断按键去向)


def try_shortcut(shortcut: str, account: str, poll_sec: float = 30.0) -> bool:
    print(f"== 尝试 {shortcut} ==")
    state = desktop_state()
    if state != "ok":
        print(f"[SKIP] 桌面状态={state}(锁屏/无前台窗口),合成键鼠不可达 —— 请解锁后再试",
              file=sys.stderr)
        return False
    clip = ""
    try:
        clip = uia.GetClipboardText() or ""
    except Exception:
        pass
    try:
        uia.SendKeys(shortcut)
        time.sleep(1.0)
        fg = USER32.GetForegroundWindow()
        print(f"  发送 {shortcut} 时前台窗口 hwnd=0x{fg or 0:X}")
        uia.SetClipboardText(account)
        uia.SendKeys("{Ctrl}a")
        uia.SendKeys("{Ctrl}v")
        time.sleep(1.0)
        uia.SendKeys("{Enter}")
    finally:
        try:
            uia.SetClipboardText(clip)
        except Exception:
            pass
    t0 = time.time()
    while time.time() - t0 < poll_sec:
        _w, _h, edit = find_search_entry()
        if edit is not None:
            print(f"[OK] 搜索页可用(由 {shortcut} 打开)")
            return True
        time.sleep(2.0)
    print(f"[--] {shortcut} 后 {poll_sec:.0f}s 内未出现搜索页输入框")
    return False


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    account = sys.argv[1] if len(sys.argv) > 1 else "中金点睛"
    print(f"目标账号: {account}")
    if not focus_main():
        return 1
    for shortcut in ("{Ctrl}f", "{Ctrl}k", "{Ctrl}f"):
        if try_shortcut(shortcut, account):
            return 0
    print("[FAIL] 三次尝试后仍未打开「搜一搜」——请人工在微信中打开一次「搜一搜」",
          file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
