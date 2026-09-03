"""钉钉自定义机器人通知:每抓到一篇新文章 URL,立即逐篇推送一条 markdown 消息。

设计要点(为什么这么做):
  * 逐篇即推不聚合(用户要求)——群里每篇一条,看到即取用;
  * 限流兜底:机器人 20 条/分钟,设 MIN_GAP 全局最小发送间隔
    (正常抓取约 30~70s/篇不会触限;防御批量重推等异常节奏);
  * send 绝不抛异常、失败仅返回 (False, 原因) —— 通知是附属功能,
    钉钉抖动不能影响抓取主流程与退出码;
  * 加签按官方算法:HMAC-SHA256("{timestamp}\\n{secret}") → base64 → urlencode;
  * @:手机号走 atMobiles(markdown 消息正文须同时写 @手机号 才生效)、
    userId 走 atUserIds(客户端自动解析,无需正文);应用机器人没有手机号,
    其 userId 也可填入 atUserIds;另按群昵称写 @文本(at_robot_name)兜底,
    能否触发其「接收消息」以真机为准。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import urllib.parse
import urllib.request

from .config import NotifyConfig

RETRIES = 2           # 首次 + 2 次重试 = 共 3 次尝试
BACKOFF = (1.0, 3.0)  # 第 1/2 次重试前的等待秒数
MIN_GAP = 3.0         # 两次发送的全局最小间隔秒(限流 20 条/分钟兜底)

_last_send = -1e18    # 上次发送时刻(monotonic),模块级:跨账号/跨轮同样限流


def sign_webhook(webhook: str, secret: str, timestamp_ms: int | None = None) -> str:
    """机器人选「加签」时按官方算法给 webhook 追加 timestamp 与 sign;
    未配 secret 原样返回(关键词/IP 白名单模式不需要加签)。"""
    if not secret:
        return webhook
    ts = int(time.time() * 1000) if timestamp_ms is None else timestamp_ms
    string_to_sign = f"{ts}\n{secret}"
    digest = hmac.new(secret.encode("utf-8"), string_to_sign.encode("utf-8"),
                      hashlib.sha256).digest()
    sig = urllib.parse.quote_plus(base64.b64encode(digest).decode("utf-8"))
    return f"{webhook}&timestamp={ts}&sign={sig}"


def build_payload(article: dict, keyword: str = "", at_robot_name: str = "",
                  at_mobiles: list[str] | None = None,
                  at_user_ids: list[str] | None = None,
                  at_all: bool = False) -> dict:
    """单篇 markdown 消息:标题即链接 + 账号/日期 + @ 文本;at 字段承载真 @。

    article 含 account/title/url/date_text(orchestrator 逐篇收集)。
    """
    at_mobiles = [str(m) for m in (at_mobiles or [])]
    at_user_ids = [str(u) for u in (at_user_ids or [])]
    kw = f"【{keyword.strip()}】" if keyword.strip() else ""
    lines = [
        "### 新文章发布",
        f"- [{article['title']}]({article['url']})",
        f"{article['account']} · {article['date_text']}",
    ]
    mentions = [x for x in [f"@{at_robot_name}" if at_robot_name else "",
                            *(f"@{m}" for m in at_mobiles)] if x]
    if mentions:
        lines.append(" ".join(mentions))
    return {
        "msgtype": "markdown",
        "markdown": {"title": f"新文章{kw}: {article['title'][:30]}",
                     "text": "\n".join(lines)},
        "at": {"atMobiles": at_mobiles, "atUserIds": at_user_ids,
               "isAtAll": at_all},
    }


def _default_post(url: str, payload: dict) -> tuple[int, dict]:
    """真实 HTTP POST(超时 10s);返回 (状态码, 解析后的 JSON)。
    独立成模块级函数:测试里 monkeypatch 它即可整体断流,无需触网。"""
    req = urllib.request.Request(
        url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as resp:
        body = resp.read().decode("utf-8", "replace")
    try:
        return resp.status, json.loads(body)
    except ValueError:  # 非 JSON 响应按业务失败处理,不向上抛
        return resp.status, {"errcode": -1, "errmsg": f"非JSON响应: {body[:120]}"}


def _throttle(sleep) -> None:
    """两次发送(含失败的发送)之间保证 MIN_GAP 间隔。"""
    global _last_send
    wait = MIN_GAP - (time.monotonic() - _last_send)
    if wait > 0:
        sleep(wait)
    _last_send = time.monotonic()


def send_article(notify: NotifyConfig, article: dict, post=None,
                 sleep=time.sleep, log=None) -> tuple[bool, str]:
    """逐篇发送。绝不抛异常:结果一律 (是否成功, 原因说明)。

    post/sleep 可注入以便离线测试;成功判定 = HTTP 200 且 errcode == 0。
    未启用 / 缺 webhook / 无 URL 直接短路不发。
    """
    if not notify.enabled:
        return False, "通知未启用"
    if not notify.webhook:
        return False, "缺少 webhook"
    if not article.get("url"):
        return False, "无URL不发送"
    post = post or _default_post
    _throttle(sleep)
    url = sign_webhook(notify.webhook, notify.secret)
    payload = build_payload(article, notify.keyword, notify.at_robot_name,
                            notify.at_mobiles, notify.at_user_ids, notify.at_all)
    last = ""
    for attempt in range(RETRIES + 1):
        try:
            status, data = post(url, payload)
            if status == 200 and data.get("errcode") == 0:
                return True, "ok"
            last = (f"HTTP {status} errcode={data.get('errcode')} "
                    f"errmsg={data.get('errmsg')}")
        except Exception as exc:  # 网络抖动等:记下原因,走重试
            last = f"通知异常: {exc}"
        if attempt < RETRIES:
            if log is not None:
                log.warning("钉钉通知第%d次失败(%s),%.0fs 后重试",
                            attempt + 1, last, BACKOFF[attempt])
            sleep(BACKOFF[attempt])
    return False, last
