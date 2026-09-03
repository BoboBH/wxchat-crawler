"""wechat_bot._titles_match 标题校验测试(防嵌入链接误归属的 -2 哨兵)。"""
from src.wechat_bot import _titles_match

TITLE = "【广发宏观陈嘉荔】沃什Jackson Hole演讲的新信号"


def test_exact_match():
    assert _titles_match(TITLE, TITLE)
    assert _titles_match("中金 | 9月行业配置", "中金 | 9月行业配置")


def test_containment_either_direction():
    # 文章页标题带账号前缀/后缀(排版差异):包含即算同一篇
    assert _titles_match(TITLE, f"郭磊宏观茶座 {TITLE}")
    assert _titles_match(f"{TITLE} - 微信公众平台", TITLE)


def test_whitespace_noise_ignored():
    # 空白与零宽字符全部剔除后比对(列表卡片/页面 <h1> 排版空白常不一致)
    assert _titles_match("高频数据 下的8月经济：价格篇", "高频数据下的8月经济：价格篇")
    assert _titles_match("高频数据下的8月经济：价格篇", " 高频数据　下的8月经济： 价格篇 ")
    assert _titles_match("标题A​B", "标题AB")


def test_empty_actual_cannot_verify():
    assert not _titles_match(TITLE, "")
    assert not _titles_match(TITLE, None)
    assert not _titles_match(TITLE, "   ")


def test_both_empty():
    assert not _titles_match("", "")
    assert not _titles_match(None, None)
    assert not _titles_match("", "某标题")


def test_mismatch():
    assert not _titles_match(TITLE, "高频数据下的8月经济：价格篇")
    # 仅部分字符重叠(非包含关系)不算匹配
    assert not _titles_match("宏观茶座第1期", "第2期宏观茶座")
