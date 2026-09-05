"""MySQL 持久层测试(独立测试库 wxchat_crawler_test,不触主库 test)。

测试表常驻不清理(绝不 DROP/TRUNCATE):账号名与 dedup 键(canonical/
fallback)都带每测唯一 uuid 后缀 —— dedup_key 是全库唯一约束,常量键重跑
必撞上一轮的行;标题/日期等账号作用域字段可保持常量。断言一律按
account_id 过滤,不做全表计数。
"""
import uuid
from dataclasses import replace

from src.db import Store


def _fb(tag=""):
    """每测唯一的 fallback 键。"""
    return f"t|{uuid.uuid4().hex[:8]}{tag}"


def test_account_idempotent(store, make_name):
    n1, n2 = make_name("甲"), make_name("乙")
    a1 = store.get_or_create_account(n1)
    a2 = store.get_or_create_account(n1)
    b = store.get_or_create_account(n2)
    assert a1 == a2
    assert a1 != b


def test_upsert_new_exists_upgrade(store, rows, make_name, make_canon):
    acc = store.get_or_create_account(make_name("中金点睛"))
    fb = _fb()
    assert store.upsert_article(acc, fb, None, "标题", "8月23日", None) == "new"
    assert store.upsert_article(acc, fb, None, "标题", "8月23日", None) == "exists"
    # 同文补到 URL → upgraded
    canon = make_canon()
    assert store.upsert_article(acc, fb, canon, "标题", "8月23日", None) == "upgraded"
    assert store.upsert_article(acc, fb, canon, "标题", "8月23日", None) == "exists"
    recs = rows(f"SELECT dedup_key, url_status FROM `{store.cfg.table_articles}` "
                "WHERE account_id=%s", (acc,))
    assert recs[0]["dedup_key"] == canon
    assert recs[0]["url_status"] == "ok"


def test_upgrade_collision_upgrades_in_place(store, rows, make_name, make_canon):
    """升级撞 canonical(被他行持有)→ 原地升 URL/状态,保留原 dedup_key。"""
    acc = store.get_or_create_account(make_name("中金点睛"))
    f1, f2 = _fb("1"), _fb("2")
    canon = make_canon()
    assert store.upsert_article(acc, f1, None, "A", None, None) == "new"
    # 另一行先持有该 canonical
    assert store.upsert_article(acc, f2, canon, "B", None, None) == "new"
    # 升级 pending 行时 dedup_key 碰撞 → 原地升级(pending → ok),不抛异常
    assert store.upsert_article(acc, f1, canon, "A", None, None) == "upgraded"
    rec = rows(f"SELECT dedup_key, url, url_status FROM `{store.cfg.table_articles}` "
               "WHERE account_id=%s AND fallback_key=%s", (acc, f1))[0]
    assert rec["url_status"] == "ok"
    assert rec["url"] == canon
    assert rec["dedup_key"] == f1  # canonical 被占,保留本行原 dedup_key


def test_insert_collision_other_article_gets_own_row(store, rows, make_name, make_canon):
    """同账号两篇撞同一 canonical(异常数据):后到者仍落自己的终态行。"""
    acc = store.get_or_create_account(make_name("中金点睛"))
    fa, fbx = _fb("a"), _fb("b")
    canon = make_canon()
    assert store.upsert_article(acc, fa, canon, "A", None, None) == "new"
    assert store.upsert_article(acc, fbx, canon, "B", None, None) == "new"
    recs = rows(f"SELECT dedup_key, url, url_status FROM `{store.cfg.table_articles}` "
                "WHERE account_id=%s ORDER BY id", (acc,))
    assert len(recs) == 2
    assert recs[0]["dedup_key"] == canon
    assert recs[1]["dedup_key"] == fbx  # 退化为 fallback 键,canonical 不被覆盖
    assert recs[1]["url"] == canon and recs[1]["url_status"] == "ok"


def test_insert_genuine_duplicate_returns_exists(store, rows, make_name, make_canon):
    """同账号同 fallback_key(真重复)→ exists,不重复插行。"""
    acc = store.get_or_create_account(make_name("中金点睛"))
    fb = _fb("a")
    canon = make_canon()
    assert store.upsert_article(acc, fb, canon, "A", None, None) == "new"
    assert store.upsert_article(acc, fb, canon, "A", None, None) == "exists"
    recs = rows(f"SELECT 1 FROM `{store.cfg.table_articles}` WHERE account_id=%s",
                (acc,))
    assert len(recs) == 1


