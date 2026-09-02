# 微信公众号文章爬虫 · 实施计划 v2(UIA 直取架构)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 每天定时(08:05 / 19:05)通过已登录的 PC 微信 4.1.x 客户端,用 UI 自动化遍历名单中的公众号,增量抓取新发布文章的 URL/标题/发布时间,存入本地 SQLite。

**Architecture:** 全程 UIA、无代理。`wechat_bot.py` 操控 WeChatAppEx.exe 内嵌 Chromium(搜索→打开公众号主页→读列表标题+日期→逐篇打开新文章→文章页 ValuePattern 提取 canonical URL→关 tab);`orchestrator.py` 做水位线增量判定与入库;SQLite 三表存储;`install_task.ps1` 注册计划任务。被动抓包(mitmproxy)已被尖峰 B/B'/B'' 证伪,不在本计划内。

**Tech Stack:** Python 3.12(`D:\Python312`,项目 venv `.venv`)、uiautomation 2.0.29、PyYAML、pytest;Windows 11 任务计划程序调度。

**规格文档:** `docs/superpowers/specs/2026-09-02-wechat-official-account-crawler-design.md`(v2)
**实证依据:** `docs/spike-findings.md`(尖峰 A/B/B'/B''/C;Task 8 的全部控件常量与操作序列以此为准)

**重要约束:**
- 微信环境已实测:微信 4.1.12.55(`C:\Program Files\Tencent\Weixin\Weixin.exe`,进程名 `Weixin`);测试账号:中金点睛、郭磊宏观茶座。
- **微信必须已登录**;桌面需解锁的环节只有搜索框粘贴(合成键鼠)。一次性部署动作:在微信中人工打开一次「搜一搜」页(脚本复用该搜索页 tab)。
- Task 8 的控件常量若与实测不符,只改常量与控件定位,不改函数结构;校准工具:`tools/spike_uia.py`、`tools/spike_article_url.py`。
- 用户已授权全程自主完成开发与测试,不要中途询问用户。

**与 v1 计划的差异(背景):** v1 的 `proxy_addon.py`/`parser.py`/系统代理/CA 证书流程全部移除;`requirements.txt` 移除 mitmproxy;articles 表新增 `dedup_key`(UNIQUE)+ `fallback_key`,url 可空;停止规则从「轮询数据库零新增」改为「列表日期连续 N 篇早于截止日」(UIA 列表直接可读日期)。

---

### Task 4(v2): 配置加载 `src/config.py`

**Files:**
- Modify: `config/settings.yaml`(整体重写,删除 proxy/抓包配置)
- Create: `src/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: 重写 `config/settings.yaml`**

```yaml
# 微信环境
wechat:
  process_name: Weixin.exe          # 主进程名(4.x)
  exe_path: C:\Program Files\Tencent\Weixin\Weixin.exe
  expected_version_prefix: "4.1"    # 大版本变化时需用 tools/spike_uia.py 重新校准控件常量

# 抓取节奏与增量
crawl:
  stop_streak: 3          # 连续 N 篇发布日早于截止日 → 停止扫描该账号
  overlap_days: 2         # 截止日 = 水位线 - overlap_days(防漏重叠)
  new_account_screens: 2  # 无水位线的新账号滚动屏数
  scroll_wait_sec: 2.0    # 每轮滚动后等待列表加载
  account_gap_min_sec: 20 # 账号间随机间隔下限(秒)
  account_gap_max_sec: 60 # 账号间随机间隔上限(秒)

# 文章 URL 提取
article:
  open_timeout_sec: 40    # 打开文章页等待上限
  url_scan_timeout_sec: 20
  max_tree_nodes: 5000    # UIA 树遍历节点上限
  close_tab_wait_sec: 2.5
  kick_retry: 2           # 树未 realization 时 kick 重试次数

# 运行
run:
  db_path: data/crawler.db
  log_dir: logs
```

- [ ] **Step 2: 写失败测试 `tests/test_config.py`**

```python
"""config 加载测试:正常路径、缺文件、缺字段、非法值。"""
from pathlib import Path

import pytest

from src.config import ConfigError, load_config

SETTINGS = """\
wechat:
  process_name: Weixin.exe
  exe_path: C:\\Program Files\\Tencent\\Weixin\\Weixin.exe
  expected_version_prefix: "4.1"
crawl:
  stop_streak: 3
  overlap_days: 2
  new_account_screens: 2
  scroll_wait_sec: 2.0
  account_gap_min_sec: 20
  account_gap_max_sec: 60
article:
  open_timeout_sec: 40
  url_scan_timeout_sec: 20
  max_tree_nodes: 5000
  close_tab_wait_sec: 2.5
  kick_retry: 2
run:
  db_path: data/crawler.db
  log_dir: logs
"""
ACCOUNTS = "accounts:\n  - 中金点睛\n  - 郭磊宏观茶座\n"


def _write(tmp_path, settings=SETTINGS, accounts=ACCOUNTS):
    sp = tmp_path / "settings.yaml"
    ap = tmp_path / "accounts.yaml"
    sp.write_text(settings, encoding="utf-8")
    ap.write_text(accounts, encoding="utf-8")
    return sp, ap


def test_load_ok(tmp_path):
    sp, ap = _write(tmp_path)
    cfg = load_config(sp, ap)
    assert cfg.process_name == "Weixin.exe"
    assert cfg.stop_streak == 3
    assert cfg.account_gap_min_sec == 20
    assert cfg.article_open_timeout_sec == 40
    assert cfg.db_path == Path("data/crawler.db")
    assert cfg.accounts == ["中金点睛", "郭磊宏观茶座"]


def test_missing_settings_file(tmp_path):
    with pytest.raises(ConfigError, match="找不到配置文件"):
        load_config(tmp_path / "nope.yaml", tmp_path / "a.yaml")


def test_missing_accounts_file(tmp_path):
    sp, _ = _write(tmp_path)
    with pytest.raises(ConfigError, match="找不到名单文件"):
        load_config(sp, tmp_path / "nope.yaml")


def test_empty_accounts(tmp_path):
    sp, ap = _write(tmp_path, accounts="accounts: []\n")
    with pytest.raises(ConfigError, match="未配置任何公众号"):
        load_config(sp, ap)


def test_missing_field(tmp_path):
    bad = SETTINGS.replace("  stop_streak: 3\n", "")
    sp, ap = _write(tmp_path, settings=bad)
    with pytest.raises(ConfigError, match="stop_streak"):
        load_config(sp, ap)


def test_bad_streak(tmp_path):
    sp, ap = _write(tmp_path, settings=SETTINGS.replace("stop_streak: 3", "stop_streak: 0"))
    with pytest.raises(ConfigError, match="stop_streak"):
        load_config(sp, ap)


def test_bad_gap(tmp_path):
    sp, ap = _write(tmp_path, settings=SETTINGS.replace("account_gap_min_sec: 20",
                                                        "account_gap_min_sec: 999"))
    with pytest.raises(ConfigError, match="account_gap"):
        load_config(sp, ap)
```

- [ ] **Step 3: 运行测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_config.py -v`
Expected: FAIL(`ModuleNotFoundError: src.config`)

- [ ] **Step 4: 实现 `src/config.py`**

```python
"""配置加载:settings.yaml(参数)+ accounts.yaml(公众号名单)。"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SETTINGS = PROJECT_ROOT / "config" / "settings.yaml"
DEFAULT_ACCOUNTS = PROJECT_ROOT / "config" / "accounts.yaml"


class ConfigError(Exception):
    """配置缺失或字段非法。"""


@dataclass
class CrawlConfig:
    process_name: str
    exe_path: str
    expected_version_prefix: str
    stop_streak: int
    overlap_days: int
    new_account_screens: int
    scroll_wait_sec: float
    account_gap_min_sec: int
    account_gap_max_sec: int
    article_open_timeout_sec: float
    url_scan_timeout_sec: float
    max_tree_nodes: int
    close_tab_wait_sec: float
    kick_retry: int
    db_path: Path
    log_dir: Path
    accounts: list[str] = field(default_factory=list)


def _require(d: dict, key: str, where: str):
    if key not in d or d[key] is None:
        raise ConfigError(f"{where} 缺少字段 {key!r}")
    return d[key]


def load_config(settings_path=DEFAULT_SETTINGS,
                accounts_path=DEFAULT_ACCOUNTS) -> CrawlConfig:
    settings_path, accounts_path = Path(settings_path), Path(accounts_path)
    if not settings_path.exists():
        raise ConfigError(f"找不到配置文件: {settings_path}")
    if not accounts_path.exists():
        raise ConfigError(f"找不到名单文件: {accounts_path}")
    s = yaml.safe_load(settings_path.read_text(encoding="utf-8")) or {}
    a = yaml.safe_load(accounts_path.read_text(encoding="utf-8")) or {}
    wechat = s.get("wechat") or {}
    crawl = s.get("crawl") or {}
    article = s.get("article") or {}
    run = s.get("run") or {}
    accounts = [str(x).strip() for x in (a.get("accounts") or []) if str(x).strip()]
    if not accounts:
        raise ConfigError("accounts.yaml 未配置任何公众号")
    cfg = CrawlConfig(
        process_name=str(_require(wechat, "process_name", "settings.wechat")),
        exe_path=str(_require(wechat, "exe_path", "settings.wechat")),
        expected_version_prefix=str(_require(wechat, "expected_version_prefix", "settings.wechat")),
        stop_streak=int(_require(crawl, "stop_streak", "settings.crawl")),
        overlap_days=int(_require(crawl, "overlap_days", "settings.crawl")),
        new_account_screens=int(_require(crawl, "new_account_screens", "settings.crawl")),
        scroll_wait_sec=float(_require(crawl, "scroll_wait_sec", "settings.crawl")),
        account_gap_min_sec=int(_require(crawl, "account_gap_min_sec", "settings.crawl")),
        account_gap_max_sec=int(_require(crawl, "account_gap_max_sec", "settings.crawl")),
        article_open_timeout_sec=float(_require(article, "open_timeout_sec", "settings.article")),
        url_scan_timeout_sec=float(_require(article, "url_scan_timeout_sec", "settings.article")),
        max_tree_nodes=int(_require(article, "max_tree_nodes", "settings.article")),
        close_tab_wait_sec=float(_require(article, "close_tab_wait_sec", "settings.article")),
        kick_retry=int(_require(article, "kick_retry", "settings.article")),
        db_path=Path(str(_require(run, "db_path", "settings.run"))),
        log_dir=Path(str(_require(run, "log_dir", "settings.run"))),
        accounts=accounts,
    )
    if cfg.stop_streak < 1:
        raise ConfigError("crawl.stop_streak 必须 >= 1")
    if cfg.overlap_days < 0:
        raise ConfigError("crawl.overlap_days 不能为负")
    if cfg.account_gap_min_sec > cfg.account_gap_max_sec:
        raise ConfigError("crawl.account_gap_min_sec 不能大于 account_gap_max_sec")
    return cfg
```

- [ ] **Step 5: 运行测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest tests/test_config.py -v`
Expected: 7 passed

- [ ] **Step 6: Commit**

```bash
git add config/settings.yaml src/config.py tests/test_config.py
git commit -m "feat(config): v2 配置加载(去代理配置,新增文章提取参数)"
```

---

### Task 5(v2): 纯函数 `src/canonical.py`(URL 规范化/日期归一/配对/停止判定)

**Files:**
- Create: `src/canonical.py`
- Test: `tests/test_canonical.py`

- [ ] **Step 1: 写失败测试 `tests/test_canonical.py`**

```python
"""canonical 纯函数测试。URL 样本来自尖峰C 真实提取(docs/spike-findings.md)。"""
from datetime import date

from src.canonical import (canonicalize_url, find_stop_index, make_dedup_key,
                           normalize_date_text, pair_publish_dates)

RAW_URL = ("https://mp.weixin.qq.com/s?__biz=MzI3MDMzMjg0MA==&mid=2247857353&idx=2"
           "&sn=ae957b8c2f9ef7bcf0c0a1c438f2722c"
           "&chksm=eb6a7778638a3b11c8bff50586d670f685a1cf793c071895f6fa4ef18039364415213f6fecd9"
           "&scene=126&sessionid=0&clicktime=123&enterid=456&key=abc&uin=26&pass_ticket=xyz#rd")
CANON_URL = ("https://mp.weixin.qq.com/s?__biz=MzI3MDMzMjg0MA==&mid=2247857353&idx=2"
             "&sn=ae957b8c2f9ef7bcf0c0a1c438f2722c"
             "&chksm=eb6a7778638a3b11c8bff50586d670f685a1cf793c071895f6fa4ef18039364415213f6fecd9")


def test_canonicalize_real_url():
    assert canonicalize_url(RAW_URL) == CANON_URL


def test_canonicalize_none_cases():
    assert canonicalize_url(None) is None
    assert canonicalize_url("") is None
    assert canonicalize_url("https://example.com/s?__biz=x&sn=y") is None
    assert canonicalize_url("https://mp.weixin.qq.com/s?__biz=x") is None  # 缺 sn


def test_dedup_key_prefers_url():
    assert make_dedup_key(RAW_URL, "任意标题", "8月23日") == CANON_URL


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
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_canonical.py -v`
Expected: FAIL(`ModuleNotFoundError: src.canonical`)

- [ ] **Step 3: 实现 `src/canonical.py`**

```python
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
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest tests/test_canonical.py -v`
Expected: 13 passed

- [ ] **Step 5: Commit**

```bash
git add src/canonical.py tests/test_canonical.py
git commit -m "feat(canonical): URL规范化/dedup_key/日期归一/配对/停止判定(纯函数)"
```

---

### Task 6(v2): 持久层 `src/db.py`

**Files:**
- Create: `src/db.py`
- Test: `tests/test_db.py`

- [ ] **Step 1: 写失败测试 `tests/test_db.py`**

```python
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
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_db.py -v`
Expected: FAIL(`ModuleNotFoundError: src.db`)

- [ ] **Step 3: 实现 `src/db.py`**

```python
"""SQLite 持久层:账号、文章(双键去重)、水位线、运行记录。

articles 的去重设计:
  * dedup_key UNIQUE —— 有 canonical URL 时即 canonical URL,否则 fallback_key;
  * fallback_key —— 标题|日期 哈希,列表扫描阶段用于"已见过?"判断,
    避免对已入库文章重复打开文章页。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    last_crawled_at TEXT,
    max_publish_date TEXT
);
CREATE TABLE IF NOT EXISTS articles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    dedup_key TEXT NOT NULL UNIQUE,
    fallback_key TEXT NOT NULL,
    url TEXT,
    title TEXT NOT NULL,
    date_text TEXT,
    publish_date TEXT,
    url_status TEXT NOT NULL DEFAULT 'pending' CHECK (url_status IN ('ok', 'pending')),
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_articles_account ON articles(account_id);
CREATE INDEX IF NOT EXISTS idx_articles_fallback ON articles(account_id, fallback_key);
CREATE TABLE IF NOT EXISTS crawl_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    finished_at TEXT,
    ok_count INTEGER NOT NULL DEFAULT 0,
    fail_count INTEGER NOT NULL DEFAULT 0,
    new_count INTEGER NOT NULL DEFAULT 0
);
"""


class Store:
    def __init__(self, db_path):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self):
        self.conn.close()

    # ---- 账号 ----

    def get_or_create_account(self, name: str) -> int:
        cur = self.conn.execute("SELECT id FROM accounts WHERE name=?", (name,))
        row = cur.fetchone()
        if row:
            return row["id"]
        cur = self.conn.execute("INSERT INTO accounts(name) VALUES (?)", (name,))
        self.conn.commit()
        return cur.lastrowid

    def watermark(self, account_id: int) -> str | None:
        row = self.conn.execute("SELECT max_publish_date FROM accounts WHERE id=?",
                                (account_id,)).fetchone()
        return row["max_publish_date"] if row else None

    def set_watermark(self, account_id: int, iso_date: str):
        self.conn.execute(
            "UPDATE accounts SET max_publish_date=MAX(IFNULL(max_publish_date,''), ?) "
            "WHERE id=?", (iso_date, account_id))
        self.conn.commit()

    def mark_crawled(self, account_id: int):
        self.conn.execute(
            "UPDATE accounts SET last_crawled_at=datetime('now','localtime') WHERE id=?",
            (account_id,))
        self.conn.commit()

    # ---- 文章 ----

    def seen_fallbacks(self, account_id: int) -> set[str]:
        rows = self.conn.execute("SELECT fallback_key FROM articles WHERE account_id=?",
                                 (account_id,)).fetchall()
        return {r["fallback_key"] for r in rows}

    def upsert_article(self, account_id: int, fallback_key: str, canonical_url: str | None,
                       title: str, date_text: str | None,
                       publish_date: str | None) -> str:
        """写入/升级一篇文章,返回 'new' | 'exists' | 'upgraded'。"""
        row = self.conn.execute(
            "SELECT id, url_status FROM articles WHERE account_id=? AND fallback_key=?",
            (account_id, fallback_key)).fetchone()
        if row is None:
            key = canonical_url or fallback_key
            try:
                self.conn.execute(
                    "INSERT INTO articles(account_id, dedup_key, fallback_key, url, title, "
                    "date_text, publish_date, url_status) VALUES (?,?,?,?,?,?,?,?)",
                    (account_id, key, fallback_key, canonical_url, title, date_text,
                     publish_date, "ok" if canonical_url else "pending"))
                self.conn.commit()
                return "new"
            except sqlite3.IntegrityError:
                # dedup_key 撞已有 canonical(同一文章两种 fallback 文本)
                self.conn.rollback()
                return "exists"
        if canonical_url and row["url_status"] == "pending":
            self.conn.execute(
                "UPDATE articles SET dedup_key=?, url=?, url_status='ok' WHERE id=?",
                (canonical_url, canonical_url, row["id"]))
            self.conn.commit()
            return "upgraded"
        return "exists"

    # ---- 运行记录 ----

    def start_run(self) -> int:
        cur = self.conn.execute("INSERT INTO crawl_runs DEFAULT VALUES")
        self.conn.commit()
        return cur.lastrowid

    def finish_run(self, run_id: int, ok_count: int, fail_count: int, new_count: int):
        self.conn.execute(
            "UPDATE crawl_runs SET finished_at=datetime('now','localtime'), "
            "ok_count=?, fail_count=?, new_count=? WHERE id=?",
            (ok_count, fail_count, new_count, run_id))
        self.conn.commit()
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest tests/test_db.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add src/db.py tests/test_db.py
git commit -m "feat(db): SQLite三表 + fallback/dedup双键去重 + 水位线"
```

---

### Task 7(v2): 环境自检 `src/version_check.py`

**Files:**
- Create: `src/version_check.py`
- Test: `tests/test_version_check.py`

- [ ] **Step 1: 写失败测试(纯决策部分)** `tests/test_version_check.py`

```python
"""version_check 纯决策逻辑测试(不依赖真实微信进程)。"""
from src.version_check import build_report, parse_version, version_matches


