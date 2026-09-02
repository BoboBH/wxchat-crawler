"""尖峰C:文章逐篇增强可行性验证 —— 从文章页 UIA 树提取 canonical URL。

用法(仓库根目录,微信已登录):
    .venv/Scripts/python.exe tools/spike_article_url.py [--account 中金点睛] [--times 2]

结论(docs/spike-findings.md 尖峰C):成立。文章页 a11y 树里,HyperlinkControl 的
ValuePattern.Value / LegacyIAccessible.Value 携带**当前文章自身的 canonical URL**
(https://mp.weixin.qq.com/s?__biz=…&sn=…,带 uin/pass_ticket 等客户端跟踪参数,
可精简到 __biz/mid/idx/sn/chksm 五参数后公网可开)。列表页仍拿不到 URL(尖峰B'),
因此采用「逐篇打开再提取」:每篇文章 Invoke 卡片 → 等文章宿主出现 → 全树扫
ValuePattern → 取 mp.weixin.qq.com/s? URL → 关闭文章 tab(还原主页)。

确定性操作序列(全部 UIA Pattern 动作,**不需要前台/键鼠**,锁屏下实测可用):
  1. 定位 WeChatAppEx 顶层窗口:枚举 Chrome_WidgetWin_0,按**内容**(存在
     article__item__title 的宿主)选中「中金点睛」主页所在窗口 —— 不要按面积/顺序选
     (存在第二个 AppEx 顶层窗口「中金点睛」doc=AppIndex,Z 序会变);
  2. 主页树里取 `article__item__title` 控件(按 BoundingRectangle.top 排序),
     向上最多 4 级找带 InvokePattern 的祖先卡片(class=js_article_card…),
     `GetPattern(PatternId.InvokePattern).Invoke()` 打开文章(新 tab 并激活);
  3. 轮询各 AppEx 窗口,找到含 aid=activity-name/js_content 的宿主即文章页
     (不能按 doc 名过滤 —— 侧窗 doc=AppIndex 同样 != 主页名);
  4. 文章宿主全树(≈830 节点)逐控件 `GetPattern(PatternId.ValuePattern).Value`,
     收集 `mp.weixin.qq.com/s?__biz=…` URL;
  5. 关闭文章 tab:tab 条上「激活 tab」= 子控件 `ImageButton Name='关闭'` 的 rect
     **完整落在 Tab rect 内**者(非激活 tab 的关闭按钮 rect 是错位的悬停位),
     Invoke 该按钮 → 主页 tab 重新激活、树随之恢复;
  6. 剪贴板全程未触碰(此路线不需要);若 tree 丢失(打开新 tab 后主页树折叠属正常)
     关闭文章 tab 后重爬即恢复。

注意:
  * uiautomation 2.0.29 的 PatternId 成员名是 `InvokePattern`/`ValuePattern`(无
    `*PatternId` 后缀);部分 ControlType(如 GroupControl)**没有**
    `GetInvokePattern()` 方法,统一用 `GetPattern(uia.PatternId.InvokePattern)`;
  * 锁屏(LockApp.exe 前台)时合成键鼠全部失效,本流程纯 UIA Pattern 故可用,
    但 `--verify` 的公网 fetch 不受影响;
  * 打开文章 tab 后主页树会折叠(节点数骤减),这是 Chromium 懒 realization,
    关闭文章 tab 自动恢复,不要误判为环境坏了。
"""
import argparse
import ctypes
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import uiautomation as uia
from spike_uia import find_render_hosts, process_name, restore_if_minimized

log = lambda m: print(f"[art] {m}", flush=True)

URL_RE = re.compile(r"https?://mp\.weixin\.qq\.com/s\?\S+")
ARTICLE_MARKERS = ("activity-name", "js_content", "js_name")


# ---------------------------------------------------------------- 窗口/宿主定位

def appex_windows(retries=4):
    """枚举 WeChatAppEx 顶层窗口(顺序不稳,Z 序会变;调用方必须按内容筛选)。"""
    for _ in range(retries):
        out = []
        try:
            for cand in uia.GetRootControl().GetChildren():
                try:
                    if (cand.ClassName or "") == "Chrome_WidgetWin_0" and \
                            process_name(cand.ProcessId).lower() == "wechatappex.exe":
                        out.append(cand)
                except Exception:
                    continue
        except Exception:
            pass
        if out:
            return out
        time.sleep(1.0)
    return []


def walk_ctrls(root, max_nodes=4000, max_depth=32):
    """深度遍历(兼做无障碍树激活)。"""
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


def host_doc_name(host):
    try:
        d = host.DocumentControl(searchDepth=3)
        if d.Exists(1, 0.2):
            return (d.Name or "").strip()
    except Exception:
        pass
    return ""


