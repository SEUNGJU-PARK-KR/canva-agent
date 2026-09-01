#!/usr/bin/env python3
"""Deterministic Largo-style material grade and pre-09:06 validation helpers.

The module does not use a generative model. It scores only evidence available at the
signal timestamp: company-specific news/notices, theme breadth, leadership, follower
turnover, breadth persistence, closing structure, and a structural stop distance.

A historical proxy score is normalized over fields that were actually preserved. Live
production scores require high data coverage and remain separate from eligibility gates.
"""
from __future__ import annotations

import datetime as dt
import html as html_lib
import json
import math
import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

KST = dt.timezone(dt.timedelta(hours=9))
UTC = dt.timezone.utc
VERSION = "largo-material-0906-v1"
NAVER_STOCK = "https://stock.naver.com"
NAVER_FINANCE = "https://finance.naver.com"
UA = "Mozilla/5.0 (compatible; LargoMaterial0906/1.0; read-only)"

NEGATIVE_TERMS = (
    "유상증자", "전환사채", "신주인수권", "횡령", "배임", "거래정지", "상장폐지",
    "관리종목", "감사의견", "적자전환", "불성실공시", "회생", "파산", "투자주의",
    "투자경고", "투자위험", "계약 해지", "공급계약 해지", "철회", "단기과열",
)
MECHANICAL_TERMS = (
    "주식선물", "주식옵션", "가격제한폭", "기업설명회", "조회공시", "매매거래정지",
    "권리락", "배당락", "추가상장", "변경상장", "재상장", "지정해제", "지정예고",
)
STRONG_EVENT_TERMS = (
    "단일판매", "공급계약", "수주", "허가", "승인", "임상", "기술이전", "마일스톤",
    "흑자전환", "자기주식 취득", "자사주", "합병", "인수", "양산", "신규시설투자",
    "시설투자", "특허", "국책", "배당 결정", "판매계약", "독점", "상용화",
)
MODERATE_EVENT_TERMS = (
    "실적", "영업이익", "매출", "신제품", "개발", "투자", "수혜", "계약", "솔루션",
    "공장", "공급", "출시", "증설", "생산", "자원개발", "파트너십", "협력",
)

WEIGHTS: dict[str, float] = {
    "directness": 18,
    "freshness": 10,
    "theme_breadth": 14,
    "leadership": 8,
    "follower_turnover": 5,
    "breadth_persistence": 10,
    "close_location": 9,
    "upper_wick": 7,
    "body": 6,
    "close_sequence": 4,
    "pattern": 4,
    "digestion": 3,
    "risk": 2,
}
assert abs(sum(WEIGHTS.values()) - 100.0) < 1e-9

HARD_FAIL_IDS = {
    "X_RISK", "X_TYPE", "X_POLITICAL", "X_PENNY", "X_MARKET_CAP", "X_TRADE_VALUE",
}


def num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(str(value).replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def normalize(value: Any) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]+", "", str(value or "")).casefold()


def parse_datetime(value: Any, *, source: str = "news") -> dt.datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        stamp = float(value)
        if stamp > 10_000_000_000:
            stamp /= 1000
        try:
            return dt.datetime.fromtimestamp(stamp, tz=KST)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    if re.fullmatch(r"20\d{10}", text):
        try:
            return dt.datetime.strptime(text, "%Y%m%d%H%M").replace(tzinfo=KST)
        except ValueError:
            return None
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        match = re.search(
            r"(20\d{2})[-./년]\s*(\d{1,2})[-./월]\s*(\d{1,2})(?:[T\s일]\s*(\d{1,2})[:시]\s*(\d{1,2})?)?",
            text,
        )
        if not match:
            return None
        year, month, day, hour, minute = match.groups()
        parsed = dt.datetime(int(year), int(month), int(day), int(hour or 0), int(minute or 0))
    if parsed.tzinfo is None:
        # The archived notice endpoint exposed UTC-like ISO values. Compact news values are KST.
        parsed = parsed.replace(tzinfo=UTC if source == "notice" and "T" in text else KST)
    return parsed.astimezone(KST)


def fetch_bytes(url: str, *, timeout: int = 20, retries: int = 3) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Referer": NAVER_STOCK + "/",
            "Accept": "application/json,text/plain,text/html,*/*",
        },
    )
    last: Exception | None = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except Exception as exc:  # pragma: no cover - exercised in GitHub Actions
            last = exc
            time.sleep(0.8 * (attempt + 1))
    if last is None:
        raise RuntimeError("request failed without an exception")
    raise last


def fetch_json(url: str, *, timeout: int = 20, retries: int = 3) -> Any:
    return json.loads(fetch_bytes(url, timeout=timeout, retries=retries).decode("utf-8"))


def walk_rows(payload: Any) -> list[Mapping[str, Any]]:
    """Return the first plausible record list in a Naver JSON payload."""
    queue: list[Any] = [payload]
    while queue:
        value = queue.pop(0)
        if isinstance(value, list):
            rows = [row for row in value if isinstance(row, Mapping)]
            if rows:
                return rows
        elif isinstance(value, Mapping):
            for key in ("stocks", "items", "datas", "data", "result", "contents", "clusters"):
                if key in value:
                    queue.append(value[key])
            for child in value.values():
                if isinstance(child, (Mapping, list)):
                    queue.append(child)
    return []


