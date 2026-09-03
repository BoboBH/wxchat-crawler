# 可行性尖峰A —— 微信 4.1.12.55 UIA 控件树探测记录

- **探测日期**: 2026-09-02
- **环境**: Windows 11 (2560×1440, 100% 缩放) / 微信 4.1.12.55（已登录）/
  Python 3 + uiautomation 2.0.29 / 探测脚本 `tools/spike_uia.py`
- **目标**: 判定「搜索公众号 → 打开主页 → 历史消息列表 → 滚动加载 → 打开文章」
  是否可通过 UI Automation 编程驱动。

## 实测字段

| 字段 | 实测值 |
| --- | --- |
| 主窗口 Name 子串 | `微信`（进程 Weixin.exe，pid 43448） |
| 主窗口 ClassName | `Qt51514QWindowIcon`（**不是** 3.x 的 `WeChatMainWndForPC`，也不是文档中的 `mmui::MainWindow`） |
| 主窗口 UIA 树 | **几乎为空**：仅 `Weixin`（1×1 pane）与 `MMUIRenderSubWindowHW`（渲染子表面）两个子节点，无任何按钮/编辑框/列表 → 主窗口本体**不可** UIA 寻址，只能整体 SetFocus + 盲发键盘 |
| 搜索方式（主窗口） | `Ctrl+F` 热键**可用**（截图证实顶部搜索框被激活、粘贴的关键词生效、弹出搜索面板）。但面板内的结果项（朋友圈/文章/公众号分类及联系人）不暴露任何 UIA 控件 → 只能按固定坐标盲点，**不推荐作为主路径**。回车不会把结果页送进内置浏览器 |
| 搜索方式（内置浏览器，**主路径**） | 公众号/搜一搜页面运行在 **WeChatAppEx.exe**（Chromium）窗口，顶层 `ClassName='Chrome_WidgetWin_0'`。搜索框为 `EditControl AutomationId='weixin-search-input'`；`ValuePattern.SetValue()` 报成功但**不触发页面事件（无效）**，必须 `Click` 聚焦 → 剪贴板粘贴（`{Ctrl}a` `{Ctrl}v`）；触发搜索用 `ButtonControl Name='搜索'` 的 `Invoke()`（或 `{Enter}`） |
| 搜索结果公众号条目 | `ButtonControl class='header-detail'`，Name 为拼接长文案，如 `中金点睛 公众号 中国国际金融股份有限公司 账号描述： 图文并茂讲解中金深度研究报告 7612篇原创内容 5小时前更新` → `Invoke()` 直接打开该公众号主页。可用 Name 前缀 `中金点睛` 或包含 `公众号` 过滤 |
| 历史消息入口按钮 | **4.x 无独立「历史消息/查看历史消息」按钮**。搜索结果 Invoke 公众号卡片后进入的 `RootWebArea Name='中金点睛'` 页面**本身就是完整历史列表**（按时间倒序 + 日期分组 + 无限滚动），顶部有筛选 tabs：`全部 / 贴图 / 文章 / 视频号`（`HyperlinkControl class='profile_details__tabs-item'`，`文章` tab Invoke 后只显示群发文章） |
| 历史消息页容器 | 同一 WeChatAppEx 窗口内的网页 `DocumentControl auto_id='RootWebArea'`；文章列表容器 class=`profile_details__content`；单篇条目标题 `TextControl class='article__item__title'`，日期分组标签 `class='publish_time'`（今天/昨天/星期一/8月23日…）；筛选 tabs class=`profile_details__tabs-item`。**网页控件必须锚定在 `PaneControl class='Chrome_RenderWidgetHostHWND' name='Chrome Legacy Window'` 宿主之下检索** |
| 滚动方式验证 | **鼠标滚轮有效**：`uia.MoveTo(页面中心)` + `uia.WheelDown(3)` 后可见标题集合完全变化（y 坐标整体上移约 125px/格），`WheelUp(3)` 精确还原；连续 `WheelDown(10)`×3 轮，标题数 17→39→50，日期从 8月23日 递推到 8月18日 → **无限滚动（懒加载）已实测可用**。`ScrollPattern` 不可用（容器无此 pattern），`End`/`PageDown` 键未验证 |
| 文章内页（附带发现） | 历史列表 Invoke 单篇标题 → 在**同一浏览器窗口新开一个 Tab**（无新 hwnd）。正文控件齐全：`aid='activity-name'`（标题）、`aid='js_name'`（公众号名）、`aid='publish_time'`（如 `Sep 2, 2026, 7:45 AM`）、`aid='js_author_name_text'`（作者）、`aid='js_content'`（正文全文，单页约 837 个控件、内容高约 21000px）。Tab 切换：Tab 控件（`TabStrip → FlueTabContainer → Tab`）**无 Name**，只能按 BoundingRectangle 中心坐标点击 |
| 窗口还原 | 窗口最小化时 rect=(-32000,…) 且树为空；自动化前需 `ShowWindow(hwnd, SW_RESTORE)` |
| 无障碍树激活 | Chromium 的 UIA 树默认懒加载：**必须先手动 `GetChildren()` 爬一遍**，之后 `FindFirst` 系列检索才能命中；从窗口根 `FindFirst` 会失败，必须从 `Chrome_RenderWidgetHostHWND` 宿主向下搜 |

## 结论（Gate A）

**可自动化（有条件）** —— 公众号历史文章全流程可在 WeChatAppEx.exe 内置浏览器窗口中
用 UIA 稳定驱动：搜索输入（剪贴板粘贴）→ 搜索按钮 Invoke → 公众号卡片 Invoke →
主页即历史列表 → 滚轮无限加载 → 文章标题/正文全量可读。**风险与限制**:

1. 微信 4.x **主窗口本体不可 UIA 寻址**（Qt 自绘），任何主窗口内交互只能盲发热键/盲点坐标，
   可靠性差 —— 方案必须全部锚定 WeChatAppEx 窗口；
2. `Chrome_RenderWidgetHostHWND` 的 `AutomationId` 每次导航/切 Tab 都会变化
   （实测 98790672→210965584→211003440→204734672），**只能按 ClassName 定位宿主**；
3. **文章 URL 不经 UIA 暴露**（文章页无地址栏）→ 采集文章链接必须走尖峰B 抓包路线；
4. `SetValue()` 对 Chromium 输入框无效，输入一律走剪贴板（会覆盖用户剪贴板，需保存/恢复）;
5. 搜索结果/主页控件 Name 为长文案拼接，版本更新可能变化，定位应优先用
   `AutomationId`/`class` 而非 Name 全文匹配。