def kick_window(win):
    """最小化→还原,强制 Chromium 渲染器重绘并重新暴露无障碍树。

    锁屏/失焦下合成键鼠失效,但 ShowWindow 是直发消息不受影响;
    新开 tab 后内容树经常不 realization(全窗只有 ~122 节点、0 个渲染宿主),
    本操作实测一次即恢复(955 节点)。"""
    try:
        hwnd = win.NativeWindowHandle
        if not hwnd:
            return
        user32 = ctypes.windll.user32
        user32.ShowWindow(hwnd, 6)   # SW_MINIMIZE
        time.sleep(1.2)
        user32.ShowWindow(hwnd, 9)   # SW_RESTORE
        time.sleep(1.5)
    except Exception:
        pass


def visible_hosts(max_nodes=2500):
    """所有 AppEx 窗口里宽度>100 的渲染宿主(先爬树激活)。"""
    out = []
    for w in appex_windows():
        try:
            restore_if_minimized(w)
        except Exception:
            pass
        for h in find_render_hosts(w):
            try:
                if h.BoundingRectangle.right - h.BoundingRectangle.left > 100:
                    out.append((w, h))
            except Exception:
                continue
    return out


def find_host(pred, max_nodes=2500):
    """按内容谓词在所有窗口里找宿主,返回 (win, host) 或 (None, None)。"""
    for w, h in visible_hosts(max_nodes=max_nodes):
        for c in walk_ctrls(h, max_nodes=max_nodes):
            try:
                if pred(c):
                    return w, h
            except Exception:
                continue
    return None, None


def find_profile_host(account="中金点睛", kicks=2):
    """主页宿主:树里有 article__item__title;树折叠时 kick 后重找。"""
    for i in range(kicks + 1):
        w, h = find_host(lambda c: (c.ClassName or "") == "article__item__title")
        if h is not None:
            return w, h
        if i < kicks:
            log("主页树未 realization,kick 后重试")
            for win in appex_windows():
                kick_window(win)
    return None, None


def find_article_host(timeout=40.0):
    """轮询等待文章页宿主(含正文 aid 锚点);每轮落空 kick 一个窗口
    (主浏览器窗口优先 —— 新 tab 在它里面)。"""
    t0 = time.time()
    kicked = 0
    while time.time() - t0 < timeout:
        w, h = find_host(lambda c: (c.AutomationId or "") in ARTICLE_MARKERS,
                         max_nodes=1200)
        if h is not None:
            return w, h
        wins = sorted(appex_windows(),
                      key=lambda x: -((x.BoundingRectangle.right - x.BoundingRectangle.left) *
                                      (x.BoundingRectangle.bottom - x.BoundingRectangle.top))
                      if x.BoundingRectangle.right > x.BoundingRectangle.left else 0)
        if kicked < len(wins):
            kick_window(wins[kicked])
            kicked += 1
        time.sleep(0.8)
    return None, None


# ---------------------------------------------------------------- 文章打开/关闭

def invoke_pattern(ctrl):
    """返回控件上的 InvokePattern(没有则 None)。"""
    try:
        return ctrl.GetPattern(uia.PatternId.InvokePattern)
    except Exception:
        return None


def profile_titles(host):
    """主页文章标题控件,按屏幕纵序(top 值)排序。"""
    titles = [c for c in walk_ctrls(host)
              if (c.ClassName or "") == "article__item__title"]
    try:
        titles.sort(key=lambda c: c.BoundingRectangle.top)
    except Exception:
        pass
    return titles


def open_article(host, index=0, timeout=25.0):
    """Invoke 第 index 篇文章标题所在卡片,轮询等待文章页。返回 (doc名, 耗时s)。"""
    titles = profile_titles(host)
    if index >= len(titles):
        return None, -1.0
    t = titles[index]
    name = (t.Name or "").strip()
    log(f"打开第{index}篇: {name[:48]!r}")
    t0 = time.time()
    # 优先 InvokePattern 的祖先卡片(标题本身无 InvokePattern)
    p = t
    pat = None
    for _ in range(5):
        pat = invoke_pattern(p)
        if pat is not None:
            break
        try:
            p = p.GetParentControl()
        except Exception:
            break
        if p is None:
            break
    if pat is None:
        # 退化:LegacyIAccessible.DoDefaultAction
        try:
            t.GetPattern(uia.PatternId.LegacyIAccessiblePattern).DoDefaultAction()
        except Exception as exc:
            return None, -1.0
    else:
        pat.Invoke()
    w, h = find_article_host(timeout=timeout)
    dt = time.time() - t0
    if h is None:
        log(f"打开失败({dt:.1f}s 内未出现文章宿主)")
        return None, dt
    doc = host_doc_name(h)
    log(f"文章页就绪: doc={doc!r} ({dt:.1f}s)")
    return doc, dt