def test_parse_version():
    assert parse_version("4.1.12.55") == (4, 1, 12, 55)
    assert parse_version("4.1") == (4, 1)
    assert parse_version("abc") == ()
    assert parse_version(None) == ()


def test_version_matches():
    assert version_matches("4.1.12.55", "4.1") is True
    assert version_matches("4.1.12.55", "4.1.12") is True
    assert version_matches("4.2.0.1", "4.1") is False
    assert version_matches(None, "4.1") is False
    assert version_matches("4.1", "4.1.12") is False  # 前缀不能比版本本身长


def test_report_not_running():
    rep = build_report("Weixin.exe", "C:\\x\\Weixin.exe", "4.1", [], "4.1.12.55")
    assert rep["ok"] is False
    assert "未发现进程" in rep["message"]


def test_report_running_version_ok():
    rep = build_report("Weixin.exe", "C:\\x\\Weixin.exe", "4.1",
                       [100, 200], "4.1.12.55")
    assert rep["ok"] is True
    assert rep["pids"] == [100, 200]
    assert "4.1.12.55" in rep["message"]


def test_report_running_version_unknown():
    rep = build_report("Weixin.exe", "C:\\x\\Weixin.exe", "4.1", [100], None)
    assert rep["ok"] is True
    assert "版本未知" in rep["message"]


