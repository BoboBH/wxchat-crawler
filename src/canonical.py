"""纯函数:URL 规范化、dedup_key、日期文本归一化、列表日期配对、停止判定。

依据 docs/spike-findings.md 尖峰C:文章页提取的原始 URL 带跟踪参数
(uin/pass_ticket 等),去参保留 __biz/mid/idx/sn/chksm 五参数后即公网可开的
canonical 链接;同一文章多次提取逐字节一致,可作稳定去重键。
"""
from __future__ import annotations

import hashlib
import re
from datetime import date, timedelta

MP_URL_RE = re.compile(r"^https?://mp\.weixin\.qq\.com/s\?\S+$")
CANONICAL_PARAMS = ("__biz", "mid", "idx", "sn", "chksm")

_WEEKDAYS = {"星期一": 0, "星期二": 1, "星期三": 2, "星期四": 3,
             "星期五": 4, "星期六": 5, "星期日": 6, "星期天": 6}


def canonicalize_url(url: str | None) -> str | None:
    """提取并精简 mp 文章 URL(5 参数 canonical);非 mp 文章 URL 返回 None。"""
    if not url:
        return None
    url = str(url).strip()
    if not MP_URL_RE.match(url):
        return None
    query = url.split("?", 1)[1].split("#", 1)[0]
    keep = {}
    for kv in query.split("&"):
        if "=" not in kv:
            continue
        k, v = kv.split("=", 1)
        if k in CANONICAL_PARAMS and k not in keep:
            keep[k] = v
    if "__biz" not in keep or "sn" not in keep:
        return None
    return "https://mp.weixin.qq.com/s?" + \
        "&".join(f"{k}={keep[k]}" for k in CANONICAL_PARAMS if k in keep)


def make_dedup_key(url: str | None, title: str, date_text: str | None) -> str:
    """有 canonical URL 用之;否则用 标题|日期 的 sha1 兜底(t| 前缀区分)。"""
    canon = canonicalize_url(url)
    if canon:
        return canon
    return "t|" + hashlib.sha1(f"{title}|{date_text or ''}".encode("utf-8")).hexdigest()


def normalize_date_text(text: str | None, today: date | None = None) -> str | None:
    """微信日期分组文本 → ISO 日期(YYYY-MM-DD);无法识别返回 None。

    实测样本(尖峰A):今天 / 昨天 / 星期一 / 8月23日;更早的为
    「2025年12月31日」形式。相对日期按 today 折算(便于测试注入)。
    """
    if not text:
        return None
    t = str(text).strip()
    today = today or date.today()
    if t == "今天":
        return today.isoformat()
    if t in ("昨天", "昨日"):
        return (today - timedelta(days=1)).isoformat()
    if t == "前天":
        return (today - timedelta(days=2)).isoformat()
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", t)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
        except ValueError:
            return None
    m = re.match(r"^(\d{1,2})月(\d{1,2})日$", t)
    if m:
        try:
            d = date(today.year, int(m.group(1)), int(m.group(2)))
        except ValueError:
            return None
        if d > today:  # 列表倒序中出现"未来"日期只可能是去年
            d = date(today.year - 1, int(m.group(1)), int(m.group(2)))
        return d.isoformat()
    m = re.match(r"^(\d{4})年(\d{1,2})月(\d{1,2})日$", t)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
        except ValueError:
            return None
    if t in _WEEKDAYS:
        delta = (today.weekday() - _WEEKDAYS[t]) % 7
        if delta == 0:  # 今天就是该星期:分组指上周(今天的分组会标「今天」)
            delta = 7
        return (today - timedelta(days=delta)).isoformat()
    return None


def pair_publish_dates(titles: list[tuple[str, float]],
                       times: list[tuple[str, float]]) -> list[tuple[str, str | None]]:
    """按纵向位置把日期分组标签配给其下方的标题,返回 [(标题, 日期文本|None)]。

    主页列表结构:日期标签在上,其下所有标题都属于该日期,直到下一个标签。
    titles/times 均为 (文本, 纵坐标 top),各自排序后归并。
    """
    t_sorted = sorted(titles, key=lambda x: x[1])
    s_sorted = sorted(times, key=lambda x: x[1])
    out = []
    cur = None
    i = 0
    for name, top in t_sorted:
        while i < len(s_sorted) and s_sorted[i][1] <= top:
            cur = s_sorted[i][0]
            i += 1
        out.append((name, cur))
    return out


def find_stop_index(dates: list[str | None], cutoff: str | None,
                    streak: int = 3) -> int:
    """增量停止判定:返回扫描前缀长度;-1 表示扫完也未触发。

    dates 为倒序列表的归一化日期(无法归一化为 None,视为可能的新文章,
    重置连续计数)。cutoff 为 ISO 截止日期;日期早于 cutoff 计入连续旧文
    计数,连续 streak 条即停。cutoff 为 None(新账号)返回 -1。
    """
    if not cutoff:
        return -1
    run = 0
    for i, d in enumerate(dates):
        if d is not None and d < cutoff:
            run += 1
            if run >= streak:
                return i + 1
        else:
            run = 0
    return -1
