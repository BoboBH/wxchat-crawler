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
from contextlib import contextmanager
from datetime import date, timedelta

from . import canonical, mysql_sync, notify as notify_mod, version_check, wechat_bot as bot
from .canonical import find_stop_index, make_dedup_key, normalize_date_text, pair_publish_dates
from .config import CrawlConfig, NotifyConfig
from .db import Store


def setup_logging(log_dir) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    logfile = log_dir / f"crawl_{date.today().isoformat()}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",  # 每行带完整日期:跨日巡检/与 crawl_runs 对得上
        handlers=[logging.FileHandler(logfile, encoding="utf-8"),
                  logging.StreamHandler()],
        force=True,
    )
    return logging.getLogger("crawler")


def fmt_duration(seconds: float) -> str:
    """38.24 → '38.2s';272.34 → '4分32.3s'(逐篇用秒,整号/整轮用分)。"""
    if seconds < 60:
        return f"{seconds:.1f}s"
    m, s = divmod(seconds, 60)
    return f"{int(m)}分{s:.1f}s"


@contextmanager
def log_step(log: logging.Logger, fmt: str, *args):
    """步骤计时:进入打「<fmt> …」,正常退出打「<fmt> 完成[(尾注)] 用时Xs」,
    异常打「<fmt> 失败 用时Xs」后原样抛出(外层照常记堆栈)。块内往 yield
    出的列表追加尾注(条数/结果),完成行以「(尾注)」带出。
    仅适用于无失败早退的步骤;带早退的(如打开主页)手动计时,失败才不会
    被记成误导性的「完成」。"""
    t0 = time.perf_counter()
    tail: list[str] = []
    log.info(fmt + " …", *args)
    try:
        yield tail
    except Exception:
        log.info(fmt + " 失败 用时%s", *args, fmt_duration(time.perf_counter() - t0))
        raise
    extra = f"({tail[0]})" if tail else ""
    log.info(fmt + " 完成%s 用时%s", *args, extra,
             fmt_duration(time.perf_counter() - t0))


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


def _pairs_and_dates(titles, times):
    """扫描结果 → (pairs, 归一化日期列表)。"""
    names = [(c.Name or "").strip() for c in titles]
    tops = [c.BoundingRectangle.top for c in titles]
    pairs = pair_publish_dates(list(zip(names, tops)), times)
    dates = [normalize_date_text(d) for _n, d in pairs]
    return pairs, dates


def _covers_cutoff(dates: list[str | None], cutoff: str) -> bool:
    """可见列表是否已扫过截止日窗口:最旧可解析日期 ≤ cutoff 即覆盖。

    全部无法解析 → 视为未覆盖(继续滚,由屏数上限/触底兜底)。
    滚动中途的日期标签受 sticky 头钳制可能错位,但可视集合仍近似真实
    底部区域,仅用于「要不要继续滚」的深度判断,不作入库依据。
    """
    real = [d for d in dates if d]
    return bool(real) and min(real) <= cutoff


def _first_top(titles) -> float:
    """首标题视口 top;空列表返回 0.0。"""
    return titles[0].BoundingRectangle.top if titles else 0.0


def _screen_fingerprint(titles) -> tuple[int, float]:
    """扫描瞬间的屏幕指纹 (条数, 首标题 top)。

    UIA 控件的 BoundingRectangle 是活查询:滚动后再读旧控件拿到的是
    滚动后的新位置(2026-09-04 实测:扫描时 828、两屏滚动后重查旧控件
    变 -1692),把「已滚动」误判成「未移动」→ 假触底。触底判定必须用
    扫描当时立即快照的 Python 数值,不得延迟重查 UIA rect。
    """
    return (len(titles), _first_top(titles))


def _same_screen(fp_old: tuple[int, float], fp_new: tuple[int, float]) -> bool:
    """两屏指纹相同 → 视口没移动(触底)。条数不增或 top 不动都可能是
    滚半屏(2026-09-04 实测条数常不变),两者须同时不变才算没动。"""
    return (fp_new[0] == fp_old[0]
            and abs(fp_new[1] - fp_old[1]) <= 1.5)