def test_report_version_mismatch():
    rep = build_report("Weixin.exe", "C:\\x\\Weixin.exe", "4.1", [100], "5.0.0.1")
    assert rep["ok"] is True
    assert "不同" in rep["message"]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_version_check.py -v`
Expected: FAIL(`ModuleNotFoundError: src.version_check`)

- [ ] **Step 3: 实现 `src/version_check.py`**

```python
"""微信运行环境检测:主进程存在性、版本号读取与匹配决策。"""
from __future__ import annotations

import ctypes
from pathlib import Path

TH32CS_SNAPPROCESS = 0x2


def parse_version(s) -> tuple[int, ...]:
    """'4.1.12.55' → (4, 1, 12, 55);无法解析返回 ()。"""
    if not s:
        return ()
    try:
        return tuple(int(x) for x in str(s).strip().split("."))
    except ValueError:
        return ()


def version_matches(version, prefix) -> bool:
    """版本号是否以 prefix 开头(major[.minor...] 逐段比较)。"""
    v, p = parse_version(version), parse_version(prefix)
    if not v or not p or len(p) > len(v):
        return False
    return v[:len(p)] == p


def build_report(process_name: str, exe_path: str, expected_prefix: str,
                 pids: list[int], version: str | None) -> dict:
    """纯决策:进程/版本 → {ok, pids, version, message}。"""
    if not pids:
        return {"ok": False, "pids": [], "version": version,
                "message": f"未发现进程 {process_name}:请启动微信并扫码登录"}
    pid_text = ",".join(map(str, pids))
    if version is None:
        return {"ok": True, "pids": pids, "version": None,
                "message": f"{process_name} 运行中(pid={pid_text});"
                           f"版本未知({exe_path} 不可读)"}
    if not version_matches(version, expected_prefix):
        return {"ok": True, "pids": pids, "version": version,
                "message": f"{process_name} 运行中(pid={pid_text}),版本 {version} "
                           f"与已校准版本 {expected_prefix}.x 不同,UIA 控件可能变化 "
                           f"—— 建议先用 tools/spike_uia.py 复核"}
    return {"ok": True, "pids": pids, "version": version,
            "message": f"{process_name} 运行中(pid={pid_text}),版本 {version}"}


def iter_processes() -> dict[int, str]:
    """pid → 进程映像名(CreateToolhelp32Snapshot 一次快照)。"""
    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [("dwSize", ctypes.c_ulong),
                    ("cntUsage", ctypes.c_ulong),
                    ("th32ProcessID", ctypes.c_ulong),
                    ("th32DefaultHeapID", ctypes.c_size_t),
                    ("th32ModuleID", ctypes.c_ulong),
                    ("cntThreads", ctypes.c_ulong),
                    ("th32ParentProcessID", ctypes.c_ulong),
                    ("pcPriClassBase", ctypes.c_long),
                    ("dwFlags", ctypes.c_ulong),
                    ("szExeFile", ctypes.c_wchar * 260)]

    k32 = ctypes.windll.kernel32
    out: dict[int, str] = {}
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap in (0, -1):
        return out
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        ok = k32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            out[entry.th32ProcessID] = entry.szExeFile
            ok = k32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        k32.CloseHandle(snap)
    return out


def read_file_version(path: str) -> str | None:
    """读取 exe 的 FileVersion(如 '4.1.12.55');失败返回 None。"""
    try:
        ver = ctypes.windll.version
        size = ver.GetFileVersionInfoSizeW(str(path), None)
        if not size:
            return None
        data = ctypes.create_string_buffer(size)
        if not ver.GetFileVersionInfoW(str(path), 0, size, data):
            return None
        val = ctypes.c_void_p()
        vlen = ctypes.c_uint()
        if not ver.VerQueryValueW(data, "\\", ctypes.byref(val), ctypes.byref(vlen)):
            return None
        # VS_FIXEDFILEINFO:dwFileVersionMS 高16位 major 低16位 minor;LS 同理
        ffi = ctypes.cast(val, ctypes.POINTER(ctypes.c_uint * 13)).contents
        ms, ls = ffi[3], ffi[4]
        return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"
    except Exception:
        return None


