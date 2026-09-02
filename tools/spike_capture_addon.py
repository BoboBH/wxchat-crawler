"""尖峰B:mitmproxy addon —— 打印并保存 mp.weixin.qq.com 相关响应,验证能否截获文章列表接口。

用法(仓库根目录):
    .venv/Scripts/mitmdump.exe --listen-port 8888 -s tools/spike_capture_addon.py

设计基准为计划中的 profile_ext getmsg 插件,按尖峰A情报放宽匹配:
4.x 公众号主页可能已不走老接口 /mp/profile_ext?action=getmsg,因此这里:
  1) 打印所有 qq.com / weixin / wechat 域请求(方法 + host + path + action 参数);
  2) 对 mp.weixin.qq.com 的文本响应存盘到 data/spike_cap/NNN_<path>.<ext>(上限 8MB);
  3) 自动识别「文章列表」特征(嵌套 JSON 字符串中的列表对象同时含标题类与 mp 文章
     链接类字段,或命中 general_msg_list / article_list / appmsg 标记),命中时打印
     ★ 行并把响应体另存 data/spike_cap/hit_NNN_*.json;
  4) 其他域请求只采样打印(每域前 2 条 + 之后每 25 条),用于诊断 AppEx 流量
     是否真的经过了代理(系统代理路线失败与否的关键证据)。
"""
import json
import os
import re
from urllib.parse import urlparse, parse_qs

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAVE_DIR = os.path.join(ROOT, "data", "spike_cap")
os.makedirs(SAVE_DIR, exist_ok=True)

MAX_SAVE_BYTES = 8 * 1024 * 1024
_seq = {"n": 0, "req": 0}
_hits = {"n": 0}
_host_count = {}

ARTICLE_URL_RE = re.compile(r'https?://mp\.weixin\.qq\.com/s[^"\'\\\s<>)\]]+')
TITLE_KEYS = ("title", "app_title", "title_disp")
URL_KEYS = ("url", "link", "content_url", "app_url", "msg_link")
MARKERS = ("general_msg_list", "article_list", "appmsg", "msg_list", "getmsg")

INTEREST = ("qq.com", "weixin", "wechat", "wx.")


def _walk(node, depth=0):
    """深度优先产出所有节点;同时解析内嵌 JSON 字符串(如 general_msg_list)。"""
    if depth > 10:
        return
    if isinstance(node, str):
        s = node.strip()
        if s[:1] in ("{", "["):
            try:
                yield from _walk(json.loads(s), depth + 1)
            except Exception:
                return
        return
    yield node
    if isinstance(node, list):
        for item in node[:200]:
            yield from _walk(item, depth + 1)
    elif isinstance(node, dict):
        for v in node.values():
            yield from _walk(v, depth + 1)


def _find_article_list(payload):
    """返回 (命中?, 描述):某列表节点元素为 dict 且同含标题类与链接类字段。"""
    for node in _walk(payload):
        if not isinstance(node, list) or len(node) < 3:
            continue
        dicts = [x for x in node[:8] if isinstance(x, dict)]
        if len(dicts) < 3:
            continue
        keys = set()
        for d in dicts:
            keys |= {k.lower() for k in d.keys()}
        if any(k in keys for k in TITLE_KEYS) and any(k in keys for k in URL_KEYS):
            sub = {k: type(v).__name__ for k, v in dicts[0].items()}
            return True, f"list_len={len(node)} sample_keys={sorted(sub)[:25]}"
    return False, ""


def _ext_of(ctype, path):
    if "json" in ctype or path.endswith(".json"):
        return "json"
    if "html" in ctype or path.endswith(".htm") or path.endswith(".html"):
        return "html"
    if "javascript" in ctype:
        return "js"
    return "txt"


def _textual(body):
    """body 看起来是文本(JSON/HTML/JS)而非二进制。"""
    if not body:
        return False
    head = body[:64]
    return all(ch in "\t\n\r " or 32 <= ord(ch) < 127 or ord(ch) > 0x2E7F or ch.isprintable()
               for ch in head)


