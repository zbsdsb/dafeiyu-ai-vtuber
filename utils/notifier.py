"""把异常报警推到手机上。

设计要点：
- 通道不是一个「选一个发」的下拉，而是各自独立开关；配了哪个发哪个，可以同时发多路
- 钉钉群机器人走 markdown + 加签（secret 必填），发完校验 errcode
- Telegram 走代理降级链：配置的代理 → 7890 / 7897 / 1080 → 直连
- 文案按 UTF-8 字节截断，不是按字符数截（中文 3 字节，按字符截会超限被拒）

任何失败都只写一条 warning 日志，绝不抛出——通知通道出问题不能反过来影响直播主链路。
"""

import base64
import hashlib
import hmac
import json
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from urllib.request import ProxyHandler, Request, build_opener, urlopen

from .my_log import logger

CHANNELS = ("dingtalk", "telegram", "wecom", "feishu", "bark", "serverchan")

TELEGRAM_API = "https://api.telegram.org"
SERVERCHAN_API = "https://sctapi.ftqq.com"

# 钉钉 markdown 上限 20000 字节，留点余量
MAX_TEXT_BYTES = 18_000

DEFAULT_REPEAT_INTERVAL = 300

# 国内直连 Telegram 基本不通，按顺序挨个试，最后一个候选是直连
DEFAULT_PROXIES = ("http://127.0.0.1:7890", "http://127.0.0.1:7897", "http://127.0.0.1:1080")

NOTIFY_TIMEZONE = timezone(timedelta(hours=8), name="Asia/Shanghai")

# key -> 上次发送时间。同一个类别在间隔内只推一次，避免一次连续故障刷满手机
_last_sent = {}
_lock = threading.Lock()


def reset_rate_limit():
    """测试用：清掉限流记录。"""
    with _lock:
        _last_sent.clear()


# ---------------------------------------------------------------- 文案

def _escape_markdown(value):
    """钉钉 markdown 里昵称/错误信息带特殊字符会把整条消息渲染坏。"""
    text = " ".join(str(value).splitlines()).strip()
    for character in ("\\", "`", "*", "_", "[", "]", "#", ">", "|"):
        text = text.replace(character, f"\\{character}")
    return text


def _truncate_utf8(text, max_bytes=MAX_TEXT_BYTES):
    """按字节截断，中文一个字 3 字节，按字符数截会照样超限。"""
    if len(text.encode("utf-8")) <= max_bytes:
        return text
    suffix = "\n\n> 内容过长，已省略。"
    available = max_bytes - len(suffix.encode("utf-8"))
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if len(text[:middle].encode("utf-8")) <= available:
            low = middle
        else:
            high = middle - 1
    return f"{text[:low]}{suffix}"


def build_markdown(title, content, now=None):
    """钉钉用的 markdown：标题 + 引用块字段，格式统一。"""
    finished = (now or datetime.now(timezone.utc)).astimezone(NOTIFY_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        f"### {_escape_markdown(title)}",
        "",
        f"> **时间**：{finished}  ",
        f"> **主机**：{_escape_markdown(_hostname())}  ",
        "",
        _escape_markdown(content),
    ]
    return _truncate_utf8("\n".join(lines))


def build_text(title, content, now=None):
    """Telegram / 企业微信 / 飞书 / Bark / Server酱用的纯文本，带一点分隔感。"""
    finished = (now or datetime.now(timezone.utc)).astimezone(NOTIFY_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        f"🐟 {title}",
        "━━━━━━━━━━━━━━━━━━",
        f"🕒 时间：{finished}",
        f"🖥 主机：{_hostname()}",
        "",
        content,
    ]
    return _truncate_utf8("\n".join(lines))


def _hostname():
    import socket
    try:
        return socket.gethostname()
    except Exception:
        return "unknown"


# ---------------------------------------------------------------- 各通道

def signed_dingtalk_url(webhook, secret, timestamp_ms=None):
    """钉钉群机器人「加签」：参数是毫秒时间戳 + secret 的 HMAC-SHA256 base64。"""
    timestamp = timestamp_ms if timestamp_ms is not None else int(time.time() * 1000)
    string_to_sign = f"{timestamp}\n{secret}".encode("utf-8")
    signature = base64.b64encode(
        hmac.new(secret.encode("utf-8"), string_to_sign, hashlib.sha256).digest()
    ).decode()
    parsed = urllib.parse.urlsplit(webhook)
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    query.extend((("timestamp", str(timestamp)), ("sign", signature)))
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urllib.parse.urlencode(query), parsed.fragment)
    )


def _post_json(url, payload, timeout=15, proxies=None):
    handlers = []
    if proxies:
        handlers.append(ProxyHandler({"http": proxies, "https": proxies}))
    opener = build_opener(*handlers)
    request = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with opener.open(request, timeout=timeout) as response:
        return response.read().decode("utf-8")


def _get(url, timeout=10):
    with urlopen(url, timeout=timeout) as response:
        return response.read().decode("utf-8")


def send_dingtalk(channel, title, content):
    webhook = (channel.get("webhook") or "").strip()
    secret = (channel.get("secret") or "").strip()
    if not webhook or not secret:
        raise RuntimeError("钉钉需要同时配 webhook 和 secret（加签）")
    payload = {
        "msgtype": "markdown",
        "markdown": {"title": title, "text": build_markdown(title, content)},
        "at": {"isAtAll": False},
    }
    body = _post_json(signed_dingtalk_url(webhook, secret), payload)
    result = json.loads(body)
    # 钉钉永远返回 200，错误只体现在 errcode 上，不看这个字段等于假成功
    if result.get("errcode") != 0:
        raise RuntimeError(f"钉钉返回错误：{result.get('errmsg', body)[:200]}")
    return body


