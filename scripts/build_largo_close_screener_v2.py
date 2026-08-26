from __future__ import annotations

import argparse
import html
import json
import math
import os
import re
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Iterable

BASE_URL = "https://stock.naver.com"
KST = timezone(timedelta(hours=9))
USER_AGENT = "Mozilla/5.0 (compatible; LargoCloseScreener/2.0; personal read-only research)"

RANKING_PATH = "/api/domestic/market/stock/default"
DETAIL_PATH = "/api/domestic/detail/{code}/{kind}"
CHART_PATH = "/api/securityService/chart/domestic/item/{code}"
NEWS_PATH = "/api/domestic/detail/news"
NOTICE_PATH = "/api/domestic/detail/notice"
CATEGORY_PATH = "/api/stockSecurity/rankings/v2/domestic/themes"

POSITIVE_DIRECT = (
    "공급계약", "수주", "계약 체결", "영업이익 증가", "흑자전환", "실적 개선",
    "정책", "허가", "승인", "자사주 취득", "신사업", "인수", "합병",
)
NEGATIVE = (
    "유상증자", "전환사채", "신주인수권", "거래정지", "불성실공시", "상장폐지",
    "횡령", "배임", "적자전환", "영업손실", "감사의견", "관리종목",
)

ALIASES = {
    "code": ("itemCode", "stockCode", "code", "symbolCode", "reutersCode", "itemcode"),
    "name": ("stockName", "itemName", "name", "korName", "stockname"),
    "price": ("closePrice", "currentPrice", "nowPrice", "price", "close", "lastPrice"),
    "open": ("openPrice", "open", "openingPrice"),
    "high": ("highPrice", "high", "highestPrice"),
    "low": ("lowPrice", "low", "lowestPrice"),
    "volume": ("accumulatedTradingVolume", "accTradeVolume", "tradingVolume", "volume", "accumulatedVolume"),
    "trade_value": ("accumulatedTradingValue", "accTradePrice", "tradingValue", "tradeValue", "accAmount", "accumulatedTradingAmount"),
    "change_rate": ("fluctuationsRatio", "changeRate", "compareToPreviousClosePriceRate", "rate", "changeRatio"),
    "date": ("localTradedAt", "businessDate", "bizDate", "date", "tradeDate", "x"),
    "time": ("localTradedAt", "tradeTime", "time", "dateTime", "updatedAt"),
    "market": ("marketType", "market", "stockExchangeType"),
    "industry": ("industryName", "sectorName", "themeName", "upjongName", "industry", "sector"),
    "rank": ("rank", "ranking", "order", "rankNo"),
    "title": ("title", "headline", "articleTitle", "newsTitle", "subject"),
}


def unwrap(value: Any) -> Any:
    if isinstance(value, dict):
        for key in ("value", "rawValue", "number", "amount", "price", "text"):
            if key in value and not isinstance(value[key], (dict, list)):
                return value[key]
    return value


def number(value: Any, default: float | None = None) -> float | None:
    value = unwrap(value)
    if value is None or isinstance(value, (dict, list, bool)):
        return default
    text = str(value).strip().replace(",", "").replace("%", "")
    if not text or text in {"-", "--", "N/A", "null"}:
        return default
    multiplier = 1.0
    units = (("조", 1e12), ("억", 1e8), ("만", 1e4), ("천", 1e3))
    for suffix, scale in units:
        if text.endswith(suffix):
            multiplier = scale
            text = text[: -len(suffix)]
            break
    text = re.sub(r"[^0-9+\-.]", "", text)
    try:
        return float(text) * multiplier
    except ValueError:
        return default


def text_value(value: Any) -> str:
    value = unwrap(value)
    return "" if value is None or isinstance(value, (dict, list)) else str(value).strip()


def lookup(obj: dict[str, Any], names: Iterable[str]) -> Any:
    lower = {str(key).casefold(): value for key, value in obj.items()}
    for name in names:
        if name in obj:
            return obj[name]
        if name.casefold() in lower:
            return lower[name.casefold()]
    return None


