# 微信公众号文章爬虫(UIA 直取)

定时用公众号名称在 PC 微信中搜索并打开其主页,增量抓取**新发布文章**的
URL、标题、发布时间,存入本地 SQLite。每天 08:05 / 19:05 各自动运行一轮。

**原理**:全程 UI 自动化操控微信内置浏览器(WeChatAppEx.exe):搜索 →
公众号主页(即完整历史列表)→ 读取列表的标题与日期分组做增量判定 →
仅对未入库的新文章逐篇打开,从文章页无障碍树的 ValuePattern 提取
canonical URL(多候选或归属存疑时,改用页面「··· → 复制链接」菜单取
页面自身链接)→ 入库。不注入、不逆向、不需要公众号后台权限;行为等价于
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
   或运行 `.venv\Scripts\python.exe tools\bootstrap_search.py`
   由脚本代开(需桌面解锁)。
3. 自检:`.venv\Scripts\python.exe run_crawl.py --check`
   → 应输出「OK,可以抓取」。
4. 单号试跑:`.venv\Scripts\python.exe run_crawl.py --account 中金点睛`
5. 注册计划任务(当前用户,登录时运行):
   `powershell -ExecutionPolicy Bypass -File install_task.ps1`
   注销:`powershell -ExecutionPolicy Bypass -File install_task.ps1 -Remove`

## 名单与参数

- `config/accounts.yaml` — 公众号名单,`accounts:` 键下每行一个 `- 名称`。
- `config/settings.yaml` — 停止条数、重叠天数、账号间隔、超时等。

## 查看数据

```
.venv\Scripts\python.exe tools\db_stats.py
```

或直接 SQL:`sqlite3 data\crawler.db "select title,url from articles order by id desc"`

## 运行与排障

- 日志:`logs\crawl_YYYY-MM-DD.log`(正常轮次各写一条 `crawl_runs` 记录(环境预检未通过的轮次不写))。
- `url_status='pending'` 的文章 = 当轮没提取到 URL(可能是无页内链接的
  简排版文章、超时、或打开的页与标题不符/链接归属存疑 —— 日志以
  `PENDING`/`未打开PENDING`/`标题不符PENDING` 区分);次日该文仍在列表时
  自动重试补全。
- **桌面需解锁**:文章页提取全程可用 UIA 完成(锁屏也行),但搜索框
  粘贴依赖合成键鼠 —— 锁屏时段运行的轮次中,账号会失败并留日志,
  下一轮自动重试。
- **微信大版本更新**后控件可能变化:先 `.venv\Scripts\python.exe run_crawl.py --check`
  (版本不匹配会告警),再按 `docs\spike-findings.md` 用 `tools\`
  下的尖峰工具重新校准 `src\wechat_bot.py` 顶部常量。
- 微信未登录/未启动:本轮立即中止,日志提示,下一轮自动恢复。

## 已知限制

- 依赖微信客户端真实界面;窗口最小化可以,但**不能关闭**微信。
- 增量场景每号每轮通常 0~5 篇新文章,每篇提取约 12~20 秒(解锁态);
  树内多候选/归属存疑的文章改走「复制链接」菜单,约再 +6~10 秒。
- 文章 URL 存两种稳定主键形态:① canonical 4 参数
  (`__biz/mid/idx/sn`;chksm 为会话级签名,同文每次提取都不同,已剔除);
  ② 「复制链接」菜单产出的短链 `mp.weixin.qq.com/s/<token>`(token 永久)。
  两种均公网可直接访问、跨次提取一致,可作稳定主键。
- 「复制链接」菜单依赖合成鼠标点击,锁屏轮次不可用(此时多候选文章
  留 pending,下轮解锁态自动补全)。
