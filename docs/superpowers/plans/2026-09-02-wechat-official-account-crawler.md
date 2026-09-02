# 微信公众号文章爬虫 · 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 每天定时(08:05 / 19:05)通过已登录的 PC 微信 4.1.x 客户端自动遍历名单中的公众号,抓取其新发布文章的 URL/标题/发布时间,增量存入本地 SQLite。

**Architecture:** `run_crawl.py` 单轮主控启动 mitmdump 子进程(挂采集插件)并临时设置系统代理;`wechat_bot.py` 用 uiautomation 操控微信窗口(搜索→打开公众号→历史消息→滚动);`proxy_addon.py` 被动截获 `mp.weixin.qq.com/mp/profile_ext?action=getmsg` 响应,交 `parser.py` 解析、`db.py` 去重入库;主控轮询数据库判断增量抓尽即停止。

**Tech Stack:** Python 3.12(`D:\Python312`,项目 venv `.venv`)、mitmproxy(mitmdump)、uiautomation、PyYAML、pytest;Windows 11 任务计划程序调度。

**规格文档:** `docs/superpowers/specs/2026-09-02-wechat-official-account-crawler-design.md`

**重要约束:**
- 微信环境已实测:微信 4.1.12.55(`C:\Program Files\Tencent\Weixin\Weixin.exe`,进程名 `Weixin`)。
- Task 2 / Task 3 是**可行性尖峰**,需要在微信已登录的桌面上执行,部分步骤需人工配合。**任一尖峰失败 → 停止后续任务,向用户报告并重议技术路线**(规格§5.2)。
- Task 9(wechat_bot)的控件常量以 Task 2 的实测结果 `docs/spike-findings.md` 为准;代码按预期值写全,若尖峰实测不同,只改常量与控件路径,不改结构。

---

### Task 1: 项目骨架与虚拟环境

**Files:**
- Create: `.gitignore`, `requirements.txt`, `conftest.py`, `src/__init__.py`, `config/settings.yaml`, `config/accounts.yaml`, `data/.gitkeep`, `logs/.gitkeep`, `tests/fixtures/.gitkeep`

- [ ] **Step 1: 创建目录与基础文件**

`.gitignore`:
```
.venv/
__pycache__/
*.pyc
.pytest_cache/
data/*.db*
data/.current_account
data/spike_profile_ext.json
logs/*
!logs/.gitkeep
```

`requirements.txt`:
```
mitmproxy>=10.2
uiautomation>=2.0.20
PyYAML>=6.0.1
pytest>=8.2
```

`conftest.py`(空文件,使 pytest 把项目根目录加入 sys.path):
```python
```

`src/__init__.py`(空文件)。

`config/settings.yaml`:
```yaml
proxy_port: 8888            # mitmproxy 监听端口
scroll_screens: 2           # 每个账号最多滚动屏数(新账号用满)
account_timeout_sec: 90     # 单账号总超时
scroll_wait_sec: 15         # 每屏加载等待新增的超时;0 新增即认为抓尽
poll_interval_sec: 1        # 数据库轮询间隔
delay_min_sec: 20           # 账号之间随机间隔下限
delay_max_sec: 60           # 账号之间随机间隔上限
```

`config/accounts.yaml`(示例名单,部署时替换为真实名单):
```yaml
accounts:
  - 人民日报
  - 央视新闻
```

`data/.gitkeep`、`logs/.gitkeep`、`tests/fixtures/.gitkeep` 均为空文件。

- [ ] **Step 2: 创建虚拟环境并安装依赖**

Run(项目根目录 `d:\git\wechat-crawler`):
```bash
D:/Python312/python.exe -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
```
Expected: pip 安装完成无 error(mitmproxy 依赖较多,需 1~3 分钟)。

- [ ] **Step 3: 验证环境**

Run: `.venv/Scripts/python -m pytest`
Expected: `no tests ran`(exit code 5,属正常)。

Run: `.venv/Scripts/python -c "import mitmproxy, uiautomation, yaml; print('imports ok')"`
Expected: `imports ok`

- [ ] **Step 4: Commit**

```bash
git add -A && git commit -m "chore: 项目骨架、虚拟环境与依赖清单"
```

---

### Task 2: 可行性尖峰 A —— 微信 4.1.x UIA 控件树探测

**Files:**
- Create: `tools/spike_uia.py`, `docs/spike-findings.md`

> 本任务是人工辅助探测:需要微信已登录。产出 `docs/spike-findings.md`,Task 9 依赖它。

- [ ] **Step 1: 编写探测脚本**

`tools/spike_uia.py`:
```python
"""尖峰A:探测微信 4.1.x 的 UIA 控件树。运行前确保微信已登录。"""
import sys
import uiautomation as uia

def dump(ctrl, depth=0, max_depth=5, max_children=40):
    name = (ctrl.Name or "").strip()
    print(f"{'  ' * depth}{ctrl.ControlType} class={ctrl.ClassName!r} "
          f"name={name!r} auto_id={ctrl.AutomationId!r}")
    if depth >= max_depth:
        return
    for kid in ctrl.GetChildren()[:max_children]:
        dump(kid, depth + 1, max_depth, max_children)

if __name__ == "__main__":
    win = uia.WindowControl(searchDepth=1, Name="微信")
    if not win.Exists(5, 1):
        sys.exit("未找到微信主窗口(标题含'微信'),请确认已登录")
    print(f"主窗口: Name={win.Name!r} ClassName={win.ClassName!r}")
    dump(win, max_depth=4)
```

- [ ] **Step 2: 探测主窗口控件树**

Run: `.venv/Scripts/python tools/spike_uia.py > spike_uia_main.txt 2>&1`
Expected: 输出控件树。人工检查 `spike_uia_main.txt`:
- 主窗口 ClassName 是否形如 `mmui::MainWindow`(4.x)或 `WeChatMainWndForPC`(3.x)
- 树中能否看到搜索框相关控件(含「搜索」字样的 EditControl)

- [ ] **Step 3: 探测公众号历史消息页控件树(需人工配合)**

人工操作:在微信里搜索任一公众号(如「人民日报」),打开其主页,再点进历史消息/文章列表页,**保持该页面打开**,然后:

Run: `.venv/Scripts/python tools/spike_uia.py > spike_uia_history.txt 2>&1`

对比两次输出,确认:
- 历史消息页是主窗口内的子 Pane,还是独立顶层窗口(记下其 ClassName/Name)
- 找到「查看历史消息 / 历史消息 / 全部消息 / 更多消息」入口按钮的实际 Name 文案
- 滚动目标区域的控件类型(DocumentControl / PaneControl / ListControl)

- [ ] **Step 4: 记录发现并做判定**

创建 `docs/spike-findings.md`:
```markdown
# 尖峰发现记录(2026-09-02)

## 尖峰A:UIA 控件树(微信 4.1.12.55)
- 主窗口 Name 子串:【填写,预期「微信」】
- 主窗口 ClassName:【填写】
- 搜索方式:【Ctrl+F 热键 可用/不可用;搜索框 AutomationId:____】
- 搜索结果公众号条目定位:【填写 Name/控件类型】
- 历史消息入口按钮 Name 文案:【填写】
- 历史消息页容器:【独立窗口 class=____ / 主窗口内 Pane】
- 滚动方式验证:【鼠标滚轮 WheelDown 于页面中心 有效/无效;其他方式:____】
- 结论:【UIA 可自动化 / 不可自动化】

## 尖峰B:抓包(见 Task 3 后补)
- profile_ext 截获:【成功/失败】
- 真实响应样本:【tests/fixtures/profile_ext_real.json 已保存/接口结构与合成样本差异:____】
```