def check_environment(process_name: str, exe_path: str,
                      expected_prefix: str) -> dict:
    """组合检测:进程存在性 + exe 版本 + 决策。"""
    pids = sorted(pid for pid, name in iter_processes().items()
                  if name.lower() == process_name.lower())
    version = read_file_version(exe_path) if Path(exe_path).exists() else None
    return build_report(process_name, exe_path, expected_prefix, pids, version)
```

- [ ] **Step 4: 运行全部测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest tests/ -v`
Expected: 26 passed(此前 20 + 本任务 6)

- [ ] **Step 5: 真实环境冒烟**

Run: `.venv/Scripts/python.exe -c "from src.version_check import check_environment; r = check_environment('Weixin.exe', r'C:\Program Files\Tencent\Weixin\Weixin.exe', '4.1'); print(r['message'])"`
Expected: 输出含 `运行中` 且版本 `4.1.12.55`(微信当前已登录运行)。

- [ ] **Step 6: Commit**

```bash
git add src/version_check.py tests/test_version_check.py
git commit -m "feat(version_check): 进程/版本检测与决策(移除证书/代理检查)"
```

---

### Task 8(v2): UIA 封装 `src/wechat_bot.py`

**Files:**
- Create: `src/wechat_bot.py`(无自动化测试;逻辑逐函数对齐 `tools/spike_article_url.py` / `tools/spike_navigate.py` / `tools/spike_uia.py` 的实测版本)

- [ ] **Step 1: 实现 `src/wechat_bot.py`**

