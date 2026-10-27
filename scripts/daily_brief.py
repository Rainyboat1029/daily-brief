#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每日简报：天气 + 新闻 -> DeepSeek 摘要 -> 推送到手机。

只用标准库，无第三方依赖。Python 3.9+。

环境变量（GitHub Actions 里用 Secrets 注入）：
    DEEPSEEK_API_KEY   必填，DeepSeek 平台 API key
    CITY               城市中文名，多个用逗号分隔，如 苏州,成都（也可用 --city）
    PUSH_CHANNEL       pushplus | feishu | email | none（默认 none，只打印）
    DEEPSEEK_MODEL     默认 deepseek-chat
    NEWS_LIMIT         送进模型的新闻条数上限，默认 24
    PUSHPLUS_TOKEN     PUSH_CHANNEL=pushplus 时必填
    FEISHU_WEBHOOK     PUSH_CHANNEL=feishu 时必填
    FEISHU_SECRET      飞书机器人开了"签名校验"时填
    SMTP_HOST/SMTP_PORT/SMTP_USER/SMTP_PASS/MAIL_TO  PUSH_CHANNEL=email 时必填

用法：
    python3 scripts/daily_brief.py --dry-run
    python3 scripts/daily_brief.py --city 杭州 --channel pushplus
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import html
import html.entities
import json
import os
import re
import smtplib
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate

CST = timezone(timedelta(hours=8))
UA = "daily-brief/1.0 (+https://github.com)"

# WMO weather code -> 中文
WMO = {
    0: "晴", 1: "大致晴朗", 2: "多云", 3: "阴",
    45: "有雾", 48: "雾凇",
    51: "小毛毛雨", 53: "毛毛雨", 55: "大毛毛雨",
    56: "冻毛毛雨", 57: "强冻毛毛雨",
    61: "小雨", 63: "中雨", 65: "大雨",
    66: "冻雨", 67: "强冻雨",
    71: "小雪", 73: "中雪", 75: "大雪", 77: "米雪",
    80: "阵雨", 81: "强阵雨", 82: "暴雨",
    85: "阵雪", 86: "强阵雪",
    95: "雷暴", 96: "雷暴伴冰雹", 99: "强雷暴伴冰雹",
}


def log(msg: str) -> None:
    print(f"[{datetime.now(CST).strftime('%H:%M:%S')}] {msg}", flush=True)


def _decompress(raw: bytes, enc: str) -> bytes:
    """有些服务器 deflate 用 zlib 包装，有些用 raw，按魔数兜底。"""
    if raw[:2] == b"\x1f\x8b" or enc == "gzip":
        import gzip
        try:
            return gzip.decompress(raw)
        except Exception:
            pass
    if raw[:1] == b"\x78" or enc == "deflate":
        import zlib
        for wbits in (zlib.MAX_WBITS, -zlib.MAX_WBITS, zlib.MAX_WBITS | 32):
            try:
                return zlib.decompress(raw, wbits)
            except Exception:
                continue
    return raw


def http_get(url: str, timeout: int = 20) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip, deflate"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        enc = (r.headers.get("Content-Encoding") or "").lower()
    return _decompress(raw, enc)


def http_post_json(url: str, payload: dict, timeout: int = 60, headers: dict = None) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    hdrs = {"Content-Type": "application/json", "User-Agent": UA}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, data=data, headers=hdrs, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:400]
        raise RuntimeError(f"HTTP {e.code}: {detail}") from None


# ---------------------------------------------------------------- weather
def geocode(city: str) -> dict:
    q = urllib.parse.quote(city)
    url = (
        "https://geocoding-api.open-meteo.com/v1/search"
        f"?name={q}&count=1&language=zh&format=json"
    )
    data = json.loads(http_get(url).decode("utf-8", "replace"))
    results = data.get("results") or []
    if not results:
        raise RuntimeError(f"找不到城市：{city}")
    r = results[0]
    return {
        "name": r.get("name") or city,
        "admin": r.get("admin1") or "",
        "lat": r["latitude"],
        "lon": r["longitude"],
    }