## Task 9 端到端操作流程(实测伪流程)

以下为本次尖峰**实际打通**的导航流程，可作 Task 9（端到端采集）的实现蓝本：

1. **定位浏览器窗口**：枚举顶层窗口，取 `ClassName='Chrome_WidgetWin_0'` 且进程为
   `WeChatAppEx.exe` 的窗口（多个时取面积最大者）。不要走微信主窗口——其 Qt 树
   （`Qt51514QWindowIcon`）为空，不可 UIA 寻址。
2. **还原窗口**：若最小化（`BoundingRectangle.left <= -30000` 或 `WS_MINIMIZE`），
   先 `ShowWindow(hwnd, SW_RESTORE)`，否则树为空、坐标全错。
3. **激活无障碍树**：从窗口根手动 `GetChildren()` 爬一遍，找到
   `PaneControl class='Chrome_RenderWidgetHostHWND' name='Chrome Legacy Window'`
   宿主（**按 ClassName 定位**，其 AutomationId 每次导航都会变化）；不先爬一遍，
   后续 FindFirst 全部失败。
4. **输入搜索词**：在宿主子树下检索 `EditControl AutomationId='weixin-search-input'`，
   `Click()` 聚焦 → 保存用户剪贴板 → `SetClipboardText('中金点睛')` →
   `SendKeys('{Ctrl}a')`+`SendKeys('{Ctrl}v')` → 读回 Value 校验 → 结束后恢复剪贴板。
   （`ValuePattern.SetValue()` 报成功但不触发页面事件，实测无效。）
5. **触发搜索**：`ButtonControl Name='搜索'`（宿主子树，searchDepth≈15）`Invoke()`，
   页面转为 `RootWebArea Name='中金点睛 - 搜一搜'`。
6. **打开公众号主页**：`Invoke()` 结果页中 `ButtonControl class='header-detail'`
   的卡片（按 Name 含 `公众号` 或账号名前缀过滤）→ 进入
   `RootWebArea Name='中金点睛'`——**该页即完整历史列表**（4.x 无独立「历史消息」按钮）。
7. **滚动加载**：`uia.MoveTo(宿主矩形中心)` 后 `uia.WheelDown(n)`，每轮之间轮询
   `article__item__title` 数量直至不再增长；已实测 `WheelDown(10)`×3 轮标题数
   17→39→50（无限滚动生效，`ScrollPattern` 不可用）。
8. **采集列表**：标题 = `TextControl class='article__item__title'`（宿主子树，
   容器 `profile_details__content`），日期分组 = `class='publish_time'`
   （今天/昨天/星期一/8月23日…）；如只要群发文章，先 Invoke
   `profile_details__tabs-item` 中的「文章」tab。
9. **采集正文**：Invoke 单篇标题 → 同窗口新开 Tab（无新 hwnd）；从新宿主读取
   `aid='activity-name'`（标题）、`aid='js_name'`（公众号）、`aid='publish_time'`、
   `aid='js_author_name_text'`（作者）、`aid='js_content'`（正文全文）。
   **文章 URL 不经 UIA 暴露（文章页无地址栏）→ 必须走抓包（尖峰B / Task 3）**；
   Tab 切换只能按 Tab 控件（`TabStrip → FlueTabContainer → Tab`，无 Name）
   的 BoundingRectangle 中心坐标点击。

## 探测过程备注

1. `tools/spike_uia.py --list` 枚举顶层窗口 → 命中 `微信`（Weixin.exe）与
   `Chrome_WidgetWin_0`（WeChatAppEx.exe）两类窗口。
2. 主窗口 dump（深度4，--all-children 反复确认）：树仅 2 个无意义子节点；曾怀疑是窗口
   最小化所致，`ShowWindow(SW_RESTORE)` 还原后仍然如此 → 判定为 Qt 自绘窗口的真实形态。
3. 转向浏览器窗口 dump：`Chrome_WidgetWin_0` 下有完整 UIA 树；首次 `FindFirst(AutomationId='weixin-search-input')`
   失败，手动 `GetChildren` 爬一遍后同一检索成功 → 确认「先爬后找」的激活步骤必不可少。
4. 搜索流程实测：Click 搜索框 → `SetClipboardText('中金点睛')` → `{Ctrl}a`+`{Ctrl}v`
   （读回 Value 确认为「中金点睛」）→ Invoke `Name='搜索'` 按钮 → 页面转为
   `中金点睛 - 搜一搜` 结果页。
5. 结果页 Invoke `header-detail` 卡片 → 进入 `RootWebArea Name='中金点睛'` 公众号主页
   （即历史列表），dump 确认 `profile_details__content` 容器与 `article__item__title` 条目。
6. 滚动实测：`WheelDown(3)` 于页面中心 (cx, 700)，前后可见标题集合与 publish_time 对比，
   内容变化且 `WheelUp(3)` 精确还原；`WheelDown(10)`×3 轮标题数 17→39→50，无限滚动确认。
7. 主窗口 Ctrl+F 对照实验：SetFocus → `{Ctrl}f` → 粘贴 → `{Enter}`，截图证实搜索面板打开、
   关键词生效，但面板无 UIA 控件；且回车不会在浏览器新开结果 Tab → 主窗口路径仅作兜底。
8. 附带验证文章页：正文控件 aid 齐全（js_content 全文可读），无地址栏 → URL 不可得。

---

# 可行性尖峰B —— mitmproxy 截获公众号文章列表接口验证(2026-09-02)

- **环境**: Windows 11 / 微信 4.1.12.55(已登录)/ mitmproxy 12.2.3(venv)/ 系统代理 127.0.0.1:8888
- **产物**: `tools/spike_set_proxy.ps1`(系统代理开关)、`tools/spike_capture_addon.py`(采集插件)、
  `tools/spike_install_cert.py`(证书弹窗自动化)、`tools/spike_navigate.py`(UIA 导航)、
  `tools/spike_scroll_probe.py`(滚动探针)、`tools/spike_article_probe.py`/`tools/spike_final_probe.py`(综合探针);
  证据 `data/spike_mitmdump.log`、`data/spike_cap/`(39+ 份真实响应)、`data/spike_shot_before/after.png`、
  `data/spike_final_end.png`