def _collect_list(cfg: CrawlConfig, host, cutoff: str | None,
                  log: logging.Logger | None = None):
    """读列表并按需滚动扩量,返回 (title_ctrls, pairs, dates)。

    新账号(无水位线):固定滚 new_account_screens 屏(原逻辑)。
    老账号(有截止日):回填模式 —— 最旧可见日期尚未早于截止日就继续下滚
    (至多 deep_scroll_screens 屏,0=关闭),把 overlap 窗口真正扫全;
    首屏已覆盖则一屏不滚(日常增量零额外开销),触底(条数不增)提前停。

    扩量滚动后必须回页顶重扫:日期标签是 sticky 头,滚动状态下 rect 被视口
    钳制,扫描得到的日期会与标题错位(验收实测),回顶后才是自然排布。
    主页打开时的滚动状态不可知(首扫 top 是任意屏幕坐标,可能不在页顶),
    所以每次收采一律先回顶再首扫。
    """

    def dbg(msg, *args):
        if log:
            log.info("    [回填] " + msg, *args)

    bot.scroll_to_top(host, wait=cfg.scroll_wait_sec)
    titles, times = bot.scan_list(host, max_nodes=cfg.max_tree_nodes)
    fp = _screen_fingerprint(titles)
    if cutoff is None:
        for _ in range(cfg.new_account_screens):
            bot.scroll_once(host)
            time.sleep(cfg.scroll_wait_sec)
            t2, s2 = bot.scan_list(host, max_nodes=cfg.max_tree_nodes)
            fp2 = _screen_fingerprint(t2)
            if _same_screen(fp, fp2):
                break
            titles, times, fp = t2, s2, fp2
        bot.scroll_to_top(host, wait=cfg.scroll_wait_sec)
        titles, times = bot.scan_list(host, max_nodes=cfg.max_tree_nodes)
    elif cfg.deep_scroll_screens > 0:
        scrolled = False
        refocus_retried = False

        def _scroll_and_scan():
            bot.scroll_once(host)
            time.sleep(cfg.scroll_wait_sec)
            t2, s2 = bot.scan_list(host, max_nodes=cfg.max_tree_nodes)
            return t2, s2, _screen_fingerprint(t2)

        for screen in range(1, cfg.deep_scroll_screens + 1):
            dates_now = _pairs_and_dates(titles, times)[1]
            real = [d for d in dates_now if d]
            oldest = min(real) if real else "?"
            if _covers_cutoff(dates_now, cutoff):
                dbg("第%d屏前判定: 可见%d条 最旧%s ≤ 截止%s → 已覆盖,停滚",
                    screen, len(titles), oldest, cutoff)
                break
            dbg("第%d屏前判定: 可见%d条 top%.0f 最旧%s > 截止%s → 下滚一屏",
                screen, len(titles), fp[1], oldest, cutoff)
            t2, s2, fp2 = _scroll_and_scan()
            if _same_screen(fp, fp2) and not refocus_retried:
                # 「未移动」也可能是滚轮通道偶发失效(退前台被抢等),
                # 重试一屏再判,避免把偶发吞轮当成真触底
                refocus_retried = True
                dbg("视口未移动 → 重试一屏")
                t2, s2, fp2 = _scroll_and_scan()
            if _same_screen(fp, fp2):
                dbg("滚动后视口未移动(%d条,top %.0f) → 判触底,停滚",
                    len(t2), fp2[1])
                break  # 触底,没有更多可加载
            titles, times, fp = t2, s2, fp2
            scrolled = True
            dbg("滚后可见 %d 条 top%.0f", len(titles), fp[1])
        if scrolled:
            bot.scroll_to_top(host, wait=cfg.scroll_wait_sec)
            titles, times = bot.scan_list(host, max_nodes=cfg.max_tree_nodes)
            dbg("回顶重扫: %d 条", len(titles))
    # 「余下N篇」折叠组:多图文只显示头条,余篇收在折叠条里不展开就漏抓
    # (2026-09-04 真机:中金点睛首屏 4 组折叠=漏 4 篇)。深滚回顶后树里
    # 已含窗口内全部组卡,UIA Invoke 后台展开,展开后重扫一遍。
    n_exp = bot.expand_fold_bars(host, max_nodes=cfg.max_tree_nodes)
    if n_exp:
        titles, times = bot.scan_list(host, max_nodes=cfg.max_tree_nodes)
        dbg("展开「余下N篇」折叠 %d 组 → 重扫 %d 条", n_exp, len(titles))
    pairs, dates = _pairs_and_dates(titles, times)
    return titles, pairs, dates


