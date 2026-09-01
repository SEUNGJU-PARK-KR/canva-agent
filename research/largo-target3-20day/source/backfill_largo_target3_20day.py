#!/usr/bin/env python3
"""Backfill the fixed Largo +3% pre-09:06 research rule over 20 trading days.

The rule itself is imported unchanged from largo_material_0906.target3_gate.
Historical candidates are reconstructed from Naver Finance market listings, daily
charts and exact legacy 15:18/09:00-09:05 quote pages. Historical theme membership
is unavailable, so theme membership is the current catalog while member returns are
measured on each historical date. This proxy is reported explicitly.
"""
from __future__ import annotations

import argparse
import base64
import concurrent.futures as cf
import csv
import datetime as dt
import gzip
import json
import math
import re
import statistics
import threading
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import urlencode, urljoin

import requests
from bs4 import BeautifulSoup

from largo_material_0906 import (
    KST,
    NEGATIVE_TERMS,
    MECHANICAL_TERMS,
    event_strength,
    flatten_news,
    legacy_quote,
    num,
    outcome_before_0906,
    parse_legacy_rows,
    score_candidate,
    target3_gate,
)

UA = "Mozilla/5.0 (compatible; LargoTarget3Backfill/1.0; read-only)"
NAVER_FINANCE = "https://finance.naver.com"
NAVER_STOCK = "https://stock.naver.com"
CHART = "https://fchart.stock.naver.com/sise.nhn"
RANK_WEIGHTS = {"trading_value": 4.0, "rise": 3.0, "volume_surge": 2.0, "high52": 1.5}
POLITICAL_TERMS = ("정치", "대선", "총선", "후보", "선거", "정치인")
EXCLUDE_NAME = re.compile(r"(?:우|우B|우C|우선주|스팩|ETF|ETN|인버스|레버리지|선물)$", re.I)
_tls = threading.local()


def session() -> requests.Session:
    value = getattr(_tls, "session", None)
    if value is None:
        value = requests.Session()
        value.headers.update({"User-Agent": UA, "Referer": NAVER_FINANCE + "/"})
        _tls.session = value
    return value


def get(url: str, *, timeout: int = 25, retries: int = 4) -> requests.Response:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            response = session().get(url, timeout=timeout)
            response.raise_for_status()
            return response
        except Exception as exc:
            last = exc
            time.sleep(min(3.0, 0.35 * (2 ** attempt)))
    raise RuntimeError(f"GET failed: {url}: {last}")


def fnum(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).replace(",", "").replace("%", "").strip()
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    return float(match.group()) if match else None


def ordinary(name: str) -> bool:
    compact = re.sub(r"\s+", "", name)
    if EXCLUDE_NAME.search(compact):
        return False
    if any(term in compact.upper() for term in ("SPAC", "KODEX", "TIGER", "KOSEF", "ARIRANG", "SOL", "ACE", "PLUS")):
        return False
    return True


def market_universe() -> tuple[dict[str, dict[str, Any]], list[str]]:
    rows: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    for market in (0, 1):
        seen_pages: set[tuple[str, ...]] = set()
        for page in range(1, 100):
            url = f"{NAVER_FINANCE}/sise/sise_market_sum.naver?sosok={market}&page={page}"
            try:
                raw = get(url).content.decode("euc-kr", errors="replace")
            except Exception as exc:
                errors.append(f"market {market} page {page}: {type(exc).__name__}: {exc}")
                break
            soup = BeautifulSoup(raw, "html.parser")
            page_codes: list[str] = []
            for tr in soup.select("table.type_2 tr"):
                anchor = tr.select_one("a.tltle")
                if not anchor:
                    continue
                match = re.search(r"code=(\d{6})", anchor.get("href", ""))
                cols = [td.get_text(" ", strip=True) for td in tr.select("td")]
                if not match or len(cols) < 10:
                    continue
                code = match.group(1)
                name = anchor.get_text(strip=True)
                price = fnum(cols[2])
                market_cap_eok = fnum(cols[9])
                if price is None or market_cap_eok is None or price <= 0:
                    continue
                page_codes.append(code)
                rows[code] = {
                    "code": code,
                    "name": name,
                    "market": "KOSPI" if market == 0 else "KOSDAQ",
                    "current_price": price,
                    "current_market_cap": market_cap_eok * 100_000_000,
                    "shares_proxy": market_cap_eok * 100_000_000 / price,
                    "ordinary": ordinary(name),
                }
            signature = tuple(page_codes)
            if not page_codes or signature in seen_pages:
                break
            seen_pages.add(signature)
            if len(page_codes) < 40:
                break
    return rows, errors


def chart_rows(code: str, count: int = 360) -> list[dict[str, float | str]]:
    url = CHART + "?" + urlencode({"symbol": code, "timeframe": "day", "count": count, "requestType": 0})
    raw = get(url, timeout=25).content.decode("utf-8", errors="replace")
    result: list[dict[str, float | str]] = []
    for packed in re.findall(r'data="([^"]+)"', raw):
        parts = packed.split("|")
        if len(parts) < 6 or not re.fullmatch(r"20\d{6}", parts[0]):
            continue
        values = [fnum(value) for value in parts[1:6]]
        if any(value is None for value in values):
            continue
        o, h, l, c, v = [float(value) for value in values]
        if min(o, h, l, c) <= 0 or h < l:
            continue
        result.append({"date": parts[0], "o": o, "h": h, "l": l, "c": c, "v": v})
    dedup = {str(row["date"]): row for row in result}
    return [dedup[key] for key in sorted(dedup)]