def flatten_news(payload_box: Mapping[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []

    def payload_of(key: str) -> Any:
        box = payload_box.get(key)
        if isinstance(box, Mapping):
            return box.get("payload", box)
        return box

    def walk(value: Any, source: str, depth: int = 0) -> None:
        if depth > 8:
            return
        if isinstance(value, Mapping):
            title = value.get("title") or value.get("subject")
            stamp = value.get("datetime") or value.get("date") or value.get("publishedAt")
            if title and stamp:
                result.append({
                    "title": str(title),
                    "body": str(value.get("body") or value.get("comment") or value.get("summary") or ""),
                    "at": parse_datetime(stamp, source=source),
                    "source": source,
                })
            for child in value.values():
                if isinstance(child, (Mapping, list, tuple)):
                    walk(child, source, depth + 1)
        elif isinstance(value, (list, tuple)):
            for child in value[:250]:
                walk(child, source, depth + 1)

    walk(payload_of("news"), "news")
    walk(payload_of("notice"), "notice")
    dedup: dict[tuple[str, str], dict[str, Any]] = {}
    for item in result:
        at = item.get("at")
        dedup[(item["title"], at.isoformat() if isinstance(at, dt.datetime) else "")] = item
    return list(dedup.values())


def title_tokens(value: Any) -> set[str]:
    stop = {"관련주", "주식회사", "주식", "종목", "시장", "공시", "기준", "연결재무제표"}
    return {token for token in re.findall(r"[0-9A-Za-z가-힣]{2,}", str(value or "").casefold()) if token not in stop}


def meaningful_title_match(reference: str, title: str) -> bool:
    left, right = normalize(reference), normalize(title)
    if not left or not right:
        return False
    if min(len(left), len(right)) >= 10 and (left in right or right in left):
        return True
    shared_event = any(normalize(term) in left and normalize(term) in right for term in STRONG_EVENT_TERMS + MODERATE_EVENT_TERMS)
    return shared_event and bool(title_tokens(reference) & title_tokens(title))


def event_strength(text: str) -> int:
    lowered = text.casefold()
    if any(term.casefold() in lowered for term in NEGATIVE_TERMS):
        return -1
    if any(term.casefold() in lowered for term in MECHANICAL_TERMS):
        return 0
    if any(term.casefold() in lowered for term in STRONG_EVENT_TERMS):
        return 2
    if any(term.casefold() in lowered for term in MODERATE_EVENT_TERMS):
        return 1
    return 0


def event_significance(text: str) -> dict[str, Any]:
    """Extract a conservative event-size proxy from a title or notice snippet."""
    revenue_ratio = None
    for pattern in (
        r"(?:최근\s*)?(?:매출액|매출)\s*(?:대비)?\s*[:：]?\s*(\d+(?:\.\d+)?)\s*%",
        r"(\d+(?:\.\d+)?)\s*%\s*(?:규모|수준|비중)",
    ):
        match = re.search(pattern, text, flags=re.I)
        if match:
            revenue_ratio = num(match.group(1))
            break

    amount_krw = None
    amount_matches = re.findall(r"(\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*(조|억|만)?\s*원", text)
    converted: list[float] = []
    units = {"조": 1_000_000_000_000, "억": 100_000_000, "만": 10_000, "": 1}
    for value, unit in amount_matches:
        parsed = num(value)
        if parsed is not None:
            converted.append(parsed * units.get(unit, 1))
    if converted:
        amount_krw = max(converted)

    if revenue_ratio is not None and revenue_ratio >= 10:
        magnitude = "HIGH"
        bonus = 3.0
    elif revenue_ratio is not None and revenue_ratio >= 5:
        magnitude = "MEDIUM"
        bonus = 2.0
    elif amount_krw is not None and amount_krw >= 100_000_000_000:
        magnitude = "HIGH"
        bonus = 3.0
    elif amount_krw is not None and amount_krw >= 30_000_000_000:
        magnitude = "MEDIUM"
        bonus = 2.0
    elif amount_krw is not None:
        magnitude = "LOW"
        bonus = 1.0
    else:
        magnitude = "UNKNOWN"
        bonus = 0.0
    return {
        "revenue_ratio_pct": revenue_ratio,
        "amount_krw": amount_krw,
        "magnitude": magnitude,
        "magnitude_bonus": bonus,
    }


def catalyst_evidence(candidate: Mapping[str, Any], signal_at: dt.datetime, items: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    catalyst = candidate.get("catalyst") if isinstance(candidate.get("catalyst"), Mapping) else {}
    reason = str(catalyst.get("reason") or "")
    references = [str(x).strip() for x in catalyst.get("positive_titles") or [] if str(x).strip()]
    company = normalize(candidate.get("name"))
    code = str(candidate.get("code") or "").zfill(6)
    theme = candidate.get("theme") if isinstance(candidate.get("theme"), Mapping) else {}
    theme_name = normalize(theme.get("name"))
    theme_mode = bool(theme) or "관련주" in reason

    positives: list[tuple[int, int, dt.datetime, Mapping[str, Any], bool, bool]] = []
    negatives: list[tuple[dt.datetime, Mapping[str, Any]]] = []
    for item in items:
        at = item.get("at")
        if not isinstance(at, dt.datetime) or at > signal_at + dt.timedelta(minutes=1):
            continue
        age_hours = max(0.0, (signal_at - at).total_seconds() / 3600)
        title = str(item.get("title") or "")
        body = str(item.get("body") or "")
        combined = f"{title} {body}"
        strength = event_strength(combined)
        combined_norm = normalize(combined)
        company_named = bool(company and company in combined_norm) or bool(code and code in combined)
        reference_match = any(meaningful_title_match(ref, title) for ref in references)
        reason_match = meaningful_title_match(reason, title)
        theme_named = bool(theme_name and theme_name in combined_norm)

        if strength < 0 and age_hours <= 168 and company_named:
            negatives.append((at, item))
            continue
        if strength <= 0 or age_hours > 336:
            continue

        match_points = (2 if company_named else 0) + (2 if reference_match else 0) + (1 if reason_match else 0) + (1 if theme_named else 0)
        if theme_mode:
            eligible = match_points >= 3
        else:
            eligible = company_named and (reference_match or reason_match or strength >= 2)
        if eligible:
            positives.append((strength, match_points, at, item, company_named, theme_named))

    if negatives:
        at, item = max(negatives, key=lambda pair: pair[0])
        return {
            "negative": True,
            "directness_points": 0.0,
            "freshness_points": 0.0,
            "hours": round((signal_at - at).total_seconds() / 3600, 1),
            "title": str(item.get("title") or ""),
            "at": at.isoformat(),
            "source": str(item.get("source") or ""),
            "reason": "신호 전에 회사별 부정 재료가 확인됨",
            "observed": True,
        }

    if not positives:
        return {
            "negative": False,
            "directness_points": 0.0,
            "freshness_points": 0.0,
            "hours": None,
            "title": None,
            "at": None,
            "source": None,
            "reason": "신호 전에 확인 가능한 직접·테마 재료 원문을 찾지 못함",
            "observed": True,
        }

    strength, match_points, at, item, company_named, theme_named = max(positives, key=lambda x: (x[0], x[1], x[2]))
    hours = max(0.0, (signal_at - at).total_seconds() / 3600)
    source = str(item.get("source") or "")
    combined = f"{item.get('title') or ''} {item.get('body') or ''}"
    significance = event_significance(combined)
    magnitude_bonus = num(significance.get("magnitude_bonus")) or 0.0
    if strength >= 2 and company_named:
        base = 15.0 if source == "notice" else 13.0
        directness = min(18.0, base + magnitude_bonus)
    elif strength >= 1 and company_named:
        base = 10.0 if source == "notice" else 8.0
        directness = min(15.0, base + magnitude_bonus)
    elif theme_named or match_points >= 3:
        directness = 8.0
    else:
        directness = 5.0
    freshness = 10.0 if hours <= 6 else 9.0 if hours <= 24 else 7.0 if hours <= 72 else 3.0 if hours <= 168 else 0.0
    if hours > 168:
        directness = min(directness, 4.0)
    return {
        "negative": False,
        "directness_points": directness,
        "freshness_points": freshness,
        "hours": round(hours, 1),
        "title": str(item.get("title") or ""),
        "at": at.isoformat(),
        "source": source,
        "direct_benefit": bool(company_named),
        "event_strength": strength,
        **significance,
        "reason": "신호 시각 전에 동일 사건으로 확인된 재료",
        "observed": True,
    }


def parse_theme_reason(candidate: Mapping[str, Any]) -> tuple[int | None, int | None, float | None]:
    theme = candidate.get("theme") if isinstance(candidate.get("theme"), Mapping) else {}
    rising = num(theme.get("rising"))
    falling = num(theme.get("falling"))
    catalyst = candidate.get("catalyst") if isinstance(candidate.get("catalyst"), Mapping) else {}
    match = re.search(r"(\d+)\s*/\s*(\d+)\s*상승", str(catalyst.get("reason") or ""))
    if match:
        r, total = int(match.group(1)), int(match.group(2))
        return r, total, r / total if total else None
    if rising is not None and falling is not None:
        total = int(rising + falling)
        return int(rising), total, rising / total if total else None
    return None, None, None


def code_of(row: Mapping[str, Any]) -> str | None:
    for key in ("itemCode", "code", "stockCode", "symbol"):
        value = row.get(key)
        if value is not None and str(value).strip():
            match = re.search(r"\d{6}", str(value))
            return match.group(0) if match else str(value).strip().zfill(6)
    return None


def name_of(row: Mapping[str, Any]) -> str:
    for key in ("stockName", "itemName", "name", "korName"):
        if row.get(key):
            return str(row[key])
    return ""


def change_of(row: Mapping[str, Any]) -> float | None:
    for key in ("fluctuationsRatio", "changeRate", "changeRatio", "priceChangeRate", "compareToPreviousClosePrice"):
        value = num(row.get(key))
        if value is not None:
            return value
    return None


def turnover_of(row: Mapping[str, Any]) -> float | None:
    for key in ("tradeAmount", "accumulatedTradingValueRaw", "tradingValue", "accumulatedTradingValue", "tradeValue"):
        value = num(row.get(key))
        if value is not None:
            # Some Naver lists expose tradeAmount in millions of KRW.
            return value * 1_000_000 if value < 10_000_000 else value
    return None


def theme_metrics_from_candidate(candidate: Mapping[str, Any]) -> dict[str, Any]:
    theme = candidate.get("theme") if isinstance(candidate.get("theme"), Mapping) else None
    rising, total, breadth = parse_theme_reason(candidate)
    rank = num(theme.get("leader_rank")) if theme else None
    return {
        "name": str(theme.get("name") or "") if theme else None,
        "code": str(theme.get("code") or "") if theme else None,
        "rising": rising,
        "total": total,
        "breadth": breadth,
        "leader_rank": int(rank) if rank is not None else None,
        "follower_strong_count": None,
        "follower_turnover": None,
        "follower_turnover_ratio": None,
        "members": None,
        "observed_breadth": breadth is not None,
        "observed_leadership": rank is not None,
        "observed_followers": False,
    }


def theme_metrics_from_members(candidate: Mapping[str, Any], members: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    normalized: list[dict[str, Any]] = []
    for row in members:
        code = code_of(row) if not row.get("code") else str(row.get("code")).zfill(6)
        if not code:
            continue
        normalized.append({
            "code": code,
            "name": str(row.get("name") or name_of(row)),
            "change_rate": num(row.get("change_rate")) if row.get("change_rate") is not None else change_of(row),
            "trade_value": num(row.get("trade_value")) if row.get("trade_value") is not None else turnover_of(row),
        })
    if not normalized:
        base = theme_metrics_from_candidate(candidate)
        base["observed_followers"] = False
        return base

    rising = sum(1 for row in normalized if (row.get("change_rate") or 0) > 0)
    total = len(normalized)
    breadth = rising / total if total else None
    ranked = sorted(
        normalized,
        key=lambda row: (row.get("change_rate") if row.get("change_rate") is not None else -999.0,
                         row.get("trade_value") if row.get("trade_value") is not None else -1.0),
        reverse=True,
    )
    candidate_code = str(candidate.get("code") or "").zfill(6)
    rank = next((index for index, row in enumerate(ranked, 1) if row["code"] == candidate_code), None)
    top3 = ranked[:3]
    followers = top3[1:3]
    strong_followers = [
        row for row in followers
        if (row.get("change_rate") or 0) >= 2.0 and (row.get("trade_value") or 0) >= 10_000_000_000
    ]
    follower_turnover = sum(row.get("trade_value") or 0 for row in followers)
    top3_turnover = sum(row.get("trade_value") or 0 for row in top3)
    ratio = follower_turnover / top3_turnover if top3_turnover > 0 else None
    theme = candidate.get("theme") if isinstance(candidate.get("theme"), Mapping) else {}
    return {
        "name": str(theme.get("name") or ""),
        "code": str(theme.get("code") or ""),
        "rising": rising,
        "total": total,
        "breadth": breadth,
        "leader_rank": rank,
        "follower_strong_count": len(strong_followers),
        "follower_turnover": follower_turnover,
        "follower_turnover_ratio": ratio,
        "members": ranked,
        "observed_breadth": True,
        "observed_leadership": rank is not None,
        "observed_followers": True,
    }


def theme_metrics_from_payload(candidate: Mapping[str, Any], payload: Any) -> dict[str, Any]:
    rows = walk_rows(payload)
    members = [
        {
            "code": code_of(row),
            "name": name_of(row),
            "change_rate": change_of(row),
            "trade_value": turnover_of(row),
        }
        for row in rows if code_of(row)
    ]
    return theme_metrics_from_members(candidate, members)


def persistence_metrics(theme_code: str | None, snapshots: Sequence[Mapping[str, Any]], final_theme: Mapping[str, Any]) -> dict[str, Any]:
    if not theme_code:
        return {"observed": False, "points": None, "stable": None, "faded": None, "stages": []}
    matched: list[dict[str, Any]] = []
    for snapshot in snapshots:
        themes = snapshot.get("themes") if isinstance(snapshot.get("themes"), Mapping) else {}
        value = themes.get(str(theme_code))
        if isinstance(value, Mapping):
            matched.append({"stage": snapshot.get("stage"), "at": snapshot.get("at"), **dict(value)})
    # Include the final snapshot if the caller has not appended it yet.
    if final_theme.get("breadth") is not None:
        matched.append({"stage": "15:18", "at": None, **dict(final_theme)})
    dedup: dict[tuple[Any, Any], dict[str, Any]] = {}
    for item in matched:
        dedup[(item.get("stage"), item.get("breadth"))] = item
    matched = list(dedup.values())
    if len(matched) < 2:
        return {"observed": False, "points": None, "stable": None, "faded": None, "stages": matched}
    breadths = [num(item.get("breadth")) for item in matched]
    breadths = [value for value in breadths if value is not None]
    if len(breadths) < 2:
        return {"observed": False, "points": None, "stable": None, "faded": None, "stages": matched}
    first, final = breadths[0], breadths[-1]
    drawdown = max(breadths) - final
    faded = final < 0.40 or drawdown > 0.20 or final < first - 0.15
    stable = not faded and final >= 0.55
    if faded:
        points = 0.0
    elif len(breadths) >= 3 and stable:
        points = 10.0
    elif stable:
        points = 7.0
    else:
        points = 3.0
    return {
        "observed": True,
        "points": points,
        "stable": stable,
        "faded": faded,
        "first_breadth": first,
        "final_breadth": final,
        "drawdown": drawdown,
        "stages": matched,
    }


def check_status(candidate: Mapping[str, Any], check_id: str) -> str | None:
    for item in candidate.get("checks") or []:
        if isinstance(item, Mapping) and str(item.get("id")) == check_id:
            return str(item.get("status") or "").upper()
    return None


def candidate_metric(candidate: Mapping[str, Any], *keys: str) -> float | None:
    metrics = candidate.get("metrics") if isinstance(candidate.get("metrics"), Mapping) else {}
    for key in keys:
        value = num(candidate.get(key))
        if value is not None:
            return value
        value = num(metrics.get(key))
        if value is not None:
            return value
    return None


def risk_rate(candidate: Mapping[str, Any]) -> float | None:
    automation = candidate.get("automation_356") if isinstance(candidate.get("automation_356"), Mapping) else {}
    plan = automation.get("risk_plan") if isinstance(automation.get("risk_plan"), Mapping) else {}
    value = num(plan.get("risk_rate"))
    if value is not None:
        return value
    raw_plan = candidate.get("plan") if isinstance(candidate.get("plan"), Mapping) else {}
    return num(raw_plan.get("stop_distance"))


def _set_component(components: dict[str, float], observed: dict[str, bool], key: str, value: float | None, is_observed: bool) -> None:
    observed[key] = bool(is_observed)
    components[key] = max(0.0, min(WEIGHTS[key], float(value or 0.0))) if is_observed else 0.0


def score_candidate(
    candidate: Mapping[str, Any],
    signal_at: dt.datetime,
    news_items: Sequence[Mapping[str, Any]],
    *,
    theme_metrics: Mapping[str, Any] | None = None,
    theme_history: Sequence[Mapping[str, Any]] = (),
    proxy_note: str | None = None,
) -> dict[str, Any]:
    theme_metrics = dict(theme_metrics or theme_metrics_from_candidate(candidate))
    evidence = catalyst_evidence(candidate, signal_at, news_items)
    persist = persistence_metrics(str(theme_metrics.get("code") or "") or None, theme_history, theme_metrics)
    components: dict[str, float] = {}
    observed: dict[str, bool] = {}

    _set_component(components, observed, "directness", evidence.get("directness_points"), bool(evidence.get("observed")))
    _set_component(components, observed, "freshness", evidence.get("freshness_points"), bool(evidence.get("observed")))

    breadth = num(theme_metrics.get("breadth"))
    rising = num(theme_metrics.get("rising"))
    breadth_points = 0.0
    if breadth is not None and rising is not None:
        if breadth >= 0.80 and rising >= 5:
            breadth_points = 14.0
        elif breadth >= (2 / 3) and rising >= 5:
            breadth_points = 12.0
        elif breadth >= 0.50 and rising >= 3:
            breadth_points = 8.0
        elif breadth >= 0.40 and rising >= 2:
            breadth_points = 5.0
    _set_component(components, observed, "theme_breadth", breadth_points, bool(theme_metrics.get("observed_breadth")))

    rank = num(theme_metrics.get("leader_rank"))
    leadership_points = 0.0
    if rank is not None:
        leadership_points = 8.0 if rank <= 1 else 6.0 if rank <= 2 else 3.0 if rank <= 3 else 0.0
    elif num(evidence.get("directness_points")) and float(evidence["directness_points"]) >= 15:
        leadership_points = 5.0
    leadership_observed = bool(theme_metrics.get("observed_leadership")) or leadership_points == 5.0
    _set_component(components, observed, "leadership", leadership_points, leadership_observed)

    strong_followers = num(theme_metrics.get("follower_strong_count"))
    follower_turnover = num(theme_metrics.get("follower_turnover"))
    follower_ratio = num(theme_metrics.get("follower_turnover_ratio"))
    follower_points = 0.0
    if strong_followers is not None and follower_turnover is not None:
        if strong_followers >= 2 and follower_turnover >= 30_000_000_000 and (follower_ratio is None or follower_ratio >= 0.25):
            follower_points = 5.0
        elif strong_followers >= 1 and follower_turnover >= 10_000_000_000:
            follower_points = 3.0
        elif follower_turnover > 0:
            follower_points = 1.0
    _set_component(components, observed, "follower_turnover", follower_points, bool(theme_metrics.get("observed_followers")))
    _set_component(components, observed, "breadth_persistence", num(persist.get("points")), bool(persist.get("observed")))

    close_location = candidate_metric(candidate, "close_location")
    close_points = 0.0 if close_location is None else 9.0 if close_location >= 0.90 else 7.0 if close_location >= 0.80 else 5.0 if close_location >= 0.70 else 2.0 if close_location >= 0.60 else 0.0
    _set_component(components, observed, "close_location", close_points, close_location is not None)

    upper_wick = candidate_metric(candidate, "upper_wick", "upper_wick_ratio")
    wick_points = 0.0 if upper_wick is None else 7.0 if upper_wick <= 0.05 else 5.0 if upper_wick <= 0.10 else 2.0 if upper_wick <= 0.20 else 0.0
    _set_component(components, observed, "upper_wick", wick_points, upper_wick is not None)

    body = candidate_metric(candidate, "body_ratio")
    body_points = 0.0 if body is None else 6.0 if body >= 0.65 else 4.0 if body >= 0.45 else 2.0 if body >= 0.25 else 0.0
    _set_component(components, observed, "body", body_points, body is not None)

    sequence_status = check_status(candidate, "C_SEQUENCE")
    sequence_points = 4.0 if sequence_status == "PASS" else 2.0 if sequence_status in {"WARN", "MISSING", "UNKNOWN"} else 0.0
    _set_component(components, observed, "close_sequence", sequence_points, sequence_status is not None)

    pattern = candidate.get("pattern") if isinstance(candidate.get("pattern"), Mapping) else {}
    pattern_score = num(pattern.get("score"))
    pattern_points = 0.0 if pattern_score is None else 4.0 if pattern_score >= 90 else 3.0 if pattern_score >= 80 else 2.0 if pattern_score >= 70 else 0.0
    _set_component(components, observed, "pattern", pattern_points, pattern_score is not None)

    digestion = candidate_metric(candidate, "digest_ratio")
    digestion_points = 0.0 if digestion is None else 3.0 if digestion >= 1.0 else 2.0 if digestion >= 0.60 else 1.0 if digestion >= 0.30 else 0.0
    _set_component(components, observed, "digestion", digestion_points, digestion is not None)

    risk = risk_rate(candidate)
    risk_points = 0.0 if risk is None else 2.0 if risk <= 0.02 else 1.5 if risk <= 0.04 else 1.0 if risk <= 0.06 else 0.0
    _set_component(components, observed, "risk", risk_points, risk is not None)

    raw_score = round(sum(components.values()), 2)
    available_weight = round(sum(WEIGHTS[key] for key, value in observed.items() if value), 2)
    comparable_score = round(raw_score / available_weight * 100, 2) if available_weight > 0 else None
    coverage = round(available_weight / 100, 3)

    failed_ids = [
        str(item.get("id")) for item in candidate.get("checks") or []
        if isinstance(item, Mapping) and str(item.get("status") or "").upper() == "FAIL"
    ]
    hard_reject = bool(evidence.get("negative")) or any(check_id in HARD_FAIL_IDS for check_id in failed_ids)

    directness = num(evidence.get("directness_points")) or 0.0
    freshness = num(evidence.get("freshness_points")) or 0.0
    follower_available = bool(theme_metrics.get("observed_followers"))
    persistence_available = bool(persist.get("observed"))
    faded = bool(persist.get("faded")) if persistence_available else False

    s_core = (
        not hard_reject and not faded and breadth is not None and rising is not None
        and breadth >= (2 / 3) and rising >= 5 and rank is not None and rank <= 3
        and (freshness >= 7 or directness >= 15)
    )
    s_followers = not follower_available or ((strong_followers or 0) >= 2 and follower_points >= 3)
    s_persistence = not persistence_available or (num(persist.get("points")) or 0) >= 7
    direct_a = not hard_reject and directness >= 12 and freshness >= 7
    theme_a = (
        not hard_reject and not faded and breadth is not None and rising is not None
        and breadth >= 0.40 and rising >= 2 and rank is not None and rank <= 2
        and (not follower_available or (strong_followers or 0) >= 1)
    )
    if hard_reject:
        grade = "REJECT"
    elif s_core and s_followers and s_persistence:
        grade = "S"
    elif direct_a or theme_a:
        grade = "A"
    else:
        grade = "B"

    missing_grade_fields = []
    if s_core and not follower_available:
        missing_grade_fields.append("2·3등주 거래대금")
    if s_core and not persistence_available:
        missing_grade_fields.append("14:53→15:10→15:18 확산 유지")
    grade_status = "PROVISIONAL" if missing_grade_fields else "CONFIRMED"

    production_score = comparable_score if coverage >= 0.85 and grade in {"S", "A"} and not hard_reject else None
    return {
        "version": VERSION,
        "code": str(candidate.get("code") or "").zfill(6),
        "name": str(candidate.get("name") or ""),
        "raw_score": raw_score,
        "comparable_score": comparable_score,
        "production_score": production_score,
        "coverage": coverage,
        "available_weight": available_weight,
        "grade": grade,
        "grade_status": grade_status,
        "grade_missing": missing_grade_fields,
        "hard_reject": hard_reject,
        "failed_ids": failed_ids,
        "components": components,
        "observed": observed,
        "evidence": evidence,
        "theme": theme_metrics,
        "persistence": persist,
        "structure": {
            "close_location": close_location,
            "upper_wick": upper_wick,
            "body_ratio": body,
            "sequence_status": sequence_status,
            "pattern_id": str(pattern.get("id") or ""),
            "pattern_score": pattern_score,
            "digest_ratio": digestion,
            "risk_rate": risk,
        },
        "proxy_note": proxy_note,
    }


def parse_legacy_rows(raw_or_rows: bytes | str | Sequence[Sequence[str]]) -> list[list[str]]:
    if isinstance(raw_or_rows, (list, tuple)):
        return [[str(value) for value in row] for row in raw_or_rows]
    if isinstance(raw_or_rows, bytes):
        text = raw_or_rows.decode("euc-kr", errors="replace")
    else:
        text = str(raw_or_rows)
    rows: list[list[str]] = []
    for block in re.findall(r"<tr[^>]*>(.*?)</tr>", text, flags=re.I | re.S):
        time_match = re.search(r"(?:gray03[^>]*>|>)\s*(\d{2}:\d{2})\s*</span>", block, flags=re.I | re.S)
        if not time_match:
            continue
        values = [
            html_lib.unescape(re.sub(r"<[^>]+>", "", value)).strip()
            for value in re.findall(r'<span[^>]*class="[^"]*tah p11[^"]*"[^>]*>(.*?)</span>', block, flags=re.I | re.S)
        ]
        rows.append([time_match.group(1), *[re.sub(r"\s+", " ", value) for value in values]])
    return rows


def legacy_quote(row: Sequence[str]) -> dict[str, Any] | None:
    if len(row) < 2 or not re.fullmatch(r"\d{2}:\d{2}", str(row[0])):
        return None
    return {
        "time": str(row[0]),
        "last": num(row[1]) if len(row) > 1 else None,
        "ask": num(row[3]) if len(row) > 3 else None,
        "bid": num(row[4]) if len(row) > 4 else None,
        "cum_volume": num(row[5]) if len(row) > 5 else None,
        "minute_volume": num(row[6]) if len(row) > 6 else None,
    }


def entry_from_legacy(rows: Sequence[Sequence[str]]) -> dict[str, Any]:
    quotes = [quote for row in rows if (quote := legacy_quote(row)) and quote.get("last")]
    if not quotes:
        return {"entry_time": None, "entry_last": None, "entry_ask": None, "entry_bid": None}
    quote = quotes[0]
    return {
        "entry_time": quote["time"],
        "entry_last": quote["last"],
        "entry_ask": quote.get("ask") or quote["last"],
        "entry_bid": quote.get("bid"),
        "signal_observations": len(quotes),
    }


def outcome_before_0906(rows: Sequence[Sequence[str]], *, entry_last: float | None, entry_ask: float | None) -> dict[str, Any]:
    quotes = [quote for row in rows if (quote := legacy_quote(row)) and "09:00" <= quote["time"] < "09:06" and quote.get("last")]
    quotes.sort(key=lambda item: item["time"])
    if not quotes or not entry_last or not entry_ask:
        return {
            "first_time": None, "last_time": None, "max_last": None, "max_bid": None,
            "last_last": None, "last_bid": None, "max_trade_return_pct": None,
            "max_executable_return_pct": None, "last_executable_return_pct": None,
            "positive_exec": None, "hit_0_5_exec": None, "hit_1_exec": None, "hit_2_exec": None,
            "open_observations": len(quotes),
        }
    last_prices = [float(quote["last"]) for quote in quotes if quote.get("last")]
    bid_prices = [float(quote.get("bid") or quote["last"]) for quote in quotes]
    max_last = max(last_prices)
    max_bid = max(bid_prices)
    last_quote = quotes[-1]
    last_bid = float(last_quote.get("bid") or last_quote["last"])
    max_trade_return = (max_last / entry_last - 1) * 100
    max_exec_return = (max_bid / entry_ask - 1) * 100
    last_exec_return = (last_bid / entry_ask - 1) * 100
    return {
        "first_time": quotes[0]["time"],
        "first_last": quotes[0]["last"],
        "first_bid": quotes[0].get("bid") or quotes[0]["last"],
        "last_time": last_quote["time"],
        "last_last": last_quote["last"],
        "last_bid": last_bid,
        "max_last": max_last,
        "max_bid": max_bid,
        "min_last": min(last_prices),
        "min_bid": min(bid_prices),
        "max_trade_return_pct": round(max_trade_return, 4),
        "max_executable_return_pct": round(max_exec_return, 4),
        "last_executable_return_pct": round(last_exec_return, 4),
        "positive_exec": max_exec_return > 0,
        "hit_0_5_exec": max_exec_return >= 0.5,
        "hit_1_exec": max_exec_return >= 1.0,
        "hit_2_exec": max_exec_return >= 2.0,
        "open_observations": len(quotes),
    }


def fetch_legacy_window(code: str, stamp: str, *, timeout: int = 20) -> list[list[str]]:
    query = urllib.parse.urlencode({"code": code, "thistime": stamp, "page": 1})
    raw = fetch_bytes(f"{NAVER_FINANCE}/item/sise_time.naver?{query}", timeout=timeout)
    return parse_legacy_rows(raw)


def atomic_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def json_safe(value: Any) -> Any:
    if isinstance(value, dt.datetime):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    return value


# Fixed pre-09:06 +3% gate used by the deployed research page.
TARGET3_VERSION = "largo-target3-v1"
TARGET3_PCT = 3.0


def target3_gate(scored: Mapping[str, Any], entry: Mapping[str, Any]) -> dict[str, Any]:
    theme = scored.get("theme") if isinstance(scored.get("theme"), Mapping) else {}
    evidence = scored.get("evidence") if isinstance(scored.get("evidence"), Mapping) else {}
    structure = scored.get("structure") if isinstance(scored.get("structure"), Mapping) else {}

    ask = num(entry.get("entry_ask"))
    bid = num(entry.get("entry_bid"))
    spread_pct = ((ask - bid) / ask * 100.0) if ask and bid and 0 < bid <= ask else None
    breadth = num(theme.get("breadth"))
    rank = num(theme.get("leader_rank"))
    directness = num(evidence.get("directness_points"))
    freshness = num(evidence.get("freshness_points"))
    close_location = num(structure.get("close_location"))
    pattern_score = num(structure.get("pattern_score"))
    risk = num(structure.get("risk_rate"))
    hard_reject = bool(scored.get("hard_reject"))

    common = {
        "hard_exclusion_clear": not hard_reject,
        "entry_quote_known": ask is not None and bid is not None and spread_pct is not None,
        "pattern_60_to_89": pattern_score is not None and 60 <= pattern_score < 90,
        "structural_risk_at_most_8pct": risk is not None and risk <= 0.08,
    }
    theme_checks = {
        **common,
        "theme_breadth_85_to_90pct": breadth is not None and 0.85 <= breadth < 0.90,
        "theme_rank_at_most_4": rank is not None and rank <= 4,
        "close_location_at_least_75pct": close_location is not None and close_location >= 0.75,
        "spread_at_most_0_20pct": spread_pct is not None and spread_pct <= 0.20,
    }
    direct_checks = {
        **common,
        "directness_at_least_14": directness is not None and directness >= 14,
        "freshness_at_least_3": freshness is not None and freshness >= 3,
        "spread_at_most_0_10pct": spread_pct is not None and spread_pct <= 0.10,
    }
    theme_pass = all(theme_checks.values())
    direct_pass = all(direct_checks.values())
    if theme_pass and direct_pass:
        lane = "THEME_AND_DIRECT"
    elif theme_pass:
        lane = "THEME_CONTINUATION"
    elif direct_pass:
        lane = "DIRECT_EVENT"
    else:
        lane = "NONE"

    if hard_reject:
        status = "BLOCK"
    elif lane != "NONE":
        status = "PASS"
    else:
        theme_ratio = sum(theme_checks.values()) / len(theme_checks)
        direct_ratio = sum(direct_checks.values()) / len(direct_checks)
        status = "WATCH" if max(theme_ratio, direct_ratio) >= 0.70 else "NONE"

    if risk is None:
        size_band = "NO_POSITION"
    elif risk <= 0.04:
        size_band = "BASE"
    elif risk <= 0.06:
        size_band = "HALF"
    elif risk <= 0.08:
        size_band = "QUARTER"
    else:
        size_band = "NO_POSITION"

    if lane == "THEME_CONTINUATION":
        blockers = [key for key, passed in theme_checks.items() if not passed]
    elif lane == "DIRECT_EVENT":
        blockers = [key for key, passed in direct_checks.items() if not passed]
    elif lane == "THEME_AND_DIRECT":
        blockers = []
    else:
        theme_missing = [key for key, passed in theme_checks.items() if not passed]
        direct_missing = [key for key, passed in direct_checks.items() if not passed]
        blockers = theme_missing if len(theme_missing) <= len(direct_missing) else direct_missing

    return {
        "version": TARGET3_VERSION,
        "target_pct": TARGET3_PCT,
        "research_only": True,
        "status": status,
        "eligible": status == "PASS",
        "lane": lane,
        "spread_pct": None if spread_pct is None else round(spread_pct, 4),
        "size_band": size_band,
        "theme_checks": theme_checks,
        "direct_checks": direct_checks,
        "theme_pass_count": sum(theme_checks.values()),
        "theme_check_count": len(theme_checks),
        "direct_pass_count": sum(direct_checks.values()),
        "direct_check_count": len(direct_checks),
        "blockers": blockers,
        "note": "표본 내 발굴 규칙입니다. 고정 전진검증 전에는 매수 신호로 사용하지 않습니다.",
    }
