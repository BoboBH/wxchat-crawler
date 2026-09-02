# 微信公众号文章爬虫 · 设计文档(v2)

日期:2026-09-02(v2 修订:2026-09-03,依据四轮尖峰实证)
状态:v2 架构已经尖峰验证;交付物与 v1 一致(文章 URL+标题+发布时间),技术路线变更

## 1. 背景与目标

用户维护一份公众号名单(10~50 个),希望每天定时自动抓取这些公众号新发布文章的 URL 及元信息,存入本地数据库,供后续使用。

**已确认的需求约束:**

| 维度 | 决定 |
|---|---|
| 公众号资质 | 无自己的公众号,不注册;使用个人微信扫码登录 PC 微信客户端 |
| 技术路线 | **v2:UI 自动化直取**(v1 的 PC 微信+mitmproxy 被动抓包已被尖峰证伪,见 §8) |
| 抓取范围 | 只抓增量新文章(上次之后新发布的) |
| 数据范围 | 文章 URL + 标题 + 发布时间 + 公众号名称,存 SQLite;不推送 |
| 名单规模 | 10~50 个账号 |
| 执行频率 | 每天两次(早/晚),运行时要求电脑开机且微信已登录 |
| 工具形态 | 命令行脚本 + Windows 任务计划程序 |

**验收标准:**计划任务连续 2 天自动运行;每轮结束后 SQLite 中能查到名单内各号新发布文章的 URL/标题/发布时间(URL 提取失败的条目降级为"无 URL 待补",不阻塞);失败账号有日志可查。

## 2. 总体架构(v2)

```
Windows 任务计划程序(每天 08:05 / 19:05 触发)
        │
        ▼
┌────────────────────────────────────────────┐
│ run_crawl.py(单轮主控,全程 UIA,无代理)     │
│  1. 预检:微信进程/版本、UIA 主窗口可见        │
│  2. 逐个账号(乱序):                         │
│     a. wechat_bot 打开公众号主页              │
│        (AppEx 搜索框粘贴名称→搜索→header-detail)│
│     b. 从主页列表 UIA 读取(标题, 日期文本)    │
│        按发布日期与水位线比较,连续 N 篇旧文→停  │
│     c. 仅对库中不存在的新文章:                │
│        Invoke 打开文章页→全树扫 ValuePattern   │
│        提取 mp.weixin.qq.com/s?__biz=… URL    │
│        →规范化为 5 参数 canonical→关闭 tab     │
│     d. 去重入库(dedup_key=canonical URL;     │
│        提取失败→title+日期哈希,url 留空待补)   │
│  3. 汇总统计、写运行日志                       │
└────────────────────────────────────────────┘
        │
        ▼
   data/crawler.db(SQLite)
```

**核心原理:**UI 自动化执行的是真实导航;文章列表的标题与日期直接从主页无障碍树读取;文章 URL 在打开文章页后从 UIA **ValuePattern** 属性提取(尖峰C 实测:同一文章多次提取逐字节一致,5 参数 canonical 公网可直接访问)。全程不需要代理、证书与密钥管理。

**关键实测依据(详见 §8 与 docs/spike-findings.md):**
- 微信 4.1.12.55 的公众号页面运行于 WeChatAppEx.exe 内嵌 Chromium;Qt 主窗口 UIA 树为空。
- 主页即完整历史列表(时间倒序、无限滚动);标题 `article__item__title`、日期 `publish_time` 可读。
- 文章 URL 不经 UIA 链接属性、不经网络抓包暴露,只在**文章页**以 ValuePattern 值出现。
- 增量场景下每号每轮新文章极少,逐篇打开的成本(解锁态约 12~20 秒/篇)可接受。

**已实测环境:**Windows 11;PC 微信 4.1.12.55(`C:\Program Files\Tencent\Weixin\Weixin.exe`,进程名 `Weixin`,常驻运行中);Python 3.12.10(`D:\Python312`,项目虚拟环境 `.venv`);mitmproxy 证书已装但 v2 不使用。

## 3. 组件设计

### 3.1 目录结构

```
wechat-crawler/
├── config/
│   ├── accounts.yaml      # 公众号名单
│   └── settings.yaml      # 超时/滚动深度/间隔等参数
├── src/
│   ├── orchestrator.py    # 入口:预检、遍历账号、增量判定、URL 增强、统计
│   ├── wechat_bot.py      # UIA 封装:选窗/导航/读列表/开文章提URL/关tab
│   ├── canonical.py       # 纯函数:URL 规范化、dedup_key、日期文本归一化
│   ├── db.py              # SQLite:建表、去重写入、水位线
│   ├── config.py          # settings/accounts 加载
│   └── version_check.py   # 微信进程/版本检测
├── tools/                 # 尖峰工具(保留供微信更新后重新校准)
├── data/crawler.db
├── logs/
├── tests/                 # canonical 与 db 的单元测试 + 真实样本
├── install_task.ps1       # 注册/注销计划任务(08:05 与 19:05)
├── run_crawl.py           # 命令行入口(--account / --check)
└── requirements.txt       # uiautomation、PyYAML、pytest(mitmproxy 已移除)
```

### 3.2 数据模型(SQLite)

