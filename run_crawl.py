#!/usr/bin/env python
"""命令行入口:python run_crawl.py [--account 名称] [--check]"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.config import ConfigError, load_config
from src.orchestrator import run, run_check


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="微信公众号文章增量爬虫(单轮,UIA 直取)")
    ap.add_argument("--account", help="只抓指定公众号(单号试跑)")
    ap.add_argument("--check", action="store_true", help="环境自检,不抓取")
    args = ap.parse_args(argv)
    if args.account is not None and not args.account.strip():
        args.account = None  # --account "" 视为未指定,走全量轮(含乱序)
    try:
        cfg = load_config()
    except ConfigError as exc:
        print(f"[配置错误] {exc}")
        return 2
    if args.check:
        return run_check(cfg)
    return run(cfg, only_account=args.account)


if __name__ == "__main__":
    sys.exit(main())