def _make_pusher(notify_cfg: NotifyConfig, log: logging.Logger):
    """逐篇推送回调;未启用或缺 webhook 返回 None(enabled 且空 webhook
    已被 load_config 拦截,此处兜底照顾直接构造 CrawlConfig 的调用方)。"""
    if not notify_cfg.enabled or not notify_cfg.webhook:
        return None

    def push(row: dict) -> None:
        ok, msg = notify_mod.send_article(notify_cfg, row, log=log)
        if ok:
            log.info("[钉钉] 已推送: %s", row["title"][:40])
        else:
            log.warning("[钉钉] 推送失败(%s): %s", msg, row["title"][:40])

    return push


def process_account(store: Store, cfg: CrawlConfig, name: str,
                    log: logging.Logger, position: str | None = None,
                    push=None) -> dict:
    """抓一个账号,返回 {ok, new, upgraded, pending, message}。

    position 为「1/2」形式的序号(仅用于日志);主页每轮都经搜索重新打开
    (不复用已开的旧 tab):旧 tab 列表不刷新,会导致每轮扫到同一份旧列表、
    0 新增、水位线永久冻结。push 非空时,本轮取到 URL 的新增/升级文章
    逐篇立即推送钉钉(推送失败仅告警,不影响抓取)。
    """
    log.info("[%s] 开始处理账号%s", name, f"({position})" if position else "")
    acc_id = store.get_or_create_account(name)
    watermark = store.watermark(acc_id)
    cutoff = None
    if watermark:
        cutoff = (date.fromisoformat(watermark) -
                  timedelta(days=cfg.overlap_days)).isoformat()

    # 打开主页前先收掉上次崩溃/强杀遗留的页签(2026-09-04 需求「抓完即关」):
    # 同账号旧主页 tab 不清,find_profile_host 可能命中旧 tab(列表永不刷新,
    # 水位线被冻结);残留文章 tab 会让首篇提取触发残留防护甚至整篇 pending。
    bot.close_article_tabs(max_close=3, wait=cfg.close_tab_wait_sec)
    bot.close_profile_tab(name, wait=cfg.close_tab_wait_sec)
    # 打开主页带失败早退,手动计时(失败打「失败 用时」而非「完成」)
    t_prof = time.perf_counter()
    log.info("[%s] 搜索并打开主页 …", name)
    ok, msg = bot.search_open_profile(name)
    if not ok and bot.SEARCH_PAGE_MISSING in (msg or ""):
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
        log.info("[%s] 搜索并打开主页 失败 用时%s: %s", name,
                 fmt_duration(time.perf_counter() - t_prof), msg)
        bot.close_profile_tab(name, wait=cfg.close_tab_wait_sec)  # 尽力清理半开 tab
        return {"ok": False, "new": 0, "upgraded": 0, "pending": 0, "message": msg}
    w, host = bot.find_profile_host(account=name, kicks=cfg.kick_retry)
    if host is None:
        log.info("[%s] 搜索并打开主页 失败 用时%s: 主页已搜索但未就绪", name,
                 fmt_duration(time.perf_counter() - t_prof))
        bot.close_profile_tab(name, wait=cfg.close_tab_wait_sec)  # 尽力清理
        return {"ok": False, "new": 0, "upgraded": 0, "pending": 0,
                "message": "主页已搜索但未就绪"}
    if not bot.close_article_tabs(max_close=2, wait=cfg.close_tab_wait_sec):
        log.warning("[%s] 存在残留文章 tab,继续(提取前仍会校验)", name)
    log.info("[%s] 搜索并打开主页 完成 用时%s", name,
             fmt_duration(time.perf_counter() - t_prof))

    with log_step(log, "[%s] 列表扫描", name) as scanned:
        title_ctrls, pairs, dates = _collect_list(cfg, host, cutoff, log=log)
        scanned.append(f"{len(pairs)}条")
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
    total = len(pairs)
    for pos, (t_ctrl, (title, date_text)) in enumerate(zip(title_ctrls, pairs), 1):
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
        t_art = time.perf_counter()
        log.info("[%s] 第%d/%d篇《%.20s》打开文章页提取URL …", name, pos, total, title)
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
        # 结果大类(与 url_state 互补:url= 交代 pending 细因,→ 交代入库走向)
        if result == "new" and canon:
            outcome = "新增"
        elif result == "new" and nodes == -2:
            outcome = "待补-标题不符"
        elif result == "new" and nodes == -1:
            outcome = "待补-未打开"
        elif result == "new":
            outcome = "待补"
        elif result == "upgraded":
            outcome = "升级"
        else:
            outcome = "已存在"
        log.info("[%s] + %s (%s) url=%s → %s(用时%s)", name, title[:40],
                 date_text or "?", url_state, outcome,
                 fmt_duration(time.perf_counter() - t_art))
        if push and canon and result in ("new", "upgraded"):
            # 逐篇即推不聚合(用户要求);send_article 内部绝不抛异常
            push({"account": name, "title": title, "url": canon,
                  "date_text": date_text or ""})
        processed += 1

    if max_date:
        store.set_watermark(acc_id, max_date)
    store.mark_crawled(acc_id)
    if not bot.close_profile_tab(name, wait=cfg.close_tab_wait_sec):
        log.warning("[%s] 主页 tab 未能关闭(不影响数据)", name)
    return {"ok": True, "new": new, "upgraded": upgraded, "pending": pending,
            "message": f"扫描{len(pairs)}条,新增{new},补URL{upgraded},待补{pending}"}