def test_seen_fallbacks(store, make_name, make_canon):
    acc = store.get_or_create_account(make_name("中金点睛"))
    fa, fbx = _fb("a"), _fb("b")
    store.upsert_article(acc, fa, None, "A", None, None)
    store.upsert_article(acc, fbx, make_canon(), "B", None, None)
    assert store.seen_fallbacks(acc) == {fa, fbx}


def test_pending_retry_upgrades_same_row(store, rows, make_name, make_canon):
    acc = store.get_or_create_account(make_name("中金点睛"))
    fb, canon = _fb("1"), make_canon()
    assert store.upsert_article(acc, fb, None, "标题", None, None) == "new"
    # 次日重试:同 fb 提取到 URL → 升级同一行,而非新增
    assert store.upsert_article(acc, fb, canon, "标题", None, None) == "upgraded"
    recs = rows(f"SELECT url, url_status FROM `{store.cfg.table_articles}` "
                "WHERE account_id=%s", (acc,))
    assert len(recs) == 1
    assert recs[0]["url"] == canon
    assert recs[0]["url_status"] == "ok"


def test_canonical_dedup_on_drifted_fallback(store, rows, make_name, make_canon):
    acc = store.get_or_create_account(make_name("中金点睛"))
    fx, fy = _fb("x"), _fb("y")
    canon = make_canon()
    assert store.upsert_article(acc, fx, None, "A", None, None) == "new"
    # fallback 文本漂移(标题|'' → 标题|日期),canonical 命中 pending 旧行 → 升级
    assert store.upsert_article(acc, fy, canon, "A", "8月1日", "2026-08-01") == "upgraded"
    recs = rows(f"SELECT fallback_key, dedup_key, url_status FROM "
                f"`{store.cfg.table_articles}` WHERE account_id=%s", (acc,))
    assert len(recs) == 1
    assert recs[0]["fallback_key"] == fy  # fb 同步刷新,下轮不再重复打开文章页
    assert recs[0]["dedup_key"] == canon
    assert recs[0]["url_status"] == "ok"


def test_canonical_collision_with_ok_row_returns_exists(store, rows, make_name, make_canon):
    acc = store.get_or_create_account(make_name("中金点睛"))
    canon = make_canon()
    assert store.upsert_article(acc, _fb("1"), canon, "A", None, None) == "new"
    assert store.upsert_article(acc, _fb("2"), canon, "B", None, None) == "new"
    recs = rows(f"SELECT 1 FROM `{store.cfg.table_articles}` WHERE account_id=%s",
                (acc,))
    assert len(recs) == 2


def test_cross_account_same_canonical_both_stored(store, rows, make_name, make_canon):
    """转载场景:两账号同一 canonical → 各得一条终态行,后到者返回 'new'。"""
    acc_a = store.get_or_create_account(make_name("中金点睛"))
    acc_b = store.get_or_create_account(make_name("郭磊宏观茶座"))
    f_a, f_b = _fb("a1"), _fb("b1")
    canon = make_canon()
    assert store.upsert_article(acc_a, f_a, canon, "同文", None, None) == "new"
    assert store.upsert_article(acc_b, f_b, canon, "同文", None, None) == "new"
    for acc, fb in ((acc_a, f_a), (acc_b, f_b)):
        recs = rows(f"SELECT url, url_status, fallback_key FROM "
                    f"`{store.cfg.table_articles}` WHERE account_id=%s", (acc,))
        assert len(recs) == 1
        assert recs[0]["url"] == canon
        assert recs[0]["url_status"] == "ok"
        assert recs[0]["fallback_key"] == fb
        assert fb in store.seen_fallbacks(acc, status="ok")


def test_cross_account_rerun_is_exists(store, rows, make_name, make_canon):
    """账号 B 二轮再遇同文:fb 已入库(状态 ok)→ 'exists',不再重开文章页。"""
    acc_b = store.get_or_create_account(make_name("郭磊宏观茶座"))
    fb, canon = _fb("b1"), make_canon()
    assert store.upsert_article(acc_b, fb, canon, "同文", None, None) == "new"
    assert fb in store.seen_fallbacks(acc_b, status="ok")  # 编排层据此跳过
    assert store.upsert_article(acc_b, fb, canon, "同文", None, None) == "exists"
    recs = rows(f"SELECT 1 FROM `{store.cfg.table_articles}` WHERE account_id=%s",
                (acc_b,))
    assert len(recs) == 1


