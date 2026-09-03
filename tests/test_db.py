"""SQLite 持久层测试(临时库,不触真实 data/)。"""
import pytest

from src.db import Store


@pytest.fixture()
def store(tmp_path):
    s = Store(tmp_path / "t.db")
    yield s
    s.close()


def test_account_idempotent(store):
    a1 = store.get_or_create_account("中金点睛")
    a2 = store.get_or_create_account("中金点睛")
    b = store.get_or_create_account("郭磊宏观茶座")
    assert a1 == a2
    assert a1 != b


def test_upsert_new_exists_upgrade(store):
    acc = store.get_or_create_account("中金点睛")
    fb = "t|abc"
    assert store.upsert_article(acc, fb, None, "标题", "8月23日", None) == "new"
    assert store.upsert_article(acc, fb, None, "标题", "8月23日", None) == "exists"
    # 同文补到 URL → upgraded
    canon = "https://mp.weixin.qq.com/s?__biz=x&mid=1&idx=1&sn=s&chksm=c"
    assert store.upsert_article(acc, fb, canon, "标题", "8月23日", None) == "upgraded"
    assert store.upsert_article(acc, fb, canon, "标题", "8月23日", None) == "exists"
    rows = store.conn.execute("SELECT dedup_key, url, url_status FROM articles").fetchall()
    assert rows[0]["dedup_key"] == canon
    assert rows[0]["url_status"] == "ok"


def test_upgrade_collision_upgrades_in_place(store):
    """升级撞 canonical(被他行持有)→ 原地升 URL/状态,保留原 dedup_key。"""
    acc = store.get_or_create_account("中金点睛")
    canon = "https://mp.weixin.qq.com/s?__biz=x&mid=1&idx=1&sn=s&chksm=c"
    assert store.upsert_article(acc, "t|f1", None, "A", None, None) == "new"
    # 另一行先持有该 canonical
    assert store.upsert_article(acc, "t|f2", canon, "B", None, None) == "new"
    # 升级 pending 行时 dedup_key 碰撞 → 原地升级(pending → ok),不抛异常
    assert store.upsert_article(acc, "t|f1", canon, "A", None, None) == "upgraded"
    row = store.conn.execute(
        "SELECT dedup_key, url, url_status FROM articles WHERE fallback_key='t|f1'").fetchone()
    assert row["url_status"] == "ok"
    assert row["url"] == canon
    assert row["dedup_key"] == "t|f1"  # canonical 被占,保留本行原 dedup_key


def test_insert_collision_other_article_gets_own_row(store):
    """同账号两篇撞同一 canonical(异常数据):后到者仍落自己的终态行。"""
    acc = store.get_or_create_account("中金点睛")
    canon = "https://mp.weixin.qq.com/s?__biz=x&mid=1&idx=1&sn=s&chksm=c"
    assert store.upsert_article(acc, "t|a", canon, "A", None, None) == "new"
    assert store.upsert_article(acc, "t|b", canon, "B", None, None) == "new"
    rows = store.conn.execute(
        "SELECT dedup_key, url, url_status FROM articles ORDER BY id").fetchall()
    assert len(rows) == 2
    assert rows[0]["dedup_key"] == canon
    assert rows[1]["dedup_key"] == "t|b"  # 退化为 fallback 键,canonical 不被覆盖
    assert rows[1]["url"] == canon and rows[1]["url_status"] == "ok"


def test_insert_genuine_duplicate_returns_exists(store):
    """同账号同 fallback_key(真重复)→ exists,不重复插行。"""
    acc = store.get_or_create_account("中金点睛")
    canon = "https://mp.weixin.qq.com/s?__biz=x&mid=1&idx=1&sn=s&chksm=c"
    assert store.upsert_article(acc, "t|a", canon, "A", None, None) == "new"
    assert store.upsert_article(acc, "t|a", canon, "A", None, None) == "exists"
    assert len(store.conn.execute("SELECT 1 FROM articles").fetchall()) == 1


def test_seen_fallbacks(store):
    acc = store.get_or_create_account("中金点睛")
    store.upsert_article(acc, "t|a", None, "A", None, None)
    store.upsert_article(acc, "t|b", "https://mp.weixin.qq.com/s?__biz=x&mid=1&idx=1&sn=s&chksm=c",
                         "B", None, None)
    seen = store.seen_fallbacks(acc)
    assert seen == {"t|a", "t|b"}


def test_pending_retry_upgrades_same_row(store):
    acc = store.get_or_create_account("中金点睛")
    canon = "https://mp.weixin.qq.com/s?__biz=1&sn=a"
    assert store.upsert_article(acc, "t|f1", None, "标题", None, None) == "new"
    # 次日重试:同 fb 提取到 URL → 升级同一行,而非新增
    assert store.upsert_article(acc, "t|f1", canon, "标题", None, None) == "upgraded"
    rows = store.conn.execute("SELECT url, url_status FROM articles").fetchall()
    assert len(rows) == 1
    assert rows[0]["url"] == canon
    assert rows[0]["url_status"] == "ok"