def sync_mysql(cfg: CrawlConfig, conn, log: logging.Logger) -> None:
    """轮末把 SQLite 镜像进 MySQL(单向,SQLite 是主库;见 mysql_sync)。

    失败仅告警不重抛:镜像库不可用不能拖垮抓取主流程,缺行下轮自动补齐。
    """
    if not (cfg.mysql and cfg.mysql.enabled):
        return
    try:
        mysql_sync.sync_store(cfg.mysql, conn, log=log)
    except Exception:
        log.exception("[MySQL] 同步失败(SQLite 不受影响,下轮自动补齐)")


def _attempt_account(store: Store, cfg: CrawlConfig, name: str,
                     log: logging.Logger, position: str | None,
                     push) -> tuple[dict, int]:
    """处理一个账号,失败按 cfg.fail_retry 重试(每次重试前暂停
    fail_retry_wait_sec 秒);返回 (最终状态, 实际尝试次数)。

    重试窗口内成功即止;逐次失败打「第N次尝试失败」行,让时间线可追。
    异常与失败同等待遇(异常轮也尽力收掉半开的主页 tab,防 tab 堆积)。
    """
    t0 = time.perf_counter()
    st: dict = {"ok": False, "new": 0, "upgraded": 0, "pending": 0,
                "message": "未执行"}
    attempts = 0
    for attempt in range(cfg.fail_retry + 1):
        attempts += 1
        try:
            st = process_account(store, cfg, name, log,
                                 position=position, push=push)
        except Exception:
            log.exception("账号 %s 处理异常", name)
            st = {"ok": False, "new": 0, "upgraded": 0, "pending": 0,
                  "message": "异常(见日志)"}
            try:  # 异常轮也要尽力收掉半开的页签,防 tab 堆积(文章+主页都收)
                bot.close_article_tabs(max_close=2, wait=cfg.close_tab_wait_sec)
                bot.close_profile_tab(name, wait=cfg.close_tab_wait_sec)
            except Exception:
                pass
        if st["ok"]:
            tail = f",第{attempts}次尝试成功" if attempts > 1 else ""
            log.info("账号 %s: %s(用时%s%s)", name, st["message"],
                     fmt_duration(time.perf_counter() - t0), tail)
            return st, attempts
        if attempt < cfg.fail_retry:
            log.info("[账号 %s] 第%d次尝试失败: %s → 停 %.0fs 后重试",
                     name, attempts, st["message"], cfg.fail_retry_wait_sec)
            time.sleep(cfg.fail_retry_wait_sec)
    return st, attempts


def _failure_alert_text(name: str, reason: str, attempts: int) -> str:
    """账号最终失败(重试耗尽)的钉钉告警正文。"""
    return "\n".join([
        "### 公众号抓取失败",
        f"- 账号: **{name}**",
        f"- 原因: {reason}",
        f"- 共尝试 {attempts} 次(含重试 {attempts - 1} 次)仍失败,本轮放弃;"
        "下一轮计划任务会自动重抓",
    ])


def _run_summary_text(ok_names: list[str], failed: list[tuple[str, str]],
                      new_n: int, total: int) -> str:
    """轮末总结正文:成功数/列表、新增篇数、失败数/名单/原因。"""
    lines = [
        "### 本轮抓取总结",
        f"- 成功账号: **{len(ok_names)}/{total}** 个",
        f"- 新增文章: **{new_n}** 篇",
        f"- 失败账号: **{len(failed)}** 个",
    ]
    for n, r in failed:
        lines.append(f"- 失败: {n} —— {r}")
    if ok_names:
        lines.append("\n成功列表: " + "、".join(ok_names))
    return "\n".join(lines)