def fetch_charts(universe: Mapping[str, Mapping[str, Any]], workers: int) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    charts: dict[str, list[dict[str, Any]]] = {}
    errors: list[str] = []
    codes = [code for code, row in universe.items() if row.get("ordinary")]
    with cf.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(chart_rows, code): code for code in codes}
        for index, future in enumerate(cf.as_completed(futures), 1):
            code = futures[future]
            try:
                rows = future.result()
                if rows:
                    charts[code] = rows
            except Exception as exc:
                errors.append(f"chart {code}: {type(exc).__name__}: {exc}")
            if index % 250 == 0:
                print(f"charts {index}/{len(codes)} ok={len(charts)} errors={len(errors)}", flush=True)
    return charts, errors


def index_charts(charts: Mapping[str, Sequence[Mapping[str, Any]]]) -> dict[str, dict[str, int]]:
    return {code: {str(row["date"]): index for index, row in enumerate(rows)} for code, rows in charts.items()}


def trading_dates(charts: Mapping[str, Sequence[Mapping[str, Any]]], end_date: str, days: int) -> tuple[list[str], dict[str, str]]:
    benchmark = charts.get("005930") or max(charts.values(), key=len)
    dates = [str(row["date"]) for row in benchmark]
    eligible = [value for value in dates if value <= end_date.replace("-", "")]
    chosen = eligible[-days:]
    next_map: dict[str, str] = {}
    positions = {value: i for i, value in enumerate(dates)}
    for value in chosen:
        position = positions[value]
        if position + 1 < len(dates):
            next_map[value] = dates[position + 1]
    chosen = [value for value in chosen if value in next_map]
    if len(chosen) < days:
        raise RuntimeError(f"only {len(chosen)} completed trading pairs found")
    return chosen, next_map


def average(values: Iterable[float]) -> float:
    rows = [float(value) for value in values]
    return statistics.mean(rows) if rows else 0.0


def market_features(
    code: str,
    date_text: str,
    universe: Mapping[str, Mapping[str, Any]],
    charts: Mapping[str, Sequence[Mapping[str, Any]]],
    indices: Mapping[str, Mapping[str, int]],
) -> dict[str, Any] | None:
    rows = charts.get(code)
    position = indices.get(code, {}).get(date_text)
    if rows is None or position is None or position < 1:
        return None
    row = rows[position]
    previous = rows[position - 1]
    hist = rows[:position]
    prev_close = float(previous["c"])
    close = float(row["c"])
    volume = float(row["v"])
    avg20 = average(float(value["v"]) for value in hist[-20:])
    high52 = max((float(value["h"]) for value in hist[-252:]), default=close)
    typical = average([float(row["o"]), float(row["h"]), float(row["l"]), close])
    shares = float(universe[code]["shares_proxy"])
    return {
        "code": code,
        "name": universe[code]["name"],
        "market": universe[code]["market"],
        "date": date_text,
        "o": float(row["o"]), "h": float(row["h"]), "l": float(row["l"]), "c": close, "v": volume,
        "prev_close": prev_close,
        "change_rate": (close / prev_close - 1.0) * 100.0,
        "trade_value": typical * volume,
        "volume_surge": volume / avg20 if avg20 > 0 else 0.0,
        "high52_ratio": close / high52 if high52 > 0 else 0.0,
        "market_cap": shares * close,
        "shares_proxy": shares,
    }


def source_ranks(rows: Sequence[Mapping[str, Any]], top: int = 80) -> dict[str, dict[str, int]]:
    values = {
        "trading_value": sorted(rows, key=lambda x: float(x["trade_value"]), reverse=True),
        "rise": sorted(rows, key=lambda x: float(x["change_rate"]), reverse=True),
        "volume_surge": sorted(rows, key=lambda x: float(x["volume_surge"]), reverse=True),
        "high52": sorted(rows, key=lambda x: float(x["high52_ratio"]), reverse=True),
    }
    ranks: dict[str, dict[str, int]] = defaultdict(dict)
    for source, ordered in values.items():
        for rank, row in enumerate(ordered[:top], 1):
            ranks[str(row["code"])][source] = rank
    return ranks


def rank_score(ranks: Mapping[str, int]) -> float:
    return sum(RANK_WEIGHTS[source] * max(0, 101 - rank) for source, rank in ranks.items())


def quote_rows(code: str, stamp: str) -> list[list[str]]:
    url = f"{NAVER_FINANCE}/item/sise_time.naver?" + urlencode({"code": code, "thistime": stamp, "page": 1})
    raw = get(url, timeout=25).content
    return parse_legacy_rows(raw)


def signal_quote(code: str, date_text: str) -> dict[str, Any]:
    rows = quote_rows(code, date_text + "151800")
    quotes = [quote for row in rows if (quote := legacy_quote(row)) and quote.get("last")]
    quote = next((item for item in quotes if item["time"] == "15:18"), quotes[0] if quotes else None)
    if quote is None:
        return {"code": code, "date": date_text, "entry_time": None, "entry_last": None, "entry_ask": None, "entry_bid": None, "cum_volume": None}
    return {
        "code": code, "date": date_text,
        "entry_time": quote.get("time"), "entry_last": quote.get("last"),
        "entry_ask": quote.get("ask") or quote.get("last"), "entry_bid": quote.get("bid"),
        "cum_volume": quote.get("cum_volume"), "signal_observations": len(quotes),
    }