- [ ] **Step 5: Commit**

```bash
git add tools/spike_uia.py docs/spike-findings.md
git commit -m "chore: 尖峰A——微信4.1 UIA控件树探测记录"
```

**🚦 Gate A:** 若「结论:不可自动化」(控件树完全不可见/无法寻址),停止后续任务,向用户报告,回退规格§5.1 降级方案(并行安装微信 3.9.x + wxauto)或重议路线。

---

### Task 3: 可行性尖峰 B —— mitmproxy 截获 profile_ext 验证

**Files:**
- Create: `tools/spike_capture_addon.py`, `tools/spike_set_proxy.ps1`
- Create(可能): `tests/fixtures/profile_ext_real.json`

- [ ] **Step 1: 编写系统代理开关脚本**

`tools/spike_set_proxy.ps1`:
```powershell
# 尖峰B辅助:开启/关闭用户级系统代理。用法:
#   开:powershell -ExecutionPolicy Bypass -File tools/spike_set_proxy.ps1
#   关:powershell -ExecutionPolicy Bypass -File tools/spike_set_proxy.ps1 -Off
param([switch]$Off, [int]$Port = 8888)
$k = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings'
if ($Off) {
    Set-ItemProperty -Path $k -Name ProxyEnable -Value 0
} else {
    Set-ItemProperty -Path $k -Name ProxyEnable -Value 1
    Set-ItemProperty -Path $k -Name ProxyServer -Value "127.0.0.1:$Port"
}
Add-Type -TypeDefinition @"
using System; using System.Runtime.InteropServices;
public class WinInet {
    [DllImport("wininet.dll")] public static extern bool InternetSetOption(IntPtr h, int opt, IntPtr buf, int len);
}
"@
[WinInet]::InternetSetOption([IntPtr]::Zero, 39, [IntPtr]::Zero, 0) | Out-Null
[WinInet]::InternetSetOption([IntPtr]::Zero, 37, [IntPtr]::Zero, 0) | Out-Null
Write-Host "系统代理已 $(if ($Off) { '关闭' } else { "指向 127.0.0.1:$Port" })"
```

- [ ] **Step 2: 编写采集验证插件**

`tools/spike_capture_addon.py`:
```python
"""尖峰B:打印 mp.weixin.qq.com 相关请求,验证能否截获 profile_ext。"""
import json
from urllib.parse import urlparse, parse_qs

def response(flow):
    url = flow.request.pretty_url
    q = urlparse(url)
    host = q.hostname or ""
    if "qq.com" not in host:
        return
    action = dict(parse_qs(q.query)).get("action", [])
    print(f"[spike] {q.hostname}{q.path} action={action}", flush=True)
    if q.hostname == "mp.weixin.qq.com" and q.path == "/mp/profile_ext" \
            and action and action[0] == "getmsg":
        body = flow.response.get_text()
        with open("data/spike_profile_ext.json", "w", encoding="utf-8") as f:
            f.write(body)
        try:
            payload = json.loads(body)
            n = len(json.loads(payload["general_msg_list"]).get("list", []))
            print(f"[spike] ★ 截获 profile_ext getmsg,list 条数={n},已存 data/spike_profile_ext.json", flush=True)
        except Exception as e:
            print(f"[spike] profile_ext 响应结构非预期: {e}(原始体已保存)", flush=True)
```

- [ ] **Step 3: 生成并安装 mitmproxy CA 证书(一次性)**

Run:
```bash
.venv/Scripts/mitmdump --listen-port 8888 &
sleep 6 && ls ~/.mitmproxy/
```
Expected: `~/.mitmproxy/` 下出现 `mitmproxy-ca-cert.cer` 等文件。然后 Ctrl+C 杀掉 mitmdump,安装证书(会弹确认框,**请用户点「是」**):

```bash
certutil -user -addstore root "$USERPROFILE/.mitmproxy/mitmproxy-ca-cert.cer"
```
Expected: `CA 证书已添加到存储区中` 或 `...已存在`。

- [ ] **Step 4: 启动 mitmdump + 系统代理,人工触发一次公众号访问**

```bash
.venv/Scripts/mitmdump --listen-port 8888 -s tools/spike_capture_addon.py
```
另开终端:
```bash
powershell -ExecutionPolicy Bypass -File tools/spike_set_proxy.ps1
```
**人工操作**(关键验证步骤):在微信中搜索任一公众号 → 打开主页 → 点进历史消息/文章列表,滚动一屏。

Expected: mitmdump 控制台出现 `[spike] ★ 截获 profile_ext getmsg, list 条数=N`。

若微信完全不出任何 `[spike]` 行 → 内嵌页面未走系统代理,尝试以管理员运行 mitmdump 换用 local 模式:
```bash
# 管理员终端:
.venv/Scripts/mitmdump --mode local:Weixin.exe -s tools/spike_capture_addon.py
```
local 模式成功 → 记录到 findings 并把 orchestrator 的代理启动方式改为该模式;仍失败 → 🚦 Gate B。

- [ ] **Step 5: 还原代理并保存真实样本**

```bash
powershell -ExecutionPolicy Bypass -File tools/spike_set_proxy.ps1 -Off
cp data/spike_profile_ext.json tests/fixtures/profile_ext_real.json
```

- [ ] **Step 6: 更新 `docs/spike-findings.md` 尖峰B小节并提交**

在 `docs/spike-findings.md` 的「尖峰B」小节填入:截获结果、代理方式(system / local)、真实样本与合成样本结构差异。

```bash
git add tools/ tests/fixtures/ docs/spike-findings.md
git commit -m "chore: 尖峰B——mitmproxy截获profile_ext验证与真实样本"
```

**🚦 Gate B:** 若两种模式均无法截获 → 停止,向用户报告,重议技术路线(规格§5.2)。

---

### Task 4: 配置加载 `src/config.py`(TDD)

**Files:**
- Create: `src/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: 写失败测试**

`tests/test_config.py`:
```python
import textwrap
from src.config import load_settings, load_accounts


def test_load_settings_reads_yaml(tmp_path):
    p = tmp_path / "settings.yaml"
    p.write_text(textwrap.dedent("""
        proxy_port: 9999
        delay_max_sec: 45
        unknown_key: ignored
    """), encoding="utf-8")
    s = load_settings(p)
    assert s.proxy_port == 9999
    assert s.delay_max_sec == 45
    assert s.scroll_screens == 2          # 未写的键取默认值


def test_load_settings_missing_file_uses_defaults(tmp_path):
    s = load_settings(tmp_path / "nope.yaml")
    assert s.proxy_port == 8888


def test_load_accounts_strips_and_skips_empty(tmp_path):
    p = tmp_path / "accounts.yaml"
    p.write_text('accounts:\n  - 人民日报\n  - "  央视新闻 "\n  - ""\n', encoding="utf-8")
    assert load_accounts(p) == ["人民日报", "央视新闻"]
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/Scripts/python -m pytest tests/test_config.py -v`
Expected: FAIL / ERROR(`ModuleNotFoundError: No module named 'src.config'`)

- [ ] **Step 3: 实现**

`src/config.py`:
```python
"""配置加载:settings.yaml 与 accounts.yaml。"""
from dataclasses import dataclass, fields
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    proxy_port: int = 8888
    scroll_screens: int = 2
    account_timeout_sec: int = 90
    scroll_wait_sec: int = 15
    poll_interval_sec: int = 1
    delay_min_sec: int = 20
    delay_max_sec: int = 60