def fetch_weather(city: str) -> dict:
    g = geocode(city)
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={g['lat']}&longitude={g['lon']}"
        "&current=temperature_2m,apparent_temperature,relative_humidity_2m,"
        "precipitation,weather_code,wind_speed_10m"
        "&daily=weather_code,temperature_2m_max,temperature_2m_min,"
        "precipitation_probability_max,sunrise,sunset"
        "&timezone=auto&forecast_days=2"
    )
    d = json.loads(http_get(url).decode("utf-8", "replace"))
    cur = d.get("current") or {}
    daily = d.get("daily") or {}
    days = []
    for i, date in enumerate(daily.get("time") or []):
        days.append({
            "date": date,
            "code": _at(daily.get("weather_code"), i),
            "tmax": _at(daily.get("temperature_2m_max"), i),
            "tmin": _at(daily.get("temperature_2m_min"), i),
            "rain": _at(daily.get("precipitation_probability_max"), i),
            "sunrise": _at(daily.get("sunrise"), i),
            "sunset": _at(daily.get("sunset"), i),
        })
    return {
        "city": g["name"],
        "admin": g["admin"],
        "current": {
            "temp": cur.get("temperature_2m"),
            "feels": cur.get("apparent_temperature"),
            "humidity": cur.get("relative_humidity_2m"),
            "rain": cur.get("precipitation"),
            "code": cur.get("weather_code"),
            "wind": cur.get("wind_speed_10m"),
        },
        "days": days,
    }


def _at(seq, i):
    try:
        return seq[i]
    except Exception:
        return None


def describe(code) -> str:
    try:
        return WMO.get(int(code), f"未知({code})")
    except Exception:
        return "未知"


def weather_text(w: dict) -> str:
    c, d0 = w["current"], (w["days"][0] if w["days"] else {})
    lines = [
        f"{w['city']}{('·' + w['admin']) if w['admin'] and w['admin'] != w['city'] else ''}",
        f"当前 {c['temp']}°C（体感 {c['feels']}°C），{describe(c['code'])}，"
        f"湿度 {c['humidity']}%，风速 {c['wind']} km/h",
    ]
    if d0:
        lines.append(
            f"今日 {d0['tmin']}~{d0['tmax']}°C，{describe(d0['code'])}，"
            f"降水概率 {d0['rain']}%"
            + (f"，日出 {str(d0['sunrise'])[-5:]} / 日落 {str(d0['sunset'])[-5:]}" if d0.get("sunrise") else "")
        )
    if len(w["days"]) > 1:
        d1 = w["days"][1]
        lines.append(f"明日 {d1['tmin']}~{d1['tmax']}°C，{describe(d1['code'])}，降水概率 {d1['rain']}%")
    return "\n".join(lines)


# ---------------------------------------------------------------- news
def strip_html(s: str, limit: int = 260) -> str:
    s = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", s or "", flags=re.S | re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:limit]


def parse_date(raw: str):
    if not raw:
        return None
    raw = raw.strip()
    fmts = [
        "%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S GMT",
        "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ]
    for f in fmts:
        try:
            dt = datetime.strptime(raw, f)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=CST)
            return dt.astimezone(CST)
        except ValueError:
            continue
    return None


_ILLEGAL_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_NAMED_ENTITY = re.compile(r"&([a-zA-Z][a-zA-Z0-9]{1,7});")
_BARE_AMP = re.compile(r"&(?!(?:[a-zA-Z][a-zA-Z0-9]{1,7}|#\d{1,7}|#x[0-9a-fA-F]{1,6});)")


def sanitize_xml(raw: bytes) -> bytes:
    """很多中文 RSS 不是合法 XML：裸 & 、HTML 实体、控制字符。先修再解析。"""
    s = raw.decode("utf-8", "replace")
    s = _ILLEGAL_XML.sub("", s)

    def fix_entity(m):
        name = m.group(1) + ";"
        if name in ("amp;", "lt;", "gt;", "quot;", "apos;"):
            return m.group(0)
        val = html.entities.html5.get(name) if hasattr(html, "entities") else None
        if val:
            return "".join(f"&#{ord(c)};" for c in val)
        return "&amp;" + m.group(1) + ";"

    s = _NAMED_ENTITY.sub(fix_entity, s)
    s = _BARE_AMP.sub("&amp;", s)
    return s.encode("utf-8")