def fetch_signal_quotes(pairs: Sequence[tuple[str, str]], workers: int) -> tuple[dict[tuple[str, str], dict[str, Any]], list[str]]:
    cache: dict[tuple[str, str], dict[str, Any]] = {}
    errors: list[str] = []
    with cf.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(signal_quote, code, date): (date, code) for date, code in pairs}
        for index, future in enumerate(cf.as_completed(futures), 1):
            key = futures[future]
            try:
                cache[key] = future.result()
            except Exception as exc:
                errors.append(f"signal {key[0]} {key[1]}: {type(exc).__name__}: {exc}")
                cache[key] = {"date": key[0], "code": key[1]}
            if index % 250 == 0:
                print(f"signal quotes {index}/{len(pairs)} errors={len(errors)}", flush=True)
    return cache, errors


def reranked_shortlist(
    full_rows: Sequence[Mapping[str, Any]],
    first_ranks: Mapping[str, Mapping[str, int]],
    date_text: str,
    quotes: Mapping[tuple[str, str], Mapping[str, Any]],
    limit: int,
) -> list[dict[str, Any]]:
    pre = sorted(full_rows, key=lambda row: rank_score(first_ranks.get(str(row["code"]), {})), reverse=True)[:limit]
    adjusted: list[dict[str, Any]] = []
    for base in pre:
        item = dict(base)
        quote = quotes.get((date_text, str(base["code"])), {})
        last = num(quote.get("entry_last"))
        volume = num(quote.get("cum_volume"))
        if last is not None:
            item["signal_last"] = last
            item["change_rate"] = (last / float(item["prev_close"]) - 1) * 100
            item["market_cap"] = float(item["shares_proxy"]) * last
            item["high52_ratio"] = min(2.0, item["high52_ratio"] * last / float(item["c"]))
        if volume is not None and volume > 0:
            item["signal_volume"] = volume
            item["trade_value"] = (last or float(item["c"])) * volume
            daily_volume = float(item["v"])
            if daily_volume > 0:
                item["volume_surge"] = float(item["volume_surge"]) * volume / daily_volume
        item["entry"] = dict(quote)
        adjusted.append(item)
    ranks = source_ranks(adjusted, top=min(80, len(adjusted)))
    eligible = [
        row for row in adjusted
        if (num(row.get("signal_last")) or num(row.get("c")) or 0) >= 1000
        and float(row.get("market_cap") or 0) >= 50_000_000_000
        and float(row.get("trade_value") or 0) >= 20_000_000_000
    ]
    eligible.sort(key=lambda row: rank_score(ranks.get(str(row["code"]), {})), reverse=True)
    for row in eligible:
        row["source_ranks"] = ranks.get(str(row["code"]), {})
        row["source_rank_score"] = round(rank_score(row["source_ranks"]), 2)
    return eligible[:24]


def theme_catalog() -> tuple[dict[str, dict[str, Any]], dict[str, list[str]], list[str]]:
    themes: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    for page in range(1, 20):
        try:
            raw = get(f"{NAVER_FINANCE}/sise/theme.naver?page={page}").content.decode("euc-kr", errors="replace")
        except Exception as exc:
            errors.append(f"theme list {page}: {type(exc).__name__}: {exc}")
            break
        soup = BeautifulSoup(raw, "html.parser")
        before = len(themes)
        for anchor in soup.select('a[href*="sise_group_detail.naver?type=theme"]'):
            match = re.search(r"[?&]no=(\d+)", anchor.get("href", ""))
            name = anchor.get_text(" ", strip=True)
            if match and name:
                themes.setdefault(match.group(1), {"code": match.group(1), "name": name, "members": []})
        if len(themes) == before:
            break
    def load_members(theme: Mapping[str, Any]) -> tuple[str, list[str], str | None]:
        code = str(theme["code"])
        try:
            raw = get(f"{NAVER_FINANCE}/sise/sise_group_detail.naver?type=theme&no={code}").content.decode("euc-kr", errors="replace")
            soup = BeautifulSoup(raw, "html.parser")
            members: list[str] = []
            for anchor in soup.select('a[href*="/item/main.naver?code="]'):
                match = re.search(r"code=(\d{6})", anchor.get("href", ""))
                if match and match.group(1) not in members:
                    members.append(match.group(1))
            return code, members, None
        except Exception as exc:
            return code, [], f"theme {code}: {type(exc).__name__}: {exc}"
    with cf.ThreadPoolExecutor(max_workers=18) as executor:
        futures = [executor.submit(load_members, theme) for theme in themes.values()]
        for future in cf.as_completed(futures):
            code, members, error = future.result()
            themes[code]["members"] = members
            if error:
                errors.append(error)
    code_map: dict[str, list[str]] = defaultdict(list)
    for theme_code, theme in themes.items():
        for code in theme.get("members") or []:
            code_map[code].append(theme_code)
    return themes, code_map, errors


