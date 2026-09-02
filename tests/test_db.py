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


def test_upgrade_collision_returns_exists(store):
    acc = store.get_or_create_account("中金点睛")
    canon = "https://mp.weixin.qq.com/s?__biz=x&mid=1&idx=1&sn=s&chksm=c"
    assert store.upsert_article(acc, "t|f1", None, "A", None, None) == "new"
    # 另一行先持有该 canonical
    assert store.upsert_article(acc, "t|f2", canon, "B", None, None) == "new"
    # 升级 pending 行时 dedup_key 碰撞 → exists(不抛异常),且该行仍为 pending
    assert store.upsert_article(acc, "t|f1", canon, "A", None, None) == "exists"
    assert store.conn.execute(
        "SELECT url_status FROM articles WHERE fallback_key='t|f1'").fetchone()["url_status"] == "pending"


def test_insert_collision_returns_exists(store):
    acc = store.get_or_create_account("中金点睛")
    canon = "https://mp.weixin.qq.com/s?__biz=x&mid=1&idx=1&sn=s&chksm=c"
    assert store.upsert_article(acc, "t|a", canon, "A", None, None) == "new"
    assert store.upsert_article(acc, "t|b", canon, "B", None, None) == "exists"
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
    assert store.upsert_article(acc, "t|f2", canon, "B", None, None) == "exists"
    assert len(store.conn.execute("SELECT 1 FROM articles").fetchall()) == 1


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