| 表 | 字段 | 说明 |
|---|---|---|
| `accounts` | id, name(唯一), last_crawled_at, max_publish_date | 水位线(ISO 日期) |
| `articles` | id, account_id, **dedup_key(唯一)**, url(可空), title, date_text, publish_date(可空), url_status('ok'/'pending'), created_at | `dedup_key`=canonical URL(有 URL 时)或 `t\|`+sha1(标题\|日期文本)(无 URL 时);同一文章两种状态都能去重 |
| `crawl_runs` | id, started_at, finished_at, ok_count, fail_count, new_count | 每轮一条 |

### 3.3 增量停止逻辑(v2)

主页列表按时间倒序;UIA 逐条读取(标题, 日期文本)并归一化为日期。与该账号水位线比较:**连续 N 篇(默认 3)发布日期早于"水位线 − 2 天重叠"即停止扫描、切下一个账号**。日期文本无法归一化的条目按"可能是新文章"处理(尝试 URL 提取,由 dedup_key 兜底去重)。新账号(无水位线)默认扫描前 2 屏。

### 3.4 命令行接口

- `python run_crawl.py` — 正常单轮抓取(计划任务调用)
- `python run_crawl.py --account 某公众号名` — 单账号试跑
- `python run_crawl.py --check` — 环境自检(微信进程/版本/UIA 窗口),不抓取

## 4. 错误处理

| 场景 | 处理 |
|---|---|
| 微信未运行/未登录/UIA 找不到窗口 | 本轮立即中止,日志提示"请登录微信后等待下一轮" |
| 某账号搜索无结果/导航超时 | 标记该账号失败,继续下一个 |
| 单篇文章 URL 提取失败/超时 | 记 `url_status='pending'`(无 URL 入库),不阻塞,继续下一篇 |
| 文章页无障碍树未 realization(锁屏等) | 按尖峰经验执行 SW_MINIMIZE→SW_RESTORE "kick" 一次后重试 |
| 接口/控件结构随微信改版变化 | 异常按 0 条计,日志记录控件树摘要;tools/ 下尖峰工具可用于重新校准 |
| 账号间/文章间节奏 | 随机延时,模拟真人 |

## 5. 风控与已知风险

**风控策略:**账号间随机间隔 20~60 秒;每轮账号顺序打乱;列表只扫到水位线+重叠为止;URL 提取只针对新文章(通常每号每轮 0~5 篇);每天仅 2 轮。总量为每天 2 次真实浏览行为,远低于人工使用强度。

**已知风险与对策:**

1. **微信大版本更新导致 UIA 控件变化**(主要风险)→ 控件定位常量集中在 `wechat_bot.py`;`tools/spike_uia.py` 等尖峰工具可一键重新校准;启动时检测版本告警。
2. **逐篇 URL 提取耗时** → 增量场景新文章少,成本可控;失败降级为 pending,次日该文仍在列表可重试。
3. **锁屏/非前台时段运行** → 文章打开、URL 提取、关 tab 全程 UIA Pattern 动作,锁屏下尖峰C 实测可用(SW_MINIMIZE→SW_RESTORE kick 恢复树 realization);但**搜索框粘贴依赖合成键鼠,锁屏下会失败**。计划任务"仅用户登录时运行",建议运行时段桌面处于解锁状态;失败账号记日志降级,下一轮自动重试。另需一次性人工部署动作:在微信中打开一次「搜一搜」页,之后脚本复用该搜索页 tab。
4. ~~被动抓包~~ — 已被尖峰 B/B'/B'' 证伪(mmtls 私有长链),不再是本设计的组成部分。

## 6. 测试策略

- `canonical.py` 与 `db.py`:单元测试(canonical 化用尖峰C 真实 URL 样本;dedup 两种状态、水位线、连续 N 篇停止判定)。
- `wechat_bot.py`:UI 部分无法自动化测试 → `--account X` 单号试跑人工验证 + 尖峰工具回归。
- 里程碑:①尖峰(已完成,结论见 §8)→ ②核心模块 TDD → ③单号端到端 → ④全量名单 → ⑤注册计划任务观察两天。

## 7. 运行环境要求(已实测)

- Windows 11,用户已登录桌面(计划任务"仅用户登录时运行")
- Python 3.12.10,位于 `D:\Python312`;项目虚拟环境 `.venv`
- PC 微信 4.1.12.55,运行时已登录;版本更新需重新校准控件常量
- **无需**代理/证书/管理员权限

## 8. 尖峰实证记录(2026-09-02 ~ 09-03,详见 docs/spike-findings.md)

| 尖峰 | 假设 | 结论 |
|---|---|---|
| A | Qt 主窗口可 UIA 自动化 | **否**——树为空;可自动化面在 WeChatAppEx.exe 内嵌 Chromium(Chrome_WidgetWin_0);主页=完整历史列表,无限滚动可用 |
| B | mitmproxy 被动截获列表接口 | **否**——TLS 解密成功,但列表/文章数据走 mmtls 私有长链;老接口 profile_ext 已不存在 |
| B' | UIA 链接属性直取 URL / CDP 挂载 | **均否**——flue.dll 提供者剥离 href,DOM 无 `<a href>`;无 DevTools 端口 |
| B'' | mp_profile HTML 为 SSR 内嵌数据 | **否**——仅 4KB Vite/Vue SPA 壳,数据走 XWeb worker 桥 |
| C | 逐篇打开文章页提取 URL | **成立**——文章页全树 ValuePattern 含 canonical URL;5 参数去参后公网稳定可访问;连续 2 次提取一致;锁屏可用 |
