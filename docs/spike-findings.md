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