def load_settings(path: Path = PROJECT_ROOT / "config" / "settings.yaml") -> Settings:
    path = Path(path)
    if not path.exists():
        return Settings()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    known = {f.name for f in fields(Settings)}
    return Settings(**{k: v for k, v in data.items() if k in known})


def load_accounts(path: Path = PROJECT_ROOT / "config" / "accounts.yaml") -> list[str]:
    path = Path(path)
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    names = data.get("accounts") or []
    return [str(n).strip() for n in names if str(n).strip()]
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/Scripts/python -m pytest tests/test_config.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add src/config.py tests/test_config.py
git commit -m "feat: 配置加载(settings/accounts yaml)"
```

---

### Task 5: 持久层 `src/db.py`(TDD)

**Files:**
- Create: `src/db.py`
- Test: `tests/test_db.py`

- [ ] **Step 1: 写失败测试**

`tests/test_db.py`:
```python
from datetime import datetime, timedelta

import pytest

from src import db
from src.parser import ArticleRecord   # Task 6 才创建;本任务先建一个空壳亦可(见 Step 3 备注)


def make_rec(url, title="t", hours=0):
    return ArticleRecord(
        account_name="x", title=title, url=url,
        publish_time=datetime(2026, 9, 1, 12, 0) + timedelta(hours=hours))


@pytest.fixture()
def conn():
    c = db.connect(":memory:")
    c.executescript(db.SCHEMA)
    yield c
    c.close()


def test_account_get_or_create_idempotent(conn):
    a = db.get_or_create_account(conn, "人民日报")
    assert a == db.get_or_create_account(conn, "人民日报")


def test_upsert_articles_dedups_by_url(conn):
    aid = db.get_or_create_account(conn, "人民日报")
    n1 = db.upsert_articles(conn, aid, [make_rec("https://a/1"), make_rec("https://a/2")])
    n2 = db.upsert_articles(conn, aid, [make_rec("https://a/2"), make_rec("https://a/3")])
    assert (n1, n2) == (2, 1)
    assert conn.execute("SELECT COUNT(*) c FROM articles").fetchone()["c"] == 3


def test_watermark_moves_forward_only(conn):
    aid = db.get_or_create_account(conn, "人民日报")
    db.upsert_articles(conn, aid, [make_rec("https://a/1", hours=1)])
    db.upsert_articles(conn, aid, [make_rec("https://a/2", hours=0)])   # 更早的文章
    assert db.get_watermark(conn, aid) == datetime(2026, 9, 1, 13, 0).isoformat(sep=" ")


def test_mark_crawled_and_recent(conn):
    aid = db.get_or_create_account(conn, "人民日报")
    db.upsert_articles(conn, aid, [make_rec("https://a/1", hours=2), make_rec("https://a/2", hours=5)])
    assert db.fetch_recent_articles(conn, aid, limit=1)[0]["url"] == "https://a/2"
    db.mark_crawled(conn, aid)
    assert conn.execute("SELECT last_crawled_at FROM accounts WHERE id=?", (aid,)) \
        .fetchone()["last_crawled_at"] is not None


def test_runs(conn):
    rid = db.start_run(conn)
    db.finish_run(conn, rid, ok_count=3, fail_count=1, new_count=7)
    row = conn.execute("SELECT * FROM crawl_runs WHERE id=?", (rid,)).fetchone()
    assert row["ok_count"] == 3 and row["finished_at"] is not None
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/Scripts/python -m pytest tests/test_db.py -v`
Expected: FAIL / ERROR(No module named 'src.db';若 parser 也缺失则一并报错,属预期——可先创建 `src/parser.py` 的空壳 `ArticleRecord` 数据类让本任务可跑,Task 6 再补全解析函数):

```python
# src/parser.py(Task 5 阶段的最小空壳)
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class ArticleRecord:
    account_name: str
    title: str
    url: str
    publish_time: datetime
