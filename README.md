# 微信公众号文章爬虫(UIA 直取)

用公众号名称在 PC 微信中搜索并打开其主页,**手动单轮**增量抓取新发布文章的
URL、标题、发布时间,存入本机 MySQL 主库(2026-09-05 起 SQLite 已弃用)。
(定时任务已于 2026-09-04 删除,现仅手动运行:`python run_crawl.py`,见下。)

**原理**:全程 UI 自动化操控微信内置浏览器(WeChatAppEx.exe):搜索 →
公众号主页(即完整历史列表)→ 深滚回填至「水位线 − overlap_days」截止日、
展开「余下N篇」折叠条 → 读取列表的标题与日期分组做增量判定 → 仅对未入库
的新文章逐篇打开,先用页面「··· → 复制链接」菜单取文章自身短链(下游
playwright 消费 canonical 全参链接会被微信拦去人工验证,短链可全自动;
菜单失败再退回从无障碍树的 ValuePattern 提取,且仅在唯一候选且归属可
验证时采信)→ 入库;抓完即关文章页签,轮末兜底清扫。不注入、不逆向、
不需要公众号后台权限;行为等价于真人浏览。

## 技术栈

- Python 3.12(venv)+ uiautomation 2.0.29 —— UIA 是唯一触达微信的通道;
- MySQL(PyMySQL,唯一主库;库/表自动创建,连接参数在 `.env`);
- 钉钉自定义机器人 webhook(标准库 urllib 直发 markdown);
- 配置:PyYAML(`config\*.yaml`)+ `.env`(MySQL 凭据,已 gitignore)。

## 安装

```powershell
D:\Python312\python.exe -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item config\settings.example.yaml config\settings.yaml   # 再填钉钉 webhook
Copy-Item .env.example .env                                    # 再填 MySQL 密码
.venv\Scripts\python.exe -m pytest tests\ -v   # 单测应全绿
```

## 换机部署(迁移到新电脑)

> 完整版(环境前提 / 首次部署 / 数据迁移二选一 / 部署验证 / 版本校准)
> 见 **`docs\deployment.md`**;本节速查。

**不在 git 里、必须从旧机器带走的三样**:`config\settings.yaml`(钉钉
webhook + 调优参数)、`.env`(MySQL 密码)、MySQL `test` 库里的数据
(水位线与全部文章 URL)。`backups\`(计划任务 XML)与 `data\crawler.db`
(SQLite 历史备份,2026-09-05 前的长链接存档)可选。

1. **装环境**:Windows + PC 微信 4.x(扫码登录;新机器安装路径若不同,
   改 `settings.yaml` 的 `wechat.exe_path`)+ Python 3.12 64 位 +
   本机 MySQL 8(`localhost:3306`)。
2. **拉代码**:`git clone https://github.com/BoboBH/wxchat-crawler.git`
   (`accounts.yaml` 21 个号随库;定时任务默认不装)。
3. **Python 依赖**:按上面「安装」节建 venv 装 `requirements.txt`。
4. **配置**:拷旧机器 `settings.yaml` 与 `.env`(或按 example 重建后填
   webhook 与密码)。
5. **迁移数据(二选一)**:
   - **延续水位线(推荐)** —— 旧机器只导三张表:
     `mysqldump -uroot -p test wechat_crawler_accounts wechat_crawler_articles wechat_crawler_runs > wc.sql`,
     新机器 `CREATE DATABASE test;` 后 `mysql -uroot -p test < wc.sql`
     (`test` 若是共库,只动这三张表);
   - **全新开始** —— 不导数据,首跑自动建库建表,全部账号按新账号
     (`new_account_screens` 屏)重建水位线;旧机数据留档兜底。
6. **验证**:`pytest tests\ -v` 全绿(测试库 `wxchat_crawler_test` 自动建)
   → `run_crawl.py --check` 输出「OK,可以抓取」→ `tools\db_stats.py`
   看数据 → `run_crawl.py --account 中金点睛` 单号试跑 → 空闲时段全量轮。