def window_start(anchor: str = "07:00") -> datetime:
    """简报取稿窗口的起点（北京时间）。

    语义：覆盖「昨天 07:00 → 今天 07:07」。做法是取最近一个 anchor 时刻，再往前退 24 小时：
    07:07 运行时，最近的 07:00 是今天，退 24 小时即昨天 07:00，于是窗口刚好是昨天一整天。
    若在 anchor 之前手动运行（如 06:30），最近的锚点是昨天 07:00，窗口为前天 07:00 起。
    若在 07:00 之后手动运行（如 20:00），窗口为昨天 07:00 起，自动变长，不会漏稿。
    """
    try:
        hh, mm = [int(x) for x in (anchor or "07:00").split(":")[:2]]
    except ValueError:
        hh, mm = 7, 0
    now = datetime.now(CST)
    mark = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if mark > now:
        mark -= timedelta(days=1)
    return mark - timedelta(days=1)


def parse_feed(xml_bytes: bytes, source: str, lookback_hours: int,
               since: datetime | None = None) -> list:
    out = []
    try:
        root = ET.fromstring(sanitize_xml(xml_bytes))
    except ET.ParseError as e:
        log(f"  解析失败 {source}: {e}")
        return out

    cutoff = since or (datetime.now(CST) - timedelta(hours=lookback_hours))

    # RSS 2.0 / RDF
    items = root.findall(".//item")
    atom = False
    if not items:
        items = root.findall(".//{http://www.w3.org/2005/Atom}entry")
        atom = True

    for it in items:
        if atom:
            def t(tag):
                el = it.find(f"{{http://www.w3.org/2005/Atom}}{tag}")
                return (el.text or "") if el is not None else ""
            link_el = it.find("{http://www.w3.org/2005/Atom}link")
            link = link_el.get("href") if link_el is not None else ""
            title, desc, raw_date = t("title"), t("summary") or t("content"), t("published") or t("updated")
        else:
            def t(tag):
                el = it.find(tag)
                return (el.text or "") if el is not None else ""
            link = t("link") or t("guid")
            title, desc = t("title"), t("description")
            raw_date = t("pubDate") or t("date")

        dt = parse_date(raw_date)
        if dt and dt < cutoff:
            continue
        title = strip_html(title, 120)
        if not title:
            continue
        out.append({
            "source": source,
            "title": title,
            "link": (link or "").strip(),
            "summary": strip_html(desc, 220),
            "time": dt.strftime("%m-%d %H:%M") if dt else "",
        })
    return out


def parse_hypergryph(raw: bytes, source: str, lookback_hours: int = 48) -> list:
    """鹰角官网 /news 页把公告列表以转义 JSON 内嵌在 HTML 里，按 cid 切块解析。

    产出字段：title / summary(brief) / published(含时区的时间戳)。该页只有《明日方舟》国服公告。
    """
    text = raw.decode("utf-8", "replace")
    out, seen = [], set()
    for chunk in text.split('{\\"cid\\":\\"')[1:]:
        m_title = re.search(r'\\"title\\":\\"(.*?)\\",\\"author\\"', chunk, re.S)
        m_ts = re.search(r'\\"displayTime\\":(\d+)', chunk)
        if not m_title or not m_ts:
            continue
        m_brief = re.search(r'\\"brief\\":\\"(.*?)\\"\\}', chunk, re.S)
        title = _unescape_js(m_title.group(1))
        brief = _unescape_js(m_brief.group(1)) if m_brief else ""
        if not title or title in seen:
            continue
        seen.add(title)
        dt = datetime.fromtimestamp(int(m_ts.group(1)), CST)
        out.append({
            "source": source,
            "title": title,
            "link": "https://ak.hypergryph.com/news",
            "summary": brief,
            "published": dt.strftime("%Y-%m-%d %H:%M"),
            "_dt": dt,
        })
    out.sort(key=lambda x: x["_dt"], reverse=True)
    if lookback_hours and lookback_hours > 0:
        cutoff = datetime.now(CST) - timedelta(hours=lookback_hours)
        out = [x for x in out if x["_dt"] >= cutoff]
    return out


def _unescape_js(t: str) -> str:
    return (t.replace('\\\\"', '"').replace("\\\\n", " ").replace("\\\\/", "/")
             .replace("\\\\t", " ").strip())


NAV_NOISE = ("国务院部门网站", "地方政府网站", "驻港澳机构网站", "中国政府网微博", "网站地图",
              "关于我们", "联系我们", "版权声明", "京ICP备")