```

- [ ] **Step 3: 实现**

`src/db.py`:
```python
"""SQLite 持久层:账号、文章(按 URL 去重)、每轮运行统计。

upsert_articles 接受任意带 .url/.title/.publish_time 属性的对象(鸭子类型),
不 import parser,避免模块耦合。
"""
import sqlite3
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "crawler.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    last_crawled_at TEXT,
    max_publish_time TEXT
);
CREATE TABLE IF NOT EXISTS articles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    url TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    publish_time TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS crawl_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    ok_count INTEGER NOT NULL DEFAULT 0,
    fail_count INTEGER NOT NULL DEFAULT 0,
    new_count INTEGER NOT NULL DEFAULT 0
);
"""


def connect(db_path=DEFAULT_DB_PATH):
    path = Path(db_path)
    if str(db_path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db(db_path=DEFAULT_DB_PATH):
    conn = connect(db_path)
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


def get_or_create_account(conn, name) -> int:
    row = conn.execute("SELECT id FROM accounts WHERE name = ?", (name,)).fetchone()
    if row:
        return row["id"]
    conn.execute("INSERT INTO accounts(name) VALUES (?)", (name,))
    conn.commit()
    return conn.execute("SELECT id FROM accounts WHERE name = ?", (name,)).fetchone()["id"]


def get_watermark(conn, account_id) -> str | None:
    row = conn.execute(
        "SELECT max_publish_time FROM accounts WHERE id = ?", (account_id,)).fetchone()
    return row["max_publish_time"] if row else None


def upsert_articles(conn, account_id, records) -> int:
    """按 URL 去重插入;返回新插入条数;顺带把水位线前移(只进不退)。"""
    new_count, max_pub = 0, None
    for r in records:
        pub = r.publish_time.isoformat(sep=" ")
        cur = conn.execute(
            "INSERT OR IGNORE INTO articles(account_id, url, title, publish_time) VALUES (?,?,?,?)",
            (account_id, r.url, r.title, pub))
        new_count += cur.rowcount
        if max_pub is None or pub > max_pub:
            max_pub = pub
    if max_pub and max_pub > (get_watermark(conn, account_id) or ""):
        conn.execute("UPDATE accounts SET max_publish_time = ? WHERE id = ?", (max_pub, account_id))
    conn.commit()
    return new_count


def fetch_recent_articles(conn, account_id, limit=10):
    return conn.execute(
        "SELECT url, title, publish_time FROM articles WHERE account_id = ? "
        "ORDER BY publish_time DESC LIMIT ?", (account_id, limit)).fetchall()


def mark_crawled(conn, account_id, when: datetime | None = None):
    ts = (when or datetime.now()).isoformat(sep=" ", timespec="seconds")
    conn.execute("UPDATE accounts SET last_crawled_at = ? WHERE id = ?", (ts, account_id))
    conn.commit()


def start_run(conn) -> int:
    conn.execute("INSERT INTO crawl_runs(started_at) VALUES (?)",
                 (datetime.now().isoformat(sep=" ", timespec="seconds"),))
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def finish_run(conn, run_id, ok_count, fail_count, new_count):
    conn.execute(
        "UPDATE crawl_runs SET finished_at=?, ok_count=?, fail_count=?, new_count=? WHERE id=?",
        (datetime.now().isoformat(sep=" ", timespec="seconds"),
         ok_count, fail_count, new_count, run_id))
    conn.commit()
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/Scripts/python -m pytest tests/test_db.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/db.py src/parser.py tests/test_db.py
git commit -m "feat: SQLite持久层(账号/文章去重/水位线/运行统计)"
```

---

### Task 6: 解析器 `src/parser.py` + 抓包样本(TDD)

**Files:**
- Create: `tests/fixtures/profile_ext_sample.json`
- Modify: `src/parser.py`(补全 Task 5 的空壳)
- Test: `tests/test_parser.py`

- [ ] **Step 1: 创建合成样本 fixture(结构与真实 profile_ext 一致:`general_msg_list` 是内嵌 JSON 字符串,含多图文与纯文本消息)**

`tests/fixtures/profile_ext_sample.json`:
```json
{
  "ret": 0,
  "errmsg": "ok",
  "can_msg_continue": 1,
  "next_offset": 10,
  "general_msg_list": "{\"list\":[{\"comm_msg_info\":{\"id\":10001,\"datetime\":1756790400,\"type\":49},\"app_msg_ext_info\":{\"title\":\"第一篇主图文&amp;专稿\",\"content_url\":\"http://mp.weixin.qq.com/s?__biz=MzA1&amp;mid=100&amp;idx=1&amp;sn=abc#rd\",\"digest\":\"摘要1\",\"multi_app_msg_item_list\":[{\"title\":\"次图文一\",\"content_url\":\"http://mp.weixin.qq.com/s?__biz=MzA1&amp;mid=100&amp;idx=2&amp;sn=def\"},{\"title\":\"次图文二\",\"content_url\":\"http://mp.weixin.qq.com/s?__biz=MzA1&amp;mid=100&amp;idx=3&amp;sn=ghi\"}]}},{\"comm_msg_info\":{\"id\":10002,\"datetime\":1756704000,\"type\":1}}]}"
}
```

> Task 3 若已产出 `tests/fixtures/profile_ext_real.json`,测试追加一条真实样本冒烟断言(存在时才跑):
> `pytest.importorskip` 不适用;用 `@pytest.mark.skipif(not Path(...).exists(), ...)`。

- [ ] **Step 2: 写失败测试**

`tests/test_parser.py`:
```python
import json
from datetime import datetime
from pathlib import Path

import pytest

from src.parser import parse_profile_ext

FIXTURE = Path(__file__).parent / "fixtures" / "profile_ext_sample.json"
REAL = Path(__file__).parent / "fixtures" / "profile_ext_real.json"


def load():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_parses_main_and_multi_items():
    records = parse_profile_ext(load(), "人民日报")
    assert [r.title for r in records] == ["第一篇主图文&专稿", "次图文一", "次图文二"]


def test_unescapes_url_and_strips_fragment():
    r = parse_profile_ext(load(), "人民日报")[0]
    assert r.url == "http://mp.weixin.qq.com/s?__biz=MzA1&mid=100&idx=1&sn=abc"
    assert "&amp;" not in r.url and "#" not in r.url


def test_publish_time_from_comm_msg_info():
    records = parse_profile_ext(load(), "人民日报")
    assert records[0].publish_time == datetime.fromtimestamp(1756790400)
    assert records[2].publish_time == datetime.fromtimestamp(1756704000)


def test_text_only_push_produces_no_record():
    assert len(parse_profile_ext(load(), "人民日报")) == 3


def test_garbage_payload_returns_empty():
    assert parse_profile_ext(None, "x") == []
    assert parse_profile_ext({"general_msg_list": "not-json"}, "x") == []
    assert parse_profile_ext({}, "x") == []


@pytest.mark.skipif(not REAL.exists(), reason="尖峰B真实样本未保存")
def test_real_fixture_smoke():
    records = parse_profile_ext(json.loads(REAL.read_text(encoding="utf-8")), "真实号")
    assert all(r.url.startswith("http") for r in records)
```

- [ ] **Step 3: 运行确认失败**

Run: `.venv/Scripts/python -m pytest tests/test_parser.py -v`
Expected: FAIL(`ImportError: cannot import name 'parse_profile_ext'`)

- [ ] **Step 4: 实现(在 Task 5 的 `src/parser.py` 空壳上追加)**

`src/parser.py` 完整内容:
```python
"""profile_ext 接口 JSON → ArticleRecord。纯函数,不做 IO,便于测试。

注意:general_msg_list 在响应里是「内嵌 JSON 字符串」,需二次解析;
一次群发多篇(多图文)时,次图文在 app_msg_ext_info.multi_app_msg_item_list。
"""
import html
import json
from dataclasses import dataclass
from datetime import datetime

EPOCH = datetime(1970, 1, 1)


@dataclass(frozen=True)
class ArticleRecord:
    account_name: str
    title: str
    url: str
    publish_time: datetime


def parse_profile_ext(payload, account_name: str) -> list[ArticleRecord]:
    if not isinstance(payload, dict):
        return []
    msg_list = payload.get("general_msg_list")
    if isinstance(msg_list, str):
        try:
            msg_list = json.loads(msg_list)
        except (TypeError, ValueError):
            return []
    if not isinstance(msg_list, dict):
        return []
    records = []
    for item in msg_list.get("list") or []:
        if not isinstance(item, dict):
            continue
        ts = (item.get("comm_msg_info") or {}).get("datetime")
        try:
            pub = datetime.fromtimestamp(ts) if ts else EPOCH
        except (TypeError, ValueError, OSError, OverflowError):
            pub = EPOCH
        main = item.get("app_msg_ext_info")
        if not (isinstance(main, dict) and main.get("content_url")):
            continue  # 纯文本/图片消息,无文章
        records.append(_record(main, pub, account_name))
        for sub in main.get("multi_app_msg_item_list") or []:
            if isinstance(sub, dict) and sub.get("content_url"):
                records.append(_record(sub, pub, account_name))
    return records


def _record(info: dict, pub: datetime, account_name: str) -> ArticleRecord:
    url = html.unescape(info["content_url"]).split("#", 1)[0]
    title = html.unescape(info.get("title") or "").strip()
    return ArticleRecord(account_name=account_name, title=title, url=url, publish_time=pub)
```

- [ ] **Step 5: 运行确认通过**

Run: `.venv/Scripts/python -m pytest tests/test_parser.py tests/test_db.py -v`
Expected: 全部 passed(db 测试使用 ArticleRecord,回归验证)

- [ ] **Step 6: Commit**

```bash
git add src/parser.py tests/test_parser.py tests/fixtures/profile_ext_sample.json
git commit -m "feat: profile_ext接口JSON解析(多图文/URL反转义/纯函数)"
```

---

### Task 7: mitmproxy 插件 `src/proxy_addon.py`(TDD)

**Files:**
- Create: `src/proxy_addon.py`
- Test: `tests/test_proxy_addon.py`

- [ ] **Step 1: 写失败测试**

`tests/test_proxy_addon.py`:
```python
from pathlib import Path

from src.proxy_addon import extract_records

FIXTURE = Path(__file__).parent / "fixtures" / "profile_ext_sample.json"
URL = "https://mp.weixin.qq.com/mp/profile_ext?action=getmsg&__biz=MzA1&f=json&count=10"


def test_extracts_from_valid_url():
    records = extract_records(URL, FIXTURE.read_text(encoding="utf-8"), "人民日报")
    assert len(records) == 3
    assert records[0].account_name == "人民日报"


def test_ignores_other_urls_and_actions():
    body = FIXTURE.read_text(encoding="utf-8")
    assert extract_records("https://mp.weixin.qq.com/cgi-bin/home?t=home", body, "x") == []
    assert extract_records("https://mp.weixin.qq.com/mp/profile_ext?action=other", body, "x") == []


def test_ignores_invalid_body():
    assert extract_records(URL, "<html>403</html>", "x") == []
    assert extract_records(URL, "", "x") == []
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/Scripts/python -m pytest tests/test_proxy_addon.py -v`
Expected: FAIL(`ModuleNotFoundError: No module named 'src.proxy_addon'`)

- [ ] **Step 3: 实现**

`src/proxy_addon.py`:
```python
"""mitmproxy 插件:被动截获 profile_ext 响应 → parser 解析 → db 入库。