def telegram_proxy_candidates(channel):
    """代理降级顺序：配置的代理 → 7890 / 7897 / 1080 → 直连。

    单独抽出来是为了能不联网地校验顺序。注意：urllib 会按系统代理例外表跳过
    localhost，所以指向本机地址的测试并不能证明某一级真的走通了代理。
    """
    candidates = []
    configured = (channel.get("proxy") or "").strip()
    if configured:
        candidates.append(configured)
    for candidate in DEFAULT_PROXIES:
        if candidate not in candidates:
            candidates.append(candidate)
    candidates.append(None)  # 最后直连
    return candidates


def send_telegram(channel, title, content):
    token = (channel.get("bot_token") or "").strip()
    chat_id = (channel.get("chat_id") or "").strip()
    if not token or not chat_id:
        raise RuntimeError("Telegram 需要同时配 bot_token 和 chat_id")

    candidates = telegram_proxy_candidates(channel)
    url = f"{TELEGRAM_API}/bot{token}/sendMessage"
    payload = json.dumps({"chat_id": chat_id, "text": build_text(title, content)}).encode("utf-8")
    last_error = None
    for candidate in candidates:
        try:
            handlers = []
            if candidate:
                handlers.append(ProxyHandler({"http": candidate, "https": candidate}))
            opener = build_opener(*handlers)
            request = Request(
                url,
                data=payload,
                headers={"Content-Type": "application/json; charset=utf-8"},
                method="POST",
            )
            with opener.open(request, timeout=12) as response:
                result = json.loads(response.read().decode("utf-8"))
            if result.get("ok"):
                return f"proxy={candidate or 'direct'}"
            raise RuntimeError(f"Telegram 返回错误：{str(result)[:200]}")
        except Exception as error:
            last_error = error
            continue
    raise last_error or RuntimeError("Telegram 所有代理候选均失败")


def send_wecom(channel, title, content):
    webhook = (channel.get("webhook") or "").strip()
    if not webhook:
        raise RuntimeError("企业微信需要配 webhook")
    body = _post_json(webhook, {"msgtype": "text", "text": {"content": build_text(title, content)}})
    result = json.loads(body)
    if result.get("errcode") not in (0, None):
        raise RuntimeError(f"企业微信返回错误：{result.get('errmsg', body)[:200]}")
    return body


def send_feishu(channel, title, content):
    webhook = (channel.get("webhook") or "").strip()
    if not webhook:
        raise RuntimeError("飞书需要配 webhook")
    body = _post_json(webhook, {"msg_type": "text", "content": {"text": build_text(title, content)}})
    result = json.loads(body)
    code = result.get("code", result.get("StatusCode", 0))
    if code not in (0, None):
        raise RuntimeError(f"飞书返回错误：{str(result)[:200]}")
    return body


def send_bark(channel, title, content):
    url = (channel.get("url") or "").strip()
    if not url:
        raise RuntimeError("Bark 需要配 url")
    target = url.rstrip("/") + "/" + urllib.parse.quote(f"{title} {content}")
    return _get(target)


def send_serverchan(channel, title, content):
    sendkey = (channel.get("sendkey") or "").strip()
    if not sendkey:
        raise RuntimeError("Server酱需要配 sendkey")
    url = f"{SERVERCHAN_API}/{sendkey}.send"
    return _post_json(url, {"title": title, "desp": build_text(title, content)})


SENDERS = {
    "dingtalk": send_dingtalk,
    "telegram": send_telegram,
    "wecom": send_wecom,
    "feishu": send_feishu,
    "bark": send_bark,
    "serverchan": send_serverchan,
}


# ---------------------------------------------------------------- 入口

def enabled_channels(config):
    """返回配好并启用的通道名列表；总开关关着就一个都没有。"""
    if not config.get("notify", "enable"):
        return []
    channels = []
    for name in CHANNELS:
        channel = config.get("notify", name)
        if isinstance(channel, dict) and channel.get("enable"):
            channels.append(name)
    return channels


def send(config, title, content, key=None):
    """把一条通知发到所有启用的通道，返回成功的通道名列表。

    同一 key 在 `notify.repeat_interval_seconds` 内只发一次。
    未启用、没有可用通道、限流中、全部失败都返回空列表，绝不抛出。
    """
    try:
        names = enabled_channels(config)
        if not names:
            return []

        interval = config.get("notify", "repeat_interval_seconds")
        interval = DEFAULT_REPEAT_INTERVAL if interval in (None, "") else float(interval)
        dedupe_key = key or title
        now = time.time()
        with _lock:
            last = _last_sent.get(dedupe_key)
            if last is not None and now - last < interval:
                logger.debug(f"通知 {dedupe_key} 在 {interval} 秒限流窗口内，跳过")
                return []
            _last_sent[dedupe_key] = now

        sent = []
        for name in names:
            channel = config.get("notify", name) or {}
            sender = SENDERS.get(name)
            if sender is None:
                continue
            try:
                detail = sender(channel, title, content)
                sent.append(name)
                logger.warning(f"【异常通知-{name}】已推送：{title}（{str(detail)[:120]}）")
            except Exception as error:
                # 单通道失败不影响其它通道，也不影响直播
                logger.warning(f"【异常通知-{name}】推送失败，已忽略：{error}")
        return sent
    except Exception as error:
        logger.warning(f"【异常通知】推送流程异常，已忽略：{error}")
        return []
