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

## 尖峰B（抓包/协议层）预留

- 【待 Task 3】文章真实 URL 抓取方式（Fiddler/mitmproxy 证书安装 + 微信流量过滤规则）
- 【待 Task 3】历史列表「加载更多」的网络请求接口（request 参数/签名、翻页游标）
- 【待 Task 3】公众号 biz / uin / key 等会话参数的有效期与刷新方式
- 【待 Task 3】抓包代理与 UIA 驱动的协同流程（谁触发翻页、谁采集 URL）
- 【待 Task 3】频控/风控观测：安全翻页速率、账号异常告警信号