def parse_html_list(raw: bytes, source: str, base: str, pattern: str) -> list:
    """从新闻站首页/列表页的 <a> 里抠标题，用于那些已经停掉 RSS 的官方站。"""
    text = raw.decode("utf-8", "replace")
    out, seen = [], set()
    for href, inner in re.findall(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', text, re.S):
        if pattern and pattern not in href:
            continue
        if not re.search(r"content_\d+\.htm", href):
            continue
        title = strip_html(inner, 120)
        if len(title) < 8 or any(w in title for w in NAV_NOISE):
            continue
        key = title[:24]
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "source": source,
            "title": title,
            "link": urllib.parse.urljoin(base, href),
            "summary": "",
            "published": "",
        })
    return out


def fetch_news(feeds: list, lookback_hours: int, limit: int,
               since: datetime | None = None) -> list:
    collected = []
    for f in feeds:
        name = f.get("name") or f.get("url", "?")
        url = f.get("url")
        if not url:
            continue
        topic = f.get("topic", "")
        try:
            raw = http_get(url)
            kind = f.get("type", "rss")
            if kind == "html_list":
                items = parse_html_list(raw, name, f.get("base", url), f.get("pattern", ""))
            elif kind == "hypergryph_news":
                items = parse_hypergryph(raw, name, int(f.get("lookback_hours", 48)))
            else:
                items = parse_feed(raw, name, lookback_hours, since)
            for it in items:
                it["topic"] = topic
                it["weight"] = f.get("weight", 1)
            drop = [k.lower() for k in f.get("exclude") or []]
            if drop:
                before = len(items)
                items = [i for i in items
                         if not any(k in (i["title"] + i.get("summary", "")).lower() for k in drop)]
                if before != len(items):
                    log(f"  {name}: 排除 {before - len(items)} 条（{f.get('exclude_label', '黑名单')}）")
            only = [k.lower() for k in f.get("only") or []]
            if only:
                before = len(items)
                items = [i for i in items
                         if any(k in (i["title"] + i.get("summary", "")).lower() for k in only)]
                log(f"  {name}: {len(items)} 条（限定 {f.get('only_label', '关键词')} 筛选，原 {before} 条）")
            else:
                log(f"  {name}: {len(items)} 条")
            collected.extend(items[: f.get("max", 8)])
        except Exception as e:
            log(f"  抓取失败 {name}: {e}")
    # 去重 + 交错来源（避免同一家刷屏）
    seen, by_src = set(), {}
    for it in collected:
        key = it["title"][:40]
        if key in seen:
            continue
        seen.add(key)
        by_src.setdefault(it["source"], []).append(it)
    merged, idx = [], 0
    while any(len(v) > idx for v in by_src.values()):
        for v in by_src.values():
            if len(v) > idx:
                merged.append(v[idx])
        idx += 1
    return merged[:limit]


# ---------------------------------------------------------------- LLM
def call_deepseek(prompt: str, system: str, api_key: str, model: str) -> str:
    url = "https://api.deepseek.com/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.6,
        "max_tokens": 1500,
        "stream": False,
    }
    resp = http_post_json(url, payload, headers={"Authorization": f"Bearer {api_key}"})
    return (resp.get("choices") or [{}])[0].get("message", {}).get("content", "").strip()


