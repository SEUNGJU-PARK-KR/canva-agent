#!/usr/bin/env python3
"""Shared rules for Largo win-rate pilot v4.

The module separates an overnight close-entry lane from a next-session confirmation
lane. Scores are descriptive only; explicit rule gates determine eligibility.
"""
from __future__ import annotations

import datetime as dt
import html
import json
import math
import re
import urllib.request
from pathlib import Path
from typing import Any, Mapping, Sequence

KST = dt.timezone(dt.timedelta(hours=9))
VERSION = "largo-winrate-v4.1"
BASE = "https://stock.naver.com"
UA = "Mozilla/5.0 (compatible; LargoWinRateResearch/4.0; read-only)"
MAX_RISK = 0.06
MOMENTUM_PATTERNS = {"C1", "C3", "C6"}
HOGA_IDS = {"BEST_BID_HOLD_AUTO", "H_ABSORPTION", "H_LIQUIDITY"}
LATE_GATE_TIMES: dict[str, dt.time] = {
    "C_LOCATION": dt.time(14, 35),
    "C_WICK": dt.time(14, 35),
    "C_SEQUENCE": dt.time(15, 5),
}
HARD_GATE_IDS = {
    "X_MARKET_CAP", "X_TRADE_VALUE", "X_RISK", "X_TYPE", "X_IPO",
    "X_POLITICAL", "X_PENNY", "L_LIMIT_PATTERN", "C_SEQUENCE",
}
NEGATIVE_TERMS = (
    "유상증자", "전환사채", "신주인수권", "횡령", "배임", "거래정지",
    "상장폐지", "관리종목", "감사의견", "적자전환", "불성실공시",
    "회생", "파산", "매출액 미달", "투자주의", "투자경고", "계약 해지",
)
MECHANICAL_EVENT_TERMS = (
    "주식선물", "주식옵션", "가격제한폭", "공매도 과열", "기업설명회",
    "ir 개최", "조회공시", "매매거래정지", "투자주의", "투자경고",
)
EVENT_MATCH_TERMS = (
    "단일판매", "공급계약", "수주", "실적", "영업이익", "매출",
    "투자판단", "주요경영", "허가", "승인", "임상", "기술이전",
    "마일스톤", "특허", "자사주", "합병", "인수", "신제품",
    "양산", "공장", "시설투자", "배당", "계약", "전시회", "솔루션",
)


def num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(str(value).replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def won(value: Any) -> str:
    result = num(value)
    return "-" if result is None else f"{result:,.0f}원"


def pct_ratio(value: Any, digits: int = 2) -> str:
    result = num(value)
    return "-" if result is None else f"{result * 100:.{digits}f}%"


def pct_point(value: Any, digits: int = 2) -> str:
    result = num(value)
    return "-" if result is None else f"{result:.{digits}f}%"


def big_won(value: Any) -> str:
    result = num(value)
    if result is None:
        return "-"
    if abs(result) >= 1_0000_0000_0000:
        return f"{result / 1_0000_0000_0000:.2f}조원"
    if abs(result) >= 1_0000_0000:
        return f"{result / 1_0000_0000:.0f}억원"
    return won(result)


def parse_datetime(value: Any, *, naive_tz: dt.tzinfo = KST) -> dt.datetime | None:
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
    text = text.replace("Z", "+00:00")
    try:
        parsed = dt.datetime.fromisoformat(text)
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
        parsed = parsed.replace(tzinfo=naive_tz)
    return parsed.astimezone(KST)


def source_timestamp(latest: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]) -> dt.datetime | None:
    values: list[dt.datetime] = []
    for key in ("generated_at", "updated_at", "market_at"):
        parsed = parse_datetime(latest.get(key))
        if parsed:
            values.append(parsed)
    for row in rows:
        for key in ("updated_at", "generated_at", "source_at"):
            parsed = parse_datetime(row.get(key))
            if parsed:
                values.append(parsed)
    return max(values) if values else None