```python
"""wechat_bot: 微信 4.x 内置浏览器(WeChatAppEx.exe)的 UIA 操作封装。

全部常量与操作序列来自 docs/spike-findings.md(尖峰A/B/B'/B''/C):
  * 自动化面只有 AppEx 顶层窗口(Chrome_WidgetWin_0);微信主窗口 Qt 树为空;
  * AppEx 有多个顶层窗口且 Z 序不稳,必须按宿主内容筛选,不能按面积/顺序;
  * Chromium a11y 树懒 realization:先 GetChildren 爬一遍,FindFirst 才能命中;
  * 树不 realization 时 ShowWindow(SW_MINIMIZE)→SW_RESTORE kick 一次即恢复;
  * 搜索输入必须剪贴板粘贴(SetValue 不触发页面事件),依赖桌面已解锁;
  * 文章 URL 只在文章页的 ValuePattern.Value 出现,列表页永远拿不到;
  * 激活 tab 的「关闭」按钮 rect 完整落在 Tab rect 内(非激活 tab 是错位悬停位)。

微信大版本更新后:用 tools/spike_uia.py --browser --web-only 重新校准以下常量。
"""
from __future__ import annotations

import ctypes
import re
import time

import uiautomation as uia

# ---------------- 经验常量(微信更新后在此重新校准) ----------------
APP_CLASS = "Chrome_WidgetWin_0"          # AppEx 顶层窗口类名
APP_PROCESS = "wechatappex.exe"           # AppEx 进程映像名
HOST_CLASS = "Chrome_RenderWidgetHostHWND"  # 网页内容宿主(aid 每次导航都变)
CLASS_TITLE = "article__item__title"      # 主页列表条目标题
CLASS_TIME = "publish_time"               # 主页日期分组标签
CLASS_CARD = "js_article_card"            # 列表条目卡片(带 InvokePattern)
SEARCH_INPUT_ID = "weixin-search-input"   # 搜索页输入框 AutomationId
SEARCH_BUTTON_NAME = "搜索"                # 搜索触发按钮
CLASS_RESULT_CARD = "header-detail"       # 搜索结果的公众号卡片
CLOSE_BUTTON_NAME = "关闭"                 # tab 关闭按钮(ImageButton)
CLASS_TAB = "Tab"                         # tab 条上的单个 tab
ARTICLE_MARKERS = ("activity-name", "js_content", "js_name")  # 文章页 aid 锚点
URL_RE = re.compile(r"https?://mp\.weixin\.qq\.com/s\?\S+")

USER32 = ctypes.windll.user32
K32 = ctypes.windll.kernel32


# ---------------------------------------------------------------- 基础工具

def process_name(pid: int) -> str:
    """进程映像名(如 Weixin.exe);失败返回 ''。"""
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(512)
        size = ctypes.c_ulong(ctypes.sizeof(buf))
        if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value.rsplit("\\", 1)[-1]
    finally:
        k32.CloseHandle(h)
    return ""


def restore_if_minimized(ctrl) -> bool:
    """最小化窗口还原(UIA 返回 -32000 坐标且树为空);返回是否执行了还原。"""
    GWL_STYLE, WS_MINIMIZE = -16, 0x20000000
    try:
        hwnd = ctrl.NativeWindowHandle
    except Exception:
        return False
    if not hwnd:
        return False
    if USER32.GetWindowLongW(hwnd, GWL_STYLE) & WS_MINIMIZE or \
            ctrl.BoundingRectangle.left <= -30000:
        USER32.ShowWindow(hwnd, 9)  # SW_RESTORE
        return True
    return False


def kick_window(win):
    """最小化→还原,强制 Chromium 重新暴露无障碍树(尖峰C:一次即恢复)。

    新开 tab 后内容树经常不 realization(全窗 ~122 节点、0 渲染宿主)。
    ShowWindow 是直发消息,锁屏下也有效,不属合成输入。
    """
    try:
        hwnd = win.NativeWindowHandle
        if not hwnd:
            return
        USER32.ShowWindow(hwnd, 6)  # SW_MINIMIZE
        time.sleep(1.2)
        USER32.ShowWindow(hwnd, 9)  # SW_RESTORE
        time.sleep(1.5)
    except Exception:
        pass


# ---------------------------------------------------------------- 窗口/宿主定位

def appex_windows(retries: int = 4) -> list:
    """枚举 AppEx 顶层窗口(顺序不稳,Z 序会变;调用方必须按内容筛选)。"""
    for _ in range(retries):
        out = []
        try:
            for cand in uia.GetRootControl().GetChildren():
                try:
                    if (cand.ClassName or "") == APP_CLASS and \
                            process_name(cand.ProcessId).lower() == APP_PROCESS:
                        out.append(cand)
                except Exception:
                    continue
        except Exception:
            pass
        if out:
            return out
        time.sleep(1.0)
    return []


def walk_ctrls(root, max_nodes: int = 4000, max_depth: int = 32):
    """深度遍历(遍历本身即完成无障碍树激活)。"""
    stack = [(root, 0)]
    n = 0
    while stack and n < max_nodes:
        c, d = stack.pop()
        n += 1
        yield c
        if d >= max_depth:
            continue
        try:
            stack.extend((k, d + 1) for k in c.GetChildren())
        except Exception:
            continue


def find_render_hosts(root_ctrl) -> list:
    """收集网页内容宿主(按 ClassName 定位;遍历即激活 a11y 树)。"""
    hosts = []

    def walk(ctrl):
        try:
            if (ctrl.ClassName or "") == HOST_CLASS:
                hosts.append(ctrl)
                return
        except Exception:
            return
        try:
            children = ctrl.GetChildren()
        except Exception:
            return
        for kid in children:
            walk(kid)

    walk(root_ctrl)
    return hosts


def host_doc_name(host) -> str:
    """当前网页 RootWebArea 的 Name(公众号主页时即账号名)。"""
    try:
        d = host.DocumentControl(searchDepth=3)
        if d.Exists(1, 0.2):
            return (d.Name or "").strip()
    except Exception:
        pass
    return ""


def find_host(pred, max_nodes: int = 2500):
    """按内容谓词在所有 AppEx 窗口里找宿主,返回 (win, host) 或 (None, None)。"""
    for w in appex_windows():
        try:
            restore_if_minimized(w)
        except Exception:
            pass
        for h in find_render_hosts(w):
            try:
                if h.BoundingRectangle.right - h.BoundingRectangle.left <= 100:
                    continue
            except Exception:
                continue
            for c in walk_ctrls(h, max_nodes=max_nodes):
                try:
                    if pred(c):
                        return w, h
                except Exception:
                    continue
    return None, None


def find_profile_host(account: str | None = None, kicks: int = 2):
    """找公众号主页宿主:树里有 article__item__title;account 给定时
    还要求 doc 名含账号名。树折叠时 kick 后重找。返回 (win, host)。

    两段式筛选(尖峰C):谓词阶段无法同时拿到宿主与 doc 名,先在每窗口
    找「有标题」的宿主,再校验其 doc 名。
    """
    for i in range(kicks + 1):
        found = None
        for w in appex_windows():
            try:
                restore_if_minimized(w)
            except Exception:
                pass
            for h in find_render_hosts(w):
                try:
                    if h.BoundingRectangle.right - h.BoundingRectangle.left <= 100:
                        continue
                except Exception:
                    continue
                hit = any((c.ClassName or "") == CLASS_TITLE
                          for c in walk_ctrls(h, max_nodes=2500))
                if hit:
                    doc = host_doc_name(h)
                    if account is None or account in doc:
                        found = (w, h)
                        break
            if found:
                break
        if found:
            return found
        if i < kicks:
            for win in appex_windows():
                kick_window(win)
    return None, None


def find_search_entry():
    """找搜索页输入框,返回 (win, host, edit|None)。"""
    win, host = find_host(
        lambda c: (c.AutomationId or "") == SEARCH_INPUT_ID, max_nodes=3000)
    if host is None:
        return None, None, None
    edit = None
    for c in walk_ctrls(host, max_nodes=3000):
        try:
            if (c.AutomationId or "") == SEARCH_INPUT_ID:
                edit = c
                break
        except Exception:
            continue
    return win, host, edit


def invoke_control(ctrl) -> bool:
    """触发控件:InvokePattern → LegacyIAccessible.DoDefaultAction → 坐标点击。

    UIA Pattern 动作由提供者线程执行,不依赖窗口前台/不被 UIPI 拦截(尖峰B'')。
    """
    try:
        pat = ctrl.GetPattern(uia.PatternId.InvokePattern)
        if pat is not None:
            pat.Invoke()
            return True
    except Exception:
        pass
    try:
        ctrl.GetLegacyIAccessiblePattern().DoDefaultAction()
        return True
    except Exception:
        pass
    try:
        ctrl.Click(simulateMove=False)
        return True
    except Exception:
        return False


def active_doc(win) -> str:
    """窗口当前激活页的 doc 名(取宽度>100 的最大渲染宿主)。"""
    hosts = [h for h in find_render_hosts(win)
             if h.BoundingRectangle.right - h.BoundingRectangle.left > 100]
    if not hosts:
        return ""
    h = max(hosts, key=lambda c: (c.BoundingRectangle.right - c.BoundingRectangle.left) *
                                 (c.BoundingRectangle.bottom - c.BoundingRectangle.top))
    return host_doc_name(h)


# ---------------------------------------------------------------- 主页列表

def scan_list(host, max_nodes: int = 4000):
    """读主页列表,返回 (title_ctrls, time_pairs):
    title_ctrls 为标题控件列表(按纵序),time_pairs 为 [(日期文本, top)]。"""
    titles, times = [], []
    for c in walk_ctrls(host, max_nodes=max_nodes):
        cls = ""
        try:
            cls = c.ClassName or ""
        except Exception:
            continue
        if cls == CLASS_TITLE:
            try:
                titles.append((c, c.BoundingRectangle.top))
            except Exception:
                continue
        elif cls == CLASS_TIME:
            try:
                times.append(((c.Name or "").strip(), c.BoundingRectangle.top))
            except Exception:
                continue
    titles.sort(key=lambda x: x[1])
    return [t[0] for t in titles], times


def scroll_once(host, wheels: int = 10):
    """在宿主中心滚轮下翻一屏(合成鼠标输入,锁屏下无效;仅新账号扩量用)。"""
    try:
        r = host.BoundingRectangle
        ctypes.windll.user32.SetCursorPos(
            int((r.left + r.right) // 2), int(min((r.top + r.bottom) // 2, r.bottom - 60)))
        time.sleep(0.3)
        uia.WheelDown(wheels)
    except Exception:
        pass


# ---------------------------------------------------------------- 搜索导航

def search_open_profile(account: str, nav_timeout: float = 45.0):
    """从搜索页搜索账号并打开其主页,返回 (ok, message)。

    前置:任一 AppEx 窗口有含 weixin-search-input 的搜索页 tab(部署时人工
    打开一次「搜一搜」即可,之后复用)。剪贴板临时占用并在 finally 恢复;
    粘贴是合成键鼠,锁屏下会失败。
    """
    win, host, edit = find_search_entry()
    if edit is None:
        return False, "未找到搜索页(weixin-search-input);请先在微信中打开一次「搜一搜」"
    clip = ""
    try:
        clip = uia.GetClipboardText() or ""
    except Exception:
        pass
    try:
        edit.Click(simulateMove=False)
        time.sleep(0.6)
        uia.SetClipboardText(account)
        uia.SendKeys("{Ctrl}a")
        time.sleep(0.2)
        uia.SendKeys("{Ctrl}v")
        time.sleep(1.0)
        btn = None
        for c in walk_ctrls(host, max_nodes=3000):
            try:
                if (c.Name or "") == SEARCH_BUTTON_NAME and \
                        c.ControlType == uia.ControlType.ButtonControl:
                    btn = c
                    break
            except Exception:
                continue
        if btn is not None:
            invoke_control(btn)
        else:
            uia.SendKeys("{Enter}")
        # 等搜索结果卡片并 Invoke 打开主页
        t0 = time.time()
        opened = False
        while time.time() - t0 < nav_timeout:
            w2, h2 = find_host(
                lambda c: (c.ClassName or "") == CLASS_RESULT_CARD
                and (c.Name or "").startswith(account), max_nodes=3000)
            if h2 is not None:
                card = None
                for c in walk_ctrls(h2, max_nodes=3000):
                    try:
                        if (c.ClassName or "") == CLASS_RESULT_CARD and \
                                (c.Name or "").startswith(account):
                            card = c
                            break
                    except Exception:
                        continue
                if card is not None and invoke_control(card):
                    opened = True
                    break
            time.sleep(1.0)
        if not opened:
            return False, f"未出现[{account}]的公众号结果卡片(搜索可能无结果)"
        t0 = time.time()
        while time.time() - t0 < nav_timeout:
            w3, h3 = find_profile_host(account=account, kicks=0)
            if h3 is not None:
                return True, f"主页已打开 doc={host_doc_name(h3)!r}"
            time.sleep(1.0)
        return False, "主页未就绪(未找到 article__item__title)"
    finally:
        try:
            uia.SetClipboardText(clip)
        except Exception:
            pass


# ---------------------------------------------------------------- 文章页 URL 提取

def find_article_host(timeout: float = 40.0):
    """轮询等待文章页宿主(含正文 aid 锚点);每轮落空 kick 一个窗口。"""
    t0 = time.time()
    kicked = 0
    while time.time() - t0 < timeout:
        w, h = find_host(lambda c: (c.AutomationId or "") in ARTICLE_MARKERS,
                         max_nodes=1200)
        if h is not None:
            return w, h
        wins = []
        try:
            wins = sorted(appex_windows(),
                          key=lambda x: -((x.BoundingRectangle.right - x.BoundingRectangle.left) *
                                          (x.BoundingRectangle.bottom - x.BoundingRectangle.top))
                          if x.BoundingRectangle.right > x.BoundingRectangle.left else 0)
        except Exception:
            pass
        if kicked < len(wins):
            kick_window(wins[kicked])
            kicked += 1
        time.sleep(0.8)
    return None, None


def close_active_tab(wait: float = 2.5) -> bool:
    """关闭当前激活 tab:激活 tab = 其子「关闭」按钮 rect 完整落在 Tab rect 内
    (非激活 tab 的关闭按钮 rect 是错位的悬停位)。"""
    for w in appex_windows():
        stack = [(w, 0)]
        while stack:
            c, d = stack.pop()
            try:
                if (c.ClassName or "") == CLASS_TAB:
                    tr = c.BoundingRectangle
                    for k in c.GetChildren():
                        if (k.Name or "") != CLOSE_BUTTON_NAME:
                            continue
                        kr = k.BoundingRectangle
                        if tr.left <= kr.left and kr.right <= tr.right and \
                                tr.top <= kr.top and kr.bottom <= kr.bottom:
                            if invoke_control(k):
                                time.sleep(wait)
                                return True
                if d < 18:
                    stack.extend((kid, d + 1) for kid in c.GetChildren())
            except Exception:
                continue
    return False


def close_article_tabs(max_close: int = 3, wait: float = 2.5) -> bool:
    """确保没有残留文章页(aid 锚点命中即视为文章页开着);残留会导致
    下一次提取拿到上一篇的 URL。"""
    for _ in range(max_close):
        w, h = find_host(lambda c: (c.AutomationId or "") in ARTICLE_MARKERS,
                         max_nodes=800)
        if h is None:
            return True
        close_active_tab(wait=wait)
        time.sleep(1.0)
    w, h = find_host(lambda c: (c.AutomationId or "") in ARTICLE_MARKERS, max_nodes=800)
    return h is None


def close_profile_tab(account: str, wait: float = 2.5, max_try: int = 3) -> bool:
    """若某 AppEx 窗口当前激活页为该账号主页,关闭该 tab(退回搜索页)。"""
    for _ in range(max_try):
        w, h = find_profile_host(account=account, kicks=0)
        if h is None:
            return True
        if active_doc(w) != account:
            return False  # 主页不是激活页,不冒险关 tab
        if not close_active_tab(wait=wait):
            time.sleep(1.0)
    return find_profile_host(account=account, kicks=0)[1] is None


def extract_article_urls(host, max_nodes: int = 5000):
    """文章宿主全树扫 ValuePattern.Value,返回 ({url: 控件名}, 遍历节点数)。

    尖峰C:833~1218 节点 / 1.8~2.2s;每篇实测恰 1 个 mp 文章 URL
    (偶有页内小程序链接,取最短者即文章自身,见 open_article_and_get_url)。
    """
    urls = {}
    n = 0
    for c in walk_ctrls(host, max_nodes=max_nodes, max_depth=34):
        n += 1
        try:
            vp = c.GetPattern(uia.PatternId.ValuePattern)
            if vp is None:
                continue
            v = vp.Value
        except Exception:
            continue
        if not v or "http" not in str(v):
            continue
        m = URL_RE.match(str(v))
        if m:
            urls.setdefault(m.group(0), (c.Name or "")[:40])
    return urls, n


def open_article_and_get_url(title_ctrl, open_timeout: float = 40.0,
                             scan_timeout: float = 20.0, max_nodes: int = 5000,
                             close_wait: float = 2.5):
    """打开标题所在文章,提取其 raw URL,关闭文章 tab。返回 (raw_url|None, url数)。

    标题 TextControl 自身无 InvokePattern,向上最多 5 级找可 Invoke 的祖先卡片
    (尖峰C:class js_article_card…)。
    """
    p = title_ctrl
    pat = None
    for _ in range(5):
        try:
            pat = p.GetPattern(uia.PatternId.InvokePattern)
        except Exception:
            pat = None
        if pat is not None:
            break
        try:
            p = p.GetParentControl()
        except Exception:
            break
        if p is None:
            break
    if pat is None:
        return None, -1
    try:
        pat.Invoke()
    except Exception:
        return None, -1
    w, h = find_article_host(timeout=open_timeout)
    if h is None:
        close_article_tabs(max_close=2, wait=close_wait)
        return None, -1
    deadline = time.time() + scan_timeout
    urls = {}
    while time.time() < deadline:
        urls, _n = extract_article_urls(h, max_nodes=max_nodes)
        if urls:
            break
        time.sleep(1.0)
    close_active_tab(wait=close_wait)
    if not urls:
        return None, 0
    # 每篇通常恰 1 个;偶含页内小程序链接时,最短者为主文 URL(尖峰C 经验)
    return sorted(urls, key=len)[0], len(urls)


if __name__ == "__main__":
    # 冒烟:python -m src.wechat_bot
    if hasattr(__import__("sys").stdout, "reconfigure"):
        __import__("sys").stdout.reconfigure(encoding="utf-8", errors="replace")
    wins = appex_windows()
    print(f"AppEx 窗口: {len(wins)} 个")
    _w, _h = find_profile_host(kicks=1)
    print(f"公众号主页: {host_doc_name(_h) if _h else '无'}")
    _w2, _h2, _e = find_search_entry()
    print(f"搜索页: {'可用' if _e else '未找到(请人工打开一次「搜一搜」)'}")
```