def _save(path, text):
    try:
        with open(path, "w", encoding="utf-8", errors="replace") as f:
            f.write(text)
        return True
    except OSError:
        return False


def _log(line):
    print(f"[spike] {line}", flush=True)


def requestheaders(flow):
    """剥掉条件请求头,强制服务器返回完整 200 响应体(绕开 Chromium 缓存 304)。"""
    try:
        host = (flow.request.pretty_hostname or "").lower()
    except Exception:
        try:
            from urllib.parse import urlparse as _u
            host = (_u(flow.request.pretty_url).hostname or "").lower()
        except Exception:
            return
    if host.endswith("weixin.qq.com") or host.endswith("qq.com"):
        for h in ("if-none-match", "if-modified-since"):
            try:
                flow.request.headers.pop(h, None)
            except Exception:
                pass


MP_PROFILE_DIR = os.path.join(ROOT, "data")


def _capture_mp_profile(flow, q):
    """尖峰B'':mp_profile 页面壳 HTML 专项捕获 —— 验证 SSR/内嵌 JSON 是否含文章列表。

    每个不同 biz 存一份 data/spike3c_mp_profile_<biz前8位>.html,并打印
    响应体大小与 mp.weixin.qq.com/s? 的出现次数(含 JSON 转义形态 \\/s?)。
    """
    try:
        body = flow.response.get_text(strict=False) or ""
    except Exception as exc:
        _log(f"[spike3c] mp_profile 响应体读取失败: {exc!r}")
        return
    qs = dict(parse_qs(q.query))
    # 实测(尖峰B''):4.x 主页壳的账号参数是 bizusername=gh_xxx,不是老的 base64 biz
    biz = (qs.get("bizusername") or qs.get("biz") or [""])[0]
    key = (biz[:12] or "nobiz").replace("/", "_")
    n_raw = body.count("mp.weixin.qq.com/s?")
    n_esc = body.count("mp.weixin.qq.com\\/s?")
    n_biz_esc = body.count("\\/s?__biz=")
    try:
        status = flow.response.status_code
    except Exception:
        status = -1
    path = os.path.join(MP_PROFILE_DIR, f"spike3c_mp_profile_{key}.html")
    saved = len(body) <= MAX_SAVE_BYTES and _save(path, body)
    _log(f"[spike3c] mp_profile status={status} biz={biz!r} len={len(body)} "
         f"s_url_raw={n_raw} s_url_escaped={n_esc} esc_s_biz={n_biz_esc} "
         f"saved={saved} -> {os.path.relpath(path, ROOT)}")