def walk_dicts(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_dicts(child)


def find_records(payload: Any, require: tuple[str, ...] = ("code",)) -> list[dict[str, Any]]:
    candidates: list[list[dict[str, Any]]] = []
    def visit(value: Any) -> None:
        if isinstance(value, list) and value and all(isinstance(item, dict) for item in value[:5]):
            candidates.append(value)
        if isinstance(value, dict):
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value[:5]:
                visit(child)
    visit(payload)
    scored = []
    for records in candidates:
        score = 0
        sample = records[: min(5, len(records))]
        for logical in require:
            if any(lookup(row, ALIASES[logical]) is not None for row in sample):
                score += 10
        score += min(len(records), 100) / 100
        scored.append((score, records))
    return max(scored, key=lambda item: item[0])[1] if scored else []


class NaverClient:
    def __init__(self, timeout: int = 25, delay: float = 0.35):
        self.timeout = timeout
        self.delay = delay
        self.errors: list[dict[str, Any]] = []
        self.requests = 0

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        if params:
            path += ("&" if "?" in path else "?") + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        req = urllib.request.Request(
            BASE_URL + path,
            headers={"Accept": "application/json,text/plain,*/*", "Referer": BASE_URL + "/", "User-Agent": USER_AGENT},
        )
        if self.requests:
            time.sleep(self.delay)
        self.requests += 1
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
            status = getattr(exc, "code", None)
            self.errors.append({"path": path, "status": status, "error": str(exc)[:500]})
            return None


def normalize_code(value: Any) -> str:
    raw = re.sub(r"\D", "", text_value(value))
    return raw[-6:] if len(raw) >= 6 else ""


def normalized_record(row: dict[str, Any], source: str, fallback_rank: int = 999) -> dict[str, Any]:
    return {
        "code": normalize_code(lookup(row, ALIASES["code"])),
        "name": text_value(lookup(row, ALIASES["name"])),
        "price": number(lookup(row, ALIASES["price"])),
        "open": number(lookup(row, ALIASES["open"])),
        "high": number(lookup(row, ALIASES["high"])),
        "low": number(lookup(row, ALIASES["low"])),
        "volume": number(lookup(row, ALIASES["volume"])),
        "trade_value": number(lookup(row, ALIASES["trade_value"])),
        "change_rate": number(lookup(row, ALIASES["change_rate"])),
        "industry": text_value(lookup(row, ALIASES["industry"])),
        "market": text_value(lookup(row, ALIASES["market"])),
        "rank": int(number(lookup(row, ALIASES["rank"]), fallback_rank) or fallback_rank),
        "source": source,
        "raw": row,
    }


def ranking(client: NaverClient, order_type: str, page_size: int = 100, alert_type: str | None = None) -> list[dict[str, Any]]:
    payload = client.get(RANKING_PATH, {
        "tradeType": "KRX", "marketType": "ALL", "orderType": order_type,
        "startIdx": 0, "pageSize": page_size, "alertType": alert_type,
    })
    rows = find_records(payload, ("code",))
    return [normalized_record(row, order_type, index + 1) for index, row in enumerate(rows) if normalize_code(lookup(row, ALIASES["code"]))]


def merge_candidates(lists: list[list[dict[str, Any]]], limit: int) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for rows in lists:
        for row in rows:
            code = row["code"]
            if not code:
                continue
            target = merged.setdefault(code, {**row, "sources": [], "ranks": {}})
            target["sources"].append(row["source"])
            target["ranks"][row["source"]] = row["rank"]
            for key in ("name", "price", "open", "high", "low", "volume", "trade_value", "change_rate", "industry", "market"):
                if not target.get(key) and row.get(key):
                    target[key] = row[key]
    def priority(row: dict[str, Any]) -> tuple[float, float, float]:
        price_rank = row["ranks"].get("priceTop", 999)
        up_rank = row["ranks"].get("up", 999)
        volume_rank = row["ranks"].get("upperQuantTop", 999)
        score = min(price_rank, 150) * 0.55 + min(up_rank, 150) * 0.25 + min(volume_rank, 150) * 0.20
        return score, -(row.get("trade_value") or 0), -(row.get("change_rate") or 0)
    return sorted(merged.values(), key=priority)[:limit]


def detail_price(client: NaverClient, code: str) -> dict[str, Any]:
    payload = client.get(DETAIL_PATH.format(code=code, kind="price"))
    if payload is None:
        return {}
    best: dict[str, Any] = {}
    best_score = -1
    for row in walk_dicts(payload):
        score = sum(1 for key in ("price", "open", "high", "low", "volume", "trade_value") if lookup(row, ALIASES[key]) is not None)
        if score > best_score:
            best, best_score = row, score
    result = normalized_record(best, "detail") if best else {}
    result["timestamp"] = text_value(lookup(best, ALIASES["time"])) if best else ""
    return result


def time_series(client: NaverClient, code: str, kind: str, page_size: int) -> list[dict[str, Any]]:
    payload = client.get(DETAIL_PATH.format(code=code, kind=kind), {"startIdx": 0, "pageSize": page_size} if kind == "siseTick" else {"pageSize": page_size})
    records = find_records(payload, ("price",))
    result = []
    for row in records:
        item = normalized_record(row, kind)
        item["date"] = text_value(lookup(row, ALIASES["date"]))
        item["time"] = text_value(lookup(row, ALIASES["time"]))
        if item["price"] is not None:
            result.append(item)
    return result


def title_records(payload: Any) -> list[dict[str, str]]:
    rows = []
    seen = set()
    for obj in walk_dicts(payload):
        title = text_value(lookup(obj, ALIASES["title"]))
        if not title or title in seen:
            continue
        seen.add(title)
        when = text_value(lookup(obj, ALIASES["date"])) or text_value(lookup(obj, ALIASES["time"]))
        rows.append({"title": title, "when": when})
    return rows[:30]


def news_and_notices(client: NaverClient, code: str) -> list[dict[str, str]]:
    news = client.get(NEWS_PATH, {"itemCode": code, "page": 1, "pageSize": 15})
    notice = client.get(NOTICE_PATH, {"itemCode": code, "startIdx": 0, "pageSize": 20})
    return title_records(news) + title_records(notice)


def hoga_summary(client: NaverClient, code: str) -> dict[str, Any]:
    payload = client.get(DETAIL_PATH.format(code=code, kind="hoga"))
    if payload is None:
        return {"available": False}
    values = []
    timestamps = []
    for obj in walk_dicts(payload):
        for key, value in obj.items():
            key_l = str(key).casefold()
            num = number(value)
            if num is not None and any(token in key_l for token in ("bid", "ask", "hoga", "price")):
                values.append(num)
            if any(token in key_l for token in ("time", "date", "at")):
                t = text_value(value)
                if t:
                    timestamps.append(t)
    distinct = sorted({value for value in values if value > 0})
    spread = None
    if len(distinct) >= 2:
        gaps = [b - a for a, b in zip(distinct, distinct[1:]) if b > a]
        spread = min(gaps) if gaps else None
    return {"available": True, "timestamp": timestamps[0] if timestamps else "", "price_count": len(distinct), "minimum_gap": spread}


def parse_clock(value: str) -> int | None:
    match = re.search(r"(?:T|\s|^)(\d{2}):?(\d{2})(?::?(\d{2}))?", value or "")
    if not match:
        return None
    return int(match.group(1)) * 3600 + int(match.group(2)) * 60 + int(match.group(3) or 0)


def sorted_ticks(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    enriched = [(parse_clock(row.get("time") or row.get("date") or ""), row) for row in rows]
    valid = [(stamp, row) for stamp, row in enriched if stamp is not None]
    return [row for _, row in sorted(valid, key=lambda item: item[0])] if valid else list(reversed(rows))


def daily_metrics(rows: list[dict[str, Any]], current: dict[str, Any]) -> dict[str, Any]:
    series = [row for row in rows if row.get("price") is not None]
    closes = [row["price"] for row in series]
    highs = [row.get("high") or row["price"] for row in series]
    lows = [row.get("low") or row["price"] for row in series]
    volumes = [row.get("volume") or 0 for row in series]
    current_price = current.get("price")
    current_volume = current.get("volume") or (volumes[0] if volumes else 0)
    historical = series[1:21] if len(series) > 1 else series[:20]
    hist_volumes = [row.get("volume") or 0 for row in historical if (row.get("volume") or 0) > 0]
    avg_volume = statistics.mean(hist_volumes) if hist_volumes else 0
    volume_ratio = current_volume / avg_volume if avg_volume else None
    ma5 = statistics.mean(closes[:5]) if len(closes) >= 5 else None
    ma20 = statistics.mean(closes[:20]) if len(closes) >= 20 else None
    prev_high20 = max(highs[1:21] or highs[:20], default=None)
    prev_high60 = max(highs[1:61] or highs, default=None)
    high_proximity = current_price / prev_high20 if current_price and prev_high20 else None
    reference = None
    reference_score = -1.0
    for row in historical:
        op, hi, lo, close, vol = row.get("open"), row.get("high"), row.get("low"), row.get("price"), row.get("volume") or 0
        if not all(value is not None for value in (op, hi, lo, close)) or hi <= lo:
            continue
        body = (close - op) / (hi - lo)
        vr = vol / avg_volume if avg_volume else 0
        score = max(0, body) * 0.55 + min(3, vr) / 3 * 0.45
        if close > op and body >= 0.35 and score > reference_score:
            reference, reference_score = row, score
    supports = []
    if reference:
        for key in ("price", "low"):
            value = reference.get(key)
            if value and current_price and value < current_price:
                supports.append((value, "기준봉 종가" if key == "price" else "기준봉 저점"))
    if ma5 and current_price and ma5 < current_price:
        supports.append((ma5, "5일선"))
    recent_lows = [value for value in lows[:10] if current_price and value < current_price]
    if recent_lows:
        supports.append((max(recent_lows), "최근 눌림 저점"))
    support = max(supports, default=(None, ""), key=lambda item: item[0] or 0)
    return {
        "ma5": ma5, "ma20": ma20, "volume_ratio": volume_ratio,
        "high20": prev_high20, "high60": prev_high60, "high_proximity": high_proximity,
        "reference": reference, "reference_score": reference_score if reference else None,
        "support": support[0], "support_name": support[1],
    }


def intraday_metrics(ticks: list[dict[str, Any]], current: dict[str, Any], support: float | None) -> dict[str, Any]:
    ticks = sorted_ticks(ticks)
    prices = [row["price"] for row in ticks if row.get("price") is not None]
    if not prices:
        return {"available": False}
    current_price = current.get("price") or prices[-1]
    last_count = min(len(prices), 80)
    late = prices[-last_count:]
    late_low, late_high = min(late), max(late)
    reclaim = current_price / late_high if late_high else None
    late_location = (current_price - late_low) / (late_high - late_low) if late_high > late_low else 1.0
    lows_second_half = prices[len(prices)//2:]
    afternoon_low = min(lows_second_half) if lows_second_half else late_low
    structure_hold = support is None or afternoon_low >= support * 0.992
    # A simple absorption proxy: pullback low held, then price recovered near the recent high.
    absorption_proxy = max(0.0, min(1.0, (late_location * 0.55 + (reclaim or 0) * 0.45)))
    return {
        "available": True, "late_low": late_low, "late_high": late_high,
        "reclaim_ratio": reclaim, "late_location": late_location,
        "afternoon_low": afternoon_low, "structure_hold": structure_hold,
        "absorption_proxy": absorption_proxy,
    }


def catalyst_metrics(items: list[dict[str, str]]) -> dict[str, Any]:
    titles = [item["title"] for item in items]
    positive = [title for title in titles if any(word in title for word in POSITIVE_DIRECT)]
    negative = [title for title in titles if any(word in title for word in NEGATIVE)]
    return {"positive": positive[:5], "negative": negative[:5], "strong": bool(positive), "adverse": bool(negative), "titles": titles[:8]}


def gate(status: str, value: Any, reason: str, role: str = "required") -> dict[str, Any]:
    return {"status": status, "value": value, "reason": reason, "role": role}


def evaluate(candidate: dict[str, Any], detail: dict[str, Any], daily: list[dict[str, Any]], ticks: list[dict[str, Any]], news: list[dict[str, str]], hoga: dict[str, Any], excluded: set[str], history: dict[str, Any], now: datetime) -> dict[str, Any]:
    data = dict(candidate)
    for key, value in detail.items():
        if value not in (None, "", 0) or not data.get(key):
            data[key] = value
    price, op, high, low = data.get("price"), data.get("open"), data.get("high"), data.get("low")
    trade_value = data.get("trade_value") or 0
    day = daily_metrics(daily, data)
    intra = intraday_metrics(ticks, data, day["support"])
    catalyst = catalyst_metrics(news)
    close_location = (price - low) / (high - low) if all(v is not None for v in (price, high, low)) and high > low else None
    upper_wick = (high - max(op, price)) / (high - low) if all(v is not None for v in (price, op, high, low)) and high > low else None
    giveback = (high - price) / (high - low) if close_location is not None else None
    stop = day["support"]
    stop_distance = (price - stop) / price if price and stop and stop < price else None
    price_rank = candidate.get("ranks", {}).get("priceTop", 999)
    industry = data.get("industry") or "미분류"
    same_group = history.get("current_groups", {}).get(industry, [])
    group_rank = 1 + sum(1 for other in same_group if (other.get("trade_value") or 0) > trade_value) if industry != "미분류" else None
    rank_history = [row for row in history.get("snapshots", []) if row.get("date") == now.date().isoformat() and data["code"] in row.get("stocks", {})]
    persistent = sum(1 for row in rank_history if row["stocks"][data["code"]].get("trade_value_rank", 999) <= 30) >= 2 or price_rank <= 15

    checks: dict[str, dict[str, Any]] = {}
    checks["management"] = gate("FAIL" if data["code"] in excluded else "PASS", data["code"] in excluded, "관리·거래정지·투자경보 목록 대조")
    checks["trade_value"] = gate("PASS" if trade_value >= 50e9 else "WARN" if trade_value >= 30e9 else "FAIL", trade_value, "거래대금 500억 통과, 300억 경계")
    leader_pass = (group_rank is not None and group_rank <= 2) or price_rank <= 15
    checks["leader_or_catalyst"] = gate("PASS" if leader_pass or catalyst["strong"] else "WARN" if price_rank <= 30 or catalyst["titles"] else "FAIL", {"theme_rank": group_rank, "trade_value_rank": price_rank, "strong_catalyst": catalyst["strong"]}, "테마 대장 또는 강한 직접 재료")
    checks["adverse_material"] = gate("FAIL" if catalyst["adverse"] else "PASS", catalyst["negative"], "악재성 공시·뉴스 즉시 제외")
    chart_pass = day["reference"] is not None and (day["volume_ratio"] or 0) >= 1.3 and (day["high_proximity"] or 0) >= 0.90
    chart_warn = day["reference"] is not None or (day["volume_ratio"] or 0) >= 1.0
    checks["chart_qualification"] = gate("PASS" if chart_pass else "WARN" if chart_warn else "FAIL", {"reference": bool(day["reference"]), "volume_ratio": day["volume_ratio"], "high_proximity": day["high_proximity"]}, "기준봉·거래량·전고점 접근 결합")
    checks["intraday_lead"] = gate("PASS" if persistent else "WARN" if price_rank <= 30 else "FAIL", {"persistent": persistent, "rank": price_rank}, "장중 거래대금 상위 유지 또는 재진입")
    if intra["available"]:
        checks["absorption"] = gate("PASS" if intra["absorption_proxy"] >= 0.78 else "WARN" if intra["absorption_proxy"] >= 0.62 else "FAIL", intra["absorption_proxy"], "오후 저점 유지 뒤 최근 고가 재회복 대리변수")
        checks["structure_hold"] = gate("PASS" if intra["structure_hold"] and price and stop and price >= stop else "FAIL", {"support": stop, "afternoon_low": intra["afternoon_low"]}, "핵심 구조선·오후 저점 유지")
        checks["reclaim"] = gate("PASS" if (intra["reclaim_ratio"] or 0) >= 0.985 else "WARN" if (intra["reclaim_ratio"] or 0) >= 0.97 else "FAIL", intra["reclaim_ratio"], "장 막판 최근 고가 재접근")
    else:
        checks["absorption"] = gate("WARN", None, "장중 틱 미확인")
        checks["structure_hold"] = gate("WARN" if stop and price and price >= stop else "FAIL", {"support": stop}, "틱 없이 일봉 구조선만 확인")
        checks["reclaim"] = gate("WARN", None, "장중 고가 재회복 시각 미확인")
    checks["high_close"] = gate("PASS" if close_location is not None and close_location >= 0.75 else "WARN" if close_location is not None and close_location >= 0.65 else "FAIL", close_location, "당일 고저 범위 상단 마감")
    checks["no_distribution_wick"] = gate("PASS" if upper_wick is not None and upper_wick <= 0.30 and giveback is not None and giveback <= 0.25 else "WARN" if upper_wick is not None and upper_wick <= 0.45 and giveback is not None and giveback <= 0.35 else "FAIL", {"upper_wick": upper_wick, "giveback": giveback}, "긴 윗꼬리·상승폭 반납 배제")
    checks["stop_plan"] = gate("PASS" if stop_distance is not None and 0 < stop_distance <= 0.045 else "WARN" if stop_distance is not None and 0 < stop_distance <= 0.06 else "FAIL", {"stop": stop, "distance": stop_distance, "source": day["support_name"]}, "구조 손절선 계산 가능")
    checks["hoga_reference"] = gate("PASS" if hoga.get("available") else "WARN", hoga, "네이버 호가는 참고. 실제 HTS 수동 확인", "supporting")

    required = [value for value in checks.values() if value["role"] == "required"]
    fail_count = sum(value["status"] == "FAIL" for value in required)
    warn_count = sum(value["status"] == "WARN" for value in required)
    late_pass = fail_count == 0 and warn_count == 0
    snapshot_labels = {row.get("label"): row.get("stocks", {}).get(data["code"], {}).get("late_pass") for row in rank_history}
    clock = now.hour * 60 + now.minute
    current_label = "15:10" if 15 * 60 + 7 <= clock < 15 * 60 + 15 else "15:18" if 15 * 60 + 15 <= clock < 15 * 60 + 23 else "other"
    if current_label in {"15:10", "15:18"}:
        snapshot_labels[current_label] = late_pass
    consecutive = snapshot_labels.get("15:10") is True and snapshot_labels.get("15:18") is True
    if fail_count:
        state = "EXCLUDE"
    elif warn_count:
        state = "WATCH"
    elif consecutive:
        state = "READY"
    else:
        state = "WATCH"

    score_parts = {
        "trade_value": min(20, 20 * trade_value / 50e9) if trade_value else 0,
        "leader_material": 20 if leader_pass or catalyst["strong"] else 10 if price_rank <= 30 else 0,
        "chart": 20 if chart_pass else 10 if chart_warn else 0,
        "intraday": 20 if checks["absorption"]["status"] == "PASS" and checks["reclaim"]["status"] == "PASS" else 10,
        "close_shape": 15 if checks["high_close"]["status"] == "PASS" and checks["no_distribution_wick"]["status"] == "PASS" else 5,
        "plan": 5 if checks["stop_plan"]["status"] == "PASS" else 0,
    }
    score = round(sum(score_parts.values()), 1)
    next_day_plan = {
        "gap_up": "갭상승 후 첫 눌림이 전일 종가·구조선을 지키면 보유, 첫 1파에서 분할 청산",
        "flat": "보합 출발이면 전일 종가선 지지와 거래대금 재유입 확인",
        "gap_down": "갭하락 뒤 전일 종가선 회복 실패 시 정리",
    }
    return {
        "code": data["code"], "name": data.get("name") or data["code"], "market": data.get("market"), "industry": industry,
        "price": price, "change_rate": data.get("change_rate"), "trade_value": trade_value, "volume": data.get("volume"),
        "open": op, "high": high, "low": low, "close_location": close_location, "upper_wick": upper_wick, "giveback": giveback,
        "volume_ratio": day["volume_ratio"], "ma5": day["ma5"], "ma20": day["ma20"], "high20": day["high20"],
        "reference_candle": day["reference"], "support": stop, "support_name": day["support_name"], "stop_distance": stop_distance,
        "trade_value_rank": price_rank, "theme_rank": group_rank, "catalyst": catalyst, "intraday": intra, "hoga": hoga,
        "checks": checks, "fail_count": fail_count, "warn_count": warn_count, "late_pass": late_pass, "consecutive_late_pass": consecutive,
        "state": state, "score": score, "score_parts": score_parts, "next_day_plan": next_day_plan,
        "naver_url": f"https://stock.naver.com/domestic/stock/{data['code']}/price",
    }


def demo_rows() -> list[dict[str, Any]]:
    return [
        {"code": "000001", "name": "고가권 소화형 예시", "market": "KOSDAQ", "industry": "테마 A", "price": 15420, "change_rate": 8.7, "trade_value": 92e9, "volume": 8200000, "open": 14320, "high": 15580, "low": 14180, "close_location": 0.886, "upper_wick": 0.114, "giveback": 0.114, "volume_ratio": 2.1, "ma5": 14610, "ma20": 13880, "high20": 15600, "support": 14920, "support_name": "마지막 눌림 저점", "stop_distance": 0.0324, "trade_value_rank": 6, "theme_rank": 1, "catalyst": {"positive": ["대규모 공급계약 체결"], "negative": [], "strong": True, "adverse": False, "titles": ["대규모 공급계약 체결"]}, "intraday": {"available": True, "reclaim_ratio": 0.99, "absorption_proxy": 0.86, "structure_hold": True}, "hoga": {"available": False}, "checks": {}, "fail_count": 0, "warn_count": 0, "late_pass": True, "consecutive_late_pass": True, "state": "READY", "score": 91, "score_parts": {}, "next_day_plan": {"gap_up": "첫 눌림 지지 후 1파 청산", "flat": "전일 종가선 지지 확인", "gap_down": "종가선 회복 실패 시 정리"}, "naver_url": "#"},
        {"code": "000002", "name": "긴 윗꼬리 제외 예시", "market": "KOSDAQ", "industry": "테마 B", "price": 4375, "change_rate": 9.65, "trade_value": 41.3e9, "volume": 9440106, "open": 4010, "high": 5200, "low": 3970, "close_location": 0.329, "upper_wick": 0.671, "giveback": 0.671, "volume_ratio": 7.4, "ma5": 4020, "ma20": 3950, "high20": 5200, "support": 4050, "support_name": "최근 눌림 저점", "stop_distance": 0.074, "trade_value_rank": 31, "theme_rank": 3, "catalyst": {"positive": [], "negative": [], "strong": False, "adverse": False, "titles": ["테마 동반 상승"]}, "intraday": {"available": True, "reclaim_ratio": 0.84, "absorption_proxy": 0.46, "structure_hold": True}, "hoga": {"available": False}, "checks": {}, "fail_count": 3, "warn_count": 2, "late_pass": False, "consecutive_late_pass": False, "state": "EXCLUDE", "score": 48, "score_parts": {}, "next_day_plan": {"gap_up": "관찰", "flat": "관찰", "gap_down": "제외"}, "naver_url": "#"},
    ]


def fmt_money(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value / 1e8:,.0f}억"


def fmt_num(value: float | None, digits: int = 2) -> str:
    return "-" if value is None else f"{value:,.{digits}f}"


def rule_badge(status: str) -> str:
    return {"PASS": "통과", "WARN": "주의", "FAIL": "실패"}.get(status, status)


def render_site(results: list[dict[str, Any]], meta: dict[str, Any], strict: dict[str, Any]) -> str:
    payload = json.dumps({"results": results, "meta": meta, "strict": strict}, ensure_ascii=False).replace("</", "<\\/")
    rows = []
    for row in results:
        cls = row["state"].lower()
        rows.append(f"<tr data-state='{html.escape(row['state'])}' data-search='{html.escape((row['name']+' '+row['code']+' '+str(row.get('industry') or '')).casefold())}' onclick=\"openDetail('{row['code']}')\"><td><b>{html.escape(row['name'])}</b><small>{row['code']} · {html.escape(str(row.get('industry') or '미분류'))}</small></td><td>{row['trade_value_rank']}</td><td>{fmt_money(row.get('trade_value'))}</td><td>{fmt_num(row.get('change_rate'))}%</td><td>{fmt_num((row.get('close_location') or 0)*100,1)}%</td><td>{fmt_num((row.get('upper_wick') or 0)*100,1)}%</td><td>{fmt_num(row.get('volume_ratio'))}x</td><td>{fmt_num((row.get('stop_distance') or 0)*100,1)}%</td><td><b>{row['score']}</b></td><td><span class='state {cls}'>{row['state']}</span></td></tr>")
    support = strict.get("rule_support", {})
    evidence_rows = "".join(f"<tr><td>{html.escape(value.get('name', key))}</td><td>{html.escape(value.get('group',''))}</td><td>{value.get('videos',0)}/10</td><td>{value.get('events',0)}</td><td>{html.escape(value.get('strength',''))}</td></tr>" for key, value in support.items())
    return f"""<!doctype html><html lang='ko'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='color-scheme' content='light'><title>라르고 종가매매 스크리너</title><style>
:root{{--bg:#edf2f7;--paper:#fff;--line:#d9e1ea;--ink:#172337;--muted:#65758a;--blue:#1766d6;--green:#0f8550;--amber:#ad6c00;--red:#bd3947;--shadow:0 13px 32px rgba(30,52,80,.09)}}*{{box-sizing:border-box}}body{{margin:0;background:linear-gradient(180deg,#f8fafe,#edf2f7 210px);font-family:Arial,'Malgun Gothic',sans-serif;color:var(--ink)}}header{{position:sticky;top:0;z-index:20;background:rgba(255,255,255,.95);backdrop-filter:blur(10px);border-bottom:1px solid var(--line)}}.head{{max-width:1600px;margin:auto;padding:15px 20px;display:flex;justify-content:space-between;gap:15px;align-items:center}}h1{{margin:0;font-size:21px}}.sub{{font-size:11px;color:var(--muted);margin-top:5px}}.stamp{{text-align:right;font-size:11px;color:var(--muted)}}main{{max-width:1600px;margin:auto;padding:18px 20px 50px}}.hero{{background:linear-gradient(135deg,#fff,#f5f9ff);border:1px solid var(--line);border-radius:21px;padding:22px;box-shadow:var(--shadow)}}.hero h2{{margin:0 0 8px;font-size:25px}}.hero p{{margin:0;color:var(--muted);line-height:1.65}}.metrics{{display:grid;grid-template-columns:repeat(6,1fr);gap:9px;margin-top:16px}}.metric{{background:#fff;border:1px solid var(--line);border-radius:13px;padding:12px}}.metric b{{display:block;font-size:20px}}.metric span{{font-size:10px;color:var(--muted)}}.flow{{display:grid;grid-template-columns:repeat(5,1fr);gap:9px;margin:15px 0}}.stage{{border:1px solid var(--line);border-radius:14px;background:#fff;padding:13px}}.stage i{{display:grid;place-items:center;width:25px;height:25px;border-radius:50%;background:var(--blue);color:#fff;font-style:normal;font-weight:900}}.stage b{{display:block;margin-top:7px}}.stage span{{font-size:10px;color:var(--muted)}}.panel{{background:#fff;border:1px solid var(--line);border-radius:18px;box-shadow:var(--shadow);margin-top:15px;overflow:hidden}}.panel-head{{padding:14px 16px;border-bottom:1px solid var(--line);display:flex;justify-content:space-between;gap:10px;align-items:center}}.panel-head b{{font-size:16px}}.tools{{display:flex;gap:7px;flex-wrap:wrap}}input,select{{border:1px solid var(--line);border-radius:10px;padding:8px 10px;background:#fff}}.table-wrap{{overflow:auto}}table{{border-collapse:collapse;width:100%;font-size:12px}}th,td{{padding:10px 9px;border-bottom:1px solid #edf1f5;text-align:right;white-space:nowrap}}th{{background:#f7f9fc;color:#526176;position:sticky;top:0}}th:first-child,td:first-child{{text-align:left}}tbody tr{{cursor:pointer}}tbody tr:hover{{background:#f5f9ff}}td small{{display:block;color:var(--muted);margin-top:3px}}.state{{display:inline-flex;padding:5px 8px;border-radius:999px;font-weight:900;font-size:10px}}.state.ready{{background:#e8f7ef;color:var(--green)}}.state.watch{{background:#fff5df;color:var(--amber)}}.state.exclude{{background:#ffedf0;color:var(--red)}}.drawer{{position:fixed;inset:0;z-index:50;background:rgba(16,27,43,.42);display:none;justify-content:flex-end}}.drawer.open{{display:flex}}.sheet{{width:min(720px,96vw);height:100%;overflow:auto;background:#fff;padding:20px;box-shadow:-15px 0 40px rgba(10,20,35,.18)}}.close{{float:right;border:0;background:#edf2f7;border-radius:10px;padding:8px 10px;cursor:pointer}}.checks{{display:grid;grid-template-columns:1fr 1fr;gap:8px}}.check{{border:1px solid var(--line);border-radius:12px;padding:10px}}.check b{{display:flex;justify-content:space-between;gap:8px}}.check p{{font-size:11px;color:var(--muted);line-height:1.5;margin:6px 0 0}}.pass{{color:var(--green)}}.warn{{color:var(--amber)}}.fail{{color:var(--red)}}.plan{{background:#f6f9fd;border:1px solid var(--line);border-radius:13px;padding:12px;margin-top:12px;line-height:1.7;font-size:12px}}.confirm{{display:block;width:100%;margin-top:14px;padding:12px;border:0;border-radius:12px;background:var(--blue);color:#fff;font-weight:900;cursor:pointer}}details{{padding:12px 15px}}summary{{cursor:pointer;font-weight:800}}.notice{{background:#fff8e8;border:1px solid #efd7a4;border-radius:13px;padding:12px;margin-top:14px;font-size:12px;line-height:1.6;color:#6f4b08}}@media(max-width:1000px){{.metrics{{grid-template-columns:repeat(2,1fr)}}.flow{{grid-template-columns:1fr 1fr}}.checks{{grid-template-columns:1fr}}.head{{display:block}}.stamp{{text-align:left;margin-top:7px}}}}</style></head><body><header><div class='head'><div><h1>라르고 종가매매 스크리너</h1><div class='sub'>OpenBot 10편 · 25,192프레임 재분석 기반 · 네이버증권 공개 읽기 전용 데이터</div></div><div class='stamp'>생성 {html.escape(meta['generated_at'])}<br>{html.escape(meta.get('mode',''))} · 요청 {meta.get('request_count',0)}회</div></div></header><main><section class='hero'><h2>점수가 아니라 시간순 필수 게이트로 고릅니다.</h2><p>거래대금 후보를 모은 뒤 대장·재료, 기준봉·매물대, 장중 물량 소화, 고가 재회복, 긴 윗꼬리 배제, 구조 손절·익일 계획 순서로 통과시킵니다. 웹 데이터만으로 자동매수하지 않습니다.</p><div class='metrics'><div class='metric'><b>{len(results)}</b><span>분석 후보</span></div><div class='metric'><b>{sum(r['state']=='READY' for r in results)}</b><span>READY</span></div><div class='metric'><b>{sum(r['state']=='WATCH' for r in results)}</b><span>WATCH</span></div><div class='metric'><b>{sum(r['state']=='EXCLUDE' for r in results)}</b><span>EXCLUDE</span></div><div class='metric'><b>10/10</b><span>OpenBot 분석 완료</span></div><div class='metric'><b>25,192</b><span>분석 프레임</span></div></div></section><div class='flow'><div class='stage'><i>1</i><b>종목 자격</b><span>거래대금·대장·재료·차트</span></div><div class='stage'><i>2</i><b>장중 전개</b><span>주도성·물량소화·지지</span></div><div class='stage'><i>3</i><b>종가 구조</b><span>고가 재회복·윗꼬리 배제</span></div><div class='stage'><i>4</i><b>실행 계획</b><span>진입·손절·익일 1파</span></div><div class='stage'><i>5</i><b>HTS 확인</b><span>호가·체결·단일가</span></div></div><section class='panel'><div class='panel-head'><b>후보 종목</b><div class='tools'><input id='search' placeholder='종목·코드·테마 검색'><select id='filter'><option value='ALL'>전체</option><option>READY</option><option>WATCH</option><option>EXCLUDE</option></select></div></div><div class='table-wrap'><table><thead><tr><th>종목</th><th>거래대금 순위</th><th>거래대금</th><th>등락률</th><th>종가 위치</th><th>윗꼬리</th><th>거래량 배수</th><th>손절 거리</th><th>품질점수</th><th>상태</th></tr></thead><tbody id='rows'>{''.join(rows)}</tbody></table></div></section><section class='panel'><div class='panel-head'><b>OpenBot 직접 근거</b><span class='sub'>영상별 발언·시간·프레임을 규칙 ID로 집계</span></div><details><summary>규칙별 근거 영상 수 펼치기</summary><div class='table-wrap'><table><thead><tr><th>규칙</th><th>그룹</th><th>근거 영상</th><th>이벤트</th><th>강도</th></tr></thead><tbody>{evidence_rows}</tbody></table></div></details></section><div class='notice'>네이버증권 내부 JSON은 공식 증권 API가 아니며 구조와 지연이 바뀔 수 있습니다. READY는 웹 필수조건 통과 상태일 뿐 매수 신호가 아닙니다. 실제 주문 전 HTS에서 호가·체결·종가 단일가와 재료를 직접 확인하세요.</div></main><div class='drawer' id='drawer' onclick='if(event.target===this)closeDetail()'><div class='sheet'><button class='close' onclick='closeDetail()'>닫기</button><div id='detail'></div></div></div><script>const DATA={payload};const byCode=Object.fromEntries(DATA.results.map(x=>[x.code,x]));const f=n=>n==null?'-':Number(n).toLocaleString('ko-KR');const pct=n=>n==null?'-':(Number(n)*100).toFixed(1)+'%';function openDetail(code){{const r=byCode[code];const checks=Object.entries(r.checks||{{}}).map(([k,v])=>`<div class='check'><b><span>${{k}}</span><span class='${{(v.status||'').toLowerCase()}}'>${{v.status}}</span></b><p>${{v.reason}}<br>실제값 ${{typeof v.value==='object'?JSON.stringify(v.value):v.value??'-'}}</p></div>`).join('');const confirmed=localStorage.getItem('largo-confirm-'+code)==='1';document.getElementById('detail').innerHTML=`<h2>${{r.name}} <small>${{r.code}}</small></h2><p><span class='state ${{r.state.toLowerCase()}}'>${{r.state}}</span> 품질점수 ${{r.score}}점 · 거래대금 ${{f(r.trade_value)}}원</p><h3>필수 체크리스트</h3><div class='checks'>${{checks}}</div><div class='plan'><b>구조 손절</b><br>${{r.support_name||'-'}} ${{f(r.support)}}원 · 거리 ${{pct(r.stop_distance)}}<br><br><b>익일 계획</b><br>갭상승: ${{r.next_day_plan.gap_up}}<br>보합: ${{r.next_day_plan.flat}}<br>갭하락: ${{r.next_day_plan.gap_down}}</div><p><a href='${{r.naver_url}}' target='_blank' rel='noopener'>네이버증권 종목 화면 열기</a></p><button class='confirm' onclick='toggleConfirm("${{code}}",this)'>${{confirmed?'HTS 확인 취소':'실제 HTS 확인 완료'}}</button>`;document.getElementById('drawer').classList.add('open')}}function closeDetail(){{document.getElementById('drawer').classList.remove('open')}}function toggleConfirm(code,button){{const key='largo-confirm-'+code;const on=localStorage.getItem(key)==='1';localStorage.setItem(key,on?'0':'1');button.textContent=on?'실제 HTS 확인 완료':'HTS 확인 취소'}}function apply(){{const q=document.getElementById('search').value.casefold?.()||document.getElementById('search').value.toLowerCase();const state=document.getElementById('filter').value;document.querySelectorAll('#rows tr').forEach(row=>{{row.style.display=((state==='ALL'||row.dataset.state===state)&&row.dataset.search.includes(q))?'':'none'}})}}document.getElementById('search').addEventListener('input',apply);document.getElementById('filter').addEventListener('change',apply);</script></body></html>"""


def build(output: Path, history_path: Path, candidate_limit: int) -> dict[str, Any]:
    now = datetime.now(KST)
    client = NaverClient()
    lists = [ranking(client, "priceTop"), ranking(client, "up"), ranking(client, "upperQuantTop"), ranking(client, "high52week")]
    excluded_rows = ranking(client, "statusTag") + ranking(client, "tradeStopYn")
    for alert in ("01", "02", "03"):
        excluded_rows += ranking(client, "marketAlertType", alert_type=alert)
    excluded = {row["code"] for row in excluded_rows}
    candidates = merge_candidates(lists, candidate_limit)
    history = json.loads(history_path.read_text(encoding="utf-8")) if history_path.exists() else {"snapshots": []}
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        groups[candidate.get("industry") or "미분류"].append(candidate)
    history["current_groups"] = groups
    results = []
    for candidate in candidates:
        code = candidate["code"]
        detail = detail_price(client, code)
        daily = time_series(client, code, "siseDay", 80)
        ticks = time_series(client, code, "siseTick", 240)
        news = news_and_notices(client, code)
        hoga = hoga_summary(client, code)
        results.append(evaluate(candidate, detail, daily, ticks, news, hoga, excluded, history, now))
    mode = "NAVER_SNAPSHOT"
    if not results:
        results = demo_rows()
        mode = "DEMO_FALLBACK"
    results.sort(key=lambda row: ({"READY": 0, "WATCH": 1, "EXCLUDE": 2}[row["state"]], -row["score"]))
    label = "15:10" if 15*60+7 <= now.hour*60+now.minute < 15*60+15 else "15:18" if 15*60+15 <= now.hour*60+now.minute < 15*60+23 else now.strftime("%H:%M")
    history.setdefault("snapshots", []).append({
        "date": now.date().isoformat(), "label": label, "generated_at": now.isoformat(),
        "stocks": {row["code"]: {"trade_value_rank": row["trade_value_rank"], "late_pass": row["late_pass"], "state": row["state"], "score": row["score"]} for row in results},
    })
    history["snapshots"] = history["snapshots"][-80:]
    history.pop("current_groups", None)
    history_path.parent.mkdir(parents=True, exist_ok=True)
    history_path.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
    strict_path = Path("largo-close-screener/evidence/strict_closing_rules.json")
    strict = json.loads(strict_path.read_text(encoding="utf-8")) if strict_path.exists() else {"rule_support": {}, "stages": []}
    meta = {"generated_at": now.strftime("%Y-%m-%d %H:%M:%S KST"), "mode": mode, "request_count": client.requests, "errors": client.errors, "candidate_count": len(results)}
    output.mkdir(parents=True, exist_ok=True)
    (output / "data.json").write_text(json.dumps({"results": results, "meta": meta, "strict": strict}, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "index.html").write_text(render_site(results, meta, strict), encoding="utf-8")
    (output / ".nojekyll").write_text("", encoding="utf-8")
    return meta


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="site-output")
    parser.add_argument("--history", default="largo-close-screener/data/history.json")
    parser.add_argument("--candidate-limit", type=int, default=36)
    args = parser.parse_args()
    meta = build(Path(args.output), Path(args.history), max(5, min(args.candidate_limit, 60)))
    print(json.dumps(meta, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