def historical_theme_metrics(
    candidate_code: str,
    date_text: str,
    themes: Mapping[str, Mapping[str, Any]],
    code_map: Mapping[str, Sequence[str]],
    charts: Mapping[str, Sequence[Mapping[str, Any]]],
    indices: Mapping[str, Mapping[str, int]],
) -> dict[str, Any]:
    options: list[dict[str, Any]] = []
    for theme_code in code_map.get(candidate_code, []):
        theme = themes.get(theme_code, {})
        members: list[dict[str, Any]] = []
        for code in theme.get("members") or []:
            rows = charts.get(code)
            position = indices.get(code, {}).get(date_text)
            if rows is None or position is None or position < 1:
                continue
            row, previous = rows[position], rows[position - 1]
            prev_close = float(previous["c"])
            change = (float(row["c"]) / prev_close - 1) * 100 if prev_close else 0.0
            turnover = average([float(row["o"]), float(row["h"]), float(row["l"]), float(row["c"])]) * float(row["v"])
            members.append({"code": code, "change_rate": change, "trade_value": turnover})
        if len(members) < 3:
            continue
        rising = sum(1 for row in members if row["change_rate"] > 0)
        breadth = rising / len(members)
        ranked = sorted(members, key=lambda row: (row["change_rate"], row["trade_value"]), reverse=True)
        rank = next((index for index, row in enumerate(ranked, 1) if row["code"] == candidate_code), None)
        top3 = ranked[:3]
        followers = top3[1:3]
        strong = [row for row in followers if row["change_rate"] >= 2 and row["trade_value"] >= 10_000_000_000]
        follower_turnover = sum(row["trade_value"] for row in followers)
        top3_turnover = sum(row["trade_value"] for row in top3)
        options.append({
            "name": theme.get("name"), "code": theme_code,
            "rising": rising, "total": len(members), "breadth": breadth,
            "leader_rank": rank,
            "follower_strong_count": len(strong),
            "follower_turnover": follower_turnover,
            "follower_turnover_ratio": follower_turnover / top3_turnover if top3_turnover else None,
            "observed_breadth": True, "observed_leadership": rank is not None,
            "observed_followers": True, "membership_proxy": "current_catalog",
            "average_change": average(row["change_rate"] for row in members),
            "total_turnover": sum(row["trade_value"] for row in members),
        })
    if not options:
        return {
            "name": None, "code": None, "rising": None, "total": None, "breadth": None,
            "leader_rank": None, "follower_strong_count": None, "follower_turnover": None,
            "follower_turnover_ratio": None, "observed_breadth": False,
            "observed_leadership": False, "observed_followers": False,
            "membership_proxy": "unavailable",
        }
    return max(options, key=lambda item: (item["breadth"], item["average_change"], item["total_turnover"]))


def fetch_news_history(code: str, oldest: dt.datetime) -> tuple[str, list[dict[str, Any]], list[str]]:
    errors: list[str] = []
    items: list[dict[str, Any]] = []
    for page in range(1, 16):
        try:
            payload = get(f"{NAVER_STOCK}/api/domestic/detail/news?itemCode={code}&page={page}&pageSize=30", timeout=25).json()
            page_items = flatten_news({"news": {"payload": payload}, "notice": {"payload": []}})
            items.extend(page_items)
            dates = [item.get("at") for item in page_items if isinstance(item.get("at"), dt.datetime)]
            if not page_items or (dates and min(dates) <= oldest):
                break
        except Exception as exc:
            errors.append(f"news page {page}: {type(exc).__name__}: {exc}")
            break
    for start in (0, 100):
        try:
            payload = get(f"{NAVER_STOCK}/api/domestic/detail/notice?itemCode={code}&startIdx={start}&pageSize=100", timeout=25).json()
            page_items = flatten_news({"news": {"payload": []}, "notice": {"payload": payload}})
            items.extend(page_items)
            dates = [item.get("at") for item in page_items if isinstance(item.get("at"), dt.datetime)]
            if not page_items or (dates and min(dates) <= oldest):
                break
        except Exception as exc:
            errors.append(f"notice {start}: {type(exc).__name__}: {exc}")
            break
    dedup: dict[tuple[str, str], dict[str, Any]] = {}
    for item in items:
        at = item.get("at")
        key = (str(item.get("title") or ""), at.isoformat() if isinstance(at, dt.datetime) else "")
        dedup[key] = item
    return code, list(dedup.values()), errors


def fetch_all_news(codes: Sequence[str], oldest: dt.datetime, workers: int) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    result: dict[str, list[dict[str, Any]]] = {}
    errors: list[str] = []
    with cf.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(fetch_news_history, code, oldest) for code in codes]
        for index, future in enumerate(cf.as_completed(futures), 1):
            code, items, errs = future.result()
            result[code] = items
            errors.extend(f"{code}: {error}" for error in errs)
            if index % 50 == 0:
                print(f"news {index}/{len(codes)} errors={len(errors)}", flush=True)
    return result, errors


def ma(rows: Sequence[Mapping[str, Any]], n: int) -> float | None:
    values = [float(row["c"]) for row in rows[-n:]]
    return statistics.mean(values) if len(values) >= max(2, min(n, 5)) else None


def turnover(row: Mapping[str, Any]) -> float:
    return average([float(row["o"]), float(row["h"]), float(row["l"]), float(row["c"])]) * float(row.get("v") or 0)


def candle_sequence(rows: Sequence[Mapping[str, Any]]) -> bool:
    if len(rows) < 3:
        return False
    a, b, c = rows[-3:]
    bullish = lambda x: x["c"] > x["o"] and (x["c"] - x["o"]) / max(1, x["h"] - x["l"]) >= 0.4
    pause = lambda x: x["c"] <= x["o"] or abs(x["c"] - x["o"]) / max(1, x["h"] - x["l"]) <= 0.35
    return bullish(a) and pause(b) and bullish(c) and c["c"] > max(a["h"], b["h"])