7. **注意**:微信大版本不同(非 4.1.x)时先跑 `--check`,控件常量按
   `docs\spike-findings.md` 用 `tools\` 尖峰工具重新校准;其余运行事项
   (窗口不能关、空闲时段、搜索页 tab 自愈)见下「运行」与「已知限制」。

## 运行(手动单轮)

1. 启动 PC 微信并扫码登录(保持运行;窗口可最小化但**不能关闭**)。
2. (首次/微信重启后)在微信中点开一次「搜一搜」(放大镜),让内置浏览器
   保持一个搜索页 tab —— 脚本每次搜索都复用它。搜索页 tab 被微信回收时
   爬虫会自动引导重开(聚焦微信主窗 → Ctrl+F → 粘贴账号名;若微信主窗
   无法置前则跳过合成键,本轮账号失败、下轮重试),不需要人工干预。
   也可运行 `.venv\Scripts\python.exe tools\bootstrap_search.py`
   由脚本代开(需桌面解锁)。
3. 自检:`.venv\Scripts\python.exe run_crawl.py --check`
   → 应输出「OK,可以抓取」。
4. 单号试跑:`.venv\Scripts\python.exe run_crawl.py --account 中金点睛`
5. 全量轮(账号乱序):`.venv\Scripts\python.exe run_crawl.py`

> **定时任务已删除(2026-09-04)**:当前没有任何自动抓取,跑一轮只能手动。
> 如需恢复:`powershell -ExecutionPolicy Bypass -File install_task.ps1`
> (注销加 `-Remove`;历史 XML 备份在 `backups\`)。恢复后**手动跑单号前
> 务必确认任务没在同时跑**——历史上曾因双驱动并发产生重复 pending 行。

## 名单与参数

- `config/accounts.yaml` — 公众号名单,`accounts:` 键下每行一个 `- 名称`。
- `config/settings.yaml` — 停止条数、重叠天数、深滚屏数上限、失败重试
  (`fail_retry`/`fail_retry_wait_sec`)、账号间隔、超时、MySQL 表名、
  钉钉推送等。**含钉钉 webhook 凭据,已 gitignore 不入库**;首次部署从
  `config/settings.example.yaml` 复制。
- `.env`(gitignore)— MySQL 连接参数,配置项名 `WXCHAT_CRAWLER_HOST /
  WXCHAT_CRAWLER_PORT / WXCHAT_CRAWLER_USER / WXCHAT_CRAWLER_PASSWORD /
  WXCHAT_CRAWLER_DATABASE`(优先级:系统环境变量 > .env > 内置默认;
  用带项目前缀的名字,避免与其他项目撞名)。模板见 `.env.example`。

## 钉钉推送

机器人安全设置 = 自定义关键词「wxcrawler」:**所有消息标题统一加
`wxcrawler:` 前缀**保证过检(`src\notify.py` 的 `MESSAGE_PREFIX`,
在发送通道统一注入,勿改名/删除,否则告警与总结类无链接消息会被
errcode=310000 拒发)。三类消息:

- **逐篇推送**:每抓到一篇新文章 URL 立即发一条 markdown(不聚合);
- **失败告警**:账号最终失败(重试耗尽)即刻推送,默认带 @;
- **轮末总结**:成功清单 / 新增篇数 / 失败清单,不带 @。

配置统一在 `config/settings.yaml` 的 `notify:` 段:

- `enabled` — 总开关;`webhook` — 自定义机器人地址(含 access_token,勿外传);
- `secret` — 机器人安全设置选「加签」时填 `SEC` 开头密钥,其余模式留空;
- `keyword` — 标题【关键词】样式用,与过检无关(过检靠 `wxcrawler:` 前缀),
  保持空串;
- `at_user_ids` — 要@的 userId 列表(应用机器人的 userId 也填这里,真 @);
- `at_robot_name` — 机器人群昵称(正文补 `@昵称` 文本兜底);
- `at_mobiles` — 要@的人的手机号;`at_all` — @所有人(逐篇推送,慎开)。

发送间隔内置 3 秒限流兜底(钉钉上限 20 条/分钟);推送失败只写告警日志,
不影响抓取主流程。连通性自测:
`.venv\Scripts\python.exe tools\dingtalk_test.py [文章URL]`

## MySQL 主库

存储只有 MySQL 一条通道(2026-09-05 起,SQLite 已弃用):直写
`localhost:3306` 的 `test` 库,表 `wechat_crawler_accounts` /
`wechat_crawler_articles` / `wechat_crawler_runs`(沿用原镜像表结构与全部
历史行;库/表不存在自动创建)。连接参数在 `.env`(见上)。

## 查看数据

```
.venv\Scripts\python.exe tools\db_stats.py
```

或直接 SQL(任意 MySQL 客户端):

```
mysql -uroot -p test -e "select title,url from wechat_crawler_articles order by id desc"
```

## 运行与排障

- 日志:`logs\crawl_YYYY-MM-DD.log`;每行都带完整日期时间,每个步骤
  (含逐篇文章)打印 `用时`,账号间隔等空档也会注明缘由,可直接对着
  时间线看进展与耗时(正常轮次各写一条 `wechat_crawler_runs` 记录,环境预检未通过的轮次不写)。
- **轮级统计**:账号失败后停 `fail_retry_wait_sec` 秒重试,最多加试
  `fail_retry` 次;最终失败**即刻**推钉钉告警(带 @);轮末推总结
  (成功/新增/失败清单),日志同步落盘。退出码:有失败为 1,全成功为 0。
- `url_status='pending'` 的文章 = 当轮没提取到 URL(可能是无页内链接的
  简排版文章、超时、或打开的页与标题不符/链接归属存疑 —— 日志以
  `PENDING`/`未打开PENDING`/`标题不符PENDING` 区分);次日该文仍在列表时
  自动重试补全。
- **桌面需解锁**:搜索框粘贴与「复制链接」菜单(2026-09-05 起的提取
  主路径)依赖合成键鼠 —— 锁屏/抢不到前台的轮次中菜单失败,文章退回
  树内提取(可后台),两路都拿不到的留 pending,下轮自动重试。
- **前台被占用**:正在用电脑时微信抢不到前台,搜索框粘贴不生效
  (日志:`搜索框粘贴未生效(前台被占用或桌面锁定)`)→ 当轮账号失败;
  手动跑全量轮请选电脑空闲时段。
- **微信大版本更新**后控件可能变化:先 `.venv\Scripts\python.exe run_crawl.py --check`
  (版本不匹配会告警),再按 `docs\spike-findings.md` 用 `tools\`
  下的尖峰工具重新校准 `src\wechat_bot.py` 顶部常量。
- 微信未登录/未启动:本轮立即中止,日志提示,下一轮自动恢复。

## 已知限制

- 依赖微信客户端真实界面;窗口最小化可以,但**不能关闭**微信。
- **前台竞争**:合成键鼠(搜索粘贴/复制链接菜单)要求微信能拿到前台,
  用户正在操作电脑时会失败 —— 等空闲再跑,勿与用户抢焦点。
- 增量场景每号每轮通常 0~5 篇新文章,每篇提取约 15~25 秒(解锁态;
  主路径「复制链接」菜单约 +4~8 秒),菜单失败的文章退回树内提取。
- 文章 URL 存两种稳定主键形态:① 「复制链接」菜单产出的短链
  `mp.weixin.qq.com/s/<token>`(token 永久;2026-09-05 起为主存形态,
  历史库存量行除外);② canonical 4 参数(`__biz/mid/idx/sn`;chksm 为
  会话级签名,同文每次提取都不同,已剔除;菜单失败时的兜底形态)。
  两种均公网可直接访问、跨次提取一致,可作稳定主键;短链对 playwright
  等无头浏览器自动化更友好(全参链接会触发微信人机验证)。
- 「复制链接」在**「···」(更多)按钮的左键菜单**里;本 build 的右键菜单
  **没有**「复制链接」(文本区=Chromium 简易菜单、配图=页内图片菜单,
  尖峰E 全区域扫描证实)。菜单依赖合成鼠标点击,锁屏轮次不可用
  (此时全部文章退回树内提取,拿不到可靠 URL 的留 pending,下轮解锁态
  自动补全)。