def phase(source_at: dt.datetime | None) -> dict[str, str]:
    if source_at is None:
        return {
            "id": "UNKNOWN", "title": "기준 시각 확인 필요", "label": "기준 시각 확인 불가",
            "action": "최신 데이터 생성 시각을 확인합니다.",
        }
    current = source_at.time()
    if source_at.weekday() >= 5:
        phase_id, title, action = "MARKET_CLOSED", "휴장일 기록", "신규 진입 판단에 사용하지 않습니다."
    elif current < dt.time(9, 0):
        phase_id, title, action = "PRE_MARKET", "장 시작 전 · 전일 기록", "신규 진입 판단에 사용하지 않습니다."
    elif current < dt.time(13, 38):
        phase_id, title, action = "MORNING", "장중 데이터 축적", "종가 후보를 확정하지 않고 데이터만 축적합니다."
    elif current < dt.time(14, 35):
        phase_id, title, action = "EARLY", "조기 관찰", "재료·주도주·차트 구조를 먼저 준비합니다."
    elif current < dt.time(15, 5):
        phase_id, title, action = "PRE_CLOSE", "마감 구조 관찰", "고가권·윗꼬리·매물 소화를 확인합니다."
    elif current < dt.time(15, 18):
        phase_id, title, action = "ENTRY_PREP", "진입 준비", "구조 손절과 반복 유지를 확인합니다."
    elif current < dt.time(15, 20):
        phase_id, title, action = "FINAL_CHECK", "15:18 최종 확인", "종가 보유형과 익일 확인형을 분리합니다."
    elif current < dt.time(15, 30):
        phase_id, title, action = "AUCTION", "종가 단일가 검토", "종가 보유형만 최종 검토합니다."
    else:
        phase_id, title, action = "FINAL_CLOSE", "종가 확정 검증", "신규 진입이 아니라 결과를 기록합니다."
    return {
        "id": phase_id,
        "title": title,
        "label": f"{source_at:%Y-%m-%d %H:%M} · {title}",
        "action": action,
    }


def metric_value(row: Mapping[str, Any], *keys: str) -> float | None:
    metrics = row.get("metrics") if isinstance(row.get("metrics"), Mapping) else {}
    for key in keys:
        value = num(row.get(key))
        if value is not None:
            return value
        value = num(metrics.get(key))
        if value is not None:
            return value
    return None


def leader_rank(row: Mapping[str, Any]) -> int | None:
    for parent_key in ("theme", "catalyst", "winrate_v4"):
        parent = row.get(parent_key)
        if not isinstance(parent, Mapping):
            continue
        for key in ("leader_rank", "rank", "theme_rank", "sector_rank"):
            value = num(parent.get(key))
            if value is not None:
                return int(value)
    for check in row.get("checks") or []:
        if not isinstance(check, Mapping):
            continue
        if str(check.get("id")) != "S_LEADER":
            continue
        match = re.search(r"(?:순위|rank)\s*[:#]?\s*(\d+)", str(check.get("reason") or ""), re.I)
        if match:
            return int(match.group(1))
    return None


def risk_plan(row: Mapping[str, Any]) -> dict[str, Any]:
    automation = row.get("automation_356")
    if isinstance(automation, Mapping):
        risk = automation.get("risk_plan")
        if isinstance(risk, Mapping) and risk:
            rate = num(risk.get("risk_rate"))
            entry = num(risk.get("entry_price")) or num(row.get("price"))
            stop = num(risk.get("stop_price"))
            valid = str(risk.get("status") or "").upper() == "PASS" and rate is not None and rate <= MAX_RISK
            return {
                "status": "PASS" if valid else "FAIL", "entry": entry, "stop": stop,
                "stop_source": str(risk.get("stop_source") or "구조선"), "rate": rate,
                "one_r": num(risk.get("one_r_price")), "reason": str(risk.get("reason") or ""),
            }
    entry = num(row.get("price"))
    plan = row.get("plan") if isinstance(row.get("plan"), Mapping) else {}
    supports: list[tuple[float, str]] = []
    for item in plan.get("supports") or []:
        if not isinstance(item, Mapping):
            continue
        price = num(item.get("price"))
        if entry and price and 0 < price < entry:
            supports.append((price, str(item.get("name") or "구조선")))
    invalidation = num(plan.get("invalidation"))
    if entry and invalidation and 0 < invalidation < entry:
        supports.append((invalidation, "무효화선"))
    if not entry or not supports:
        return {
            "status": "FAIL", "entry": entry, "stop": None, "stop_source": "구조선",
            "rate": None, "one_r": None, "reason": "진입가 아래 구조선을 확인하지 못했습니다.",
        }
    stop, name = max(supports)
    rate = (entry - stop) / entry
    return {
        "status": "PASS" if rate <= MAX_RISK else "FAIL", "entry": entry, "stop": stop,
        "stop_source": name, "rate": rate, "one_r": entry + (entry - stop),
        "reason": f"가장 가까운 구조선까지 위험거리는 {rate:.2%}입니다.",
    }