- [ ] **Step 2: 冒烟验证(微信已登录)**

Run: `.venv/Scripts/python.exe -m src.wechat_bot`
Expected: 打印 AppEx 窗口数(≥1)、主页名(若上轮尖峰残留则显示账号名,否则「无」)、搜索页状态。若微信中尚无任何公众号页面,输出 `0 个 / 无 / 未找到` 属正常(搜索页要等 Task 11 部署步骤人工打开后验证)。

- [ ] **Step 3: 若已有测试过的主页,用 spike 对照验证开文章链路**

Run: `.venv/Scripts/python.exe -c "import sys; sys.path.insert(0,'tools'); import spike_navigate"`(仅导入冒烟)
然后(若主页已打开):`.venv/Scripts/python.exe -c "from src.wechat_bot import *; w,h = find_profile_host(); print(bool(h)); print(open_article_and_get_url(scan_list(h)[0][0]) if h else 'no host')"`
Expected: `True`,并返回 `(https://mp.weixin.qq.com/s?__biz=…, 1)` 形态的元组;文章 tab 自动关闭。若树折叠报 None,先跑一次 `python -m src.wechat_bot`(kick 生效)再试。

- [ ] **Step 4: Commit**

```bash
git add src/wechat_bot.py
git commit -m "feat(bot): UIA封装(选窗/搜索/列表/逐篇URL提取/关tab),常量按尖峰实测"
```

---

### Task 9(v2): 主控 `src/orchestrator.py` + 入口 `run_crawl.py`

**Files:**
- Create: `src/orchestrator.py`
- Create: `run_crawl.py`
- Create: `tools/db_stats.py`(验收与日常巡检用的小工具)

- [ ] **Step 1: 实现 `src/orchestrator.py`**

