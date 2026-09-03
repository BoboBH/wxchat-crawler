"""orchestrator:单轮抓取主控 —— 预检、遍历账号、增量判定、URL 增强、入库、日志。

单账号流程(规格 §2):
  打开主页 → UIA 读列表(标题, 日期)→ 归一化日期 → 水位线截止日 =
  水位线 - overlap_days,连续 stop_streak 篇早于截止日即停止 →
  对 fallback_key 未入库的条目逐篇打开文章页提取 URL → canonical 化
  → upsert(URL 提取失败降级 pending,不阻塞)→ 更新水位线 → 关主页 tab。
"""
from __future__ import annotations

import logging
import random
import time
from datetime import date, timedelta

from . import canonical, version_check, wechat_bot as bot
from .canonical import find_stop_index, make_dedup_key, normalize_date_text, pair_publish_dates
from .config import CrawlConfig
from .db import Store


def setup_logging(log_dir) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    logfile = log_dir / f"crawl_{date.today().isoformat()}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(logfile, encoding="utf-8"),
                  logging.StreamHandler()],
        force=True,
    )
    return logging.getLogger("crawler")


def run_check(cfg: CrawlConfig) -> int:
    """环境自检(--check):进程/版本 + AppEx 窗口 + 搜索页可用性。"""
    log = setup_logging(cfg.log_dir)
    rep = version_check.check_environment(
        cfg.process_name, cfg.exe_path, cfg.expected_version_prefix)
    log.info("微信: %s", rep["message"])
    wins = bot.appex_windows()
    log.info("AppEx 浏览器窗口: %d 个", len(wins))
    _w, _h, edit = bot.find_search_entry()
    log.info("搜索页(weixin-search-input): %s",
             "可用" if edit is not None else "未找到 —— 请在微信中打开一次「搜一搜」")
    _w2, host = bot.find_profile_host(kicks=1)
    log.info("已打开的公众号主页: %s", bot.host_doc_name(host) if host else "无")
    ok = rep["ok"] and wins and edit is not None
    log.info("结论: %s", "OK,可以抓取" if ok else "未就绪(见上)")
    return 0 if ok else 1


def _collect_list(cfg: CrawlConfig, host, cutoff: str | None):
    """读列表;新账号(无水位线)滚动扩量。返回 (title_ctrls, pairs, dates)。

    扩量滚动后必须回页顶重扫:日期标签是 sticky 头,滚动状态下 rect 被视口
    钳制,扫描得到的日期会与标题错位(验收实测),回顶后才是自然排布。
    """
    titles, times = bot.scan_list(host, max_nodes=cfg.max_tree_nodes)
    if cutoff is None:
        for _ in range(cfg.new_account_screens):
            bot.scroll_once(host)
            time.sleep(cfg.scroll_wait_sec)
            t2, s2 = bot.scan_list(host, max_nodes=cfg.max_tree_nodes)
            if len(t2) == len(titles):
                break
            titles, times = t2, s2
        bot.scroll_to_top(host, wait=cfg.scroll_wait_sec)
        titles, times = bot.scan_list(host, max_nodes=cfg.max_tree_nodes)
    names = [(c.Name or "").strip() for c in titles]
    tops = [c.BoundingRectangle.top for c in titles]
    pairs = pair_publish_dates(list(zip(names, tops)), times)
    dates = [normalize_date_text(d) for _n, d in pairs]
    return titles, pairs, dates


