"""bootstrap_search —— 编程化打开微信「搜一搜」页(部署/验收期一次性工具)。

自愈逻辑已进产品:核心实现在 src/wechat_bot.py::ensure_search_page
(编排层在搜索页被微信回收时自动调用,见 orchestrator.process_account)。
本脚本只是它的 CLI 包装,用于部署验收/人工兜底:激活微信主窗口
(Weixin.exe 的顶层非 AppEx 窗口)→ Ctrl+F 聚焦搜索 → 剪贴板粘贴账号名
→ Enter → 轮询 find_search_entry。桌面须已解锁(合成键鼠)。

用法:python tools/bootstrap_search.py [账号名,默认取 config/accounts.yaml 首个]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.wechat_bot import ensure_search_page  # noqa: E402


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    account = sys.argv[1] if len(sys.argv) > 1 else "中金点睛"
    print(f"目标账号: {account}")
    ok, msg = ensure_search_page(account)
    print(f"{'[OK]' if ok else '[FAIL]'} {msg}", file=sys.stderr if not ok else sys.stdout)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
