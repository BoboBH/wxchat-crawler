"""主页列表双模板(2026-09-05 中国银河宏观实机诊断)。

模板A(多数号):条目标题 = article__item__title,文本在自身 Name;
模板B(中国银河宏观、业谈债市、中金宏观、珮珊债券研究、一凌策略研究
等 5 个此前全败的号):根本没有 A 类节点,卡片 = article-item__body
(单连字符),标题文本挂在其无类名子 Text 上,日期文本挂在 publish_time
的子 Text 上。readiness 谓词(find_profile_host)与 scan_list 必须两种
模板都认,否则 B 号全军「主页未就绪」。

fake 树按实机解剖结果搭(容器 Name 全空,文本只在叶子 Text);
walk_ctrls 走 GetChildren,全程不触真实 UIA。
"""
import types

from src import wechat_bot as bot


def _ctrl(cls="", name="", top=0.0, children=(), **rect_extra):
    """极简 UIA 控件替身:ClassName/Name/BoundingRectangle/GetChildren。"""
    c = types.SimpleNamespace(ClassName=cls, Name=name,
                              GetChildren=lambda: list(children))
    c.BoundingRectangle = types.SimpleNamespace(top=top, **rect_extra)
    return c


# 实机结构:卡片 = list_item first_little_card
#   ├─ publish_time(容器 Name 空)→ Text('8月17日')
#   └─ article-item__image-content
#       └─ article-item__body(容器 Name 空)
#           ├─ Text('【中国银河宏观】K型分化…')      ← 文档序第1个非空 = 标题
#           └─ article-item__desc → Text('阅读1245赞15')  ← 阅读数,不能当标题
def _template_b_host(title="【中国银河宏观】K型分化加深,内需仍待企稳",
                     date="8月17日", title_top=300.0):
    date_leaf = _ctrl(name=date, top=title_top - 60.0)
    body_leaf = _ctrl(name=title, top=title_top)
    desc_leaf = _ctrl(name="阅读1245赞15", top=title_top + 40.0)
    return _ctrl(
        cls="list_item first_little_card", top=title_top,
        children=[
            _ctrl(cls=bot.CLASS_TIME, top=title_top - 70.0,
                  children=[date_leaf]),
            _ctrl(cls="article-item article-item__image-content",
                  top=title_top,
                  children=[_ctrl(cls=bot.CLASS_ITEM_BODY, top=title_top,
                                  children=[
                                      body_leaf,
                                      _ctrl(cls="article-item__desc",
                                            top=title_top + 40.0,
                                            children=[desc_leaf]),
                                  ])]),
        ])


# 模板A:标题文本在 article__item__title 自身 Name;日期在组头 publish_time
def _template_a_host(title="搞好中国式现代化理论研究", date="9月1日",
                     title_top=200.0):
    return _ctrl(
        cls=bot.CLASS_CARD, top=title_top,
        children=[
            _ctrl(cls=bot.CLASS_TITLE, name=title, top=title_top),
            _ctrl(cls=bot.CLASS_TIME, name=date, top=title_top - 40.0),
        ])


def test_scan_list_template_b_extracts_text_leaf():
    leaf = None
    host = _template_b_host()

    def find(c):
        nonlocal leaf
        if (c.Name or "").startswith("【中国银河宏观】"):
            leaf = c

    for c in bot.walk_ctrls(host):
        find(c)
    titles, times = bot.scan_list(host)
    # 标题控件必须是标题叶子本尊(open_article_and_get_url 要从它向上找卡),
    # 且绝不是同卡里文档序靠后的「阅读N赞M」(2026-09-05 真跑曾全量抓错的回归锚)
    assert len(titles) == 1 and titles[0] is leaf
    assert all(not (t.Name or "").startswith("阅读") for t in titles)
    # 模板B日期:容器 Name 空 → 走子 Text 兜底(已有「新版 UI」分支);
    # top 取的是 publish_time 容器(title_top-70)
    assert times == [("8月17日", 230.0)]


def test_scan_list_template_b_sorts_by_top():
    host = _ctrl(children=[_template_b_host(title_top=500.0),
                           _template_b_host(title="【中国银河宏观】早一点的卡",
                                            title_top=120.0)])
    titles, _ = bot.scan_list(host)
    assert [t.Name for t in titles] == ["【中国银河宏观】早一点的卡",
                                        "【中国银河宏观】K型分化加深,内需仍待企稳"]


def test_scan_list_template_a_still_works():
    titles, times = bot.scan_list(_template_a_host())
    assert [t.Name for t in titles] == ["搞好中国式现代化理论研究"]
    assert times == [("9月1日", 160.0)]


def _patch_profile_env(monkeypatch, host, doc="中国银河宏观"):
    monkeypatch.setattr(bot, "appex_windows", lambda retries=2: [object()])
    monkeypatch.setattr(bot, "restore_if_minimized", lambda w: None)
    monkeypatch.setattr(bot, "find_render_hosts", lambda w: [host])
    monkeypatch.setattr(bot, "host_doc_name", lambda h: doc)


def test_find_profile_host_accepts_template_b(monkeypatch):
    host = _template_b_host()
    _patch_profile_env(monkeypatch, host,
                       doc="中国银河宏观")
    # 宿主矩形要够宽(>100)
    host.BoundingRectangle.left = 100
    host.BoundingRectangle.right = 900
    win, h = bot.find_profile_host(account="中国银河宏观", kicks=0)
    assert h is host


def test_find_profile_host_doc_name_gate_still_applies(monkeypatch):
    host = _template_b_host()
    _patch_profile_env(monkeypatch, host, doc="别的账号")
    host.BoundingRectangle.left = 100
    host.BoundingRectangle.right = 900
    win, h = bot.find_profile_host(account="中国银河宏观", kicks=0)
    assert h is None


def test_find_profile_host_template_a_unchanged(monkeypatch):
    host = _template_a_host()
    _patch_profile_env(monkeypatch, host, doc="中金点睛")
    host.BoundingRectangle.left = 100
    host.BoundingRectangle.right = 900
    win, h = bot.find_profile_host(account="中金点睛", kicks=0)
    assert h is host
