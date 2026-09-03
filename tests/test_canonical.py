"""canonical 纯函数测试。URL 样本来自尖峰C 真实提取(docs/spike-findings.md)。"""
from datetime import date

from src.canonical import (canonicalize_url, find_stop_index, make_dedup_key,
                           normalize_date_text, pair_publish_dates)

RAW_URL = ("https://mp.weixin.qq.com/s?__biz=MzI3MDMzMjg0MA==&mid=2247857353&idx=2"
           "&sn=ae957b8c2f9ef7bcf0c0a1c438f2722c"
           "&chksm=eb6a7778638a3b11c8bff50586d670f685a1cf793c071895f6fa4ef18039364415213f6fecd9"
           "&scene=126&sessionid=0&clicktime=123&enterid=456&key=abc&uin=26&pass_ticket=xyz#rd")
CANON_URL = ("https://mp.weixin.qq.com/s?__biz=MzI3MDMzMjg0MA==&mid=2247857353&idx=2"
             "&sn=ae957b8c2f9ef7bcf0c0a1c438f2722c")


def test_canonicalize_real_url():
    assert canonicalize_url(RAW_URL) == CANON_URL


def test_canonical_ignores_session_chksm():
    """验收实测:同一文章每次提取的 chksm 都不同(会话签名),canonical 必须一致。"""
    other_session = RAW_URL.replace("chksm=eb6a7778", "chksm=eb7e60f5")
    assert other_session != RAW_URL
    assert canonicalize_url(other_session) == CANON_URL
    assert canonicalize_url(RAW_URL.rsplit("&chksm=", 1)[0]) == CANON_URL


def test_canonicalize_none_cases():
    assert canonicalize_url(None) is None
    assert canonicalize_url("") is None
    assert canonicalize_url("https://example.com/s?__biz=x&sn=y") is None
    assert canonicalize_url("https://mp.weixin.qq.com/s?__biz=x") is None  # 缺 sn


def test_dedup_key_prefers_url():
    assert make_dedup_key(RAW_URL, "任意标题", "8月23日") == CANON_URL


def test_canonicalize_short_link():
    """尖峰D:「复制链接」菜单只给短链 /s/<token>;token 永久,即身份。"""
    short = "https://mp.weixin.qq.com/s/MDVUlmL76lg0UsPl8KF86A"
    assert canonicalize_url(short) == short
    assert canonicalize_url("http://mp.weixin.qq.com/s/AbC_123") == \
        "https://mp.weixin.qq.com/s/AbC_123"
    assert canonicalize_url(short + "#rd") == short
    assert canonicalize_url("https://mp.weixin.qq.com/s/") is None
    assert canonicalize_url("https://example.com/s/AbC123") is None
    assert make_dedup_key(short, "任意标题", "8月23日") == short


def test_dedup_key_fallback_deterministic():
    k1 = make_dedup_key(None, "标题A", "8月23日")
    k2 = make_dedup_key(None, "标题A", "8月23日")
    k3 = make_dedup_key(None, "标题B", "8月23日")
    assert k1 == k2
    assert k1 != k3
    assert k1.startswith("t|")
    assert make_dedup_key(None, "标题A", None) != make_dedup_key(None, "标题A", "8月23日")


def test_normalize_relative(today=date(2026, 9, 3)):
    assert normalize_date_text("今天", today) == "2026-09-03"
    assert normalize_date_text("昨天", today) == "2026-09-02"
    assert normalize_date_text("前天", today) == "2026-09-01"
    # 2026-09-03 是星期四:星期四=今天会与「今天」冲突,故按上周算
    assert normalize_date_text("星期四", today) == "2026-08-27"
    assert normalize_date_text("星期一", today) == "2026-08-31"


def test_normalize_absolute(today=date(2026, 9, 3)):
    assert normalize_date_text("8月23日", today) == "2026-08-23"
    assert normalize_date_text("2025年12月31日", today) == "2025-12-31"
    assert normalize_date_text("2026-08-23", today) == "2026-08-23"
    # 跨年:今天 1 月时「12月31日」指去年
    assert normalize_date_text("12月31日", date(2026, 1, 2)) == "2025-12-31"


def test_normalize_garbage():
    assert normalize_date_text(None) is None
    assert normalize_date_text("") is None
    assert normalize_date_text("不知所谓", date(2026, 9, 3)) is None


def test_pair_publish_dates():
    titles = [("标题1", 300.0), ("标题2", 500.0), ("标题0", 100.0)]
    times = [("8月23日", 80.0), ("8月24日", 280.0)]
    out = pair_publish_dates(titles, times)
    # 按 top 排序后:标题0(100)→8月23日;标题1(300)→8月24日;标题2(500)→8月24日
    assert out == [("标题0", "8月23日"), ("标题1", "8月24日"), ("标题2", "8月24日")]


def test_pair_before_first_label():
    out = pair_publish_dates([("标题X", 10.0)], [("8月23日", 80.0)])
    assert out == [("标题X", None)]


def test_stop_index_triggers_on_streak():
    dates = ["2026-09-03", "2026-09-02", "2026-08-30", "2026-08-29", "2026-08-28"]
    # cutoff=09-01:第3、4、5条连续早于它 → 停在第5条后
    assert find_stop_index(dates, "2026-09-01", streak=3) == 5


def test_stop_index_none_resets():
    dates = ["2026-08-30", None, "2026-08-29", "2026-08-28"]
    # None 视为可能的新文章,重置连续计数:重置后仅 2 连,不触发
    assert find_stop_index(dates, "2026-09-01", streak=3) == -1


def test_stop_index_new_account():
    assert find_stop_index(["2026-08-30", "2026-08-29"], None, streak=3) == -1


def test_stop_index_partial():
    dates = ["2026-09-03", "2026-08-30", "2026-08-29"]
    assert find_stop_index(dates, "2026-09-01", streak=3) == -1  # 只有 2 连,扫完未停
