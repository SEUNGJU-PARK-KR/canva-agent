#!/usr/bin/env python3
"""Research-only Largo closing-bet v6 shadow gate.

The canonical v5 gate stays unchanged. v6 adds evidence auditing, price-reflection
control, stronger theme confirmation and a same-event cooldown. No order placement.
"""
from __future__ import annotations

import hashlib
import math
import re
from typing import Any, Mapping, Sequence

VERSION = "largo-close-v6-shadow-v1"
TARGET_PCT = 3.0
NEGATIVE_CONTEXT_TERMS = (
    "급락", "하락", "약세", "부담", "우려", "희석", "적자", "철회", "해지",
    "리콜", "소송", "조사", "압수수색", "부진", "쇼크", "실망", "매도",
    "유상증자", "전환사채", "신주인수권", "횡령", "배임", "거래정지",
    "상장폐지", "관리종목", "감사의견", "단기과열", "투자경고", "투자위험",
)
NON_COMMON_INSTRUMENT_TERMS = (
    "Reg.S", "스팩", "SPAC", "ETN", "ETF", "RISE ", "KODEX ", "TIGER ",
    "ACE ", "PLUS ", "HANARO ", "SOL ", "KOSEF ", "KBSTAR ", "ARIRANG ",
)


def num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(str(value).replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def truth(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().casefold() in {"1", "true", "yes", "pass"}


def normalize(value: Any) -> str:
    text = str(value or "").casefold()
    for term in ("주식회사", "(주)", "㈜", "홀딩스", "그룹", "corporation", "corp"):
        text = text.replace(term.casefold(), "")
    return re.sub(r"[^0-9a-z가-힣]+", "", text)


def is_common_stock_name(value: Any) -> bool:
    name = str(value or "")
    if any(term.casefold() in name.casefold() for term in NON_COMMON_INSTRUMENT_TERMS):
        return False
    return re.search(r"(?:우|우B|우C|우선주)$", name) is None


def event_hash(code: Any, title: Any) -> str | None:
    packed = normalize(title)
    return hashlib.sha256(f"{str(code or '').zfill(6)}|{packed}".encode()).hexdigest()[:20] if packed else None


def evidence_audit(scored: Mapping[str, Any]) -> dict[str, Any]:
    evidence = scored.get("evidence") if isinstance(scored.get("evidence"), Mapping) else {}
    title = str(evidence.get("title") or "")
    body = str(evidence.get("body") or evidence.get("summary") or "")
    combined = f"{title} {body}"
    company = normalize(scored.get("name"))
    code = re.sub(r"\D", "", str(scored.get("code") or ""))
    company_match = bool(company and company in normalize(combined)) or bool(len(code) == 6 and code in combined)
    negative = bool(evidence.get("negative")) or any(term.casefold() in combined.casefold() for term in NEGATIVE_CONTEXT_TERMS)
    strength = num(evidence.get("event_strength"))
    direct_benefit = truth(evidence.get("direct_benefit"))
    observed = truth(evidence.get("observed")) or bool(title)
    passed = bool(observed and title and company_match and direct_benefit and not negative and strength is not None and strength >= 1)
    return {
        "observed": observed,
        "passed": passed,
        "company_match": company_match,
        "direct_benefit": direct_benefit,
        "negative_context": negative,
        "event_strength": strength,
        "title": title,
        "event_hash": event_hash(scored.get("code"), title),
    }


def _box(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _metric(scored: Mapping[str, Any], section: str, *keys: str) -> float | None:
    box = _box(scored.get(section))
    for key in keys:
        value = num(box.get(key))
        if value is not None:
            return value
        value = num(scored.get(key))
        if value is not None:
            return value
    return None


def _spread(entry: Mapping[str, Any]) -> float | None:
    ask, bid = num(entry.get("entry_ask")), num(entry.get("entry_bid"))
    return (ask - bid) / ask * 100 if ask and bid and 0 < bid <= ask else None


def _duplicate(audit: Mapping[str, Any], prior_signals: Sequence[Mapping[str, Any]]) -> bool:
    target = audit.get("event_hash")
    if not target:
        return False
    for row in prior_signals[-5:]:
        shadow = _box(row.get("v6_shadow")) if isinstance(row, Mapping) else {}
        if shadow.get("event_hash") == target:
            return True
    return False


def _quality(lane: str, *, risk: float | None, spread: float | None, breadth: float | None,
             rank: float | None, close_location: float | None, upper_wick: float | None,
             digestion: float | None, directness: float | None, freshness: float | None,
             audit: Mapping[str, Any]) -> float:
    score = {"DIRECT_CONFIRMED": 78, "THEME_LEADER": 74, "DIRECT_UNPRICED": 72, "CLOSE_POWER": 68}.get(lane, 0)
    score += 5 if audit.get("passed") else 0
    score += min(4, max(0, (directness or 12) - 12))
    score += min(3, (freshness or 0) / 3)
    score += max(0, min(4, ((breadth or .5) - .5) * 10))
    score += 3 if rank is not None and rank <= 1 else 2 if rank is not None and rank <= 2 else 0
    score += max(0, min(3, ((close_location or .6) - .6) * 7.5))
    score += max(0, min(3, (.25 - (upper_wick if upper_wick is not None else .25)) * 12))
    score += 2 if digestion is not None and .30 <= digestion <= .80 else 1 if digestion is not None and digestion <= 1.20 else 0
    score += max(0, min(4, (.10 - risk) * 40)) if risk is not None else 0
    score += max(0, min(3, (.20 - spread) * 15)) if spread is not None else 0
    return round(min(100, score), 2)


def v6_shadow_gate(scored: Mapping[str, Any], entry: Mapping[str, Any], *, prior_signals: Sequence[Mapping[str, Any]] = ()) -> dict[str, Any]:
    evidence, theme, persistence = _box(scored.get("evidence")), _box(scored.get("theme")), _box(scored.get("persistence"))
    ask, bid, spread = num(entry.get("entry_ask")), num(entry.get("entry_bid")), _spread(entry)
    directness, freshness = num(evidence.get("directness_points")), num(evidence.get("freshness_points"))
    change = _metric(scored, "structure", "change_rate")
    digestion = _metric(scored, "structure", "digest_ratio")
    risk = _metric(scored, "structure", "risk_rate")
    close_location = _metric(scored, "structure", "close_location")
    upper_wick = _metric(scored, "structure", "upper_wick", "upper_wick_ratio")
    body = _metric(scored, "structure", "body_ratio")
    breadth, rank = num(theme.get("breadth")), num(theme.get("leader_rank"))
    strong_followers = num(theme.get("follower_strong_count"))
    audit = evidence_audit(scored)
    duplicate = _duplicate(audit, prior_signals)
    common = {
        "hard_exclusion_clear": not truth(scored.get("hard_reject")),
        "common_stock_only": is_common_stock_name(scored.get("name")),
        "entry_quote_known": ask is not None and bid is not None and spread is not None,
        "structural_risk_at_most_10pct": risk is not None and risk <= .10,
        "digestion_at_least_0_30": digestion is not None and digestion >= .30,
    }
    theme_leader = {**common,
        "change_rate_10_to_15pct": change is not None and 10 <= change <= 15,
        "digestion_at_most_1_00": digestion is not None and digestion <= 1,
        "theme_breadth_at_least_0_80": breadth is not None and breadth >= .80,
        "leader_rank_at_most_2": rank is not None and rank <= 2,
        "close_location_at_least_0_70": close_location is not None and close_location >= .70,
        "upper_wick_at_most_0_20": upper_wick is not None and upper_wick <= .20,
        "spread_at_most_0_20pct": spread is not None and spread <= .20,
        "theme_not_fading": truth(persistence.get("stable")) if truth(persistence.get("observed")) else True,
    }
    close_power = {**common,
        "change_rate_8_to_15pct": change is not None and 8 <= change <= 15,
        "digestion_at_most_0_80": digestion is not None and digestion <= .80,
        "close_location_at_least_0_90": close_location is not None and close_location >= .90,
        "upper_wick_at_most_0_10": upper_wick is not None and upper_wick <= .10,
        "body_ratio_at_least_0_45": body is not None and body >= .45,
        "spread_at_most_0_15pct": spread is not None and spread <= .15,
        "supporting_context": bool((breadth is not None and breadth >= .55) or audit.get("passed")),
    }
    direct_unpriced = {**common,
        "evidence_audit_pass": bool(audit.get("passed")),
        "directness_at_least_14": directness is not None and directness >= 14,
        "freshness_at_least_7": freshness is not None and freshness >= 7,
        "change_rate_minus2_to_8pct": change is not None and -2 <= change <= 8,
        "digestion_at_most_1_20": digestion is not None and digestion <= 1.2,
        "close_location_at_least_0_60": close_location is not None and close_location >= .60,
        "spread_at_most_0_10pct": spread is not None and spread <= .10,
        "same_event_not_repeated": not duplicate,
    }
    direct_confirmed = {**common,
        "evidence_audit_pass": bool(audit.get("passed")),
        "directness_at_least_15": directness is not None and directness >= 15,
        "freshness_at_least_9": freshness is not None and freshness >= 9,
        "change_rate_5_to_12pct": change is not None and 5 <= change <= 12,
        "digestion_at_most_1_00": digestion is not None and digestion <= 1,
        "spread_at_most_0_10pct": spread is not None and spread <= .10,
        "confirmation_present": bool((breadth is not None and breadth >= .60 and rank is not None and rank <= 2) or (close_location is not None and close_location >= .85 and upper_wick is not None and upper_wick <= .15) or (strong_followers is not None and strong_followers >= 1)),
        "same_event_not_repeated": not duplicate,
    }
    checks = {"DIRECT_CONFIRMED": direct_confirmed, "THEME_LEADER": theme_leader, "DIRECT_UNPRICED": direct_unpriced, "CLOSE_POWER": close_power}
    lane = next((name for name in checks if all(checks[name].values())), "NONE")
    qualified = lane != "NONE"
    ratios = {name: sum(values.values()) / len(values) for name, values in checks.items()}
    status = "BLOCK" if not common["hard_exclusion_clear"] or not common["common_stock_only"] or (risk is not None and risk > .10) else "SHADOW_PASS" if qualified else "SHADOW_WATCH" if max(ratios.values()) >= .80 else "NONE"
    nearest = max(ratios, key=ratios.get)
    blockers = [] if qualified else [key for key, passed in checks[nearest].items() if not passed]
    size = "NO_POSITION" if risk is None or risk > .10 else "BASE" if risk <= .04 else "HALF" if risk <= .06 else "QUARTER" if risk <= .08 else "EIGHTH"
    quality = _quality(lane, risk=risk, spread=spread, breadth=breadth, rank=rank, close_location=close_location, upper_wick=upper_wick, digestion=digestion, directness=directness, freshness=freshness, audit=audit)
    return {
        "version": VERSION, "target_pct": TARGET_PCT, "research_only": True,
        "status": status, "qualified": qualified, "eligible": qualified,
        "daily_pick": False, "daily_rank": None, "lane": lane, "quality": quality,
        "size_band": size, "spread_pct": None if spread is None else round(spread, 4),
        "risk_rate": risk, "change_rate": change, "digest_ratio": digestion,
        "theme_breadth": breadth, "leader_rank": rank, "close_location": close_location,
        "upper_wick": upper_wick, "body_ratio": body, "directness_points": directness,
        "freshness_points": freshness, "event_hash": audit.get("event_hash"),
        "duplicate_event": duplicate, "evidence_audit": audit, "lane_checks": checks,
        "blockers": blockers,
        "note": "v5를 대체하지 않는 v6 그림자 규칙입니다. 전진검증 전에는 매수 기준으로 사용하지 않습니다.",
    }


def selection_key(candidate: Mapping[str, Any]) -> tuple[float, float, float, float, str]:
    shadow = _box(candidate.get("v6_shadow"))
    risk, spread = num(shadow.get("risk_rate")), num(shadow.get("spread_pct"))
    metrics = _box(candidate.get("metrics"))
    turnover = num(candidate.get("trade_value")) or num(metrics.get("trade_value")) or 0
    return (-float(num(shadow.get("quality")) or 0), risk if risk is not None else math.inf, spread if spread is not None else math.inf, -turnover, str(candidate.get("code") or ""))


def apply_daily_selection(candidates: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    qualified = []
    for row in candidates:
        if not isinstance(row, dict) or not isinstance(row.get("v6_shadow"), dict):
            continue
        shadow = row["v6_shadow"]
        shadow["daily_pick"], shadow["daily_rank"] = False, None
        if shadow.get("qualified") and shadow.get("status") != "BLOCK":
            qualified.append(row)
        else:
            shadow["eligible"] = False
    qualified.sort(key=selection_key)
    for rank, row in enumerate(qualified, 1):
        shadow = row["v6_shadow"]
        shadow["daily_rank"], shadow["daily_pick"], shadow["eligible"] = rank, rank == 1, rank == 1
        if rank > 1:
            shadow["status"] = "SHADOW_ALTERNATE"
            shadow["blockers"] = list(dict.fromkeys(list(shadow.get("blockers") or []) + ["daily_one_pick_only"]))
    return qualified