- **Gate B 结论:失败(有价值的失败)** —— 系统代理路线**无法截获文章列表数据接口**;
  local 模式未执行(shell 非管理员,且本案不满足计划设定的回退前提,见下)。
  **未产生 `tests/fixtures/profile_ext_real.json` / `article_list_real.json`**(截获条件未达成,按计划不建)。

## 实测结果

| 项 | 实测值 |
| --- | --- |
| CA 证书安装 | `certutil -user -addstore root` 弹出「安全警告」(#32770)对话框,uiautomation 程序化点击「是(Y)」**成功**(快照前后差集定位新对话框,等待约 1s 即出现);`certutil -store -user root mitmproxy` 验证通过。证书保留在用户根存储,清理命令:`certutil -user -delstore root mitmproxy` |
| 代理方式 | 用户级系统代理(HKCU `ProxyEnable=1` + `ProxyServer=127.0.0.1:8888`,脚本 `spike_set_proxy.ps1`,InternetSetOption 39/37 通知生效) |
| AppEx 是否走系统代理 | **是**。`netstat -ano` 证实 `WeChatAppEx.exe`(pid 26472,`--type=utility --utility-sub-type=network.mojom.NetworkService`,隶属 xwechat RadiumWMPF 运行时)与 127.0.0.1:8888 建立 ESTABLISHED 连接;mitmdump 日志可见其 HTTP/2 请求 |
| 走代理可见的流量 | ① 页面壳:`GET channels.weixin.qq.com/web/pages/mp_profile?biz=…`(新版公众号主页 HTML,首次 200,二次 304 Not Modified——Chromium 磁盘缓存);② 静态资源:`finder.video.qq.com`/`findermp.video.qq.com/251/2030x/stodownload`(封面图 jpg、视频 mp4);③ 埋点上报:`mp.weixin.qq.com/mp/jsmonitor`(GET/POST,高频)、`channels.weixin.qq.com/web/report-perf`、`badjs.weixinbridge.com/frontend/reportspeed`、`support.weixin.qq.com/cgi-bin/mmsupport-bin/reportforweb`、`oss.work.weixin.qq.com/cgi-bin/oss_log` |
| **走代理不可见的流量(关键)** | ① 主页列表增长(UIA 标题数 11→24)与 tab 切换(全部/文章/视频号/贴图)期间**零新增 HTTP 请求**;② 打开文章页(《货币的秩序》第十五章,截图 `spike_final_end.png` 证实正文完整渲染)全程**无 `mp.weixin.qq.com/s?__biz=…` 请求**(3 次不同文章、两种点击方式复测);③ `Ctrl+R` 刷新主页零请求。已排除证书不信任(AppEx 对 mp.weixin.qq.com 的 TLS 中间人握手成功,jsmonitor 等正常解密) |
| 列表数据通道推断 | 列表翻页与文章正文经**微信客户端私有通道**注入 WebView:mitmdump 日志存在对 raw-IP 的 CONNECT 透传流(`111.63.206.76:443` 长链,持续小流量,表现为 `-> tcp ->` 非 HTTP 流),即 Weixin 主进程 mmtls 长链桥接(XWeb `XWorker` 特性);不排除部分为 QUIC/HTTP3 直连(HTTP 代理天然不可见) |
| 老接口对照 | 全程未出现 `/mp/profile_ext?action=getmsg`。**4.x 公众号主页由 `channels.weixin.qq.com/web/pages/mp_profile` 承载**(新版统一主页,顶部 tab=视频号/文章/贴图,网格卡片布局,文章封面走 finder CDN)。**Task 6/7 的解析器目标不能假设 profile_ext JSON**;即使换路线,样本结构也应以 `mp_profile` 页面壳/内嵌数据为准 |
| local 模式 | **未执行**:shell 非管理员(`IsInRole(Administrator)=False`,`net session` 拒绝),WinDivert 无法加载。且计划中 local 是「AppEx 完全无 [spike] 行」时的回退——本案 AppEx 有大量 [spike] 行,代理链路本身是通的,缺失的是数据接口不经 HTTP;local 重定向看到的仍是同一批 TCP,mmtls 载荷不可解,预期增益≈0 |

## 结论(Gate B)

**BLOCKED(就「被动抓包取文章 URL/列表」这一路线而言)** —— mitmproxy 系统代理可以截获
微信内嵌浏览器的**页面壳、静态资源与埋点流量**(TLS 中间人完全成功),但**文章列表翻页接口
与文章正文页请求均不经 HTTP 代理通道**,被动抓包拿不到文章 URL。结合尖峰A「文章 URL 不经
UIA 暴露」,**「UIA 驱动 + mitmproxy 被动抓包」组合无法产出文章链接**,Task 4+(采集器、
解析器)的输入假设需重估。候选替代路线(未验证,供决策):

1. **XWeb 远程调试**:WeChatAppEx 命令行未见 `--remote-debugging-port`,但 XWeb 系 Chromium
   内核,若能以调试端口/DevTools 协议接入,可直接读 DOM 拿 `<a href>`;
2. **页面 DOM/UIA 深挖**:文章页 a11y 树可读 `activity-name`/`js_content` 等 aid,但未验证
   HyperlinkControl 是否暴露 `Value.Value`(URL)——成本低,值得先试;
3. **本地缓存取证**:文章 HTML/列表数据既经客户端下发,大概率落在 `xwechat_files`/
   RadiumWMPF 的本地缓存(SQLite/CDN 缓存目录),可离线解析;
4. **协议层逆向(不推荐)**:mmtls 私有协议,成本与风控风险高。

## 复现步骤(本次实际执行序)

1. `.venv/Scripts/mitmdump.exe --listen-port 8888` 跑 8s 生成 `%USERPROFILE%\.mitmproxy\mitmproxy-ca-cert.cer`,kill;
2. `python tools/spike_install_cert.py <cer>` 自动点弹窗安装证书并验证;
3. 起 `mitmdump -s tools/spike_capture_addon.py`(日志 `data/spike_mitmdump.log`)→
   `powershell -File tools/spike_set_proxy.ps1` 开代理(reg 验证 ProxyEnable=1);
4. `python tools/spike_navigate.py`:UIA 搜索「中金点睛」→ Invoke `header-detail` 卡片 →
   主页滚轮加载(标题数 11,轮询平台期);
5. `tools/spike_scroll_probe.py`(计数+截图)、`tools/spike_article_probe.py`(进程映射/tab 枚举/
   重进主页/点文章)、`tools/spike_final_probe.py`(回退/刷新/坐标点击)逐步排除变量;
6. `spike_set_proxy.ps1 -Off` **还原并 reg 验证 ProxyEnable=0x0**,kill mitmdump,关闭测试打开的文章窗口。

## 工具链备注(下次自动化直接可用)

1. uiautomation 2.0.29 **没有 `Control.Invoke()`**,按钮触发要走 `ctrl.InvokePattern.Invoke()`
   (失败再退化为坐标 `Click`);`GetValuePattern().Value` 可读搜索框回显;
2. Chromium a11y 树是**懒 realization**:列表滚动后标题数增长≠发起了翻页请求(本次 11→24
   的增长发生在零请求期间),「翻页成功」必须以网络层证据为准;
3. tab 无 Name,只能按 `Tab` 控件 `BoundingRectangle` 中心坐标点击;每次卡片 Invoke 都会新开 tab,
   连续导航会堆积 8+ 个 tab,tab 条溢出后坐标点击不再可靠——脚本应先 Ctrl+W 清理;
4. `Alt+Left` 不会使 AppEx 页面历史回退;`Ctrl+R` 不产生网络请求(疑似整页走缓存/桥接);
5. 剥 `If-None-Match`/`If-Modified-Since`(mitmproxy `requestheaders` 钩子,已内置于
   spike_capture_addon.py)可强制服务器回 200 完整体,破解 Chromium 304——本次因无法再次触发
   `mp_profile` 导航而未验证成功,下次在**首次导航前**就装好该钩子;
6. mitmdump 日志含 GBK 乱码(Git Bash 管道),判定以 exit code 与 ASCII 关键字(NOT_FOUND 等)为准;
7. 系统代理开关脚本对全局应用生效(本次在线约 30 分钟),下次尽量压缩窗口期,优先把抓包
   钩子全部就位后再开代理。

---

# 可行性尖峰B' —— 备选路线探测:UIA 直读文章 URL / CDP 挂载 AppEx(2026-09-03)

- **环境**: Windows 11 / 微信 4.1.12.55(已登录,系统代理已还原关闭)/
  Python 3.12 + uiautomation 2.0.29(venv)
- **背景**: 尖峰A 判定「文章 URL 不经 UIA 暴露」时只看了控件 Name/aid,未穷举属性;
  尖峰B 判定被动抓包不可行。本尖峰按优先级验证两条备选路线:
  **路线A** = 穷举文章条目的 UIA 属性/模式拿 href;**路线B** = 找 DevTools 入口用 CDP 挂载。
- **产物**: `tools/spike_url_extract.py`(属性/模式穷举器);
  证据 `data/spike_url_dump.txt`(条目相关 105 候选节点)、
  `data/spike_url_dump_all.txt`(主页可见树全量 271 节点,均 gitignore)
- **Gate B' 结论:双双失败(BOTH_FAIL)** —— 在「不重启微信、不加启动参数、不注入」约束下,
  本机微信 4.1.12.55 **不存在已验证的文章 URL 获取通道**。Task 4+(采集/解析)的输入假设需重议。

## 路线A:穷举文章条目 UIA 属性 —— 失败(证据充分)

| 项 | 实测值 |
| --- | --- |
| 穷举方法 | uiautomation 自带的 comtypes `IUIAutomationElement` 定义**不含** `GetSupportedProperties()`/`GetSupportedPatterns()`(AttributeError)→ 改为暴力遍历 `uia.PropertyId` 全部 173 个 ID 逐一 `GetCurrentPropertyValueEx(pid, True)`(自动过滤默认值)、`uia.PatternId` 全部 34 个 ID 逐一 `GetPattern` 并读取模式属性(ValuePattern.Value、LegacyIAccessible.Value/Description/Help/Name/DefaultAction/Role/State、AriaProperties(30126)/AriaRole(30125)/FullDescription(30159)/ItemType(30021) 等);按 ControlType 缓存「连续未命中」控制 COM 往返量 |
| 覆盖面 | 中金点睛主页可见树**全量 271 节点**(含 11 个 `article__item__title` 标题及其 4 级父链、全部 Hyperlink/链接类控件),无一遗漏 |
| URL 命中 | **0 个 `mp.weixin.qq.com/s?__biz=…`**。全树唯一的 URL 是头像图 `https://wx.qlogo.cn/mmhead/...132&from=57`(经 AriaProperties/LegacyIAccessible 泄漏) |
| 结构证据(为何拿不到) | ① 标题 `AriaRoleProperty='heading'`、`AriaProperties='readonly=true;…hasactions=false'`,是标题不是链接;② 条目卡片 class=`js_article_card other_item article_item`——`js_` 前缀即「JS 绑定点击」,DOM 里**本来就没有 `<a href>`**;③ 连 class 带 link 的 `profile_album__item_link` 也被暴露为 `AriaRole='group'`、`LegacyIAccessible.Value=''`、`DefaultAction='按'`;④ 提供者描述 `[pid:29204 … Unidentified Provider (unmanaged:flue.dll)]`——UIA 树由腾讯 **flue.dll** 提供,不是原生 Chromium a11y 桥,href 一律不映射 |
| 模式面 | 271 节点仅 3 种模式:`TextChildPattern`/`ScrollItemPattern`/`LegacyIAccessiblePattern`,外加 1 个卡片有 `InvokePattern`(DefaultAction='按');无 ValuePattern/ObjectModelPattern/TextPattern |

**判定:路线A不成立。** 「Chromium 经常把 href 暴露给辅助功能树」在 XWeb(flue.dll 提供)上不成立,
且 DOM 层面列表条目本就是 JS 点击而非锚点;继续穷举 UIA 属性无增益。

## 路线B:CDP 挂载 AppEx —— 无 DevTools 入口,未挂载

| 探测手段 | 实测值 |
| --- | --- |
| `DevToolsActivePort` 文件 | `%APPDATA%\Tencent`(含 `xwechat`)、`%LOCALAPPDATA%\Tencent`、`%USERPROFILE%\xwechat_files` 递归 **depth 9** 全扫,2 秒内遍历完毕,**0 命中** |
| 进程命令行 | 23 个 `WeChatAppEx.exe` 进程参数全量归并(共 30 种 switch):只有 `--type/--wmpf/--enable-features/--disable-features/--product-id/…`,**无任何** `--remote-debugging-port/--remote-debugging-pipe/--devtools/--inspect` |
| 监听端口 | `netstat -ano` 全量 LISTENING 与 21 个 AppEx + 6 个 Weixin pid 求交:**AppEx 各进程 0 监听**;`Weixin.exe` 主进程(pid 43448)监听 `127.0.0.1:14013/14016/14019/14022/14023` |
| 主进程端口试探 | 5 个端口逐个 `curl /json/version` + `GET /`:**全部 `http_code=000`**(连接建立即被对端断开,无任何 HTTP 响应)——私有 mmrpc/长链辅助端口,不是 DevTools |
| 约束遵守 | 按任务约束**未**重启微信、**未**加启动参数(不影响用户环境)→ CDP 挂载无路径,`Network.enable` 捕获与 `data/spike_cdp_list_sample.json` 未产生;`websocket-client` 无需安装 |

**判定:路线B不成立(入口不存在)。**

## 附带观察(重议架构时需要知道)

1. **网页控件 rect 的坐标语义可疑(新发现的坑)**:`Chrome_RenderWidgetHostHWND` 宿主 rect
   `(0,52,1163,1137)` 以客户区为原点,而其顶层窗口 rect=`(490,85,1670,1230)` 是屏幕坐标;
   网页控件 y 高达 5132(文档坐标,含滚动偏移)、x 又落在客户区范围(855–1206)。
   本次按 rect 中心 `mouse_event` 点击标题 **4 次均未能打开文章页**(点击点落在屏幕外/错位)
   ——尖峰A「Task 9 流程」里「Invoke 标题打开文章」「tab 按坐标点击」两步在当前窗口布局下
   **未能复现**,实现前必须重测坐标换算(窗口 offset + 客户区 + 视口滚动)。
2. **AppEx 有多个顶层窗口**:除主窗口 `微信`(8 个 Tab 堆积,印证尖峰B 备注)外,还有
   `中金点睛` 窗口(520×979,doc=`AppIndex`)。`find_browser_window()` 按面积取最大者可能选错,
   应按激活宿主的 DocumentControl Name 过滤。
3. **本地缓存取证初查为负**:AppEx 的 Chromium 缓存 `%APPDATA%\Tencent\xwechat\radium\cache`
   仅 1.6MB 且 grep 不到 `mp.weixin.qq.com/s`;`business/xweb` 只有 mmkv 配置;
   `xwechat_files/<wxid>/cache/YYYY-MM/Message` 是聊天文件缓存 → 尖峰B 候选 3「文章 HTML 落在
   本地缓存可离线解析」在初查下不成立(未穷尽全盘)。
4. uiautomation 2.0.29 穷举全部属性/模式的可行做法已沉淀在 `tools/spike_url_extract.py`
   (暴力 PropertyId/PatternId 遍历 + 未命中缓存),可复用于其他 XWeb 页面。

## 结论(Gate B')

**BOTH_FAIL** —— 路线A(UIA 属性穷举)与路线B(CDP 挂载)都不通。结合尖峰B(被动抓包不可行),
三条低成本路线全部排除。剩余候选(成本/风险递增,均未验证):

1. **文章页「复制链接」剪贴板兜底**:逐篇 Invoke 标题 → 打开 native 分享面板 → 复制链接 →
   读剪贴板。缺点:面板在 Qt 主窗口(不可 UIA 寻址,只能盲点)、每篇 ≥8s、高频触发易风控;
   且附带观察 1 表明当前坐标点击不可靠,需先解决。
2. **协议层逆向 mmtls 长链**:成本与风控风险最高,不推荐。
3. **重定项目产物**:若文章自身链接非硬需求,UIA 已可全量拿到「标题+日期分组+正文全文」
   (尖峰A 已证实 `js_content` 单页 837 控件/21000px 可读)——最小可用采集器可以不依赖 URL;
   文章去重可改用标题+发布时间的哈希。

**对架构的影响:Task 4+ 需重议。** 在「不重启微信、不加启动参数、不注入」的约束下,
本机微信 4.1.12.55 无已验证的文章 URL 获取通道;建议按候选 3(重定产物)+ 候选 1(可选增强)
推进,并把「坐标换算重测」列为 Task 9 的前置验证项。

---

# 可行性尖峰B'' —— mp_profile 页面壳 HTML 内嵌数据验证(2026-09-03)

- **环境**: Windows 11 / 微信 4.1.12.55(已登录)/ mitmproxy 12.2.3(venv,带剥条件请求头钩子)/
  系统代理 127.0.0.1:8888(Clash Verge 在后台但未占用系统代理)
- **背景**: 尖峰B 曾观测 `GET channels.weixin.qq.com/web/pages/mp_profile?…` 返回 200 HTML 但未存响应体。
  本尖峰验证最后一个假设:**页面壳 HTML 是否 SSR/内嵌 JSON 数据块,首屏文章列表可直接提取**
  (若成立,被动抓包路线即可复活——增量采集只需首屏)。
- **产物**: `tools/spike_capture_addon.py`(新增 mp_profile 专项捕获:每个账号存
  `data/spike3c_mp_profile_<账号id前12位>.html`,打印 status/len/`mp.weixin.qq.com/s?` 计数)、
  `tools/spike_navigate.py`(invoke 修正为 `GetInvokePattern()`,新增 `reload_active_profile()`)。
  证据 `data/spike3c_mitmdump.log`、`data/spike3c_mp_profile_nobiz.html`(4140B 页面壳,gitignore)。
- **Gate B'' 结论:假设失败(HYPOTHESIS_FAIL)** —— mp_profile 的 HTML 是**纯客户端渲染的 SPA 壳**,
  不含任何文章数据;「mitmproxy 被动截 HTML → 解析内嵌列表」路线**死亡**,被动抓包路线至此全部关闭。

## 实测结果

| 项 | 实测值 |
| --- | --- |
| 捕获方式 | 系统代理 8888 + mitmdump(剥 `If-None-Match`/`If-Modified-Since` 钩子);因 AppEx 窗口不在前台,导航类输入全部失效,最终以 **UIA `InvokePattern` Invoke「重新加载」按钮** 触发整页重取 |
| 响应 | `status=200`,响应体 **4140 字节**(钩子生效,拿到的是完整体而非 304) |
| 请求 URL | `GET channels.weixin.qq.com/web/pages/mp_profile?bizusername=gh_2474c33c9534` —— **4.x 主页壳的账号参数是 `bizusername=gh_xxx`**(gh_ 账号 id),不是老的 base64 `biz`;中金点睛 = `gh_2474c33c9534` |
| 文章 URL 计数 | `mp.weixin.qq.com/s?` 原始 **0** 处,JSON 转义 `\/s?` **0** 处 |
| 内嵌数据块 | `__INITIAL_STATE__`/`__QMTPL*`/`window.__*`/`application/json`/`articleList`/`msg_list`/`general_msg_list`/`__NUXT` **全部 0 命中**;`<body>` 里只有空挂载点 `<div id="app"></div>` |
| 页面壳本质 | **Vite/Vue SPA 骨架**(`web-finder` = 视频号/公众号统一 web 工程):module scripts + polyfills + 空 `#app`;启动脚本显式调用 **`window.xweb.worker.connect()`** 并 `postMessage({apiName:'hello'})` —— 页面全部数据经 **XWeb worker 桥**(客户端私有通道,即尖峰B 判定的 mmtls 长链)注入,HTTP 上只见壳 |
| 重取时的全部流量 | 仅 ①壳本体(200/4140B)②`POST channels.weixin.qq.com/web/report-perf`(遥测);`mp.weixin.qq.com` 仅 jsmonitor 埋点。**已渲染的文章列表在整页 reload 时零数据请求** —— 列表数据从不走 HTTP |
| 「郭磊宏观茶座」样本 | **未捕获**。该号 tab 存在(条带 8 个 tab 之一)但无法切换:见下「意外」 |
| 滚动对照实验 | 未执行(代理窗口期预算内未完成);等效证据更强:**整页 reload 都不产生列表数据请求**,滚动更不可能有 |

## 意外与应对(本次踩坑实录,后续自动化必读)

1. **AppEx 窗口不在前台时,一切合成输入失效**:`SetForegroundWindow`/`SwitchToThisWindow`/
   `AttachThreadInput`/Alt-trick/minimize+restore **全部被拒**(前台属第三方窗口 Everything 时);
   合成鼠标点击虽能命中窗口(WindowFromPoint 证实)但 **tab 条不切换、按钮不触发**;
   键盘(Ctrl+W/Ctrl+R)发去了前台窗口。**唯一可靠的 UI 动作通道是 UIA Pattern**:
   `ReloadButton`(类名即此,rect≈579,96-611,128)`GetInvokePattern().Invoke()` 成功触发重载;
   uiautomation 2.0.29 的正确 API 是 `GetInvokePattern()`,**不是** `.InvokePattern` 属性
   (尖峰B 备注第 1 条有误,已修正)。
2. **tab 条真实几何**(UIA 树实测,视觉模型读数不可靠):`TabStripRegionView(610,91,1497,138)`
   → `TabStripScrollContainer → OverflowView → ScrollView::Viewport → TabStrip → FlueTabContainer`
   → 8 个 `Tab`(各 91-132 高、约 100px 宽,从 x=615 起);Tab 无 Name、无 SelectionItemPattern,
   **无法按名称寻址,也无法 UIA 选择**;关闭按钮 `ImageButton name='关闭'` 悬停才出现。
   顶部另有 `ReloadButton`/后退/前进 `ImageButton(509-580,98-129)`、`收起` 按钮(610,91,645,127)、
   `LocationBarView`(1396,91,1437,133)。
3. **第三方悬浮窗遮挡**:Everything 悬浮窗覆盖主屏中部(WindowFromPoint(1080,600)=Everything),
   截图视觉分析会把它的 UI 误读进目标窗口 —— 判定坐标必须用 `WindowFromPoint` 逐点核实,
   不能只信截图描述。
4. **addon 参数修正**:mp_profile 的账号参数是 `bizusername`(gh_ id),addon 已改为
   `bizusername` 优先(`biz` 兜底),文件名按账号 id 前 12 位。

## 结论(Gate B'')

**HYPOTHESIS_FAIL** —— mp_profile 页面壳是空 SPA 骨架,首屏文章列表**不在** HTML 里,
也无任何内嵌 JSON;数据经 XWeb worker 桥(mmtls 私有长链)注入,被动抓包在**架构上**
就不可能拿到列表。至此:**UIA 属性穷举(尖峰B')、CDP 挂载(尖峰B')、被动抓包
(尖峰B + B'')三条路线全部证实不可行**。维持尖峰B' 的架构建议:按候选 3 重定项目产物
(UIA 采集「标题+日期+正文全文」,去重用标题+时间哈希),文章 URL 仅在愿意承担
「native 分享面板盲点 + 每篇 ≥8s」成本时作可选增强。

## 复现步骤(本次实际执行序)

1. 增强插件与导航脚本 → `.venv/Scripts/mitmdump.exe --listen-port 8888 -s tools/spike_capture_addon.py > data/spike3c_mitmdump.log 2>&1 &`;
2. `tools/spike_set_proxy.ps1` 开代理(窗口#1,约 5.5min):UIA 导航尝试因窗口失焦失败,关代理还原;
3. 离线排查前台/点击/坐标问题(截图 + WindowFromPoint + UIA 树),定位 `ReloadButton`;
4. 再次开代理(窗口#2,约 2.8min,两窗合计 ≈8.3min):`GetInvokePattern().Invoke()` 触发重载 →
   插件即刻捕获壳 HTML(status=200/4140B)→ 关代理并 reg 验证 `ProxyEnable=0x0` → kill mitmdump;
5. 离线分析壳 HTML 与全量日志(上文实测表)。

---

# 可行性尖峰C —— 逐篇打开文章提取 canonical URL(2026-09-03)

- **环境**: Windows 11 / 微信 4.1.12.55(已登录)/ uiautomation 2.0.29(venv)/
  **验证期间工作站处于锁屏状态(LockApp.exe 为前台窗口)**
- **背景**: 尖峰A/B/B'/B'' 关闭了「列表直接拿 URL」与被动抓包全部路线后,验证最后一个目标假设:
  **逐篇打开新文章后,能否程序化拿到该文章自身的 canonical URL**。
- **产物**: `tools/spike_article_url.py`(沉淀版,main=打开第 N 篇→提取 URL→打印,
  含 `--times/--index/--verify`)、`tools/spike3d_probe.py`(过程探针:窗口/tab 枚举、
  聚焦属性 grep、模式统计)。
- **Gate C 结论:成立(URL_ENHANCE_WORKS)** —— 文章页 a11y 树中 HyperlinkControl 的
  `ValuePattern.Value`(与 `LegacyIAccessible.Value` 同值)携带**当前文章自身的 canonical URL**
  (`mp.weixin.qq.com/s?__biz=…&mid=…&idx=…&sn=…&chksm=…`)。两篇不同文章均取到各自 URL,
  公网 GET 的 og:title 与所开文章标题逐一吻合;同一篇文章重复提取 URL 完全一致。

## 判定证据(URL_ENHANCE_WORKS)

| 文章(Invoke 的列表条目) | 提取到的 URL(精简 5 参数) | 公网验证 |
| --- | --- | --- |
| 中金 \| Token启示录(六):开源模型价值透析 | `https://mp.weixin.qq.com/s?__biz=MzI3MDMzMjg0MA==&mid=2247857353&idx=2&sn=ae957b8c2f9ef7bcf0c0a1c438f2722c&chksm=eb6a7778638a3b11c8bff50586d670f685a1cf793c071895f6fa4ef18039364415213f6fecd9` | HTTP 200,3.5MB 全文,`og:title`=同名,账号=中金点睛,`oriCreateTime=1788306300`(2026-09-02 07:45 +08) |
| 中金• 全球研究 \| 印尼:政策紧缩遇上经济增长蓝图 | `https://mp.weixin.qq.com/s?__biz=MzI3MDMzMjg0MA==&mid=2247857036&idx=2&sn=6b368de1f5e01dc2644f24e5e51ae4ad&chksm=eb5b1728548e9f49dedceb5dcc4eedf23f78984d52a7bb71effad8fd616f2d57445d687499e5` | HTTP 200,`og:title`=同名 |

- URL 原始值还带 `scene/sessionid/clicktime/enterid/key/uin/pass_ticket/wx_header` 等客户端
  跟踪参数(uin=登录用户);**去参数后的 5 参数 URL 即公网可开的 canonical 链接**(实测直连
  GET 成功;曾出现过一次反爬 verify 挑战页,稍后重试同 URL 返回全文 → 挑战是间歇性的,
  入库建议存全参数 URL、以 5 参数为去重键)。
- 同一篇(idx=0)在约 1 小时内 **3 次提取(含两次连续脚本运行)URL 逐字节一致** → 可作稳定主键。
- 与尖峰A「Invoke 单篇标题打开文章」不同,本次必须 Invoke **带 InvokePattern 的祖先卡片**
  (class `js_article_card …`);标题 TextControl 自身无 InvokePattern。

## 成功手段与确定性操作序列(手段1 变体,全部 UIA Pattern,不依赖前台/键鼠)

1. **选窗口按内容,不按顺序/面积**:AppEx 有两个顶层窗口(主浏览器 `微信` + 侧窗
   `中金点睛` doc=`AppIndex`),Z 序在操作间会翻转;取「宿主树里存在
   `article__item__title`」的窗口为主页。
2. **打开文章**:主页树取 `article__item__title`(按 top 排序)→ 向上最多 4 级找
   `GetPattern(PatternId.InvokePattern)` 非空的祖先卡片 → `Invoke()` → 新 tab 激活。
3. **等文章页**:轮询各窗口宿主,树中出现 `aid∈{activity-name, js_content, js_name}`
   即文章页(**不能按 doc 名过滤** —— 侧窗 doc=`AppIndex` 同样 ≠ 主页名)。
4. **提取 URL**:文章宿主全树(833~1218 节点,扫描 1.8~2.2s)逐控件
   `GetPattern(PatternId.ValuePattern).Value`,正则取 `mp.weixin.qq.com/s?__biz=…`;
   实测每篇恰好 1 个,即文章自身 URL。
5. **关 tab 还原**:「激活 tab」= 其子控件 `ImageButton Name='关闭'` 的 rect **完整落在
   Tab rect 内**(非激活 tab 的关闭按钮 rect 是错位的悬停位,不能用来判断);
   Invoke 该按钮 → 主页 tab 重新激活、树随之恢复,tab 数回到 8。

**单篇耗时**: 解锁态参考首次实测 Invoke→文章页就绪 ≈6s;锁屏态依赖 kick(见下)
≈23~28s;树扫描 ≈2s;关 tab ≈2.5s;**合计 ≈47~53s/篇(锁屏)**,解锁预计 12~20s。

**可重复性**: `--times 2` 连续两轮 **2/2 成功**,URL 与首轮完全一致;`--index 2` 换一篇
同样成功。收尾后 tab 数恢复 8、主页 doc=`中金点睛`,无残留。

## 过程中新踩的坑(后续实现必读)

1. **锁屏(LockApp.exe 前台)下合成输入全部失效**(尖峰B'' 已见),但 **UIA Pattern 动作
   照常可用**(Invoke/读属性/ShowWindow)—— 本路线全程不需要键鼠与剪贴板,锁屏下完整跑通。
2. **新开 tab 后内容 a11y 树经常不 realization**(全窗仅 ~122 节点、0 个
   `Chrome_RenderWidgetHostHWND`,等待 30s+ 也未必自愈)。**解法:`ShowWindow(hwnd,
   SW_MINIMIZE)`→`SW_RESTORE` kick 一次即恢复**(955 节点);ShowWindow 是直发消息,
   锁屏下有效,不属合成输入。已封装 `kick_window()`,打开文章后轮询落空即逐窗口 kick。
3. **uiautomation 2.0.29 API 双坑**:① PatternId 成员名无 `Id` 后缀(`uia.PatternId.
   InvokePattern`,不是 `InvokePatternId`);② `GetInvokePattern()` 只是部分 ControlType
   (Button/Image 等)的方法,**GroupControl 等没有该方法(AttributeError)**——统一用
   `ctrl.GetPattern(uia.PatternId.InvokePattern)`(支持面返回 None,不支持抛异常,均需兜住)。
   尖峰B'' 的「`GetInvokePattern()` 是唯一可靠通道」结论仍对,但实现必须走 `GetPattern`。
4. **AppEx 顶层窗口 Z 序不稳定**:同一 pid 下 `Chrome_WidgetWin_0` 窗口列表顺序会在
   操作间翻转,任何「取第一个/取最大」的窗口选择都不可靠,必须按宿主内容筛选。
5. **枚举瞬时为空**:个别 `GetRootControl().GetChildren()` 会瞬时返回不含 AppEx 窗口的
   结果(锁屏切换期),需要带重试(1s×4)。
6. 文章页 URL 承载节点是 **HyperlinkControl(ControlType=50030)**:一篇中 1~2 个节点
   (其一 Name=文章标题,另一为页内链接如小程序入口「点击小程序查看报告原文」),
   `ValuePattern.Value` 与 `LegacyIAccessible.Value` 同值;列表页(主页)依旧 0 个(尖峰B'
   结论不变)。**已测样本仅 2 篇,均为含页内链接的研报文章**;不含任何链接的文章是否
   也暴露待增量验证(风险:极简排版文章可能无承载节点 → 失败时回退「无 URL 入库」即可,
   不阻塞主流程)。
7. 主页在文章 tab 打开期间树会折叠(懒 realization),关闭文章 tab 后自动恢复 ——
   不要误判为环境损坏;反之**不要**在文章 tab 激活时去找主页控件。

## 其余手段(按序应试,手段1 成功即停)

- **手段2(右键菜单复制链接)/ 手段3(更多面板)/ 手段4(在浏览器打开读进程命令行)**:
  未执行。手段1 已成立且零输入依赖;且当前锁屏状态下右键/悬停等合成输入(手段2/3 的
  前置)不可用,手段4 还会拉起浏览器进程,均劣于手段1。剪贴板全程未触碰。

## 结论(Gate C)与架构建议

**URL_ENHANCE_WORKS(手段1 变体)**。最终架构定为:
**UIA 抓主页列表「标题+日期分组」做增量判定 → 仅对新增文章逐篇 Invoke 卡片打开 →
文章页树扫 ValuePattern 提取 canonical URL(5 参数精简式去重,存全参数)→ 关 tab 还原 →
入库(标题/日期/正文全文/URL)**。URL 提取失败的文章按「无 URL」降级入库,不阻塞批次;
每篇预算 ≤60s(解锁态 ~15s),仅对增量执行,风控风险与人工逐篇打开等同。

### 追记(2026-09-03 验收/修复轮,提交 051f931 与后续)

上两行结论有两处被验收实测**修正**:

1. **chksm 不稳定**:同一篇(同 sn)三次提取得到**三个不同的 chksm** —— 它是
   会话级签名,不是内容身份。「5 参数逐字节一致」应修正为:**canonical 只保留
   `__biz/mid/idx/sn` 4 参数,chksm 必须剔除**(提交 `051f931`);否则同文每次
   提取都会被当成新文章。
2. **「每篇恰好 1 个 URL」不成立**:郭磊宏观茶座「【广发宏观陈嘉荔】沃什
   Jackson Hole演讲的新信号」一篇,页面树内嵌 **475 个** mp URL(推荐阅读/
   目录类内链),「最短者即主文」取到的是**另一篇** —— 详见尖峰D。



---

# 可行性尖峰D —— 文章页「··· → 复制链接」菜单取页面自身 URL(2026-09-03)

- **环境**: Windows 11 / 微信 4.1.12.55(已登录,桌面解锁)/ uiautomation 2.0.29(venv)
- **背景**: 验收发现「树内最短者即主文」会把内嵌链接记到本篇头上(污染页 475 个
  mp URL,最短者是**另一篇**)。菜单「复制链接」按定义复制**当前页自身** URL,
  验证其可编程触发性。产物:`tools/spike_copy_link.py`(含 `--dump/--probe/--menu-dump`
  诊断模式),证据 `data/spike_copy_run*.log`(gitignore)。
- **Gate D 结论:成立(2/2)** —— 全链路(AppMenuButton「更多」→ Click 弹层 →
  `FlueMenuItemView Name=复制链接` → Invoke → 剪贴板读 URL → canonicalize 通过)
  连续两次成功;已实现 `wechat_bot.copy_link_via_menu()` 兜底并接入主流程。

## 实测要点

| 项 | 实测值 |
| --- | --- |
| 菜单入口 | 浏览器工具条 `ButtonControl class='AppMenuButton' Name='更多'`(窗口右上,最小化/最大化旁)。**无 InvokePattern**;`ExpandCollapse.Expand()`、`LegacyIAccessible.DoDefaultAction()` 均不弹层;**只有合成鼠标 `Click` 能打开** → 锁屏轮次不可用 |
| 弹层归属 | **无新顶层窗口**(root 窗口快照前后差集为空);菜单项就落在同一 AppEx 窗口树里:`ButtonControl class='FlueMenuItemView'`,实测项 = `收藏 Ctrl+D` / `转发…` / `复制链接` / `刷新 Ctrl+R`;`InvokePattern.Invoke()` 可触发 |
| 复制产物 | 剪贴板得到 `https://mp.weixin.qq.com/s/MDVUlmL76lg0UsPl8KF86A` —— **短链 `/s/<token>` 形态**,两次提取逐字节一致(token 即文章身份、永久有效,公网 og:title 与所开文章一致);canonical 已扩展为接受短链(dedup key = 短链) |
| 污染证据 | 沃什文页面树 3647 节点、**475 个** `mp.weixin.qq.com/s?__biz=…` 内嵌 URL(推荐阅读/目录类内链,均带 `scene=21#wechat_redirect`);最短者为另一篇文章 → 树内「最短者」启发式必错 |
| 标题 aid 位置 | `activity-name`(文章标题)在树**尾部**(node≈3644/3647),`js_name`(公众号名)node≈3635,`js_content` 反而在 node≈74 → 扫标题必须给足节点预算,且**优先 activity-name**(js_name 是账号名,当标题用会全判不符) |
| 耗时 | 菜单兜底 ≈6~10s/篇(Click 1s + 找菜单项 ≤3s + Invoke 后等剪贴板 2s + 恢复剪贴板) |

## 结论(修复轮落地策略)

树扫描**仅在「唯一候选且该 URL 控件 Name ≈ 页面标题」时采信**(尖峰C:文章自身
URL 的控件 Name 就是文章标题);候选为 0、≥2、或归属存疑时走「复制链接」菜单;
标题校验不过(-2)不复制直接弃。菜单依赖解锁桌面(合成 Click),锁屏轮次多候选
文章留 pending 下轮补全。剪贴板先存后还在 `finally` 恢复;菜单不可达时按
`(None, 0)` pending,不猜链。