def response(flow):
    try:
        url = flow.request.pretty_url
        q = urlparse(url)
        host = (q.hostname or "").lower()
        if not host:
            return
        ctype = ""
        try:
            ctype = flow.response.headers.get("content-type", "")
        except Exception:
            pass
        n = _host_count.get(host, 0) + 1
        _host_count[host] = n

        interesting = any(m in host for m in INTEREST)
        if interesting:
            action = dict(parse_qs(q.query)).get("action", [])
            _log(f"{flow.request.method} {host}{q.path} action={action} ct={ctype.split(';')[0] or '(空)'}")
        elif n <= 2 or n % 25 == 0:
            _log(f"(采样#{n}) {host}{q.path}")

        # 尖峰B'':公众号主页页面壳专项捕获(每个 biz 一份,统计内嵌文章 URL)
        if host == "channels.weixin.qq.com" and "mp_profile" in q.path:
            _capture_mp_profile(flow, q)

        # 请求体:channels/mp 域的 POST 可能携带翻页游标
        if interesting and host in ("channels.weixin.qq.com", "mp.weixin.qq.com") \
                and flow.request.method in ("POST", "PUT"):
            try:
                req_body = flow.request.get_text(strict=False) or ""
            except Exception:
                req_body = ""
            if req_body and len(req_body) <= MAX_SAVE_BYTES:
                _seq["req"] += 1
                _save(os.path.join(SAVE_DIR, f"req{_seq['req']:03d}_{q.path.strip('/').replace('/', '_')}.txt"),
                      f"URL: {url}\n\n{req_body[:100000]}")

        if host != "mp.weixin.qq.com":
            # 放宽:channels.weixin.qq.com 等 qq.com/weixin 域的文本响应也存盘
            if interesting:
                try:
                    body0 = flow.response.get_text(strict=False)
                except Exception:
                    body0 = None
                if body0 and _textual(body0) and len(body0) <= MAX_SAVE_BYTES and _seq["n"] < 400:
                    _seq["n"] += 1
                    nm = f"{_seq['n']:03d}_{host}_{q.path.strip('/').replace('/', '_') or 'root'}"[:120]
                    _save(os.path.join(SAVE_DIR, f"{nm}.{_ext_of(ctype, q.path)}"), body0)
                    if host == "channels.weixin.qq.com":
                        _log(f"已存 channels 响应: {q.path} len={len(body0)} "
                             f"query={q.query[:300]}")
            return

        try:
            body = flow.response.get_text(strict=False)
        except Exception as exc:
            _log(f"响应体读取失败 {q.path}: {exc!r}")
            return
        if body is None:
            return

        _seq["n"] += 1
        name = f"{_seq['n']:03d}_{q.path.strip('/').replace('/', '_') or 'root'}"
        raw_path = os.path.join(SAVE_DIR, f"{name}.{_ext_of(ctype, q.path)}")
        if len(body) <= MAX_SAVE_BYTES:
            if not _save(raw_path, body):
                _log(f"存盘失败 {raw_path}")

        info = []
        try:
            payload = json.loads(body)
        except Exception:
            payload = None
        if payload is not None:
            top_keys = sorted(payload.keys()) if isinstance(payload, dict) else f"list[{len(payload)}]"
            info.append(f"top_keys={top_keys}")
        n_urls = len(ARTICLE_URL_RE.findall(body))
        if n_urls:
            info.append(f"mp文章链接数={n_urls}")
        markers = [m for m in MARKERS if m in body]
        if markers:
            info.append(f"markers={markers}")

        hit, why = (False, "")
        if payload is not None:
            hit, why = _find_article_list(payload)
        elif "general_msg_list" in body:
            hit, why = True, "general_msg_list(嵌套JSON字符串,顶层解析失败)"

        if hit:
            _hits["n"] += 1
            hit_path = os.path.join(SAVE_DIR, f"hit_{_hits['n']:03d}_{name}.{_ext_of(ctype, q.path)}")
            if len(body) <= MAX_SAVE_BYTES:
                try:
                    with open(hit_path, "w", encoding="utf-8", errors="replace") as f:
                        f.write(body)
                except OSError:
                    pass
            _log(f"★ 疑似文章列表: {flow.request.method} {q.scheme}://{host}{q.path}")
            _log(f"★ 查询串: {q.query[:800]}")
            _log(f"★ 判定依据: {why}; {'; '.join(info)}")
            _log(f"★ 响应体已存: {os.path.relpath(hit_path, ROOT)}")
        elif _seq["n"] % 5 == 0 or markers or n_urls:
            _log(f"mp响应 #{_seq['n']} {q.path} {'; '.join(info)} -> {os.path.relpath(raw_path, ROOT)}")
    except Exception as exc:  # addon 内任何异常都不允许拖垮 mitmdump
        try:
            _log(f"addon 异常: {exc!r}")
        except Exception:
            pass


def done():
    try:
        _log(f"会话结束: mp响应存盘 {_seq['n']} 份, 命中 {_hits['n']} 次, 域统计={_host_count}")
    except Exception:
        pass