def pattern_candidates(rows: list[dict[str, Any]], metrics: Mapping[str, Any], catalyst_grade: str) -> list[dict[str, Any]]:
    current = rows[-1]; hist = rows[:-1]; c = float(current["c"])
    ma_values = {n: ma(rows, n) for n in (5, 10, 11, 20, 21, 60, 120)}
    values = [value for value in (ma_values[5], ma_values[10], ma_values[20], ma_values[60]) if value]
    convergence = (max(values) - min(values)) / c if len(values) >= 3 else 1
    prev20_high = max((float(row["h"]) for row in hist[-20:]), default=c)
    prev60_high = max((float(row["h"]) for row in hist[-60:]), default=c)
    breakout_level = max([value for value in (prev20_high, ma_values[60], ma_values[120]) if value], default=prev20_high)
    breakout = c >= breakout_level * 0.99
    new_high = c >= prev60_high * 0.98
    recent_limit = any(b["c"] / a["c"] - 1 >= 0.25 for a, b in zip(hist[-40:-1], hist[-39:]) if a["c"])
    near_long_ma = min((abs(c - value) / c for value in (ma_values[60], ma_values[120]) if value), default=1) <= 0.06
    current_turnover = float(metrics["trade_value"])
    prior_max_turnover = max((turnover(row) for row in hist[-60:]), default=current_turnover)
    prior_huge = prior_max_turnover >= max(50_000_000_000, current_turnover * 1.8)
    last10 = rows[-10:]
    box_range = (max(row["h"] for row in last10) - min(row["l"] for row in last10)) / c if last10 else 1
    short_trend = bool(ma_values[5] and ma_values[11] and c >= max(ma_values[5], ma_values[11]) * 0.985)
    listing_high = max((row["h"] for row in rows[:min(10, len(rows))]), default=c)
    catalyst_ok = catalyst_grade in {"S", "A"}
    small_wick = 0.025 <= float(metrics["upper_wick"]) <= 0.30
    wick_ok = float(metrics["upper_wick"]) <= 0.40
    long_bull = float(metrics["body_ratio"]) >= 0.45 and float(metrics["change_rate"]) >= 5
    turnover500 = current_turnover >= 50_000_000_000
    volume_ratio_ok = float(metrics["volume_ratio"]) >= 1.3
    output: list[dict[str, Any]] = []
    def add(pid: str, name: str, components: list[tuple[str, bool, int]], initial: str, supports: list[str]) -> None:
        output.append({"id": pid, "name": name, "score": sum(weight for _, ok, weight in components if ok), "components": [{"name": n, "pass": ok, "weight": w} for n, ok, w in components], "initial": initial, "supports": supports})
    add("C1", "이평 수렴 돌파", [("거래대금 500억", turnover500, 20), ("장대양봉", long_bull, 20), ("작은 윗꼬리", small_wick, 15), ("이평 수렴", convergence <= 0.10, 15), ("저항 돌파", breakout, 20), ("재료 A/S", catalyst_ok, 10)], "NORMAL", ["돌파선", "5·10·20일선 수렴부"])
    add("C2", "매집 완료·신고가", [("최근 상한가/강봉", recent_limit, 20), ("신고가 근접", new_high, 25), ("5·11일선 유지", short_trend, 15), ("거래량 재유입", volume_ratio_ok, 20), ("재료 A/S", catalyst_ok, 10), ("고가권", metrics["close_location"] >= 0.70, 10)], "NORMAL", ["5·11일선", "박스 상단"])
    add("C3", "매집 미완료·바닥", [("최근 상한가/강봉", recent_limit, 20), ("장기이평 근접", near_long_ma, 25), ("전고점 아래", c < prev60_high * 0.96, 10), ("종가 구조", metrics["close_location"] >= 0.60, 15), ("상승1파 매물", prior_huge, 15), ("재료 A/S", catalyst_ok, 15)], "VERY_SMALL", ["20·60·120일선", "초기 가격대"])
    add("C4", "신규상장 매집", [("상장일 고가 돌파", c >= listing_high * 0.98, 25), ("최근 상한가/강봉", recent_limit, 20), ("장대양봉", long_bull, 20), ("거래대금", turnover500, 15)], "SMALL", ["5·11일선", "상장일 고가·박스 상단"])
    add("C5", "초대량 매물대 박스", [("과거 초대량", prior_huge, 25), ("박스 수렴", box_range <= 0.18, 20), ("전고점 미완전 돌파", not new_high, 15), ("20·60일선 지지", bool((ma_values[20] and c >= ma_values[20] * 0.97) or (ma_values[60] and c >= ma_values[60] * 0.97)), 15), ("윗꼬리 허용", wick_ok, 10), ("재료 A/S", catalyst_ok, 15)], "VERY_SMALL", ["박스 하단", "20·60일선"])
    add("C6", "양음양 저항 돌파", [("양음양", candle_sequence(rows), 30), ("저항 돌파", breakout, 25), ("거래대금", turnover500 or volume_ratio_ok, 20), ("앞선 매집 대리변수", prior_huge or recent_limit, 15), ("재료 A/S", catalyst_ok, 10)], "NORMAL", ["돌파 저항선", "단기·장기 이평 지지"])
    return sorted(output, key=lambda row: row["score"], reverse=True)