def test_canonical_dedup_on_drifted_fallback(store):
    acc = store.get_or_create_account("中金点睛")
    canon = "https://mp.weixin.qq.com/s?__biz=1&sn=a"
    assert store.upsert_article(acc, "t|x", None, "A", None, None) == "new"
    # fallback 文本漂移(标题|'' → 标题|日期),canonical 命中 pending 旧行 → 升级
    assert store.upsert_article(acc, "t|y", canon, "A", "8月1日", "2026-08-01") == "upgraded"
    assert len(store.conn.execute("SELECT 1 FROM articles").fetchall()) == 1
    row = store.conn.execute(
        "SELECT fallback_key, dedup_key, url_status FROM articles").fetchone()
    assert row["fallback_key"] == "t|y"  # fb 同步刷新,下轮不再重复打开文章页
    assert row["dedup_key"] == canon
    assert row["url_status"] == "ok"


def test_canonical_collision_with_ok_row_returns_exists(store):
    acc = store.get_or_create_account("中金点睛")
    canon = "https://mp.weixin.qq.com/s?__biz=1&sn=a"
    assert store.upsert_article(acc, "t|f1", canon, "A", None, None) == "new"
    assert store.upsert_article(acc, "t|f2", canon, "B", None, None) == "new"
    assert len(store.conn.execute("SELECT 1 FROM articles").fetchall()) == 2


def test_cross_account_same_canonical_both_stored(store):
    """转载场景:两账号同一 canonical → 各得一条终态行,后到者返回 'new'。"""
    acc_a = store.get_or_create_account("中金点睛")
    acc_b = store.get_or_create_account("郭磊宏观茶座")
    canon = "https://mp.weixin.qq.com/s?__biz=1&mid=2&idx=1&sn=shared"
    assert store.upsert_article(acc_a, "t|a1", canon, "同文", None, None) == "new"
    assert store.upsert_article(acc_b, "t|b1", canon, "同文", None, None) == "new"
    for acc, fb in ((acc_a, "t|a1"), (acc_b, "t|b1")):
        rows = store.conn.execute(
            "SELECT url, url_status, fallback_key FROM articles WHERE account_id=?",
            (acc,)).fetchall()
        assert len(rows) == 1
        assert rows[0]["url"] == canon
        assert rows[0]["url_status"] == "ok"
        assert rows[0]["fallback_key"] == fb
        assert fb in store.seen_fallbacks(acc, status="ok")


def test_cross_account_rerun_is_exists(store):
    """账号 B 二轮再遇同文:fb 已入库(状态 ok)→ 'exists',不再重开文章页。"""
    acc_b = store.get_or_create_account("郭磊宏观茶座")
    canon = "https://mp.weixin.qq.com/s?__biz=1&mid=2&idx=1&sn=shared"
    assert store.upsert_article(acc_b, "t|b1", canon, "同文", None, None) == "new"
    assert "t|b1" in store.seen_fallbacks(acc_b, status="ok")  # 编排层据此跳过
    assert store.upsert_article(acc_b, "t|b1", canon, "同文", None, None) == "exists"
    assert len(store.conn.execute(
        "SELECT 1 FROM articles WHERE account_id=?", (acc_b,)).fetchall()) == 1


def test_cross_account_upgrade_path_collision(store):
    """B 先有 pending 行,补 URL 时 canonical 被 A 持有 → 原地升级为 ok 行。"""
    acc_a = store.get_or_create_account("中金点睛")
    acc_b = store.get_or_create_account("郭磊宏观茶座")
    canon = "https://mp.weixin.qq.com/s?__biz=1&mid=2&idx=1&sn=shared"
    assert store.upsert_article(acc_a, "t|a1", canon, "同文", None, None) == "new"
    assert store.upsert_article(acc_b, "t|b1", None, "同文", None, None) == "new"
    assert store.upsert_article(acc_b, "t|b1", canon, "同文", None, None) == "upgraded"
    row = store.conn.execute(
        "SELECT dedup_key, url, url_status FROM articles WHERE account_id=?",
        (acc_b,)).fetchone()
    assert row["url_status"] == "ok"
    assert row["url"] == canon
    assert row["dedup_key"] == "t|b1"  # canonical 被 A 持有,B 行保留 fallback 键
    assert "t|b1" in store.seen_fallbacks(acc_b, status="ok")


def test_seen_fallbacks_status_filter(store):
    acc = store.get_or_create_account("中金点睛")
    store.upsert_article(acc, "t|a", None, "A", None, None)  # pending
    store.upsert_article(acc, "t|b", "https://mp.weixin.qq.com/s?__biz=1&sn=b",
                         "B", None, None)  # ok
    assert store.seen_fallbacks(acc) == {"t|a", "t|b"}
    assert store.seen_fallbacks(acc, status="ok") == {"t|b"}
    assert store.seen_fallbacks(acc, status="pending") == {"t|a"}


def test_watermark_keeps_max(store):
    acc = store.get_or_create_account("中金点睛")
    store.set_watermark(acc, "2026-09-01")
    store.set_watermark(acc, "2026-08-20")
    assert store.watermark(acc) == "2026-09-01"
    assert store.watermark(store.get_or_create_account("郭磊宏观茶座")) is None


def test_mark_crawled(store):
    acc = store.get_or_create_account("中金点睛")
    store.mark_crawled(acc)
    row = store.conn.execute("SELECT last_crawled_at FROM accounts WHERE id=?", (acc,)).fetchone()
    assert row["last_crawled_at"]


def test_run_lifecycle(store):
    rid = store.start_run()
    store.finish_run(rid, ok_count=2, fail_count=1, new_count=7)
    row = store.conn.execute("SELECT * FROM crawl_runs WHERE id=?", (rid,)).fetchone()
    assert row["ok_count"] == 2
    assert row["fail_count"] == 1
    assert row["new_count"] == 7
    assert row["finished_at"]