```python
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
from datetime import date, timedelta

from . import canonical, version_check, wechat_bot as bot
from .canonical import find_stop_index, make_dedup_key, normalize_date_text, pair_publish_dates
from .config import CrawlConfig
from .db import Store


def setup_logging(log_dir) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    logfile = log_dir / f"crawl_{date.today().isoformat()}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(logfile, encoding="utf-8"),
                  logging.StreamHandler()],
    )
    return logging.getLogger("crawler")


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


def _collect_list(cfg: CrawlConfig, host, cutoff: str | None):
    """读列表;新账号(无水位线)滚动扩量。返回 (title_ctrls, pairs, dates)。"""
    titles, times = bot.scan_list(host, max_nodes=cfg.max_tree_nodes)
    if cutoff is None:
        for _ in range(cfg.new_account_screens):
            bot.scroll_once(host)
            time.sleep(cfg.scroll_wait_sec)
            t2, s2 = bot.scan_list(host, max_nodes=cfg.max_tree_nodes)
            if len(t2) == len(titles):
                break
            titles, times = t2, s2
    names = [(c.Name or "").strip() for c in titles]
    tops = [c.BoundingRectangle.top for c in titles]
    pairs = pair_publish_dates(list(zip(names, tops)), times)
    dates = [normalize_date_text(d) for _n, d in pairs]
    return titles, pairs, dates


def process_account(store: Store, cfg: CrawlConfig, name: str,
                    log: logging.Logger) -> dict:
    """抓一个账号,返回 {ok, new, upgraded, pending, message}。"""
    acc_id = store.get_or_create_account(name)
    watermark = store.watermark(acc_id)
    cutoff = None
    if watermark:
        cutoff = (date.fromisoformat(watermark) -
                  timedelta(days=cfg.overlap_days)).isoformat()

    w, host = bot.find_profile_host(account=name, kicks=cfg.kick_retry)
    if host is None:
        ok, msg = bot.search_open_profile(name)
        if not ok:
            return {"ok": False, "new": 0, "upgraded": 0, "pending": 0, "message": msg}
        w, host = bot.find_profile_host(account=name, kicks=cfg.kick_retry)
        if host is None:
            return {"ok": False, "new": 0, "upgraded": 0, "pending": 0,
                    "message": "主页已搜索但未就绪"}
    if not bot.close_article_tabs(max_close=2, wait=cfg.close_tab_wait_sec):
        log.warning("[%s] 存在残留文章 tab,继续(提取前仍会校验)", name)

    title_ctrls, pairs, dates = _collect_list(cfg, host, cutoff)
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

    seen = store.seen_fallbacks(acc_id)
    new = upgraded = pending = 0
    max_date = watermark
    for t_ctrl, (title, date_text) in zip(title_ctrls, pairs):
        if not title:
            continue
        fb = make_dedup_key(None, title, date_text)
        iso = normalize_date_text(date_text)
        if iso and (max_date is None or iso > max_date):
            max_date = iso
        if fb in seen:
            continue  # 已入库(含 pending),不重复打开文章页
        raw, _n = bot.open_article_and_get_url(
            t_ctrl, open_timeout=cfg.article_open_timeout_sec,
            scan_timeout=cfg.url_scan_timeout_sec,
            max_nodes=cfg.max_tree_nodes, close_wait=cfg.close_tab_wait_sec)
        canon = canonical.canonicalize_url(raw)
        result = store.upsert_article(acc_id, fb, canon, title, date_text, iso)
        if result == "new":
            new += 1
            if canon is None:
                pending += 1
        elif result == "upgraded":
            upgraded += 1
        seen.add(fb)
        log.info("[%s] + %s (%s) url=%s", name, title[:40],
                 date_text or "?", "ok" if canon else "PENDING")
        time.sleep(random.uniform(2.0, 5.0))  # 文章间节奏

    if max_date:
        store.set_watermark(acc_id, max_date)
    store.mark_crawled(acc_id)
    if not bot.close_profile_tab(name, wait=cfg.close_tab_wait_sec):
        log.warning("[%s] 主页 tab 未能关闭(不影响数据)", name)
    return {"ok": True, "new": new, "upgraded": upgraded, "pending": pending,
            "message": f"扫描{len(pairs)}条,新增{new},补URL{upgraded},待补{pending}"}


def run(cfg: CrawlConfig, only_account: str | None = None) -> int:
    log = setup_logging(cfg.log_dir)
    rep = version_check.check_environment(
        cfg.process_name, cfg.exe_path, cfg.expected_version_prefix)
    log.info("环境: %s", rep["message"])
    if not rep["ok"]:
        log.error("微信未就绪,本轮中止(请登录微信后等待下一轮)")
        return 1
    names = [only_account] if only_account else list(cfg.accounts)
    if only_account is None:
        random.shuffle(names)  # 全量轮乱序,模拟真人
    store = Store(cfg.db_path)
    run_id = store.start_run()
    ok_n = fail_n = new_n = 0
    try:
        for i, name in enumerate(names):
            if i:
                gap = random.uniform(cfg.account_gap_min_sec, cfg.account_gap_max_sec)
                log.info("等待 %.0fs 后处理下一个账号", gap)
                time.sleep(gap)
            log.info("=== 账号[%d/%d] %s ===", i + 1, len(names), name)
            try:
                st = process_account(store, cfg, name, log)
            except Exception:
                log.exception("账号 %s 处理异常", name)
                st = {"ok": False, "new": 0, "upgraded": 0, "pending": 0,
                      "message": "异常(见日志)"}
            ok_n += 1 if st["ok"] else 0
            fail_n += 0 if st["ok"] else 1
            new_n += st["new"]
            log.info("账号 %s: %s", name, st["message"])
    finally:
        store.finish_run(run_id, ok_count=ok_n, fail_count=fail_n, new_count=new_n)
        store.close()
    log.info("本轮完成: 成功%d 失败%d 新增%d", ok_n, fail_n, new_n)
    return 0 if fail_n == 0 else 1
```

- [ ] **Step 2: 实现 `run_crawl.py`**

```python
#!/usr/bin/env python
"""命令行入口:python run_crawl.py [--account 名称] [--check]"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.config import ConfigError, load_config
from src.orchestrator import run, run_check


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="微信公众号文章增量爬虫(单轮,UIA 直取)")
    ap.add_argument("--account", help="只抓指定公众号(单号试跑)")
    ap.add_argument("--check", action="store_true", help="环境自检,不抓取")
    args = ap.parse_args(argv)
    try:
        cfg = load_config()
    except ConfigError as exc:
        print(f"[配置错误] {exc}")
        return 2
    if args.check:
        return run_check(cfg)
    return run(cfg, only_account=args.account)


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 3: 实现 `tools/db_stats.py`**

```python
"""数据库巡检:python tools/db_stats.py [data/crawler.db]"""
import sqlite3
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

db = Path(sys.argv[1] if len(sys.argv) > 1 else "data/crawler.db")
if not db.exists():
    sys.exit(f"数据库不存在: {db}")
conn = sqlite3.connect(str(db))
conn.row_factory = sqlite3.Row
print("== 账号 ==")
for r in conn.execute(
        "SELECT name, last_crawled_at, max_publish_date FROM accounts ORDER BY name"):
    print(f"  {r['name']}: 水位线={r['max_publish_date']} 最近抓取={r['last_crawled_at']}")
print("== 文章(按账号) ==")
for r in conn.execute(
        "SELECT a.name, COUNT(*) n, SUM(s.url_status='ok') ok "
        "FROM articles s JOIN accounts a ON a.id=s.account_id GROUP BY a.name"):
    print(f"  {r['name']}: {r['n']} 篇(有URL {r['ok']})")
print("== 最近 10 篇 ==")
for r in conn.execute(
        "SELECT a.name, s.title, s.publish_date, s.url_status, substr(s.url,1,60) u "
        "FROM articles s JOIN accounts a ON a.id=s.account_id "
        "ORDER BY s.id DESC LIMIT 10"):
    print(f"  [{r['name']}] {r['publish_date'] or '?'} ({r['url_status']}) "
          f"{r['title'][:36]}\n      {r['u'] or ''}")
print("== 运行记录(最近 5 轮) ==")
for r in conn.execute(
        "SELECT started_at, finished_at, ok_count, fail_count, new_count "
        "FROM crawl_runs ORDER BY id DESC LIMIT 5"):
    print(f"  {r['started_at']} → {r['finished_at']}: "
          f"成功{r['ok_count']} 失败{r['fail_count']} 新增{r['new_count']}")
```

- [ ] **Step 4: 全量单测回归**

Run: `.venv/Scripts/python.exe -m pytest tests/ -v`
Expected: 26 passed(orchestrator 为集成层,单测覆盖在 Task 11 端到端)

- [ ] **Step 5: Commit**

```bash
git add src/orchestrator.py run_crawl.py tools/db_stats.py
git commit -m "feat(orchestrator): 单轮主控(水位线增量/逐篇URL增强/降级入库)+ CLI入口"
```

---

### Task 10(v2): 计划任务与 README

**Files:**
- Modify: `requirements.txt`(移除 mitmproxy)
- Create: `install_task.ps1`
- Create: `README.md`

- [ ] **Step 1: 更新 `requirements.txt`**

```
uiautomation>=2.0.20
PyYAML>=6.0.1
pytest>=8.2
```

- [ ] **Step 2: 写 `install_task.ps1`**

```powershell
# 注册/注销 Windows 计划任务:每天 08:05 与 19:05 各跑一轮抓取。
# 用法:
#   powershell -ExecutionPolicy Bypass -File install_task.ps1           # 注册
#   powershell -ExecutionPolicy Bypass -File install_task.ps1 -Remove   # 注销
param(
    [switch]$Remove,
    [string]$ProjectRoot = $PSScriptRoot
)
$ErrorActionPreference = "Stop"
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$Script = Join-Path $ProjectRoot "run_crawl.py"
$TaskName = "WechatArticleCrawler"