def normalize_text(value: Any) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]+", "", str(value or "")).casefold()


def title_tokens(value: Any) -> set[str]:
    tokens = re.findall(r"[0-9A-Za-z가-힣]{2,}", str(value or "").casefold())
    stop = {"관련주", "주식회사", "주식", "종목", "시장", "공시", "기준", "연결재무제표"}
    return {token for token in tokens if token not in stop}


def title_match(a: Any, b: Any) -> bool:
    """Match the event, not merely the company name.

    The previous token overlap rule could mark an unrelated notice from the same issuer
    as the active catalyst.  Require either a meaningful substring or a shared event
    term before considering token overlap.
    """
    left, right = normalize_text(a), normalize_text(b)
    if not left or not right:
        return False
    if min(len(left), len(right)) >= 6 and (left in right or right in left):
        return True
    shared_event = any(normalize_text(term) in left and normalize_text(term) in right for term in EVENT_MATCH_TERMS)
    if not shared_event:
        return False
    left_tokens, right_tokens = title_tokens(a), title_tokens(b)
    overlap = left_tokens & right_tokens
    return len(overlap) >= 1


def event_item_matches(row: Mapping[str, Any], item: Mapping[str, Any]) -> bool:
    title = str(item.get("title") or "")
    lowered = title.casefold()
    if not title or any(term.casefold() in lowered for term in MECHANICAL_EVENT_TERMS + NEGATIVE_TERMS):
        return False
    positive_titles = [
        value for value in catalyst_titles(row)
        if not any(term.casefold() in value.casefold() for term in NEGATIVE_TERMS)
    ]
    if not positive_titles:
        return False
    item_norm = normalize_text(title)
    # A full curated title is specific enough to match directly.
    for value in positive_titles:
        ref_norm = normalize_text(value)
        if len(ref_norm) >= 12 and (ref_norm in item_norm or item_norm in ref_norm):
            return True
    # Generic event labels such as 공급계약 or 실적 must also name the issuer.
    company_norm = normalize_text(row.get("name"))
    direct_company = bool(company_norm and company_norm in item_norm)
    return direct_company and any(title_match(value, title) for value in positive_titles)


def walk_news_items(payload: Any, *, notice: bool = False) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    def walk(value: Any, depth: int = 0) -> None:
        if depth > 8:
            return
        if isinstance(value, Mapping):
            title = value.get("title") or value.get("subject")
            stamp = value.get("datetime") or value.get("date") or value.get("publishedAt")
            if title and stamp:
                # Naver notice ISO strings are UTC-like; compact news timestamps are KST.
                parsed = parse_datetime(stamp, naive_tz=dt.timezone.utc if notice and "T" in str(stamp) else KST)
                found.append({
                    "title": str(title), "at": parsed.isoformat() if parsed else None,
                    "source": "notice" if notice else "news",
                })
            for child in value.values():
                if isinstance(child, (Mapping, list, tuple)):
                    walk(child, depth + 1)
        elif isinstance(value, (list, tuple)):
            for child in value[:100]:
                walk(child, depth + 1)
    walk(payload)
    dedup: dict[tuple[str, str | None], dict[str, Any]] = {}
    for item in found:
        dedup[(item["title"], item["at"])] = item
    return list(dedup.values())


def catalyst_titles(row: Mapping[str, Any]) -> list[str]:
    catalyst = row.get("catalyst") if isinstance(row.get("catalyst"), Mapping) else {}
    values: list[str] = []
    # Use the curated positive set when it exists.  Mixing in every raw title made
    # unrelated notices from the same issuer look like the active catalyst.
    source = catalyst.get("positive_titles") or catalyst.get("titles") or []
    for title in source:
        text = str(title).strip()
        if text and text not in values:
            values.append(text)
    return values