def news_references(name: str, code: str, signal_at: dt.datetime, items: Sequence[Mapping[str, Any]]) -> list[str]:
    norm_name = re.sub(r"[^0-9A-Za-z가-힣]+", "", name).casefold()
    rows: list[tuple[dt.datetime, str]] = []
    for item in items:
        at = item.get("at")
        if not isinstance(at, dt.datetime) or at > signal_at or at < signal_at - dt.timedelta(days=14):
            continue
        title = str(item.get("title") or "")
        body = str(item.get("body") or "")
        combined = f"{title} {body}"
        if event_strength(combined) <= 0 or any(term.casefold() in combined.casefold() for term in MECHANICAL_TERMS + NEGATIVE_TERMS):
            continue
        compact = re.sub(r"[^0-9A-Za-z가-힣]+", "", combined).casefold()
        if norm_name and norm_name in compact or code in combined:
            rows.append((at, title))
    rows.sort(reverse=True)
    return [title for _, title in rows[:8]]


def candidate_row(
    base: Mapping[str, Any],
    date_text: str,
    charts: Mapping[str, Sequence[Mapping[str, Any]]],
    indices: Mapping[str, Mapping[str, int]],
    theme_metrics: Mapping[str, Any],
    news_items: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    code = str(base["code"]); rows = [dict(row) for row in charts[code][: indices[code][date_text] + 1]]
    entry = dict(base.get("entry") or {})
    entry_last = num(entry.get("entry_last")) or float(rows[-1]["c"])
    entry_volume = num(entry.get("cum_volume")) or float(rows[-1]["v"])
    rows[-1]["c"] = entry_last; rows[-1]["v"] = entry_volume
    current = rows[-1]; hist = rows[:-1]; price_range = max(1.0, float(current["h"]) - float(current["l"]))
    previous_close = float(hist[-1]["c"]) if hist else float(current["o"])
    avg20_volume = average(float(row["v"]) for row in hist[-20:])
    metrics = {
        "close_location": (entry_last - float(current["l"])) / price_range,
        "upper_wick": (float(current["h"]) - max(float(current["o"]), entry_last)) / price_range,
        "body_ratio": abs(entry_last - float(current["o"])) / price_range,
        "change_rate": (entry_last / previous_close - 1) * 100 if previous_close else 0.0,
        "volume_ratio": entry_volume / avg20_volume if avg20_volume else 0.0,
        "trade_value": entry_last * entry_volume,
    }
    refs = news_references(str(base["name"]), code, dt.datetime.strptime(date_text + "1518", "%Y%m%d%H%M").replace(tzinfo=KST), news_items)
    provisional_grade = "S" if theme_metrics.get("breadth") is not None and theme_metrics.get("breadth") >= 2 / 3 and (theme_metrics.get("leader_rank") or 99) <= 3 else "A" if refs or (theme_metrics.get("breadth") or 0) >= 0.4 else "B"
    patterns = pattern_candidates(rows, metrics, provisional_grade)
    best = patterns[0]
    ma_values = {n: ma(rows, n) for n in (5, 10, 11, 20, 60, 120)}
    prev20_high = max((float(row["h"]) for row in hist[-20:]), default=entry_last)
    support_candidates: list[dict[str, Any]] = []
    for label, value in [("돌파선", prev20_high), ("5일선", ma_values[5]), ("10일선", ma_values[10]), ("11일선", ma_values[11]), ("20일선", ma_values[20]), ("60일선", ma_values[60]), ("120일선", ma_values[120])]:
        if value and value < entry_last:
            support_candidates.append({"name": label, "price": round(value)})
    support_candidates.sort(key=lambda item: item["price"], reverse=True)
    chosen = [item for item in support_candidates if item["name"] in {"20일선", "60일선", "120일선"}][:3] if best["id"] in {"C3", "C5"} else support_candidates[:3]
    invalidation = min((item["price"] for item in chosen), default=round(float(current["l"]))) if chosen else None
    risk = (entry_last - invalidation) / entry_last if invalidation and 0 < invalidation < entry_last else None
    digest = metrics["trade_value"] / max((turnover(row) for row in hist[-60:]), default=metrics["trade_value"] or 1)
    theme_name = str(theme_metrics.get("name") or "")
    political = any(term in theme_name for term in POLITICAL_TERMS)
    checks = [
        {"id": "X_MARKET_CAP", "status": "PASS" if float(base["market_cap"]) >= 50_000_000_000 else "FAIL"},
        {"id": "X_TRADE_VALUE", "status": "PASS" if metrics["trade_value"] >= 20_000_000_000 else "FAIL"},
        {"id": "X_TYPE", "status": "PASS"},
        {"id": "X_PENNY", "status": "PASS" if entry_last >= 1000 else "FAIL"},
        {"id": "X_POLITICAL", "status": "FAIL" if political else "PASS"},
        {"id": "X_RISK", "status": "PASS"},
        {"id": "C_SEQUENCE", "status": "PASS" if candle_sequence(rows) else "WARN"},
    ]
    candidate = {
        "code": code, "name": str(base["name"]), "price": entry_last,
        "trade_value": metrics["trade_value"], "market_cap": float(base["market_cap"]),
        "theme": {"name": theme_metrics.get("name"), "code": theme_metrics.get("code"), "rising": theme_metrics.get("rising"), "falling": (theme_metrics.get("total") - theme_metrics.get("rising")) if theme_metrics.get("total") is not None and theme_metrics.get("rising") is not None else None, "leader_rank": theme_metrics.get("leader_rank")},
        "catalyst": {"grade": provisional_grade, "reason": refs[0] if refs else (f"{theme_name} 관련주 {theme_metrics.get('rising')}/{theme_metrics.get('total')} 상승" if theme_name else "직접 재료 미확인"), "positive_titles": refs},
        "pattern": best, "pattern_candidates": patterns[:3],
        "metrics": {**metrics, "digest_ratio": digest, **{f"ma{n}": ma_values[n] for n in ma_values}, "prev20_high": prev20_high},
        "plan": {"supports": chosen, "invalidation": invalidation, "stop_distance": risk},
        "checks": checks,
        "reconstruction": {"full_day_high_low_proxy": True, "theme_membership_proxy": "current_catalog", "market_cap_proxy": "current_shares_x_historical_price"},
    }
    signal_at = dt.datetime.strptime(date_text + "1518", "%Y%m%d%H%M").replace(tzinfo=KST)
    scored = score_candidate(candidate, signal_at, news_items, theme_metrics=theme_metrics, theme_history=[], proxy_note="20-day reconstruction; current theme membership and full-day high/low proxy")
    gate = target3_gate(scored, entry)
    return candidate, scored, gate


def outcome_row(code: str, next_date: str, entry: Mapping[str, Any]) -> dict[str, Any]:
    rows = quote_rows(code, next_date + "090600")
    return outcome_before_0906(rows, entry_last=num(entry.get("entry_last")), entry_ask=num(entry.get("entry_ask")))


def bootstrap_codes(path: Path | None) -> dict[str, set[str]]:
    if path is None or not path.exists():
        return {}
    try:
        raw = gzip.decompress(base64.b64decode(path.read_text(encoding="ascii")))
        data = json.loads(raw.decode("utf-8"))
    except Exception:
        return {}
    output: dict[str, set[str]] = {}
    for signal in data.get("signals") or []:
        date_text = str(signal.get("signal_date") or "").replace("-", "")
        output[date_text] = {str(row.get("code") or "").zfill(6) for row in signal.get("candidates") or [] if isinstance(row, Mapping)}
    return output


def write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader(); writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--end-date", default="2026-08-31")
    parser.add_argument("--days", type=int, default=20)
    parser.add_argument("--preselect", type=int, default=80)
    parser.add_argument("--workers", type=int, default=24)
    parser.add_argument("--bootstrap")
    args = parser.parse_args()
    out = Path(args.output_dir); out.mkdir(parents=True, exist_ok=True)
    started = dt.datetime.now(KST)

    universe, market_errors = market_universe()
    print(f"market universe {len(universe)}", flush=True)
    charts, chart_errors = fetch_charts(universe, args.workers)
    indices = index_charts(charts)
    dates, next_map = trading_dates(charts, args.end_date, args.days)
    print(f"trading dates {dates[0]}..{dates[-1]} n={len(dates)}", flush=True)

    day_full: dict[str, list[dict[str, Any]]] = {}
    day_pre: dict[str, list[dict[str, Any]]] = {}
    signal_pairs: list[tuple[str, str]] = []
    for date_text in dates:
        features = [item for code in charts if (item := market_features(code, date_text, universe, charts, indices))]
        ranks = source_ranks(features)
        full = [row for row in features if universe[str(row["code"])]["ordinary"]]
        full.sort(key=lambda row: rank_score(ranks.get(str(row["code"]), {})), reverse=True)
        day_full[date_text] = full
        pre = [row for row in full if row["c"] >= 800 and row["market_cap"] >= 30_000_000_000 and row["trade_value"] >= 8_000_000_000][: args.preselect]
        day_pre[date_text] = pre
        signal_pairs.extend((date_text, str(row["code"])) for row in pre)
    quotes, signal_errors = fetch_signal_quotes(signal_pairs, args.workers)

    selected: dict[str, list[dict[str, Any]]] = {}
    for date_text in dates:
        initial_ranks = source_ranks(day_full[date_text])
        selected[date_text] = reranked_shortlist(day_full[date_text], initial_ranks, date_text, quotes, args.preselect)
        print(date_text, "selected", len(selected[date_text]), [row["name"] for row in selected[date_text][:3]], flush=True)

    themes, code_themes, theme_errors = theme_catalog()
    print(f"theme catalog {len(themes)} mapped_codes={len(code_themes)}", flush=True)
    unique_codes = sorted({str(row["code"]) for rows in selected.values() for row in rows})
    oldest = dt.datetime.strptime(dates[0], "%Y%m%d").replace(tzinfo=KST) - dt.timedelta(days=15)
    news, news_errors = fetch_all_news(unique_codes, oldest, min(args.workers, 18))

    outcome_pairs = [(date, str(row["code"]), next_map[date], row.get("entry") or {}) for date, rows in selected.items() for row in rows]
    outcomes: dict[tuple[str, str], dict[str, Any]] = {}
    outcome_errors: list[str] = []
    with cf.ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(outcome_row, code, next_date, entry): (date, code) for date, code, next_date, entry in outcome_pairs}
        for index, future in enumerate(cf.as_completed(futures), 1):
            key = futures[future]
            try:
                outcomes[key] = future.result()
            except Exception as exc:
                outcome_errors.append(f"outcome {key[0]} {key[1]}: {type(exc).__name__}: {exc}")
                outcomes[key] = {}
            if index % 100 == 0:
                print(f"outcomes {index}/{len(outcome_pairs)} errors={len(outcome_errors)}", flush=True)

    flat: list[dict[str, Any]] = []
    detail: list[dict[str, Any]] = []
    known = bootstrap_codes(Path(args.bootstrap) if args.bootstrap else None)
    overlap_rows: list[dict[str, Any]] = []
    for date_text in dates:
        reconstructed_codes = {str(row["code"]) for row in selected[date_text]}
        if date_text in known:
            overlap = len(reconstructed_codes & known[date_text])
            overlap_rows.append({"signal_date": date_text, "original_n": len(known[date_text]), "reconstructed_n": len(reconstructed_codes), "overlap_n": overlap, "overlap_rate": overlap / max(1, len(known[date_text]))})
        for base in selected[date_text]:
            code = str(base["code"])
            theme_metric = historical_theme_metrics(code, date_text, themes, code_themes, charts, indices)
            candidate, scored, gate = candidate_row(base, date_text, charts, indices, theme_metric, news.get(code, []))
            outcome = outcomes.get((date_text, code), {})
            max_return = num(outcome.get("max_executable_return_pct"))
            row = {
                "signal_date": f"{date_text[:4]}-{date_text[4:6]}-{date_text[6:]}",
                "next_date": f"{next_map[date_text][:4]}-{next_map[date_text][4:6]}-{next_map[date_text][6:]}",
                "code": code, "name": base["name"], "market": base["market"],
                "entry_last": num(base.get("entry", {}).get("entry_last")),
                "entry_ask": num(base.get("entry", {}).get("entry_ask")),
                "entry_bid": num(base.get("entry", {}).get("entry_bid")),
                "spread_pct": gate.get("spread_pct"),
                "trade_value": candidate["trade_value"], "market_cap_proxy": candidate["market_cap"],
                "source_rank_score": base.get("source_rank_score"),
                "theme_name": theme_metric.get("name"), "theme_breadth": theme_metric.get("breadth"),
                "theme_rising": theme_metric.get("rising"), "theme_total": theme_metric.get("total"),
                "leader_rank": theme_metric.get("leader_rank"),
                "follower_strong_count": theme_metric.get("follower_strong_count"),
                "follower_turnover": theme_metric.get("follower_turnover"),
                "points_directness": scored.get("evidence", {}).get("directness_points"),
                "points_freshness": scored.get("evidence", {}).get("freshness_points"),
                "evidence_title": scored.get("evidence", {}).get("title"),
                "evidence_at": scored.get("evidence", {}).get("at"),
                "grade": scored.get("grade"), "grade_status": scored.get("grade_status"),
                "comparable_score": scored.get("comparable_score"), "production_score": scored.get("production_score"),
                "coverage": scored.get("coverage"),
                "close_location": scored.get("structure", {}).get("close_location"),
                "upper_wick": scored.get("structure", {}).get("upper_wick"),
                "body_ratio": scored.get("structure", {}).get("body_ratio"),
                "pattern_id": scored.get("structure", {}).get("pattern_id"),
                "pattern_score": scored.get("structure", {}).get("pattern_score"),
                "digest_ratio": scored.get("structure", {}).get("digest_ratio"),
                "risk_rate": scored.get("structure", {}).get("risk_rate"),
                "hard_reject": scored.get("hard_reject"),
                "target3_status": gate.get("status"), "target3_eligible": gate.get("eligible"),
                "target3_lane": gate.get("lane"), "target3_size_band": gate.get("size_band"),
                "target3_blockers": "|".join(gate.get("blockers") or []),
                "first_bid": outcome.get("first_bid"), "max_bid": outcome.get("max_bid"),
                "last_bid": outcome.get("last_bid"), "min_bid": outcome.get("min_bid"),
                "max_executable_return_pct": max_return,
                "last_executable_return_pct": outcome.get("last_executable_return_pct"),
                "hit_3_exec": None if max_return is None else max_return >= 3.0,
                "open_observations": outcome.get("open_observations"),
                "candidate_proxy": True, "theme_membership_proxy": "current_catalog",
                "full_day_high_low_proxy": True,
            }
            flat.append(row)
            detail.append({"row": row, "candidate": candidate, "score": scored, "target3": gate, "outcome": outcome})

    finished = dt.datetime.now(KST)
    metadata = {
        "version": "largo-target3-20day-backfill-v1",
        "rule_version": "largo-target3-v1-unchanged",
        "started_at": started.isoformat(), "finished_at": finished.isoformat(),
        "signal_dates": [f"{d[:4]}-{d[4:6]}-{d[6:]}" for d in dates],
        "date_count": len(dates), "candidate_rows": len(flat),
        "market_universe": len(universe), "chart_success": len(charts),
        "theme_count": len(themes), "unique_candidate_codes": len(unique_codes),
        "data_limits": [
            "candidate universe reconstructed from historical Naver daily bars and exact 15:18 quotes",
            "historical market cap uses current share-count proxy",
            "theme membership uses the current Naver theme catalog; member returns use each historical date",
            "signal-day high and low are final daily values and may include 15:18-15:30 movements",
            "historical market-alert status was unavailable",
        ],
        "errors": {
            "market": market_errors, "chart": chart_errors, "signal": signal_errors,
            "theme": theme_errors, "news": news_errors, "outcome": outcome_errors,
        },
        "candidate_overlap": overlap_rows,
    }
    write_csv(out / "target3_20day_rows.csv", flat)
    write_csv(out / "candidate_universe_overlap.csv", overlap_rows)
    (out / "target3_20day_detail.json").write_text(json.dumps(detail, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    (out / "target3_20day_metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"dates": len(dates), "rows": len(flat), "eligible": sum(bool(row["target3_eligible"]) for row in flat), "hits": sum(bool(row["hit_3_exec"]) for row in flat), "errors": {k: len(v) for k, v in metadata["errors"].items()}}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