def test_cross_account_upgrade_path_collision(store, rows, make_name, make_canon):
    """B 先有 pending 行,补 URL 时 canonical 被 A 持有 → 原地升级为 ok 行。"""
    acc_a = store.get_or_create_account(make_name("中金点睛"))
    acc_b = store.get_or_create_account(make_name("郭磊宏观茶座"))
    f_a, f_b = _fb("a1"), _fb("b1")
    canon = make_canon()
    assert store.upsert_article(acc_a, f_a, canon, "同文", None, None) == "new"
    assert store.upsert_article(acc_b, f_b, None, "同文", None, None) == "new"
    assert store.upsert_article(acc_b, f_b, canon, "同文", None, None) == "upgraded"
    rec = rows(f"SELECT dedup_key, url, url_status FROM `{store.cfg.table_articles}` "
               "WHERE account_id=%s", (acc_b,))[0]
    assert rec["url_status"] == "ok"
    assert rec["url"] == canon
    assert rec["dedup_key"] == f_b  # canonical 被 A 持有,B 行保留 fallback 键
    assert f_b in store.seen_fallbacks(acc_b, status="ok")


def test_seen_fallbacks_status_filter(store, make_name, make_canon):
    acc = store.get_or_create_account(make_name("中金点睛"))
    fb_pending, fb_ok = _fb("p"), _fb("o")
    store.upsert_article(acc, fb_pending, None, "A", None, None)  # pending
    store.upsert_article(acc, fb_ok, make_canon(), "B", None, None)  # ok
    assert store.seen_fallbacks(acc) == {fb_pending, fb_ok}
    assert store.seen_fallbacks(acc, status="ok") == {fb_ok}
    assert store.seen_fallbacks(acc, status="pending") == {fb_pending}


def test_watermark_keeps_max(store, make_name):
    acc = store.get_or_create_account(make_name("中金点睛"))
    store.set_watermark(acc, "2026-09-01")
    store.set_watermark(acc, "2026-08-20")
    assert store.watermark(acc) == "2026-09-01"
    assert store.watermark(store.get_or_create_account(make_name("乙"))) is None


def test_mark_crawled(store, rows, make_name):
    acc = store.get_or_create_account(make_name("中金点睛"))
    store.mark_crawled(acc)
    rec = rows(f"SELECT last_crawled_at FROM `{store.cfg.table_accounts}` "
               "WHERE id=%s", (acc,))[0]
    assert rec["last_crawled_at"]


def test_run_lifecycle(store, rows):
    rid = store.start_run()
    store.finish_run(rid, ok_count=2, fail_count=1, new_count=7)
    rec = rows(f"SELECT * FROM `{store.cfg.table_runs}` WHERE id=%s", (rid,))[0]
    assert rec["ok_count"] == 2
    assert rec["fail_count"] == 1
    assert rec["new_count"] == 7
    assert rec["finished_at"]


def test_legacy_id_column_gets_auto_increment(mysql_ready, make_name):
    """镜像期遗留表 id 列无 AUTO_INCREMENT(镜像期显式带 id 插入)→ Store
    初始化查 information_schema 后补 ALTER(纯元数据,不动数据):历史行
    id 保留,新行自增接续。表名带 uuid,用后留存于测试库(不 DROP)。"""
    import pymysql

    suffix = uuid.uuid4().hex[:8]
    cfg = replace(mysql_ready, table_accounts=f"wt_lg_acc_{suffix}",
                  table_articles=f"wt_lg_art_{suffix}",
                  table_runs=f"wt_lg_run_{suffix}")
    raw = pymysql.connect(host=cfg.host, port=cfg.port, user=cfg.user,
                          password=cfg.password, database=cfg.database,
                          charset="utf8mb4")
    with raw.cursor() as c:
        c.execute(f"""CREATE TABLE `{cfg.table_articles}` (
            id INT PRIMARY KEY, account_id INT, dedup_key VARCHAR(191),
            fallback_key VARCHAR(191), url TEXT, title VARCHAR(512),
            date_text VARCHAR(64), publish_date VARCHAR(32),
            url_status VARCHAR(16), created_at VARCHAR(32))""")
        c.execute(f"INSERT INTO `{cfg.table_articles}` "
                  "(id, account_id, dedup_key, fallback_key, title, url_status) "
                  "VALUES (7, 1, 'k', 'f', '遗留行', 'pending')")
    raw.commit()
    raw.close()

    s = Store(cfg)
    try:
        acc = s.get_or_create_account(make_name("遗留"))
        assert s.upsert_article(acc, _fb("lg"), None, "新行", None, None) == "new"
        with s.conn.cursor() as c:
            c.execute(f"SELECT id FROM `{cfg.table_articles}` ORDER BY id")
            ids = [r["id"] for r in c.fetchall()]
        assert ids[0] == 7                    # 历史行 id 不变
        assert len(ids) == 2 and ids[1] > 7   # 新行自增接续
    finally:
        s.close()
