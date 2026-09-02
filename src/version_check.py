"""微信运行环境检测:主进程存在性、版本号读取与匹配决策。"""
from __future__ import annotations

import ctypes
from pathlib import Path

TH32CS_SNAPPROCESS = 0x2


def parse_version(s) -> tuple[int, ...]:
    """'4.1.12.55' → (4, 1, 12, 55);无法解析返回 ()。"""
    if not s:
        return ()
    try:
        return tuple(int(x) for x in str(s).strip().split("."))
    except ValueError:
        return ()


def version_matches(version, prefix) -> bool:
    """版本号是否以 prefix 开头(major[.minor...] 逐段比较)。"""
    v, p = parse_version(version), parse_version(prefix)
    if not v or not p or len(p) > len(v):
        return False
    return v[:len(p)] == p


def build_report(process_name: str, exe_path: str, expected_prefix: str,
                 pids: list[int], version: str | None) -> dict:
    """纯决策:进程/版本 → {ok, pids, version, message}。"""
    if not pids:
        return {"ok": False, "pids": [], "version": version,
                "message": f"未发现进程 {process_name}:请启动微信并扫码登录"}
    pid_text = ",".join(map(str, pids))
    if version is None:
        return {"ok": True, "pids": pids, "version": None,
                "message": f"{process_name} 运行中(pid={pid_text});"
                           f"版本未知({exe_path} 不可读)"}
    if not version_matches(version, expected_prefix):
        return {"ok": True, "pids": pids, "version": version,
                "message": f"{process_name} 运行中(pid={pid_text}),版本 {version} "
                           f"与已校准版本 {expected_prefix}.x 不同,UIA 控件可能变化 "
                           f"—— 建议先用 tools/spike_uia.py 复核"}
    return {"ok": True, "pids": pids, "version": version,
            "message": f"{process_name} 运行中(pid={pid_text}),版本 {version}"}


def iter_processes() -> dict[int, str]:
    """pid → 进程映像名(CreateToolhelp32Snapshot 一次快照)。"""
    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [("dwSize", ctypes.c_ulong),
                    ("cntUsage", ctypes.c_ulong),
                    ("th32ProcessID", ctypes.c_ulong),
                    ("th32DefaultHeapID", ctypes.c_size_t),
                    ("th32ModuleID", ctypes.c_ulong),
                    ("cntThreads", ctypes.c_ulong),
                    ("th32ParentProcessID", ctypes.c_ulong),
                    ("pcPriClassBase", ctypes.c_long),
                    ("dwFlags", ctypes.c_ulong),
                    ("szExeFile", ctypes.c_wchar * 260)]

    k32 = ctypes.windll.kernel32
    out: dict[int, str] = {}
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap in (0, -1):
        return out
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        ok = k32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            out[entry.th32ProcessID] = entry.szExeFile
            ok = k32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        k32.CloseHandle(snap)
    return out


def read_file_version(path: str) -> str | None:
    """读取 exe 的 FileVersion(如 '4.1.12.55');失败返回 None。"""
    try:
        ver = ctypes.windll.version
        size = ver.GetFileVersionInfoSizeW(str(path), None)
        if not size:
            return None
        data = ctypes.create_string_buffer(size)
        if not ver.GetFileVersionInfoW(str(path), 0, size, data):
            return None
        val = ctypes.c_void_p()
        vlen = ctypes.c_uint()
        if not ver.VerQueryValueW(data, "\\", ctypes.byref(val), ctypes.byref(vlen)):
            return None
        # VS_FIXEDFILEINFO:[0]Signature [1]StrucVersion [2]FileVersionMS [3]FileVersionLS …
        # dwFileVersionMS 高16位 major 低16位 minor;LS 同理
        ffi = ctypes.cast(val, ctypes.POINTER(ctypes.c_uint * 13)).contents
        ms, ls = ffi[2], ffi[3]
        return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"
    except Exception:
        return None


def check_environment(process_name: str, exe_path: str,
                      expected_prefix: str) -> dict:
    """组合检测:进程存在性 + exe 版本 + 决策。"""
    pids = sorted(pid for pid, name in iter_processes().items()
                  if name.lower() == process_name.lower())
    version = read_file_version(exe_path) if Path(exe_path).exists() else None
    return build_report(process_name, exe_path, expected_prefix, pids, version)
