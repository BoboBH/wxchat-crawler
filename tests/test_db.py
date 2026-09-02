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


def test_seen_fallbacks(store):
    acc = store.get_or_create_account("中金点睛")
    store.upsert_article(acc, "t|a", None, "A", None, None)
    store.upsert_article(acc, "t|b", "https://mp.weixin.qq.com/s?__biz=x&mid=1&idx=1&sn=s&chksm=c",
                         "B", None, None)
    seen = store.seen_fallbacks(acc)
    assert seen == {"t|a", "t|b"}


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