def _notify_failure(cfg: CrawlConfig, name: str, reason: str,
                    attempts: int, log: logging.Logger) -> None:
    """账号最终失败即刻推送钉钉(带 @);失败不影响主流程。"""
    ok, msg = notify_mod.send_markdown(
        cfg.notify, f"抓取失败: {name}",
        _failure_alert_text(name, reason, attempts), mention=True, log=log)
    if ok:
        log.info("[钉钉] 失败告警已推送: %s", name)
    else:
        log.warning("[钉钉] 失败告警推送失败(%s): %s", msg, name)


def _notify_summary(cfg: CrawlConfig, ok_names: list[str],
                    failed: list[tuple[str, str]], new_n: int, total: int,
                    log: logging.Logger) -> None:
    """轮末总结:打日志 + 推钉钉(信息性消息,不带 @)。"""
    log.info("本轮总结: 成功%d/%d 新增%d篇 失败%d",
             len(ok_names), total, new_n, len(failed))
    if ok_names:
        log.info("  成功列表: %s", "、".join(ok_names))
    for n, r in failed:
        log.info("  失败: %s —— %s", n, r)
    ok, msg = notify_mod.send_markdown(
        cfg.notify, "本轮抓取总结",
        _run_summary_text(ok_names, failed, new_n, total),
        mention=False, log=log)
    if ok:
        log.info("[钉钉] 轮末总结已推送")
    else:
        log.warning("[钉钉] 轮末总结推送失败(%s)", msg)


def run(cfg: CrawlConfig, only_account: str | None = None) -> int:
    log = setup_logging(cfg.log_dir)
    t_run = time.perf_counter()
    rep = version_check.check_environment(
        cfg.process_name, cfg.exe_path, cfg.expected_version_prefix)
    log.info("环境: %s", rep["message"])
    if not rep["ok"]:
        log.error("微信未就绪,本轮中止(请登录微信后等待下一轮)")
        return 1
    names = [only_account] if only_account else list(cfg.accounts)
    if only_account is None:
        random.shuffle(names)  # 全量轮乱序,模拟真人
    log.info("本轮开始: 共%d个账号%s", len(names),
             f"(仅 {only_account})" if only_account else "")
    store = Store(cfg.db_path)
    run_id = store.start_run()
    push = _make_pusher(cfg.notify, log)  # 未启用→None;推送不影响抓取主流程
    ok_n = fail_n = new_n = 0
    ok_names: list[str] = []
    failed: list[tuple[str, str]] = []
    try:
        for i, name in enumerate(names):
            if i:
                gap = random.uniform(cfg.account_gap_min_sec, cfg.account_gap_max_sec)
                log.info("等待 %.1fs 后处理下一账号(防风控)", gap)  # 先说明再睡,时间线不断档
                time.sleep(gap)
            log.info("=== 账号[%d/%d] %s ===", i + 1, len(names), name)
            st, attempts = _attempt_account(store, cfg, name, log,
                                            position=f"{i + 1}/{len(names)}",
                                            push=push)
            ok_n += 1 if st["ok"] else 0
            fail_n += 0 if st["ok"] else 1
            new_n += st["new"]
            if st["ok"]:
                ok_names.append(name)
            else:
                failed.append((name, st["message"]))
                log.warning("[账号 %s] 重试%d次后仍失败,本轮放弃",
                            name, attempts - 1)
                _notify_failure(cfg, name, st["message"], attempts, log)
    finally:
        try:  # 轮末兜底清扫(2026-09-04 需求):任何退出路径都把残留文章页签收掉
            if bot.close_article_tabs(max_close=3, wait=cfg.close_tab_wait_sec):
                log.info("[轮末] 页签清扫完成,无残留文章页")
            else:
                log.warning("[轮末] 仍有残留文章页签(不影响数据)")
        except Exception:
            log.exception("[轮末] 页签清扫异常(不影响数据)")
        store.finish_run(run_id, ok_count=ok_n, fail_count=fail_n, new_count=new_n)
        sync_mysql(cfg, store.conn, log)  # MySQL 镜像;store 关闭前读 SQLite
        store.close()
    log.info("本轮完成: 成功%d 失败%d 新增%d(总耗时%s)", ok_n, fail_n, new_n,
             fmt_duration(time.perf_counter() - t_run))
    _notify_summary(cfg, ok_names, failed, new_n, len(names), log)
    return 0 if fail_n == 0 else 1