def build_prompt(weather_block: str, news_items: list, today: str) -> str:
    news_block = "\n".join(
        f"{i + 1}. [{n['source']}] {n['title']}"
        + (f"：{n['summary'][:110]}" if n.get("summary") else "")
        for i, n in enumerate(news_items)
    )
    return f"""今天是 {today}。

【天气数据】
{weather_block}

【候选新闻（原始标题+摘要，未整理，含大量噪音）】
{news_block}

请按以下要求写一份中文每日简报。

一、选稿
- 主体是国内要闻：中央和国务院的政策部署、重要会议、经济数据与产业政策、重大工程与科技突破、教育医疗住房等民生政策、重要立法与反腐动向。
- 国际部分只保留重大事件：战争与冲突的局势变化、大国政坛重大变故、冲击全球市场或供应链的事件。一般性国外社会新闻、名人动态不要。
- 科技部分只看两家公司：小米（含 Redmi、澎湃 OS、小米汽车）和苹果（含 iPhone、iPad、Mac、Apple Intelligence）。其他科技公司、泛科技趣闻、评测导购一律不要。
- 明日方舟部分只看国服《明日方舟》：新活动上线、联动、版本更新、限定寻访、制作组通讯、系统优化公告。严禁写《明日方舟：终末地》（另一款游戏），严禁写日服、国际服、繁中服、韩服的独立公告；与明日方舟无关的其他游戏新闻一律不要。
- 一律丢弃：社会琐事（落水/救人/走失/纠纷/车祸/暖新闻）、明星八卦、体育赛果、地方小事、标题党、营销稿。

二、写法（标准新闻体，官方、客观、克制）
- 每条格式：序号 + 一句标题（句号结尾），紧接一句概括，讲清发生了什么、涉及谁、关键数字或结论。
- 全篇不许出现破折号「——」；不许用「据悉」「小编」「我们」这类口吻；不用感叹号、不用网络流行语。
- 不评价、不煽情、不预测、不加评论性形容词。
- 只用候选新闻里的事实，不许补充背景、不许编造数字、引语、日期；拿不准的不写。
- 概括必须是完整句子，不要写「详见原文」「点击查看」这类话。

三、数量
- 【国内要闻】6-8 条
- 【国际】2-4 条（确实没有够格的重大事件，就写「今日无重大国际事件」一行）
- 【科技·小米与苹果】2-4 条（一条都挑不出来就整节省略）
- 【明日方舟】0-3 条（当天没有国服新公告就整节省略，绝不硬凑）
- 每条不超过 70 字，全篇不超过 900 字。

四、输出格式（纯文本，绝对不要 Markdown 符号，不要 #、*、>、反引号）

【今日天气】
（每个城市 2 句：当前温度与体感、今日温度区间与降水、要不要带伞加衣；只用上面数据，不要编造）

【国内要闻】
1. ……
2. ……

【国际】
1. ……

【科技·小米与苹果】
1. ……

【明日方舟】
1. ……（写清活动/寻访名称、起止时间、是否有新干员或联动）

【今天一句】
（一句克制的提醒，20 字以内，不加感叹号）

全程中文。"""


SYSTEM = (
    "你是通讯社的资深中文编辑，文风接近新华社、人民日报的简讯：客观、准确、克制、信息密度高。"
    "你只基于给定事实写作，绝不编造数字、人名、日期和引语；宁可少写也不凑数。"
    "你熟悉中国官方新闻的表达规范，写出来的句子可以直接用于对外发布。"
)


def fallback_digest(weather_block: str, news_items: list, today: str) -> str:
    lines = ["【今日天气】", weather_block, "", "【国内要闻】"]
    for i, n in enumerate(news_items[:10], 1):
        title = n["title"].replace("——", "，").strip()
        lines.append(f"{i}. {title}")
    lines += ["", "【今天一句】", "（模型调用失败，以上为原始标题，未经筛选）"]
    return "\n".join(lines)


# ---------------------------------------------------------------- push
def push_pushplus(title: str, content: str, token: str) -> None:
    resp = http_post_json(
        "https://www.pushplus.plus/send",
        {"token": token, "title": title, "content": content, "template": "txt"},
    )
    if resp.get("code") != 200:
        raise RuntimeError(f"pushplus 返回异常: {resp}")
    log("  pushplus 推送成功")


def push_feishu(title: str, content: str, webhook: str, secret: str = "") -> None:
    payload = {"msg_type": "text", "content": {"text": f"{title}\n\n{content}"}}
    url = webhook
    if secret:
        ts = str(int(time.time()))
        sign_str = f"{ts}\n{secret}".encode("utf-8")
        sign = base64.b64encode(
            hmac.new(sign_str, b"", digestmod=hashlib.sha256).digest()
        ).decode()
        url = f"{webhook}&timestamp={ts}&sign={urllib.parse.quote_plus(sign)}"
    resp = http_post_json(url, payload)
    code = resp.get("code", resp.get("StatusCode", -1))
    if code not in (0, "0"):
        raise RuntimeError(f"飞书返回异常: {resp}")
    log("  飞书推送成功")


