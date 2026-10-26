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
from email.mime.text import MIMEText

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
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


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


def parse_feed(xml_bytes: bytes, source: str, lookback_hours: int) -> list:
    out = []
    try:
        root = ET.fromstring(sanitize_xml(xml_bytes))
    except ET.ParseError as e:
        log(f"  解析失败 {source}: {e}")
        return out

    cutoff = datetime.now(CST) - timedelta(hours=lookback_hours)

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


def fetch_news(feeds: list, lookback_hours: int, limit: int) -> list:
    collected = []
    for f in feeds:
        name = f.get("name") or f.get("url", "?")
        url = f.get("url")
        if not url:
            continue
        try:
            items = parse_feed(http_get(url), name, lookback_hours)
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
        + (f" —— {n['summary']}" if n["summary"] else "")
        for i, n in enumerate(news_items)
    )
    return f"""今天是 {today}。

【天气数据】（可能包含多个城市）
{weather_block}

【过去 24 小时新闻（原始标题+摘要，未整理，含大量噪音）】
{news_block}

请写一份中文每日简报，取舍标准参照《人民日报》"新闻早班车"：优先国家政策、经济数据、科技突破、国际局势、教育医疗住房等公共议题；坚决丢弃社会琐事（个别人员落水/救人/走失/纠纷/交通事故/暖新闻）、明星八卦、体育赛果、地方小事、标题党。

格式严格如下，纯文本，绝对不要 Markdown 符号（不要 #、*、-、>）：

【今日天气】
（每个城市 2 句：温度区间、体感、要不要带伞/加衣；只用上面数据里的数字，不要编造）

【要闻】
（4-6 条，每条一行，格式「· 短句事实 —— 一句为什么重要」；按重要性排序，第一条最重要。宁少勿滥：宁可只留 4 条都是硬新闻，也不要为了凑数塞进琐事）

【今天一句】
（一句口语化的提醒，20 字以内）

总字数 450 字以内，全程中文。"""


SYSTEM = ("你是一个靠谱的私人简报编辑，说话简洁、不废话、不编造数据，只基于用户提供的事实写。你在做事实筛选时像党报早班车编辑一样严格：硬新闻优先，社会琐事一律不要。")


def fallback_digest(weather_block: str, news_items: list, today: str) -> str:
    lines = [f"【今日天气】", weather_block, "", "【值得知道】"]
    for n in news_items[:6]:
        lines.append(f"· {n['title']}" + (f" —— {n['summary'][:60]}" if n["summary"] else ""))
    lines += ["", "【今天一句】", "（模型调用失败，这是原始摘要）"]
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


def push_email(title: str, content: str) -> None:
    host = os.environ["SMTP_HOST"]
    port = int(os.environ.get("SMTP_PORT", "465"))
    user = os.environ["SMTP_USER"]
    pwd = os.environ["SMTP_PASS"]
    to = os.environ.get("MAIL_TO", user)
    msg = MIMEText(content, "plain", "utf-8")
    msg["Subject"] = Header(title, "utf-8")
    msg["From"] = user
    msg["To"] = to
    with smtplib.SMTP_SSL(host, port, timeout=30) as s:
        s.login(user, pwd)
        s.sendmail(user, [x.strip() for x in to.split(",")], msg.as_string())
    log("  邮件推送成功")


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
    ap.add_argument("--channel", default=os.environ.get("PUSH_CHANNEL", "none"))
    ap.add_argument("--dry-run", action="store_true", help="只打印，不推送")
    ap.add_argument("--lookback-hours", type=int, default=int(os.environ.get("LOOKBACK_HOURS", "30")))
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
    log("抓新闻")
    news_items = fetch_news(load_feeds(), args.lookback_hours, args.limit)
    log(f"  共 {len(news_items)} 条")
    news_items, dropped = drop_junk(news_items, args.filter_keywords)
    if dropped:
        log(f"  过滤掉 {dropped} 条社会琐事")

    if weather_failed and not news_items:
        log("天气和新闻都失败，放弃")
        return 1

    # 3. LLM
    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    model = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
    body = ""
    if api_key and news_items:
        try:
            log(f"调用 DeepSeek（{model}）")
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