def process_account(store: Store, cfg: CrawlConfig, name: str,
                    log: logging.Logger) -> dict:
    """抓一个账号,返回 {ok, new, upgraded, pending, message}。

    主页每轮都经搜索重新打开(不复用已开的旧 tab):旧 tab 列表不刷新,
    会导致每轮扫到同一份旧列表、0 新增、水位线永久冻结。
    """
    acc_id = store.get_or_create_account(name)
    watermark = store.watermark(acc_id)
    cutoff = None
    if watermark:
        cutoff = (date.fromisoformat(watermark) -
                  timedelta(days=cfg.overlap_days)).isoformat()

    ok, msg = bot.search_open_profile(name)
    if not ok and "未找到搜索页" in (msg or ""):
        # AppEx 搜索页 tab 会被微信在数小时内回收:自动引导一次再重试,
        # 不再因该原因整轮失败(2026-09-03 实测两账号同因失败)。
        log.info("[%s] 搜索页丢失,尝试自动引导...", name)
        healed, heal_msg = bot.ensure_search_page(name)
        if healed:
            log.info("[%s] 自动引导成功(%s)", name, heal_msg)
            ok, msg = bot.search_open_profile(name)  # 仅重试一次
        else:
            log.warning("[%s] 自动引导失败: %s", name, heal_msg)
    if not ok:
        bot.close_profile_tab(name, wait=cfg.close_tab_wait_sec)  # 尽力清理半开 tab
        return {"ok": False, "new": 0, "upgraded": 0, "pending": 0, "message": msg}
    w, host = bot.find_profile_host(account=name, kicks=cfg.kick_retry)
    if host is None:
        bot.close_profile_tab(name, wait=cfg.close_tab_wait_sec)  # 尽力清理
        return {"ok": False, "new": 0, "upgraded": 0, "pending": 0,
                "message": "主页已搜索但未就绪"}
    if not bot.close_article_tabs(max_close=2, wait=cfg.close_tab_wait_sec):
        log.warning("[%s] 存在残留文章 tab,继续(提取前仍会校验)", name)

    title_ctrls, pairs, dates = _collect_list(cfg, host, cutoff)
    if not pairs:
        store.mark_crawled(acc_id)
        bot.close_profile_tab(name, wait=cfg.close_tab_wait_sec)
        return {"ok": True, "new": 0, "upgraded": 0, "pending": 0,
                "message": "列表为空(0 条)"}
    stop = find_stop_index(dates, cutoff, cfg.stop_streak)
    if stop >= 0:
        log.info("[%s] 连续%d篇早于截止日%s,截断 %d→%d 条",
                 name, cfg.stop_streak, cutoff, len(pairs), stop)
        pairs = pairs[:stop]
        title_ctrls = title_ctrls[:stop]

    seen = store.seen_fallbacks(acc_id, status="ok")  # pending 不跳过 → 次日可重试
    new = upgraded = pending = 0
    max_date = watermark
    processed = 0
    for t_ctrl, (title, date_text) in zip(title_ctrls, pairs):
        if not title:
            continue
        if processed:
            time.sleep(random.uniform(2.0, 5.0))  # 文章间节奏(最后一篇后不休眠)
        fb = make_dedup_key(None, title, date_text)
        iso = normalize_date_text(date_text)
        if iso and (max_date is None or iso > max_date):
            max_date = iso
        if fb in seen:
            continue  # 已入库且 URL ok,不重复打开文章页
        raw, nodes = bot.open_article_and_get_url(
            t_ctrl, open_timeout=cfg.article_open_timeout_sec,
            scan_timeout=cfg.url_scan_timeout_sec,
            max_nodes=cfg.max_tree_nodes, close_wait=cfg.close_tab_wait_sec,
            expected_title=title)
        canon = canonical.canonicalize_url(raw)
        result = store.upsert_article(acc_id, fb, canon, title, date_text, iso)
        if result == "new":
            new += 1
            if canon is None:
                pending += 1
        elif result == "upgraded":
            upgraded += 1
        seen.add(fb)
        # 哨兵分型:nodes 为 open_article_and_get_url 的 url数(-2 标题不符 /
        # -1 未打开 / 0 已打开没取到),pending 原因写进日志方便巡检
        url_state = "ok" if canon else (
            "标题不符PENDING" if nodes == -2 else
            ("未打开PENDING" if nodes == -1 else "PENDING"))
        log.info("[%s] + %s (%s) url=%s", name, title[:40],
                 date_text or "?", url_state)
        processed += 1

    if max_date:
        store.set_watermark(acc_id, max_date)
    store.mark_crawled(acc_id)
    if not bot.close_profile_tab(name, wait=cfg.close_tab_wait_sec):
        log.warning("[%s] 主页 tab 未能关闭(不影响数据)", name)
    return {"ok": True, "new": new, "upgraded": upgraded, "pending": pending,
            "message": f"扫描{len(pairs)}条,新增{new},补URL{upgraded},待补{pending}"}


def run(cfg: CrawlConfig, only_account: str | None = None) -> int:
    log = setup_logging(cfg.log_dir)
    rep = version_check.check_environment(
        cfg.process_name, cfg.exe_path, cfg.expected_version_prefix)
    log.info("环境: %s", rep["message"])
    if not rep["ok"]:
        log.error("微信未就绪,本轮中止(请登录微信后等待下一轮)")
        return 1
    names = [only_account] if only_account else list(cfg.accounts)
    if only_account is None:
        random.shuffle(names)  # 全量轮乱序,模拟真人
    store = Store(cfg.db_path)
    run_id = store.start_run()
    ok_n = fail_n = new_n = 0
    try:
        for i, name in enumerate(names):
            if i:
                gap = random.uniform(cfg.account_gap_min_sec, cfg.account_gap_max_sec)
                log.info("等待 %.0fs 后处理下一个账号", gap)
                time.sleep(gap)
            log.info("=== 账号[%d/%d] %s ===", i + 1, len(names), name)
            try:
                st = process_account(store, cfg, name, log)
            except Exception:
                log.exception("账号 %s 处理异常", name)
                st = {"ok": False, "new": 0, "upgraded": 0, "pending": 0,
                      "message": "异常(见日志)"}
                try:  # 异常轮也要尽力收掉半开的主页 tab,防 tab 堆积
                    bot.close_profile_tab(name, wait=cfg.close_tab_wait_sec)
                except Exception:
                    pass
            ok_n += 1 if st["ok"] else 0
            fail_n += 0 if st["ok"] else 1
            new_n += st["new"]
            log.info("账号 %s: %s", name, st["message"])
    finally:
        store.finish_run(run_id, ok_count=ok_n, fail_count=fail_n, new_count=new_n)
        store.close()
    log.info("本轮完成: 成功%d 失败%d 新增%d", ok_n, fail_n, new_n)
    return 0 if fail_n == 0 else 1
