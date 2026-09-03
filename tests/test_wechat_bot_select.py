"""wechat_bot.select_trusted_url 取用策略测试(树序首取回归的锚点)。

背景:URL 采信必须同时满足「恰 1 个候选」与「归属有据(控件 Name ≈ 标题)」。
曾有回归只对 n==1 做归属校验,≥2 候选时按树遍历顺序取首 —— 污染页(内嵌
475 个 mp URL)上取到的是别人的文章,入库后还是终态 ok 行,永不重试。
scan_article_page 存的 Name 截到 40 字,这里用同样形态构造。
"""
from src.wechat_bot import select_trusted_url

TITLE = "【广发宏观陈嘉荔】沃什Jackson Hole演讲的新信号"
OWN_URL = "https://mp.weixin.qq.com/s/MDVUlmL76lg0UsPl8KF86A"


def test_single_owned_candidate_trusted():
    # (a) 唯一候选且控件 Name ≈ 页面标题 → 采信
    urls = {OWN_URL: TITLE[:40]}
    assert select_trusted_url(urls, TITLE, TITLE) == OWN_URL
    # 页面标题带排版噪声仍可双向包含
    assert select_trusted_url(urls, f"郭磊宏观茶座 {TITLE}", TITLE) == OWN_URL


def test_single_unowned_candidate_rejected():
    # (b) 唯一候选但 Name 不匹配(内嵌他人链接)→ 不采信
    urls = {"https://mp.weixin.qq.com/s?__biz=xxx": "推荐阅读:另一篇文章标题"}
    assert select_trusted_url(urls, TITLE, TITLE) is None


def test_single_candidate_no_titles_rejected():
    # (c) 唯一候选但页面标题与期望标题都拿不到 → 归属无法验证,不采信
    urls = {OWN_URL: TITLE[:40]}
    assert select_trusted_url(urls, "", None) is None
    assert select_trusted_url(urls, None, "") is None


def test_multiple_candidates_never_trusted():
    # (d) 回归锚点:≥2 候选时即便其中之一的 Name 匹配标题也绝不采信,
    # 必须丢弃全部、走「复制链接」菜单兜底(树序首≈内嵌链接)。
    urls = {
        "https://mp.weixin.qq.com/s?__biz=aaa": "推荐阅读:别的文章一",
        OWN_URL: TITLE[:40],  # 自身 URL 恰好也在树里,但不是树序首
        "https://mp.weixin.qq.com/s?__biz=ccc": "目录:第三章",
    }
    assert select_trusted_url(urls, TITLE, TITLE) is None


def test_no_candidates_rejected():
    # (e) 0 个候选 → None(调用方走菜单兜底)
    assert select_trusted_url({}, TITLE, TITLE) is None


def test_page_title_missing_falls_back_to_expected():
    # 页面标题读不到(activity-name 超预算等)时用期望标题做归属验证
    urls = {OWN_URL: TITLE[:40]}
    assert select_trusted_url(urls, "", TITLE) == OWN_URL
    # 但期望标题对不上 Name 依然不采信
    assert select_trusted_url(urls, "", "完全不同的标题") is None