由 mitmdump 以 `-s src/proxy_addon.py` 方式加载(独立进程)。
配置经环境变量传递:
  CRAWLER_DB              数据库路径(默认 data/crawler.db)
  CRAWLER_CURRENT_ACCOUNT 「当前账号名」文本文件路径,由主控在导航每个账号前写入
"""
import json
import os
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src import db, parser  # noqa: E402

TARGET_HOST = "mp.weixin.qq.com"
TARGET_PATH = "/mp/profile_ext"


def extract_records(url: str, body_text: str, account_name: str):
    """纯函数:请求 URL + 响应文本 → 文章记录列表。供单元测试与 addon 复用。"""
    q = urlparse(url)
    if q.hostname != TARGET_HOST or q.path != TARGET_PATH:
        return []
    qs = parse_qs(q.query)
    if qs.get("action", [""])[0] != "getmsg":
        return []
    try:
        payload = json.loads(body_text)
    except (TypeError, ValueError):
        return []
    return parser.parse_profile_ext(payload, account_name)


class ProfileExtAddon:
    def __init__(self):
        self.db_path = os.environ.get("CRAWLER_DB", str(db.DEFAULT_DB_PATH))
        self.account_file = Path(os.environ.get(
            "CRAWLER_CURRENT_ACCOUNT",
            str(PROJECT_ROOT / "data" / ".current_account")))
        db.init_db(self.db_path)

    def _current_account(self) -> str:
        try:
            return self.account_file.read_text(encoding="utf-8").strip() or "unknown"
        except OSError:
            return "unknown"

    def response(self, flow):
        body_text = flow.response.get_text()
        records = extract_records(
            flow.request.pretty_url, body_text, self._current_account())
        if not records:
            # 命中目标接口却解析出 0 条:可能是正常空列表,也可能微信改版——
            # 记录响应片段供排查(规格§4「日志记录原始响应片段」)
            q = urlparse(flow.request.pretty_url)
            if q.path == TARGET_PATH:
                print(f"[crawler] profile_ext 解析 0 条,响应体前 200 字符: {body_text[:200]!r}",
                      flush=True)
            return
        conn = db.connect(self.db_path)
        try:
            aid = db.get_or_create_account(conn, records[0].account_name)
            new = db.upsert_articles(conn, aid, records)
            print(f"[crawler] {records[0].account_name}: 截获 {len(records)} 篇, 新增 {new} 篇",
                  flush=True)
        finally:
            conn.close()


addons = [ProfileExtAddon()]
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/Scripts/python -m pytest tests/test_proxy_addon.py tests/test_parser.py -v`
Expected: 全部 passed

- [ ] **Step 5: 冒烟验证插件可被 mitmdump 加载**

Run(后台起 5 秒即停):
```bash
CRAWLER_DB=data/smoke.db timeout 5 .venv/Scripts/mitmdump --listen-port 18888 -s src/proxy_addon.py; rm -f data/smoke.db*
```
Expected: mitmdump 正常启动无 traceback(加载插件成功)。Windows Git Bash 无 timeout 时改用:`.venv/Scripts/mitmdump --listen-port 18888 -s src/proxy_addon.py & sleep 5; kill %1`。

- [ ] **Step 6: Commit**

```bash
git add src/proxy_addon.py tests/test_proxy_addon.py
git commit -m "feat: mitmproxy插件——被动截获profile_ext入库"
```

---

### Task 8: 环境自检 `src/version_check.py`(TDD)

**Files:**
- Create: `src/version_check.py`
- Test: `tests/test_version_check.py`

- [ ] **Step 1: 写失败测试**

`tests/test_version_check.py`:
```python
from src.version_check import check_port_free, is_supported_version


def test_version_exact_match():
    ok, msg = is_supported_version("4.1.12.55")
    assert ok and "4.1.12.55" in msg


def test_version_mismatch_warns_but_continues():
    ok, msg = is_supported_version("4.2.0.1")
    assert ok and "不同" in msg


def test_version_missing_fails():
    ok, _ = is_supported_version(None)
    assert not ok
    ok, _ = is_supported_version("")
    assert not ok


def test_port_free_on_random_high_port():
    assert check_port_free(58732) is True
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/Scripts/python -m pytest tests/test_version_check.py -v`
Expected: FAIL(`ModuleNotFoundError: No module named 'src.version_check'`)

- [ ] **Step 3: 实现**

`src/version_check.py`:
```python
"""环境自检:微信进程/版本、代理端口、mitmproxy CA 证书。"""
import re
import socket
import subprocess

WECHAT_PROCESS = "Weixin"           # 微信 4.x 进程名(不带 .exe)
SUPPORTED_MAJOR_MINOR = ("4", "1")  # UI 自动化按 4.1.x 适配
MITMPROXY_CERT_NAME = "mitmproxy"


def is_supported_version(version: str | None) -> tuple[bool, str]:
    """(可继续运行?, 提示信息)。版本不同→True+告警;拿不到版本→False。"""
    if not version:
        return False, "未检测到微信版本号(客户端可能未运行)"
    m = re.match(r"(\d+)\.(\d+)", version)
    if not m:
        return False, f"无法解析版本号: {version}"
    major, minor = m.groups()
    if (major, minor) == SUPPORTED_MAJOR_MINOR:
        return True, f"微信版本 {version},已适配"
    return True, (f"微信版本 {version} 与适配版本 {'.'.join(SUPPORTED_MAJOR_MINOR)} 不同,"
                  f"UI 自动化可能失败,请关注 logs/mitmdump.log 与运行日志")


def check_port_free(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket() as s:
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def get_wechat_info() -> dict | None:
    """返回 {'path':…, 'version':…};客户端未运行返回 None。"""
    ps = ("Get-Process Weixin -ErrorAction SilentlyContinue | "
          "Select-Object -First 1 -ExpandProperty Path")
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       capture_output=True, text=True, timeout=20)
    path = r.stdout.strip()
    if not path:
        return None
    ps_ver = f'(Get-Item "{path}").VersionInfo.ProductVersion'
    r2 = subprocess.run(["powershell", "-NoProfile", "-Command", ps_ver],
                        capture_output=True, text=True, timeout=20)
    return {"path": path, "version": r2.stdout.strip() or None}


def cert_installed() -> bool:
    """当前用户根证书库中是否已有 mitmproxy CA。"""
    r = subprocess.run(["certutil", "-store", "-user", "root", MITMPROXY_CERT_NAME],
                       capture_output=True, text=True, timeout=20)
    return r.returncode == 0 and MITMPROXY_CERT_NAME in (r.stdout + r.stderr)
```

- [ ] **Step 4: 运行确认通过 + 真实环境自检**

Run: `.venv/Scripts/python -m pytest tests/test_version_check.py -v`
Expected: 4 passed

Run: `.venv/Scripts/python -c "from src.version_check import get_wechat_info, is_supported_version; i=get_wechat_info(); print(i); print(is_supported_version(i['version'] if i else None))"`
Expected: `{'path': 'C:\\Program Files\\Tencent\\Weixin\\Weixin.exe', 'version': '4.1.12.55'}` 与「已适配」提示(微信运行中)。

- [ ] **Step 5: Commit**

```bash
git add src/version_check.py tests/test_version_check.py
git commit -m "feat: 环境自检(微信版本/端口/证书)"
```

---

### Task 9: UI 自动化 `src/wechat_bot.py`(按尖峰发现实现,人工验证)

**Files:**
- Create: `src/wechat_bot.py`

> 无单元测试(UI 交互);正确性以「尖峰A发现 + 本任务 Step 3 人工验证」为准。
> 控件常量必须对照 `docs/spike-findings.md` 核对,不同则只改常量与 `open_history` 的定位逻辑。

- [ ] **Step 1: 实现(控件常量对照尖峰A记录填写)**

`src/wechat_bot.py`:
```python
"""PC 微信 4.1.x UI 自动化:搜索公众号 → 打开主页 → 进入历史消息 → 滚动。

