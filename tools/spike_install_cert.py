"""尖峰B辅助:程序化安装 mitmproxy CA 证书(自动点击 Windows 确认弹窗)。

用法:
    .venv/Scripts/python.exe tools/spike_install_cert.py [cer路径]

流程:
  1. 快照当前所有 #32770 对话框窗口;
  2. 启动 `certutil -user -addstore root <cer>`(会弹「证书存储」确认框);
  3. 轮询新出现的 #32770 窗口,找到「是(&Y)」按钮并点击;
  4. 等待 certutil 退出并打印输出(GBK 解码);
  5. 用 `certutil -store -user root mitmproxy` 验证是否安装成功。
"""
import subprocess
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import uiautomation as uia

CER = sys.argv[1] if len(sys.argv) > 1 else ""


def snapshot_dialogs():
    out = set()
    for w in uia.GetRootControl().GetChildren():
        try:
            if (w.ClassName or "") == "#32770":
                out.add(w.NativeWindowHandle)
        except Exception:
            continue
    return out


def find_confirm_button(dialog):
    """在对话框里找「是/Y」确认按钮。"""
    best = None
    try:
        buttons = dialog.GetChildren()
    except Exception:
        return None
    stack = list(buttons)
    while stack:
        c = stack.pop(0)
        try:
            if c.ControlType == uia.ControlType.ButtonControl:
                name = (c.Name or "")
                if name in ("是(&Y)", "是", "&Yes", "Yes", "&Y") or "是" in name:
                    return c
                if "Y" in name or "y" in (name or ""):
                    best = best or c
            stack.extend(c.GetChildren())
        except Exception:
            continue
    return best


def try_click_dialog(new_handles, timeout=25.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        for w in uia.GetRootControl().GetChildren():
            try:
                if (w.ClassName or "") != "#32770":
                    continue
                if w.NativeWindowHandle in new_handles:
                    continue
                name = (w.Name or "")
                print(f"[cert] 发现新对话框: Name={name!r}", flush=True)
                btn = find_confirm_button(w)
                if btn is not None:
                    print(f"[cert] 点击确认按钮: {btn.Name!r}", flush=True)
                    btn.Click(simulateMove=False)
                    return True
                print("[cert] 对话框内未找到确认按钮,继续等待…", flush=True)
            except Exception as exc:
                print(f"[cert] 遍历对话框异常: {exc!r}", flush=True)
        time.sleep(0.5)
    return False


def run_certutil(args, timeout=30):
    p = subprocess.run(args, capture_output=True, timeout=timeout)
    out = p.stdout.decode("gbk", errors="replace") + p.stderr.decode("gbk", errors="replace")
    return p.returncode, out


def main():
    if not CER:
        sys.exit("用法: python tools/spike_install_cert.py <mitmproxy-ca-cert.cer路径>")
    rc, out = run_certutil(["certutil", "-store", "-user", "root", "mitmproxy"])
    if rc == 0 and "NOT_FOUND" not in out:
        print("[cert] 证书已存在,无需安装")
        return
    print("[cert] 证书未安装,开始安装…", flush=True)

    before = snapshot_dialogs()
    print(f"[cert] 安装前已有 #32770 窗口 {len(before)} 个", flush=True)
    proc = subprocess.Popen(
        ["certutil", "-user", "-addstore", "root", CER],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    clicked = try_click_dialog(before, timeout=25.0)
    try:
        cout = proc.communicate(timeout=20)[0]
    except subprocess.TimeoutExpired:
        proc.kill()
        cout = b"<timeout>"
    text = cout.decode("gbk", errors="replace")
    print(f"[cert] certutil rc={proc.returncode} clicked={clicked}")
    print("[cert] certutil 输出:\n" + text.strip()[:1500], flush=True)

    rc2, out2 = run_certutil(["certutil", "-store", "-user", "root", "mitmproxy"])
    ok = rc2 == 0 and "NOT_FOUND" not in out2
    print(f"[cert] 验证 store 查询 rc={rc2} -> {'已安装' if ok else '未找到(NOT_FOUND)'}")
    if not ok:
        print(out2.strip()[:600])
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