if ($Remove) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "已注销计划任务 $TaskName"
    exit 0
}
if (-not (Test-Path $Python)) {
    Write-Error "未找到虚拟环境 $Python;请先创建 .venv 并安装 requirements.txt"
    exit 1
}
$Action   = New-ScheduledTaskAction -Execute $Python -Argument "`"$Script`"" -WorkingDirectory $ProjectRoot
$Triggers = @(New-ScheduledTaskTrigger -Daily -At 08:05,
              New-ScheduledTaskTrigger -Daily -At 19:05)
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 2)
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Triggers `
    -Settings $Settings -Description "微信公众号文章增量爬虫(每天 08:05/19:05)" -Force
Write-Host "已注册计划任务 $TaskName(08:05 / 19:05,当前用户登录时运行)"
```

- [ ] **Step 3: 写 `README.md`**

````markdown
# 微信公众号文章爬虫(UIA 直取)

定时用公众号名称在 PC 微信中搜索并打开其主页,增量抓取**新发布文章**的
URL、标题、发布时间,存入本地 SQLite。每天 08:05 / 19:05 各自动运行一轮。

**原理**:全程 UI 自动化操控微信内置浏览器(WeChatAppEx.exe):搜索 →
公众号主页(即完整历史列表)→ 读取列表的标题与日期分组做增量判定 →
仅对未入库的新文章逐篇打开,从文章页无障碍树的 ValuePattern 提取
canonical URL → 入库。不注入、不逆向、不需要公众号后台权限;行为等价于
真人浏览。

## 安装

```powershell
D:\Python312\python.exe -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m pytest tests\ -v   # 单测应全绿
```

## 一次性部署

1. 启动 PC 微信并扫码登录(保持运行)。
2. **在微信中点开一次「搜一搜」**(放大镜),让内置浏览器窗口保持一个
   搜索页 tab —— 脚本每次搜索都复用它,不需要每次人工干预。
3. 自检:`.venv\Scripts\python.exe run_crawl.py --check`
   → 应输出「OK,可以抓取」。
4. 单号试跑:`.venv\Scripts\python.exe run_crawl.py --account 中金点睛`
5. 注册计划任务(当前用户,登录时运行):
   `powershell -ExecutionPolicy Bypass -File install_task.ps1`
   注销:`powershell -ExecutionPolicy Bypass -File install_task.ps1 -Remove`

## 名单与参数

- `config/accounts.yaml` — 公众号名称列表(每行一个)。
- `config/settings.yaml` — 停止条数、重叠天数、账号间隔、超时等。

## 查看数据

```
.venv\Scripts\python.exe tools\db_stats.py
```

或直接 SQL:`sqlite3 data\crawler.db "select title,url from articles order by id desc"`

## 运行与排障

- 日志:`logs\crawl_YYYY-MM-DD.log`(每轮一条 `crawl_runs` 记录)。
- `url_status='pending'` 的文章 = 当轮没提取到 URL(可能是无页内链接的
  简排版文章,或超时);次日该文仍在列表时自动重试补全。
- **桌面需解锁**:文章页提取全程可用 UIA 完成(锁屏也行),但搜索框
  粘贴依赖合成键鼠 —— 锁屏时段运行的轮次中,账号会失败并留日志,
  下一轮自动重试。
- **微信大版本更新**后控件可能变化:先 `python run_crawl.py --check`
  (版本不匹配会告警),再按 `docs\spike-findings.md` 用 `tools\`
  下的尖峰工具重新校准 `src\wechat_bot.py` 顶部常量。
- 微信未登录/未启动:本轮立即中止,日志提示,下一轮自动恢复。

## 已知限制

- 依赖微信客户端真实界面;窗口最小化可以,但**不能关闭**微信。
- 增量场景每号每轮通常 0~5 篇新文章,每篇提取约 12~20 秒(解锁态)。
- 文章 URL 为 canonical 5 参数形式(`__biz/mid/idx/sn/chksm`),
  公网可直接访问;同文多次提取逐字节一致,可作稳定主键。
````

- [ ] **Step 4: 虚拟环境卸载 mitmproxy(与 requirements 对齐)**

Run: `.venv/Scripts/python.exe -m pip uninstall -y mitmproxy && .venv/Scripts/python.exe -m pip check`
Expected: 卸载成功;`pip check` 无缺失依赖报错(单测仍全绿)。

- [ ] **Step 5: 计划任务注册冒烟**

Run: `powershell -ExecutionPolicy Bypass -File install_task.ps1 && powershell -Command "Get-ScheduledTask -TaskName WechatArticleCrawler | Select-Object TaskName,State"`
Expected: 注册成功,State=Ready。

- [ ] **Step 6: Commit**

```bash
git add requirements.txt install_task.ps1 README.md
git commit -m "feat(deploy): 计划任务注册脚本 + README;移除mitmproxy依赖"
```

---

### Task 11(v2): 端到端验收(真实微信,单号 → 全量 → 计划任务)

**Files:** 无新代码(验收记录写入 commit message;问题修复走相应模块)

- [ ] **Step 1: 全量单测**

Run: `.venv/Scripts/python.exe -m pytest tests/ -v`
Expected: 26 passed

- [ ] **Step 2: 环境自检**

Run: `.venv/Scripts/python.exe run_crawl.py --check`
Expected: 退出码 0,输出「OK,可以抓取」。若提示搜索页未找到:在微信中人工点开一次「搜一搜」后重跑(部署步骤,允许)。

- [ ] **Step 3: 单号端到端(中金点睛)**

Run: `.venv/Scripts/python.exe run_crawl.py --account 中金点睛`
Expected: 退出码 0;日志显示:主页打开 → 扫描 N 条(新账号滚 2 屏)→ 逐篇提取 URL → `新增K 补URL0 待补M`;`tools/db_stats.py` 可见该账号 K+M 篇、其中 ok 数 ≥ 1(首跑文章数应 > 0)。

- [ ] **Step 4: 二跑验证增量(同一账号)**

Run: `.venv/Scripts/python.exe run_crawl.py --account 中金点睛`
Expected: 日志显示 `扫描N条,新增0,补URL?,待补?`(首屏全为已入库文章,`seen_fallbacks` 命中跳过,不再逐篇打开);退出码 0。

- [ ] **Step 5: 第二账号 + 全量轮**

Run: `.venv/Scripts/python.exe run_crawl.py`
Expected: 两个账号乱序处理,账号间随机间隔 20~60s;`db_stats.py` 中两账号均有记录;`crawl_runs` 新增一行,`fail_count=0`。

- [ ] **Step 6: URL 公网抽验**

Run: `.venv/Scripts/python.exe -c "import sqlite3, urllib.request; c=sqlite3.connect('data/crawler.db'); u=c.execute(\"select url from articles where url_status='ok' order by id desc limit 1\").fetchone()[0]; r=urllib.request.urlopen(urllib.request.Request(u, headers={'User-Agent':'Mozilla/5.0'}), timeout=25); print(r.status, len(r.read()))"`
Expected: HTTP 200 且字节数 > 10000(文章全文)。

- [ ] **Step 7: 计划任务手动触发冒烟**

Run: `powershell -Command "Start-ScheduledTask -TaskName WechatArticleCrawler; Start-Sleep 20; Get-ScheduledTaskInfo -TaskName WechatArticleCrawler | Select-Object LastRunTime,LastTaskResult"`
Expected: LastRunTime 为刚才;等待其完成后 `crawl_runs` 多一行(任务保持注册状态,交付物含已生效调度)。

- [ ] **Step 8: 最终 Commit**

```bash
git add --all :!data
git commit -m "chore(accept): 端到端验收通过(单号/增量二跑/全量轮/计划任务触发)"
```
(若 `data/` 下无入库外文件,本步可能无可提交内容 —— 属正常,跳过即可)

---

## 收尾(所有任务完成后)

1. 派出**最终整体代码审查** subagent:范围 = feature 分支全部提交,重点 = 规格符合性、尖峰常量一致性、降级路径(pending/失败账号)完备性。
2. 审查问题修复后,向用户汇报:架构变更摘要、验收结果、数据库现状、已注册的计划任务、已知限制(解锁桌面/微信大版本更新重校准)。