def freshness_from_items(row: Mapping[str, Any], source_at: dt.datetime | None, items: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if source_at is None:
        return {"status": "UNKNOWN", "hours": None, "at": None, "title": None, "reason": "기준 시각이 없습니다."}
    matched: list[tuple[dt.datetime, Mapping[str, Any]]] = []
    for item in items:
        at = parse_datetime(item.get("at"))
        if at is None or at > source_at + dt.timedelta(minutes=5):
            continue
        if not event_item_matches(row, item):
            continue
        matched.append((at, item))
    if not matched:
        existing = row.get("winrate_v4")
        if isinstance(existing, Mapping):
            fresh = existing.get("catalyst_freshness")
            if isinstance(fresh, Mapping):
                return dict(fresh)
        return {"status": "UNKNOWN", "hours": None, "at": None, "title": None, "reason": "재료 발표 시각을 확인하지 못했습니다."}
    at, item = max(matched, key=lambda pair: pair[0])
    hours = max(0.0, (source_at - at).total_seconds() / 3600)
    status = "FRESH" if hours <= 24 else "RECENT" if hours <= 72 else "STALE"
    return {
        "status": status, "hours": round(hours, 1), "at": at.isoformat(),
        "title": str(item.get("title") or ""), "source": str(item.get("source") or ""),
        "reason": f"재료 발표 후 {hours:.1f}시간이 지났습니다.",
    }


def fetch_json(path: str, *, timeout: int = 15) -> Any:
    request = urllib.request.Request(
        BASE + path,
        headers={"Accept": "application/json,text/plain,*/*", "Referer": BASE + "/", "User-Agent": UA},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def enrich_news_for_candidate(
    row: Mapping[str, Any], source_at: dt.datetime | None, *, timeout: int = 15,
    payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    code = str(row.get("code") or "")
    errors: list[str] = []
    if payload is None:
        news_payload: Any = []
        notice_payload: Any = []
        try:
            news_payload = fetch_json(f"/api/domestic/detail/news?itemCode={code}&page=1&pageSize=20", timeout=timeout)
        except Exception as exc:  # network is best-effort
            errors.append(f"news: {type(exc).__name__}: {exc}")
        try:
            notice_payload = fetch_json(f"/api/domestic/detail/notice?itemCode={code}&startIdx=0&pageSize=20", timeout=timeout)
        except Exception as exc:
            errors.append(f"notice: {type(exc).__name__}: {exc}")
    else:
        news_box = payload.get("news") if isinstance(payload.get("news"), Mapping) else {}
        notice_box = payload.get("notice") if isinstance(payload.get("notice"), Mapping) else {}
        news_payload = news_box.get("payload", news_box)
        notice_payload = notice_box.get("payload", notice_box)
    items = walk_news_items(news_payload, notice=False) + walk_news_items(notice_payload, notice=True)
    freshness = freshness_from_items(row, source_at, items)
    after_signal = []
    if source_at:
        for item in items:
            at = parse_datetime(item.get("at"))
            if at and source_at < at <= source_at + dt.timedelta(hours=18):
                after_signal.append(item)
    return {
        "catalyst_freshness": freshness,
        "matched_items": [item for item in items if event_item_matches(row, item)][:10],
        "after_signal_items": sorted(after_signal, key=lambda item: str(item.get("at") or ""))[:10],
        "errors": errors,
    }


def load_history(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists() or not path.stat().st_size:
        return {"version": VERSION, "snapshots": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": VERSION, "snapshots": []}
    if not isinstance(data, Mapping) or not isinstance(data.get("snapshots"), list):
        return {"version": VERSION, "snapshots": []}
    return {"version": str(data.get("version") or VERSION), "snapshots": list(data["snapshots"])}


def persistence(history: Mapping[str, Any], code: str, pattern_id: str, market_date: dt.date | None) -> dict[str, Any]:
    observations: list[tuple[dt.datetime, Mapping[str, Any] | None]] = []
    for snapshot in history.get("snapshots") or []:
        if not isinstance(snapshot, Mapping):
            continue
        at = parse_datetime(snapshot.get("at"))
        if at is None or (market_date and at.date() != market_date):
            continue
        items = snapshot.get("items")
        item = items.get(code) if isinstance(items, Mapping) else None
        observations.append((at, item if isinstance(item, Mapping) else None))
    observations.sort(key=lambda pair: pair[0])
    appearances = [(at, item) for at, item in observations if item is not None]
    consecutive = 0
    for _, item in reversed(observations):
        if item is None:
            break
        consecutive += 1
    matches = 0
    for _, item in appearances:
        current_pattern = str(item.get("pattern_id") or item.get("pattern") or "")
        if current_pattern == pattern_id:
            matches += 1
    return {
        "snapshot_count": len(observations), "appearance_count": len(appearances),
        "consecutive_count": consecutive, "pattern_match_count": matches,
        "first_seen": appearances[0][0].strftime("%H:%M") if appearances else None,
        "last_seen": appearances[-1][0].strftime("%H:%M") if appearances else None,
    }


def freshness(row: Mapping[str, Any], source_at: dt.datetime | None) -> dict[str, Any]:
    box = row.get("winrate_v4")
    if isinstance(box, Mapping):
        value = box.get("catalyst_freshness")
        if isinstance(value, Mapping):
            return dict(value)
    catalyst = row.get("catalyst")
    if isinstance(catalyst, Mapping):
        for key in ("freshness", "catalyst_freshness"):
            value = catalyst.get(key)
            if isinstance(value, Mapping):
                return dict(value)
        for key in ("published_at", "datetime", "at", "date"):
            at = parse_datetime(catalyst.get(key))
            if at and source_at:
                hours = max(0.0, (source_at - at).total_seconds() / 3600)
                return {
                    "status": "FRESH" if hours <= 24 else "RECENT" if hours <= 72 else "STALE",
                    "hours": round(hours, 1), "at": at.isoformat(), "title": None,
                    "reason": f"재료 발표 후 {hours:.1f}시간이 지났습니다.",
                }
    return {"status": "UNKNOWN", "hours": None, "at": None, "title": None, "reason": "재료 발표 시각을 확인하지 못했습니다."}


def normalize_checks(row: Mapping[str, Any], source_at: dt.datetime | None, risk: Mapping[str, Any]) -> list[dict[str, Any]]:
    metrics = row.get("metrics") if isinstance(row.get("metrics"), Mapping) else {}
    pattern = row.get("pattern") if isinstance(row.get("pattern"), Mapping) else {}
    grade = str((row.get("catalyst") or {}).get("grade") if isinstance(row.get("catalyst"), Mapping) else "").upper()
    rank = leader_rank(row)
    close_location = metric_value(row, "close_location")
    upper_wick = metric_value(row, "upper_wick_ratio", "upper_wick")
    body_ratio = metric_value(row, "body_ratio")
    pattern_score = num(pattern.get("score")) or 0.0
    history_evidence = any(num(metrics.get(key)) is not None for key in ("ma5", "ma10", "ma20", "ma60", "prev20_high"))
    next_day_plan = None
    automation = row.get("automation_356")
    if isinstance(automation, Mapping):
        next_day_plan = automation.get("next_day_plan")
    normalized: list[dict[str, Any]] = []
    for item in row.get("checks") or []:
        if not isinstance(item, Mapping):
            continue
        check_id = str(item.get("id") or "")
        if check_id in HOGA_IDS or str(item.get("role") or "required") != "required":
            continue
        status = str(item.get("status") or "MISSING").upper()
        reason = str(item.get("reason") or item.get("rule") or "세부 사유 없음")
        original_status = status
        gate_time = LATE_GATE_TIMES.get(check_id)
        active = True if gate_time is None or source_at is None else source_at.time() >= gate_time
        if check_id == "X_IPO" and status in {"WARN", "MISSING", "UNKNOWN"} and history_evidence:
            status, reason = "PASS", "과거 일봉과 이동평균이 있어 당일 신규 상장이 아닙니다."
        elif check_id == "S_CATALYST" and grade in {"S", "A"}:
            status, reason = "PASS", f"재료 등급 {grade}"
        elif check_id == "D_PATTERN" and str(pattern.get("id") or "") in MOMENTUM_PATTERNS and pattern_score >= 75:
            status, reason = "PASS", f"{pattern.get('id')} 패턴 {pattern_score:.0f}점"
        elif check_id == "C_LOCATION" and close_location is not None and close_location >= 0.75:
            status, reason = "PASS", f"당일 범위 위치 {close_location:.2f}"
        elif check_id == "C_WICK" and upper_wick is not None and body_ratio is not None and upper_wick <= 0.20 and body_ratio >= 0.40:
            status, reason = "PASS", f"윗꼬리 {upper_wick:.2f} · 몸통 {body_ratio:.2f}"
        elif check_id == "R_PLAN" and risk.get("status") == "PASS":
            status, reason = "PASS", f"구조 손절 {pct_ratio(risk.get('rate'))}"
        elif check_id == "ENTRY_STOP_AUTO" and risk.get("status") == "PASS":
            status, reason = "PASS", "진입가와 구조 손절선이 계산되었습니다."
        elif check_id == "NEXT_DAY_PLAN_AUTO" and isinstance(next_day_plan, Mapping) and next_day_plan:
            status, reason = "PASS", "익일 갭별 계획이 생성되었습니다."
        normalized.append({
            "id": check_id, "name": str(item.get("name") or check_id), "status": status,
            "original_status": original_status, "reason": reason, "active": active,
        })
    # Rank 3 is a co-leader only inside the explicit fresh-momentum close lane; keep the legacy check visible.
    return normalized


def theme_breadth(row: Mapping[str, Any]) -> float | None:
    catalyst = row.get("catalyst") if isinstance(row.get("catalyst"), Mapping) else {}
    text = str(catalyst.get("reason") or "")
    match = re.search(r"(\d+)\s*/\s*(\d+)\s*상승", text)
    if not match:
        return None
    rising, total = int(match.group(1)), int(match.group(2))
    return rising / total if total > 0 else None


def structure_features(row: Mapping[str, Any]) -> dict[str, Any]:
    pattern = row.get("pattern") if isinstance(row.get("pattern"), Mapping) else {}
    catalyst = row.get("catalyst") if isinstance(row.get("catalyst"), Mapping) else {}
    return {
        "grade": str(catalyst.get("grade") or "-").upper(),
        "rank": leader_rank(row),
        "pattern_id": str(pattern.get("id") or "-"),
        "pattern_name": str(pattern.get("name") or "미분류"),
        "pattern_score": num(pattern.get("score")) or 0.0,
        "close_location": metric_value(row, "close_location"),
        "upper_wick": metric_value(row, "upper_wick_ratio", "upper_wick"),
        "body_ratio": metric_value(row, "body_ratio"),
        "digest_ratio": metric_value(row, "digest_ratio", "trade_value_ratio"),
        "volume_ratio": metric_value(row, "volume_ratio"),
        "change_rate": num(row.get("change_rate")),
        "trade_value": num(row.get("trade_value")),
        "theme_breadth": theme_breadth(row),
    }


def rule_result(name: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "pass": bool(passed), "detail": detail}


def analyze_candidate(
    row: Mapping[str, Any], source_at: dt.datetime | None, history: Mapping[str, Any],
    *, require_persistence: bool = True,
) -> dict[str, Any]:
    risk = risk_plan(row)
    checks = normalize_checks(row, source_at, risk)
    features = structure_features(row)
    fresh = freshness(row, source_at)
    pattern_id = str(features["pattern_id"])
    market_date = source_at.date() if source_at else None
    persist = persistence(history, str(row.get("code") or ""), pattern_id, market_date)
    grade = str(features["grade"])
    rank = features["rank"]
    loc = features["close_location"]
    wick = features["upper_wick"]
    body = features["body_ratio"]
    digest = features["digest_ratio"]
    breadth = features["theme_breadth"]
    change = features["change_rate"]
    pattern_score = features["pattern_score"]
    fresh_status = str(fresh.get("status") or "UNKNOWN")

    active_hard_failures = [
        item for item in checks
        if item["active"] and item["id"] in HARD_GATE_IDS and item["status"] == "FAIL"
    ]
    unresolved_hard = [
        item for item in checks
        if item["active"] and item["id"] in HARD_GATE_IDS and item["status"] in {"WARN", "MISSING", "UNKNOWN"}
    ]
    catalyst_reason = str((row.get("catalyst") or {}).get("reason") or "") if isinstance(row.get("catalyst"), Mapping) else ""
    negative_catalyst = grade == "REJECT" or any(term in catalyst_reason for term in NEGATIVE_TERMS)
    persistence_ok = (not require_persistence) or int(persist.get("consecutive_count") or 0) >= 2
    live_catalyst = fresh_status in {"FRESH", "RECENT"} or (breadth is not None and breadth >= 0.75)

    momentum_rules = [
        rule_result("재료 S/A", grade in {"S", "A"}, f"등급 {grade}"),
        rule_result("주도주 5위 이내", rank is not None and rank <= 5, f"순위 {rank if rank is not None else '-'}"),
        rule_result("강한 종가 패턴", pattern_id in MOMENTUM_PATTERNS and pattern_score >= 75, f"{pattern_id} {pattern_score:.0f}점"),
        rule_result("고가권 종가", loc is not None and loc >= 0.75, f"위치 {loc if loc is not None else '-'}"),
        rule_result("작은 윗꼬리", wick is not None and wick <= 0.20, f"윗꼬리 {wick if wick is not None else '-'}"),
        rule_result("충분한 양봉 몸통", body is not None and body >= 0.40, f"몸통 {body if body is not None else '-'}"),
        rule_result("강한 당일 모멘텀", change is not None and 8.0 <= change <= 15.0, f"등락 {change if change is not None else '-'}%"),
        rule_result("구조 위험 6% 이내", risk.get("status") == "PASS", risk.get("reason") or ""),
        rule_result("최소 매물 소화", digest is not None and digest >= 0.30, f"현재/과거 {digest if digest is not None else '-'}"),
    ]
    confirm_fit = (
        all(item["pass"] for item in momentum_rules)
        and not active_hard_failures and not unresolved_hard
        and risk.get("status") == "PASS" and not negative_catalyst
    )

    common_close_rules = [
        rule_result(
            "살아 있는 재료", live_catalyst,
            f"신선도 {fresh_status} · 테마 확산 {breadth:.0%}" if breadth is not None else f"신선도 {fresh_status}",
        ),
        rule_result("반복 유지", persistence_ok, f"연속 {int(persist.get('consecutive_count') or 0)}회"),
    ]
    core_rules = momentum_rules + common_close_rules + [
        rule_result("주도주 3위 이내", rank is not None and rank <= 3, f"순위 {rank if rank is not None else '-'}"),
        rule_result("매물 소화 0.45 이상", digest is not None and digest >= 0.45, f"현재/과거 {digest if digest is not None else '-'}"),
    ]
    elite_rules = momentum_rules + common_close_rules + [
        rule_result("초강한 테마 확산", breadth is not None and breadth >= 0.75, f"상승 비율 {breadth:.0%}" if breadth is not None else "확인 불가"),
        rule_result("현재 2위 이내", rank is not None and rank <= 2, f"순위 {rank if rank is not None else '-'}"),
        rule_result("종가 최상단", loc is not None and loc >= 0.95, f"위치 {loc if loc is not None else '-'}"),
        rule_result("윗꼬리 0.05 이하", wick is not None and wick <= 0.05, f"윗꼬리 {wick if wick is not None else '-'}"),
        rule_result("몸통 0.55 이상", body is not None and body >= 0.55, f"몸통 {body if body is not None else '-'}"),
        rule_result("매물 소화 0.30 이상", digest is not None and digest >= 0.30, f"현재/과거 {digest if digest is not None else '-'}"),
    ]
    core_fit = all(item["pass"] for item in core_rules)
    elite_fit = all(item["pass"] for item in elite_rules)
    close_fit = (
        (core_fit or elite_fit) and not active_hard_failures and not unresolved_hard
        and risk.get("status") == "PASS" and not negative_catalyst
    )
    close_variant = "CORE" if core_fit else "ELITE" if elite_fit else None
    close_rules = core_rules if core_fit else elite_rules if elite_fit else max(
        (core_rules, elite_rules), key=lambda values: sum(item["pass"] for item in values)
    )

    phase_id = phase(source_at)["id"]
    if active_hard_failures or unresolved_hard or risk.get("status") != "PASS" or negative_catalyst:
        lane = "EXCLUDE"
    elif phase_id in {"PRE_MARKET", "MORNING", "MARKET_CLOSED", "UNKNOWN"}:
        lane = "WATCH"
    elif close_fit and phase_id in {"FINAL_CHECK", "AUCTION"}:
        lane = "CLOSE_ENTRY"
    elif close_fit and phase_id == "FINAL_CLOSE":
        lane = "CLOSE_VALIDATED"
    elif close_fit:
        lane = "CLOSE_READY"
    elif confirm_fit:
        lane = "NEXT_DAY_CONFIRM"
    else:
        lane = "WATCH"

    close_passes = sum(item["pass"] for item in close_rules)
    confirm_passes = sum(item["pass"] for item in momentum_rules)
    entry = num(risk.get("entry")) or num(row.get("price"))
    stop = num(risk.get("stop"))
    target1 = entry * 1.01 if entry else None
    target2 = entry * 1.02 if entry else None
    plans = {
        "close_entry": {
            "entry": entry, "stop": stop, "target1": target1, "target2": target2,
            "target1_size": 0.50, "target2_size": 0.30, "runner_size": 0.20,
            "time_exit": "10:30까지 +1% 미도달이고 전일 종가 아래면 잔여 물량 정리",
            "rule": "15:18 이후 핵심형 또는 초강한 마감형과 반복 유지가 모두 통과한 경우에만 검토",
        },
        "next_day_confirm": {
            "reference_close": entry, "cancel_below": stop,
            "reclaim_trigger": entry, "target1": target1, "target2": target2,
            "target1_size": 0.50, "target2_size": 0.30, "runner_size": 0.20,
            "no_chase_gap_pct": 3.0, "confirm_times": ["09:05", "09:15"],
            "rule": "다음 날 갭이 3% 이상이면 추격하지 않고, 전일 종가 회복과 09:05·09:15 유지 확인 뒤 검토",
        },
    }
    return {
        "version": VERSION,
        "code": str(row.get("code") or ""), "name": str(row.get("name") or row.get("code") or "종목"),
        "lane": lane, "close_variant": close_variant,
        "price": num(row.get("price")), "change_rate": num(row.get("change_rate")),
        "trade_value": num(row.get("trade_value")), "market_cap": num(row.get("market_cap")),
        "quality": num(row.get("quality_score")) or 0.0,
        "catalyst": dict(row.get("catalyst") or {}) if isinstance(row.get("catalyst"), Mapping) else {},
        "freshness": fresh, "features": features, "risk": risk, "checks": checks,
        "hard_failures": active_hard_failures, "hard_unresolved": unresolved_hard,
        "persistence": persist, "close_rules": close_rules, "close_routes": {"core": core_rules, "elite": elite_rules},
        "confirm_rules": momentum_rules,
        "close_rule_count": len(close_rules), "close_rule_passes": close_passes,
        "confirm_rule_count": len(momentum_rules), "confirm_rule_passes": confirm_passes,
        "close_fit": close_fit, "confirm_fit": confirm_fit, "plans": plans,
    }

def append_history(history: Mapping[str, Any], source_at: dt.datetime | None, rows: Sequence[Mapping[str, Any]], *, max_snapshots: int = 320) -> dict[str, Any]:
    snapshots = [dict(item) for item in history.get("snapshots", []) if isinstance(item, Mapping)]
    if source_at is None:
        return {"version": VERSION, "snapshots": snapshots[-max_snapshots:]}
    items = {
        str(row.get("code")): {
            "name": row.get("name"), "lane": row.get("lane"), "price": row.get("price"),
            "pattern_id": (row.get("features") or {}).get("pattern_id"),
            "close_fit": row.get("close_fit"), "confirm_fit": row.get("confirm_fit"),
            "close_rule_passes": row.get("close_rule_passes"),
            "confirm_rule_passes": row.get("confirm_rule_passes"),
        }
        for row in rows
    }
    at = source_at.isoformat()
    snapshots = [item for item in snapshots if str(item.get("at") or "") != at]
    snapshots.append({"at": at, "market_date": source_at.date().isoformat(), "items": items})
    snapshots.sort(key=lambda item: str(item.get("at") or ""))
    return {"version": VERSION, "snapshots": snapshots[-max_snapshots:]}


def atomic_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)