def render_html(content: str, title: str) -> str:
    """把纯文本简报转成手机上排版好看的 HTML（只用内联样式，兼容 QQ/Gmail/Outlook）。"""
    parts = []
    for raw in content.splitlines():
        line = raw.rstrip()
        if not line or set(line) <= set("=- "):
            continue
        esc = html.escape(line)
        if line.startswith("【") and line.endswith("】"):
            parts.append(
                f'<h3 style="margin:22px 0 10px;font-size:16px;'
                f'color:#111;border-bottom:2px solid #f0f0f0;padding-bottom:6px;">{esc}</h3>'
            )
        elif line.startswith("·") or line.startswith("- "):
            parts.append(
                f'<div style="margin:8px 0;padding-left:12px;border-left:3px solid #e5e7eb;'
                f'line-height:1.75;">{html.escape(line.lstrip("·- ").strip())}</div>'
            )
        else:
            parts.append(f'<p style="margin:6px 0;line-height:1.75;">{esc}</p>')
    body = "\n".join(parts)
    return (
        '<div style="max-width:640px;margin:0 auto;padding:12px 18px;'
        "font-family:-apple-system,BlinkMacSystemFont,'PingFang SC','Microsoft YaHei',sans-serif;"
        'font-size:15px;color:#222;">'
        f"{body}"
        '<p style="margin-top:26px;padding-top:12px;border-top:1px solid #eee;'
        'font-size:12px;color:#999;">由 DSH 每日简报自动生成 · 天气数据 Open-Meteo · '
        "新闻源见仓库 README</p></div>"
    )


def push_email(title: str, content: str) -> None:
    """SMTP 发信：465 走 SSL，587 走 STARTTLS；同时发纯文本和 HTML 两个版本。"""
    host = (os.environ.get("SMTP_HOST", "").strip() or "smtp.qq.com")
    port = int(os.environ.get("SMTP_PORT", "").strip() or "465")
    user = os.environ.get("SMTP_USER", "").strip()
    pwd = os.environ.get("SMTP_PASS", "").strip().replace(" ", "")
    to = os.environ.get("MAIL_TO", "").strip() or user
    if not user or not pwd:
        raise RuntimeError("缺 SMTP_USER 或 SMTP_PASS（QQ 邮箱要用 16 位授权码，不是登录密码）")
    rcpts = [x.strip() for x in re.split(r"[,;，；]", to) if x.strip()]
    if not rcpts:
        raise RuntimeError("MAIL_TO 为空，没有收件人")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = Header(title, "utf-8")
    msg["From"] = user
    msg["To"] = ", ".join(rcpts)
    msg["Date"] = formatdate(localtime=True)
    msg.attach(MIMEText(content, "plain", "utf-8"))
    msg.attach(MIMEText(render_html(content, title), "html", "utf-8"))

    try:
        if port == 465:
            server = smtplib.SMTP_SSL(host, port, timeout=30)
        else:
            server = smtplib.SMTP(host, port, timeout=30)
        with server:
            if port != 465:
                server.starttls()
            try:
                server.login(user, pwd)
            except smtplib.SMTPAuthenticationError as e:
                raise RuntimeError(
                    f"SMTP 登录被拒（535）：SMTP_PASS 多半填成了登录密码，要用邮箱设置里生成的授权码。{e}"
                ) from None
            server.sendmail(user, rcpts, msg.as_string())
    except smtplib.SMTPAuthenticationError:
        raise
    except OSError as e:
        raise RuntimeError(f"连不上 {host}:{port}：{e}") from None
    log(f"  邮件已发出 → {', '.join(rcpts)}")


def push(channel: str, title: str, content: str) -> bool:
    try:
        if channel == "pushplus":
            push_pushplus(title, content, os.environ["PUSHPLUS_TOKEN"])
        elif channel == "feishu":
            push_feishu(title, content, os.environ["FEISHU_WEBHOOK"], os.environ.get("FEISHU_SECRET", ""))
        elif channel == "email":
            push_email(title, content)
        else:
            log("  PUSH_CHANNEL=none，仅打印不推送")
            return True
        return True
    except Exception as e:
        log(f"  推送失败: {e}")
        return False


# ---------------------------------------------------------------- filter
DEFAULT_JUNK = (
    "不慎落水,下水救人,跳水救人,救人,拾金不昧,见义勇为,走失,失联,找回,捡到,"
    "坠楼,酒驾,车祸,醉驾,打架,纠纷,暖心,感动,点赞,致敬,萌娃,宠物,相亲,彩礼"
)