def close_active_tab(timeout=10.0):
    """关闭当前激活 tab:激活 tab = 其子「关闭」按钮 rect 完整落在 Tab rect 内。

    返回 True 表示已 Invoke 关闭按钮(不校验最终状态,调用方自行确认)。
    """
    for w in appex_windows():
        stack = [(w, 0)]
        while stack:
            c, d = stack.pop()
            try:
                if (c.ClassName or "") == "Tab":
                    tr = c.BoundingRectangle
                    for k in c.GetChildren():
                        if (k.Name or "") != "关闭":
                            continue
                        kr = k.BoundingRectangle
                        if tr.left <= kr.left and kr.right <= tr.right and \
                                tr.top <= kr.top and kr.bottom <= kr.bottom:
                            pat = invoke_pattern(k)
                            if pat is not None:
                                pat.Invoke()
                                time.sleep(2.5)
                                return True
                if d < 18:
                    stack.extend((kid, d + 1) for kid in c.GetChildren())
            except Exception:
                continue
    return False


# ---------------------------------------------------------------- URL 提取

def extract_article_urls(host, max_nodes=5000):
    """文章宿主全树扫 ValuePattern.Value,返回去重后的 mp 文章 URL 列表。"""
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


def slim_url(url):
    """去掉跟踪参数,保留 __biz/mid/idx/sn/chksm(公网可直接打开的最小集)。"""
    keep = []
    for kv in url.split("&"):
        key = kv.split("=", 1)[0]
        if key in ("__biz", "mid", "idx", "sn", "chksm") or kv.startswith("http"):
            keep.append(kv)
    return "&".join(keep)


# ---------------------------------------------------------------- 主流程

def fetch_title(url, timeout=25.0):
    """公网验证:GET URL 返回的 og:title(失败返回 None)。"""
    import urllib.request
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                    "AppleWebKit/537.36 Chrome/126.0 Safari/537.36"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            html = resp.read().decode("utf-8", "replace")
        m = re.search(r'<meta property="og:title" content="([^"]*)"', html)
        return m.group(1) if m else ""
    except Exception as exc:
        return f"<fetch error {exc!r}>"


def run_once(index, account, verify=False):
    """完整提取一次:主页 → 打开第 index 篇 → 提取 URL → 关 tab。
    返回 (成功?, doc, url, slim, 耗时s)。"""
    t0 = time.time()
    w, h = find_profile_host()
    if h is None:
        log("未找到公众号主页(需要已打开「中金点睛」主页;可先跑 tools/spike_navigate.py)")
        return False, None, None, None, time.time() - t0
    doc, dt_open = open_article(h, index)
    if doc is None:
        return False, None, None, None, time.time() - t0
    w2, h2 = find_article_host(timeout=8.0)
    if h2 is None:
        log("文章宿主丢失")
        close_active_tab()
        return False, doc, None, None, time.time() - t0
    t1 = time.time()
    urls, n = extract_article_urls(h2)
    dt_scan = time.time() - t1
    closed = close_active_tab()
    log(f"文章树 {n} 节点 扫描{dt_scan:.1f}s,URL {len(urls)} 个;"
        f"关闭tab={'ok' if closed else '未找到关闭按钮'}")
    if not urls:
        return False, doc, None, None, time.time() - t0
    url = sorted(urls, key=len)[0]
    slim = slim_url(url)
    dt = time.time() - t0
    if verify:
        og = fetch_title(url)
        log(f"公网验证 og:title={og!r}")
    log(f"URL = {slim}")
    log(f"耗时(含打开+提取+关tab) = {dt:.1f}s")
    return True, doc, url, slim, dt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", default="中金点睛")
    ap.add_argument("--index", type=int, default=0, help="主页列表第几篇(0 起,按纵序)")
    ap.add_argument("--times", type=int, default=1, help="重复次数(可重复性验证)")
    ap.add_argument("--verify", action="store_true", help="公网 GET 验证 og:title")
    args = ap.parse_args()

    oks = 0
    for i in range(args.times):
        log(f"===== 第 {i + 1}/{args.times} 次 =====")
        ok, doc, url, slim, dt = run_once(args.index, args.account, args.verify)
        oks += bool(ok)
        time.sleep(1.5)
    log(f"===== 成功 {oks}/{args.times} =====")
    return 0 if oks == args.times else 1


if __name__ == "__main__":
    sys.exit(main())
