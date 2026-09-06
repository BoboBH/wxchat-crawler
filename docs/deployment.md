# 部署与换机迁移指南

> 适配版本:2026-09-06(存储=MySQL 主库,定时任务已删仅手动运行,微信
> 4.1.12.55 校准)。首次全新部署与换电脑迁移通用;换机额外看 §3 数据迁移。
> 运行期排障见 `README.md`「运行与排障」;控件校准原理见
> `docs/spike-findings.md`。

## 1. 环境前提(一台新 Windows 电脑要装什么)

| 组件 | 要求 | 说明 |
|---|---|---|
| PC 微信 | **4.x**(现役校准 4.1.12.55) | 扫码登录后保持运行;窗口可最小化**不能关闭**。版本非 4.1.x 时先跑 `run_crawl.py --check`,控件常量可能要用尖峰工具重新校准(§6) |
| Python | **3.12 64 位** | 只在跑爬虫的机器需要 |
| MySQL | **8.x 本机** `localhost:3306` | 唯一主库;库/表自动创建 |
| Git | 任意 | 拉代码 |

把微信装在哪要记下来:`config/settings.yaml` 的 `wechat.exe_path` 默认
`C:\Program Files\Tencent\Weixin\Weixin.exe`,装别处就改它。

## 2. 首次部署步骤

```powershell
git clone https://github.com/BoboBH/wxchat-crawler.git
cd wxchat-crawler

# Python 虚拟环境 + 依赖(uiautomation / PyYAML / PyMySQL / pytest)
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt

# 两份配置(均 gitignore,凭据不入库)
Copy-Item config\settings.example.yaml config\settings.yaml   # 填钉钉 webhook
Copy-Item .env.example .env                                   # 填 MySQL 密码
```

配置两件套:

- **`.env`** — MySQL 连接五项:`WXCHAT_CRAWLER_HOST / PORT / USER /
  PASSWORD / DATABASE`(库默认 `test`;系统环境变量 > .env > 内置默认)。
- **`config\settings.yaml`** — 微信进程/版本/路径、抓取节奏
  (`stop_streak`/`overlap_days`/深滚屏数/失败重试…)、MySQL 表名、
  `notify:` 钉钉段(webhook 含 token 勿外传;机器人安全设置=自定义关键词
  「wxcrawler」,代码统一加前缀过检,无需额外配置)。
- `config\accounts.yaml` — 公众号名单,**随 git 仓库**(当前 21 个号)。

定时任务**默认不装**(2026-09-04 起仅手动跑);确要恢复:
`powershell -ExecutionPolicy Bypass -File install_task.ps1`,装了就严禁
再手动并发跑(历史上有双驱动并发产生重复行的教训)。

## 3. 数据迁移(换电脑时做;全新部署跳过)

**git 里没有、旧机器上才有的东西**:

| 位置 | 内容 | 必要性 |
|---|---|---|
| `config\settings.yaml` | 钉钉 webhook + 调优参数 | **必带**(丢了要去钉钉群重建设置机器人) |
| `.env` | MySQL 密码 | **必带**(或新填) |
| MySQL `test` 库三张表 | 水位线 + 全部文章 URL + 运行记录 | **推荐**(见下二选一) |
| `data\crawler.db` | SQLite 历史备份(2026-09-05 前长链接存档) | 可选留档 |
| `backups\` | 计划任务 XML | 不需要(定时已删) |
| `logs\` | 运行日志 | 不需要 |

MySQL 数据**二选一**:

- **方案A:延续水位线(推荐)** —— 只动我们三张表,`test` 是共库也不
  影响别人:
  ```powershell
  # 旧机器导出
  mysqldump -uroot -p test wechat_crawler_accounts wechat_crawler_articles wechat_crawler_runs > wc.sql
  # 新机器导入
  mysql -uroot -p -e "CREATE DATABASE IF NOT EXISTS test"
  mysql -uroot -p test < wc.sql
  ```
- **方案B:全新开始** —— 不导数据;首跑自动建库建表,21 个号全按
  「新账号」抓 `new_account_screens`(默认 2)屏重建水位线。代价:每号
  ~10 篇重新推送钉钉,更早历史不回补(旧机数据留档兜底)。

## 4. 部署验证(四步,按序)

```powershell
.venv\Scripts\python.exe -m pytest tests\ -v          # 156 全绿;测试库
                                                      # wxchat_crawler_test 自动建
.venv\Scripts\python.exe run_crawl.py --check         # → 「OK,可以抓取」
                                                      # (查微信进程/版本/登录)
.venv\Scripts\python.exe tools\db_stats.py            # 账号 21、文章数、水位线
.venv\Scripts\python.exe run_crawl.py --account 中金点睛   # 单号试跑
```

单号试跑正常后,空闲时段全量轮:`.venv\Scripts\python.exe run_crawl.py`。
首次搜索页 tab 由爬虫自动引导(聚焦微信 → Ctrl+F),无需人工;也可
`.venv\Scripts\python.exe tools\bootstrap_search.py` 代开(需桌面解锁)。

## 5. 部署后运行须知

- **电脑空闲时段跑批**:搜索粘贴与「复制链接」菜单依赖合成键鼠,人正在
  用电脑时微信抢不到前台 → 当轮账号失败(用户在旁打字点鼠标也会打断);
  看屏幕无碍,别碰键鼠。
- 每号每轮约 6~7 分钟(新号首轮 10 篇量级),失败自动重试 3 次、最终失败
  即刻钉钉告警、轮末推总结;日志 `logs\crawl_YYYY-MM-DD.log`。
- 抓完即关页签;微信重启后搜索 tab 自动重开。
- 退出码:有失败为 1,全成功为 0(可挂监控)。

## 6. 微信版本差异(换机最常见的坑)

`run_crawl.py --check` 校验版本前缀(默认 `4.1`)。若新机器微信版本不同:

1. 先跑 `--check` 看告警;
2. 按 `docs/spike-findings.md` 的校准入口,用 `tools\` 尖峰工具
   (`spike_uia.py` 等)重新核对 `src\wechat_bot.py` 顶部的类名/控件常量
   (主页标题节点、搜索框、结果卡片、更多菜单等);
3. 单号试跑通过再上全量。