def drop_junk(items: list, keywords: str) -> tuple:
    """在送进模型前先掐掉明显的暖新闻/社会琐事，省 token 也少干扰。"""
    words = [w.strip() for w in re.split(r"[,，、;；]+", keywords or "") if w.strip()]
    if not words:
        return items, 0
    kept = [it for it in items if not any(w in it["title"] or w in it.get("summary", "") for w in words)]
    return kept, len(items) - len(kept)


# ---------------------------------------------------------------- main
def load_feeds() -> list:
    here = os.path.dirname(os.path.abspath(__file__))
    cfg = os.path.join(os.path.dirname(here), "config", "feeds.json")
    try:
        with open(cfg, "r", encoding="utf-8") as f:
            return json.load(f).get("feeds", [])
    except Exception as e:
        log(f"读取 {cfg} 失败: {e}，使用内置源")
        return [
            {"name": "澎湃", "url": "https://feedx.net/rss/thepaper.xml", "max": 8},
            {"name": "36氪", "url": "https://36kr.com/feed", "max": 8},
        ]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--city", default=os.environ.get("CITY", ""))
    ap.add_argument("--channel", default=(os.environ.get("PUSH_CHANNEL", "").strip() or "none"))
    ap.add_argument("--dry-run", action="store_true", help="只打印，不推送")
    ap.add_argument("--lookback-hours", type=int, default=int(os.environ.get("LOOKBACK_HOURS", "30")),
                    help="未指定 --window-start 时，按「now - N 小时」取稿")
    ap.add_argument("--window-start", default=os.environ.get("WINDOW_START", "07:00"),
                    help="取稿窗口锚点（北京时间 HH:MM），窗口=昨天该时刻→现在；留空则回退到 --lookback-hours")
    ap.add_argument("--limit", type=int, default=int(os.environ.get("NEWS_LIMIT", "24")))
    ap.add_argument("--filter-keywords", default=os.environ.get("FILTER_KEYWORDS", DEFAULT_JUNK), help="逗号分隔的过滤词")
    ap.add_argument("--out", default="", help="把生成的简报写到文件")
    args = ap.parse_args()

    now = datetime.now(CST)
    weekday = "一二三四五六日"[now.weekday()]
    today = f"{now.year}年{now.month}月{now.day}日 星期{weekday}"
    title = f"每日简报 · {now.month}月{now.day}日"

    # 1. weather（支持多城市：CITY="苏州,成都"）
    cities = [c for c in re.split(r"[,，、;；\s]+", args.city or "") if c]
    blocks, failed = [], 0
    if not cities:
        log("未配置 CITY，跳过天气")
        failed += 1
    for c in cities:
        try:
            log(f"查天气：{c}")
            blocks.append(weather_text(fetch_weather(c)))
        except Exception as e:
            failed += 1
            log(f"  {c} 天气失败: {e}")
    weather_block = "\n\n".join(blocks)
    weather_failed = not blocks

    # 2. news
    since = window_start(args.window_start) if args.window_start else None
    if since:
        log(f"取稿窗口：{since.strftime('%m-%d %H:%M')} → {now.strftime('%m-%d %H:%M')}（北京时间）")
    log("抓新闻")
    news_items = fetch_news(load_feeds(), args.lookback_hours, args.limit, since)
    log(f"  共 {len(news_items)} 条")
    news_items, dropped = drop_junk(news_items, args.filter_keywords)
    if dropped:
        log(f"  过滤掉 {dropped} 条社会琐事")

    if weather_failed and not news_items:
        log("天气和新闻都失败，放弃")
        return 1

    # 3. LLM
    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    model = os.environ.get("DEEPSEEK_MODEL", "").strip() or "deepseek-chat"
    body = ""
    if api_key and news_items:
        try:
            log(f"调用 DeepSeek（{model or '<空>'}）")
            body = call_deepseek(build_prompt(weather_block or "（无天气数据）", news_items, today), SYSTEM, api_key, model)
            log(f"  生成 {len(body)} 字")
        except Exception as e:
            log(f"  DeepSeek 失败: {e}")
    if not body:
        body = fallback_digest(weather_block or "（无天气数据）", news_items, today)

    text = f"{title}\n\n{body}"
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        log(f"已写入 {args.out}")

    if args.dry_run:
        print("\n" + "=" * 50 + "\n" + text + "\n" + "=" * 50)
        return 0

    log(f"推送（{args.channel}）")
    return 0 if push(args.channel, title, body) else 1


if __name__ == "__main__":
    sys.exit(main())