控件定位常量来自 docs/spike-findings.md(尖峰A 实测,微信 4.1.12.55)。
微信大版本更新导致失效时,仅需更新常量与 open_history 的控件路径。
"""
import random
import time

import uiautomation as uia

MAIN_WINDOW_NAME_SUBSTR = "微信"        # 尖峰A:主窗口标题子串
SEARCH_HOTKEY = "{Ctrl}f"               # 尖峰A:搜索热键
HISTORY_ENTRY_TEXTS = ("查看历史消息", "历史消息", "全部消息", "更多消息")


class WeChatBotError(RuntimeError):
    pass


class WeChatBot:
    def __init__(self, timeout_sec: int = 10):
        self.timeout = timeout_sec

    def main_window(self):
        win = uia.WindowControl(searchDepth=1, Name=MAIN_WINDOW_NAME_SUBSTR)
        if not win.Exists(3, 1):
            raise WeChatBotError("未找到微信主窗口,请确认客户端已登录")
        return win

    def is_ready(self) -> bool:
        try:
            self.main_window()
            return True
        except WeChatBotError:
            return False

    def search(self, name: str):
        win = self.main_window()
        win.SetFocus()
        uia.SendKeys(SEARCH_HOTKEY, waitTime=0.5)
        uia.SendKeys(name, waitTime=0.5)
        uia.SendKeys("{Enter}", waitTime=1.5)

    def _click_first_match(self, texts) -> bool:
        """在整棵控件树中找 Name 命中任一文案的控件并点击。"""
        win = self.main_window()
        for text in texts:
            ctrl = win.Control(searchDepth=15, Name=text)
            if ctrl.Exists(2, 0.5):
                ctrl.Click(simulateMove=False)
                return True
        return False

    def open_history(self, name: str) -> None:
        """搜索公众号并打开其历史消息页(内嵌浏览器),触发 getmsg 请求。"""
        self.search(name)
        time.sleep(1.5)
        if not self._click_first_match((name,)):
            raise WeChatBotError(f"搜索结果中未找到「{name}」")
        time.sleep(2)
        if not self._click_first_match(HISTORY_ENTRY_TEXTS):
            raise WeChatBotError(f"「{name}」主页未找到历史消息入口")
        time.sleep(3)   # 等内嵌页加载并发出首批请求

    def scroll_history(self, screens: int = 1):
        """在内嵌页中心滚动加载更多文章。"""
        rect = self.main_window().BoundingRectangle
        cx, cy = (rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2
        for _ in range(screens):
            uia.WheelDown(cx, cy, wheelCount=3, waitTime=0.5)
            time.sleep(random.uniform(1.0, 2.0))

    def close_history(self):
        uia.SendKeys("{Esc}", waitTime=0.5)
        uia.SendKeys("{Esc}", waitTime=0.5)


if __name__ == "__main__":
    import sys
    bot = WeChatBot()
    target = sys.argv[1] if len(sys.argv) > 1 else "人民日报"
    bot.open_history(target)
    bot.scroll_history(1)
    print(f"已打开「{target}」历史消息并滚动,请人工确认;30 秒后自动 Esc 关闭")
    time.sleep(30)
    bot.close_history()
```

- [ ] **Step 2: 语法/导入冒烟**

Run: `.venv/Scripts/python -c "from src.wechat_bot import WeChatBot; print(WeChatBot().is_ready())"`
Expected: `True`(微信运行时)或 `False`(未运行),不得抛异常 traceback。

- [ ] **Step 3: 人工单步验证(需微信已登录)**

Run: `.venv/Scripts/python -m src.wechat_bot 人民日报`
人工确认:①搜索框被填入并回车 ②打开了公众号主页 ③进入了历史消息页 ④页面向下滚动 ⑤30 秒后回到主界面。任一步不符 → 对照 `docs/spike-findings.md` 修正常量后重试。

- [ ] **Step 4: Commit**

```bash
git add src/wechat_bot.py
git commit -m "feat: 微信4.1 UI自动化(搜索/打开历史消息/滚动)"
```

---

### Task 10: 主控 `src/orchestrator.py` + 入口 `run_crawl.py`

**Files:**
- Create: `src/orchestrator.py`, `run_crawl.py`

> 系统代理启停、mitmdump 子进程、UI 导航均为 OS 级交互,不做单元测试,由 Task 12 端到端验证。核心停止规则「滚动一轮零新增即停」依赖 Task 5 的 URL 去重语义(重复抓到的旧文不产生新行)。

- [ ] **Step 1: 实现主控**

`src/orchestrator.py`:
```python
"""单轮抓取主控:启停 mitmdump、环境检查、遍历账号、统计汇总。"""
import os
import random
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from . import db
from .config import PROJECT_ROOT, Settings, load_accounts, load_settings
from .version_check import cert_installed, check_port_free, get_wechat_info, is_supported_version

CURRENT_ACCOUNT_FILE = PROJECT_ROOT / "data" / ".current_account"
LOG_DIR = PROJECT_ROOT / "logs"
_PROXY_KEY = r"HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings"


class CrawlerEnvError(RuntimeError):
    pass


def _run_ps(cmd: str):
    return subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                          capture_output=True, text=True, timeout=15)


def _refresh_wininet():
    import ctypes
    opt = ctypes.windll.wininet.InternetSetOptionW
    opt(0, 39, 0, 0)   # INTERNET_OPTION_SETTINGS_CHANGED
    opt(0, 37, 0, 0)   # INTERNET_OPTION_REFRESH


def _set_system_proxy(port: int):
    """记录并开启用户级系统代理指向 mitmproxy;返回还原函数。"""
    r = _run_ps(f"$p=Get-ItemProperty '{_PROXY_KEY}'; \"[$($p.ProxyEnable)]$($p.ProxyServer)\"")
    prev = r.stdout.strip()                      # 形如 "[1]127.0.0.1:xxx" 或 "[0]"
    _run_ps(f"Set-ItemProperty -Path '{_PROXY_KEY}' -Name ProxyEnable -Value 1")
    _run_ps(f"Set-ItemProperty -Path '{_PROXY_KEY}' -Name ProxyServer -Value '127.0.0.1:{port}'")
    _refresh_wininet()

    def restore():
        enable = 1 if prev.startswith("[1]") else 0
        _run_ps(f"Set-ItemProperty -Path '{_PROXY_KEY}' -Name ProxyEnable -Value {enable}")
        server = prev[3:].strip() if len(prev) > 3 else ""
        if server:
            _run_ps(f"Set-ItemProperty -Path '{_PROXY_KEY}' -Name ProxyServer -Value '{server}'")
        _refresh_wininet()
    return restore


def _start_proxy(settings: Settings, log_file):
    env = os.environ.copy()
    env["CRAWLER_DB"] = str(db.DEFAULT_DB_PATH)
    env["CRAWLER_CURRENT_ACCOUNT"] = str(CURRENT_ACCOUNT_FILE)
    mitmdump = shutil.which("mitmdump") or str(Path(sys.executable).parent / "mitmdump.exe")
    return subprocess.Popen(
        [mitmdump, "--listen-port", str(settings.proxy_port), "-q",
         "-s", str(PROJECT_ROOT / "src" / "proxy_addon.py")],
        env=env, stdout=log_file, stderr=subprocess.STDOUT)


def _wait_for_new(conn, account_id, known, wait_sec, deadline, poll_sec) -> int:
    """等待该账号文章数超过 known;返回新增数,超时返回 0。"""
    end = min(time.time() + wait_sec, deadline)
    while time.time() < end:
        cur = conn.execute("SELECT COUNT(*) c FROM articles WHERE account_id = ?",
                           (account_id,)).fetchone()["c"]
        if cur > known:
            return cur - known
        time.sleep(poll_sec)
    return 0


def _crawl_account(conn, bot, name, settings, log) -> int:
    """打开单个公众号并滚动抓增量;返回本轮新增篇数。

    停止条件(规格§3.3):滚动后 scroll_wait_sec 内零新增 ⇒ 整屏均为
    已入库旧文(URL 去重语义下不产生新行),增量已抓尽。
    """
    aid = db.get_or_create_account(conn, name)
    CURRENT_ACCOUNT_FILE.parent.mkdir(parents=True, exist_ok=True)
    CURRENT_ACCOUNT_FILE.write_text(name, encoding="utf-8")
    baseline = conn.execute("SELECT COUNT(*) c FROM articles WHERE account_id = ?",
                            (aid,)).fetchone()["c"]
    deadline = time.time() + settings.account_timeout_sec

    bot.open_history(name)
    total = _wait_for_new(conn, aid, baseline, settings.scroll_wait_sec, deadline,
                          settings.poll_interval_sec)
    log(f"  首屏新增 {total} 篇")

    for _ in range(settings.scroll_screens):
        if total == 0 or time.time() >= deadline:
            break
        bot.scroll_history(1)
        gained = _wait_for_new(conn, aid, baseline + total, settings.scroll_wait_sec,
                               deadline, settings.poll_interval_sec)
        total += gained
        if gained == 0:
            break
    db.mark_crawled(conn, aid)
    bot.close_history()
    return total


def run_once(only_account: str | None = None, settings: Settings | None = None,
             log=print) -> dict:
    settings = settings or load_settings()
    for d in (db.DEFAULT_DB_PATH.parent, CURRENT_ACCOUNT_FILE.parent, LOG_DIR):
        d.mkdir(parents=True, exist_ok=True)

    # ── 预检 ──
    info = get_wechat_info()
    ok, msg = is_supported_version(info["version"] if info else None)
    log(f"[预检] {msg}")
    if info is None:
        raise CrawlerEnvError("微信客户端未运行,本轮中止(定时任务会在下一轮自动再试)")
    if not check_port_free(settings.proxy_port):
        raise CrawlerEnvError(f"端口 {settings.proxy_port} 被占用,无法启动代理")
    if not cert_installed():
        raise CrawlerEnvError("mitmproxy 证书未安装,请按 README「首次部署」步骤 2~3 完成后重试")

    from .wechat_bot import WeChatBot, WeChatBotError   # 延迟导入,便于 --check 轻量运行
    bot = WeChatBot()
    if not bot.is_ready():
        raise CrawlerEnvError("未找到微信主窗口,请确认客户端已登录")

    names = [only_account] if only_account else load_accounts()
    if not names:
        raise CrawlerEnvError("config/accounts.yaml 为空")
    if only_account is None:
        random.shuffle(names)

    log_path = LOG_DIR / f"run_{datetime.now():%Y%m%d_%H%M%S}.log"
    with open(log_path, "a", encoding="utf-8") as lf, \
         open(LOG_DIR / "mitmdump.log", "a", encoding="utf-8") as plog:
        def log2(m):
            lf.write(f"{datetime.now():%H:%M:%S} {m}\n")
            lf.flush()
            log(m)

        conn = db.connect()
        restore = proc = None
        try:
            db.init_db()
            proc = _start_proxy(settings, plog)
            time.sleep(3)
            if proc.poll() is not None:
                raise CrawlerEnvError("mitmdump 启动失败,详见 logs/mitmdump.log")
            restore = _set_system_proxy(settings.proxy_port)
            run_id = db.start_run(conn)

            ok_n = fail_n = new_n = 0
            for i, name in enumerate(names, 1):
                log2(f"[{i}/{len(names)}] {name}")
                try:
                    n = _crawl_account(conn, bot, name, settings, log2)
                    ok_n += 1
                    new_n += n
                    log2(f"  完成,新增 {n} 篇")
                except Exception as e:   # 含 WeChatBotError,单号失败不中断整轮
                    fail_n += 1
                    log2(f"  失败:{e}")
                if i < len(names):
                    delay = random.uniform(settings.delay_min_sec, settings.delay_max_sec)
                    log2(f"  等待 {delay:.0f}s 后下一个账号")
                    time.sleep(delay)

            db.finish_run(conn, run_id, ok_n, fail_n, new_n)
            log2(f"本轮完成:成功 {ok_n} 失败 {fail_n} 新增 {new_n} 篇;日志 {log_path}")
            return {"ok": ok_n, "fail": fail_n, "new": new_n, "log": str(log_path)}
        finally:
            conn.close()
            if restore:
                restore()
            if proc and proc.poll() is None:
                proc.terminate()
```

- [ ] **Step 2: 实现命令行入口**

`run_crawl.py`:
```python
"""微信公众号文章增量爬虫 · 命令行入口。

用法:
  python run_crawl.py                    # 正常单轮(计划任务调用)
  python run_crawl.py --account 名称     # 单账号试跑
  python run_crawl.py --check            # 环境自检,不抓取
"""
import argparse

from src.config import load_settings
from src.orchestrator import CrawlerEnvError, run_once
from src.version_check import cert_installed, check_port_free, get_wechat_info, is_supported_version


def main():
    ap = argparse.ArgumentParser(description="微信公众号文章增量爬虫(单轮)")
    ap.add_argument("--account", help="只抓取指定公众号(试跑)")
    ap.add_argument("--check", action="store_true", help="环境自检,不抓取")
    args = ap.parse_args()

    if args.check:
        s = load_settings()
        info = get_wechat_info()
        _, msg = is_supported_version(info["version"] if info else None)
        print(f"微信进程: {info['path'] if info else '未运行'}")
        print(f"版本检查: {msg}")
        print(f"代理端口 {s.proxy_port}: {'空闲' if check_port_free(s.proxy_port) else '被占用'}")
        print(f"mitmproxy 证书: {'已安装' if cert_installed() else '未安装(见 README 一次性安装步骤)'}")
        return

    try:
        run_once(only_account=args.account)
    except CrawlerEnvError as e:
        print(f"[中止] {e}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: 导入与语法冒烟**

Run: `.venv/Scripts/python -c "from src.orchestrator import run_once, CrawlerEnvError; import run_crawl; print('ok')"`
Expected: `ok`

Run: `.venv/Scripts/python run_crawl.py --check`
Expected: 打印微信进程路径、版本(已适配)、端口空闲、证书状态。

Run(微信未登录/名单为空等场景的优雅中止):`.venv/Scripts/python run_crawl.py --account 不存在的号`
Expected: 中途失败但整轮不崩溃,日志记录「失败」,代理还原,进程退出。

- [ ] **Step 4: Commit**

```bash
git add src/orchestrator.py run_crawl.py
git commit -m "feat: 单轮主控(代理启停/系统代理还原/账号遍历/停止规则)"
```

---

### Task 11: 计划任务与 README

**Files:**
- Create: `install_task.ps1`, `README.md`

- [ ] **Step 1: 编写计划任务脚本**

`install_task.ps1`:
```powershell
# 注册/注销 Windows 计划任务:每天 08:05 与 19:05 各运行一轮抓取。
# 用法:注册 powershell -ExecutionPolicy Bypass -File install_task.ps1
#       注销 powershell -ExecutionPolicy Bypass -File install_task.ps1 -Remove
param([switch]$Remove)

$taskName = "WechatArticleCrawler"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"

if ($Remove) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Write-Host "已注销计划任务 $taskName"
    return
}

$action = New-ScheduledTaskAction -Execute $python -Argument "run_crawl.py" -WorkingDirectory $projectRoot
$t1 = New-ScheduledTaskTrigger -Daily -At "08:05"
$t2 = New-ScheduledTaskTrigger -Daily -At "19:05"
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 1) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $t1, $t2 -Settings $settings `
    -Description "微信公众号文章增量爬虫(需用户已登录且微信在线)"
Write-Host "已注册计划任务 $taskName(每天 08:05 / 19:05,仅用户登录时运行)"
```

- [ ] **Step 2: 编写 README**

`README.md`:
```markdown
# wechat-crawler

微信公众号文章增量爬虫:定时通过已登录的 PC 微信自动搜索名单中的公众号、打开文章列表,
用 mitmproxy 被动截获客户端加载的文章列表接口,把文章 URL/标题/发布时间增量存入 SQLite。

- 设计文档:`docs/superpowers/specs/2026-09-02-wechat-official-account-crawler-design.md`
- 实施计划:`docs/superpowers/plans/2026-09-02-wechat-official-account-crawler.md`

## 首次部署(一次性)

1. 创建虚拟环境并安装依赖:
   "D:\Python312\python.exe" -m venv .venv
   .venv\Scripts\python -m pip install -r requirements.txt
2. 生成 mitmproxy CA 证书(首次运行 mitmdump 即自动生成):
   .venv\Scripts\mitmdump --listen-port 8888   (出现 banner 后 Ctrl+C)
   证书位于 %USERPROFILE%\.mitmproxy\mitmproxy-ca-cert.cer
3. 安装证书到「当前用户 → 受信任的根证书颁发机构」(弹出确认框点「是」):
   certutil -user -addstore root "%USERPROFILE%\.mitmproxy\mitmproxy-ca-cert.cer"
4. 编辑 config/accounts.yaml,写入公众号名单(每行一个名称)
5. 环境自检:.venv\Scripts\python run_crawl.py --check
6. 单号试跑(微信保持登录):
   .venv\Scripts\python run_crawl.py --account 人民日报
   然后查询 data/crawler.db 的 articles 表确认有数据
7. 注册计划任务(每天 08:05 / 19:05):
   powershell -ExecutionPolicy Bypass -File install_task.ps1

## 日常使用

- 查看结果:用任意 SQLite 工具打开 `data/crawler.db` 的 `articles` 表
- 运行日志:`logs/run_*.log`(每轮一个)、`logs/mitmdump.log`
- 注销计划任务:powershell -ExecutionPolicy Bypass -File install_task.ps1 -Remove

## 运行条件

- 电脑开机且已登录 Windows(计划任务「仅用户登录时运行」)
- PC 微信(4.1.x)已启动并扫码登录
- 每轮运行期间(约 10~30 分钟)系统代理临时指向本地 mitmproxy,结束后自动还原

## 故障排查

| 现象 | 处理 |
|---|---|
| 日志提示「版本…不同」 | 微信大版本更新后 UI 控件可能变化:对照 docs/spike-findings.md 核对 src/wechat_bot.py 顶部常量 |
| 日志提示「微信客户端未运行」 | 打开微信扫码登录,等下一轮,或手动运行 run_crawl.py |
| 某账号持续失败 | 在微信里手动搜索该号确认存在;核对 accounts.yaml 名称与微信搜索结果完全一致 |
| mitmdump.log 无 profile_ext 截获 | 检查证书安装;用 tools/spike_set_proxy.ps1 手动复现尖峰B流程定位 |
```

- [ ] **Step 3: 验证计划任务脚本 dry-run**

Run:
```bash
powershell -ExecutionPolicy Bypass -File install_task.ps1
powershell -Command "Get-ScheduledTask -TaskName WechatArticleCrawler | Select-Object TaskName,State"
powershell -ExecutionPolicy Bypass -File install_task.ps1 -Remove
```
Expected: 注册成功 → State 为 `Ready` → 注销成功。(先注册→验证→注销,Task 12 验收时再正式注册)

- [ ] **Step 4: Commit**

```bash
git add install_task.ps1 README.md
git commit -m "docs: 部署README与计划任务注册脚本"
```

---

### Task 12: 端到端验收(人工参与)

**Files:** 无新文件;产出验收记录

- [ ] **Step 1: 单号端到端试跑**

前置:微信已登录;`tests/fixtures/profile_ext_real.json` 已在(Task 3)。
Run: `.venv/Scripts/python run_crawl.py --account 人民日报`
Expected:
- 控制台逐步输出「[1/1] 人民日报 → 首屏新增 N 篇 → 完成」
- `data/crawler.db` 的 `articles` 表出现该号文章(URL 以 `http` 开头、标题非空)
- `crawl_runs` 表新增一条 ok_count=1 的记录

再次运行同一命令:
Expected: 「首屏新增 0 篇」(增量去重生效),耗时明显短于首次。

- [ ] **Step 2: 全量名单试跑**

把真实名单填入 `config/accounts.yaml`,Run: `.venv/Scripts/python run_crawl.py`
Expected: 逐号处理,账号间有 20~60s 随机间隔;结束输出「成功 X 失败 Y 新增 Z 篇」;运行后系统代理已还原(浏览器可正常上网)。

- [ ] **Step 3: 正式注册计划任务**

Run: `powershell -ExecutionPolicy Bypass -File install_task.ps1`
Expected: 注册成功,State=Ready。

- [ ] **Step 4: 连续两天观察(规格验收标准)**

保持电脑开机、微信登录。两天后检查:
- `logs/` 下每天应有 2 个 `run_*.log`,且无「微信客户端未运行」等环境性中止
- `crawl_runs` 每天新增 2 条,ok_count 接近名单数
- `articles` 表持续出现新文章

- [ ] **Step 5: 验收记录与收尾提交**

把上述结果写入 `docs/acceptance-2026-09.md`(表格:日期/轮次/成功/失败/新增/备注),然后:
```bash
git add docs/acceptance-2026-09.md
git commit -m "docs: 端到端验收记录"
```

---

## 与规格的差异说明

1. **§3.3 停止条件的实现形式**:规格原文「连续 3 篇早于水位线」在 URL 去重语义下取不到可靠的本轮样本(旧文重复抓到时不产生新行),已改为等价且更稳健的「滚动一轮零新增即停」;水位线字段仍记录用于统计排查。规格文档已同步修订。
2. **规格未列 `src/config.py`**:配置加载独立成模块(约 40 行),职责单一,便于测试;目录结构其余与规格一致。

## 风险与回退(贯穿全程)

- **Gate A/B 尖峰失败** → 停止实施,报告用户,回退规格§5 的降级路线(3.9.x + wxauto / local 模式 / 重议路线)。
- **微信自动更新** → `wechat_bot.py` 常量适配;启动时版本告警(已实现于 Task 8/10)。
