#!/usr/bin/env python3
"""Largo closing-bet v6 shadow rule.

The module is deterministic and read-only.  It classifies already-scored 15:18
candidates.  It never connects to a broker and never places an order.

v6 is intentionally a shadow rule.  v5 remains the comparison baseline until v6
has enough timestamp-complete forward observations.
"""
from __future__ import annotations

import math
import re
from typing import Any, Iterable, Mapping, MutableMapping, Sequence

V6_VERSION = "largo-close-v6-shadow"
TARGET_PCT = 3.0

LANE_DIRECT_REACTED = "DIRECT_REACTED"
LANE_MOMENTUM = "MOMENTUM_CONFIRMED"
LANE_THEME_CONTINUATION = "THEME_CONTINUATION"
LANE_DIRECT_UNREACTED = "DIRECT_UNREACTED"
LANE_NONE = "NONE"

LANE_PRIORITY = {
    LANE_DIRECT_REACTED: 0,
    LANE_MOMENTUM: 1,
    LANE_THEME_CONTINUATION: 2,
    LANE_DIRECT_UNREACTED: 3,
}

NON_COMMON_INSTRUMENT_TERMS = (
    "reg.s", "스팩", "spac", "etn", "etf", "rise ", "kodex ", "tiger ",
    "ace ", "plus ", "hanaro ", "sol ", "kosef ", "kbstar ", "arirang ",
)

# A generic correction notice is not rejected.  The July winner included a
# corrected supply-contract notice.  Only economically ambiguous/canceling
# contexts are blocked.
NEGATIVE_OR_AMBIGUOUS_EVENT_TERMS = (
    "변경신청", "변경 승인", "시험계획 변경", "목표주가", "급락", "부담", "우려",
    "하향", "철회", "해지", "투자경고", "투자주의", "투자위험", "지정예고",
    "지정 해제", "계약 취소", "계약해제", "공급계약 해지",
)

CONTRACT_EVENT_TERMS = (
    "단일판매", "공급계약", "판매계약", "수주", "허가", "승인", "기술이전",
    "마일스톤", "양산", "독점", "상용화", "신규시설투자", "시설투자",
)
CLINICAL_EVENT_TERMS = ("임상", "시험계획")
CLINICAL_CONFIRM_TERMS = ("신청", "승인", "결과", "성공", "투약", "등록")
SHAREHOLDER_EVENT_TERMS = ("배당", "자사주", "자기주식")

V6_CONFIG: dict[str, Any] = {
    "common": {
        "min_trade_value_krw": 100_000_000_000,
    },
    LANE_MOMENTUM: {
        "change_rate_min_pct": 10.0,
        "change_rate_max_pct": 15.0,
        "digest_ratio_min": 0.30,
        "digest_ratio_max": 1.00,
        "close_location_min": 0.65,
        "upper_wick_max": 0.35,
        "body_ratio_min": 0.60,
        "theme_breadth_min": 0.80,
        "leader_rank_max": 5,
        "follower_strong_count_min": 2,
        "pattern_score_min": 70.0,
        "pattern_score_max_exclusive": 90.0,
        "risk_rate_max": 0.10,
        "spread_pct_max": 0.20,
    },
    LANE_DIRECT_UNREACTED: {
        "directness_min": 15.0,
        "freshness_min": 3.0,
        "change_rate_min_pct": -5.0,
        "change_rate_max_pct": 3.0,
        "close_location_max": 0.35,
        "digest_ratio_min": 0.10,
        "digest_ratio_max": 1.50,
        "risk_rate_max": 0.05,
        "spread_pct_max": 0.10,
    },
    LANE_DIRECT_REACTED: {
        "directness_min": 14.0,
        "freshness_min": 7.0,
        "event_strength_min": 2.0,
        "change_rate_min_exclusive_pct": 3.0,
        "change_rate_max_pct": 10.0,
        "close_location_min": 0.80,
        "upper_wick_max": 0.20,
        "body_ratio_min": 0.55,
        "digest_ratio_min": 0.20,
        "digest_ratio_max": 0.80,
        "risk_rate_max": 0.06,
        "spread_pct_max": 0.10,
    },
    LANE_THEME_CONTINUATION: {
        "change_rate_min_pct": 4.0,
        "change_rate_max_pct": 9.0,
        "digest_ratio_min": 0.30,
        "digest_ratio_max": 0.60,
        "close_location_min": 0.65,
        "close_location_max": 0.90,
        "upper_wick_min": 0.10,
        "upper_wick_max": 0.35,
        "body_ratio_min": 0.55,
        "body_ratio_max": 0.75,
        "theme_breadth_min": 0.90,
        "leader_rank_max": 3,
        "follower_strong_count_min": 1,
        "volume_ratio_min": 1.20,
        "risk_rate_max": 0.05,
        "spread_pct_max": 0.10,
    },
}


def num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(str(value).replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _nested(mapping: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = mapping.get(key)
    return value if isinstance(value, Mapping) else {}


def first_num(mappings: Iterable[Mapping[str, Any]], *keys: str) -> float | None:
    for mapping in mappings:
        for key in keys:
            value = num(mapping.get(key))
            if value is not None:
                return value
    return None


def normalize_entity(value: Any) -> str:
    text = str(value or "").casefold()
    text = re.sub(r"(?:\(주\)|㈜|주식회사)", "", text)
    return re.sub(r"[^0-9a-z가-힣]+", "", text)


def is_common_stock_name(value: Any) -> bool:
    name = str(value or "")
    folded = name.casefold()
    if any(term in folded for term in NON_COMMON_INSTRUMENT_TERMS):
        return False
    return re.search(r"(?:우|우B|우C|우선주)$", name) is None


def contains_any(text: str, terms: Sequence[str]) -> bool:
    return any(term.casefold() in text.casefold() for term in terms)


def audit_direct_evidence(scored: Mapping[str, Any], *, change_rate: float | None = None) -> dict[str, Any]:
    """Audit direct-event evidence before it can qualify a direct lane.

    The company name or code must occur in the evidence.  A qualifying event must
    be economically concrete.  Negative/ambiguous wording blocks the direct lane.
    """
    evidence = _nested(scored, "evidence")
    title = str(evidence.get("title") or "").strip()
    body = str(evidence.get("body") or "").strip()
    text = f"{title} {body}".strip()
    company = normalize_entity(scored.get("name"))
    code = re.sub(r"\D", "", str(scored.get("code") or "")).zfill(6)
    normalized_text = normalize_entity(text)

    entity_match = bool(company and company in normalized_text) or bool(code and code in text)
    ambiguous = contains_any(text, NEGATIVE_OR_AMBIGUOUS_EVENT_TERMS)
    contract = contains_any(text, CONTRACT_EVENT_TERMS)
    clinical = contains_any(text, CLINICAL_EVENT_TERMS) and contains_any(text, CLINICAL_CONFIRM_TERMS)
    shareholder = contains_any(text, SHAREHOLDER_EVENT_TERMS)
    shareholder_unreacted = shareholder and change_rate is not None and change_rate <= 0.0

    if contract:
        event_type = "CONTRACT_OR_APPROVAL"
    elif clinical:
        event_type = "CLINICAL_CONFIRMED"
    elif shareholder_unreacted:
        event_type = "SHAREHOLDER_RETURN_UNREACTED"
    elif shareholder:
        event_type = "SHAREHOLDER_RETURN_ALREADY_REACTED"
    else:
        event_type = "UNCONFIRMED"

    blockers: list[str] = []
    if not text:
        blockers.append("evidence_missing")
    if text and not entity_match:
        blockers.append("company_entity_mismatch")
    if ambiguous:
        blockers.append("negative_or_ambiguous_context")
    if not (contract or clinical or shareholder_unreacted):
        blockers.append("concrete_event_unconfirmed")

    return {
        "passed": not blockers,
        "entity_match": entity_match,
        "ambiguous_context": ambiguous,
        "event_type": event_type,
        "title": title,
        "blockers": blockers,
    }


def _between(value: float | None, low: float, high: float, *, low_inclusive: bool = True, high_inclusive: bool = True) -> bool:
    if value is None:
        return False
    lower = value >= low if low_inclusive else value > low
    upper = value <= high if high_inclusive else value < high
    return lower and upper


def _spread(entry: Mapping[str, Any]) -> tuple[float | None, float | None, float | None]:
    ask = num(entry.get("entry_ask"))
    bid = num(entry.get("entry_bid"))
    spread = ((ask - bid) / ask * 100.0) if ask and bid and 0 < bid <= ask else None
    return ask, bid, spread


def v6_gate(scored: Mapping[str, Any], entry: Mapping[str, Any]) -> dict[str, Any]:
    """Classify one candidate under v6 without changing the v5 result."""
    structure = _nested(scored, "structure")
    theme = _nested(scored, "theme")
    evidence = _nested(scored, "evidence")
    metrics = _nested(scored, "metrics")
    original = _nested(scored, "original_candidate")
    original_metrics = _nested(original, "metrics")

    ask, bid, spread_pct = _spread(entry)
    change_rate = first_num((structure, scored, metrics, original, original_metrics), "change_rate", "change_pct")
    digestion = first_num((structure, scored, metrics, original, original_metrics), "digest_ratio", "digestion_ratio")
    risk = first_num((structure, scored, original), "risk_rate", "stop_distance")
    close_location = first_num((structure, scored, metrics, original_metrics), "close_location")
    upper_wick = first_num((structure, scored, metrics, original_metrics), "upper_wick", "upper_wick_ratio")
    body_ratio = first_num((structure, scored, metrics, original_metrics), "body_ratio")
    pattern_score = first_num((structure, _nested(scored, "pattern"), _nested(original, "pattern")), "pattern_score", "score")
    trade_value = first_num((scored, metrics, original, original_metrics), "trade_value", "trading_value")
    volume_ratio = first_num((scored, metrics, original, original_metrics), "volume_ratio", "volume_surge")
    directness = first_num((evidence, scored), "directness_points", "points_directness")
    freshness = first_num((evidence, scored), "freshness_points", "points_freshness")
    event_strength = first_num((evidence, scored), "event_strength", "evidence_strength")
    theme_breadth = first_num((theme, scored), "breadth", "theme_breadth")
    leader_rank = first_num((theme, scored), "leader_rank")
    follower_strong_count = first_num((theme, scored), "follower_strong_count")

    hard_reject = bool(scored.get("hard_reject"))
    common_stock = is_common_stock_name(scored.get("name"))
    quote_known = ask is not None and bid is not None and spread_pct is not None
    trade_value_ok = trade_value is not None and trade_value >= V6_CONFIG["common"]["min_trade_value_krw"]

    audit = audit_direct_evidence(scored, change_rate=change_rate)

    mom = V6_CONFIG[LANE_MOMENTUM]
    momentum_checks = {
        "hard_exclusion_clear": not hard_reject,
        "common_stock_only": common_stock,
        "entry_quote_known": quote_known,
        "trade_value_at_least_100b": trade_value_ok,
        "risk_at_most_10pct": risk is not None and risk <= mom["risk_rate_max"],
        "spread_at_most_0_20pct": spread_pct is not None and spread_pct <= mom["spread_pct_max"],
        "change_rate_10_to_15pct": _between(change_rate, mom["change_rate_min_pct"], mom["change_rate_max_pct"]),
        "digestion_0_30_to_1_00": _between(digestion, mom["digest_ratio_min"], mom["digest_ratio_max"]),
        "close_location_at_least_0_65": close_location is not None and close_location >= mom["close_location_min"],
        "upper_wick_at_most_0_35": upper_wick is not None and upper_wick <= mom["upper_wick_max"],
        "body_at_least_0_60": body_ratio is not None and body_ratio >= mom["body_ratio_min"],
        "theme_breadth_at_least_0_80": theme_breadth is not None and theme_breadth >= mom["theme_breadth_min"],
        "leader_rank_at_most_5": leader_rank is not None and leader_rank <= mom["leader_rank_max"],
        "two_strong_followers": follower_strong_count is not None and follower_strong_count >= mom["follower_strong_count_min"],
        "pattern_70_to_below_90": _between(pattern_score, mom["pattern_score_min"], mom["pattern_score_max_exclusive"], high_inclusive=False),
    }

    du = V6_CONFIG[LANE_DIRECT_UNREACTED]
    direct_unreacted_checks = {
        "hard_exclusion_clear": not hard_reject,
        "common_stock_only": common_stock,
        "entry_quote_known": quote_known,
        "trade_value_at_least_100b": trade_value_ok,
        "risk_at_most_5pct": risk is not None and risk <= du["risk_rate_max"],
        "spread_at_most_0_10pct": spread_pct is not None and spread_pct <= du["spread_pct_max"],
        "directness_at_least_15": directness is not None and directness >= du["directness_min"],
        "freshness_at_least_3": freshness is not None and freshness >= du["freshness_min"],
        "change_rate_minus5_to_plus3": _between(change_rate, du["change_rate_min_pct"], du["change_rate_max_pct"]),
        "close_location_at_most_0_35": close_location is not None and close_location <= du["close_location_max"],
        "digestion_0_10_to_1_50": _between(digestion, du["digest_ratio_min"], du["digest_ratio_max"]),
        "direct_evidence_audit": bool(audit["passed"]),
    }

    dr = V6_CONFIG[LANE_DIRECT_REACTED]
    direct_reacted_checks = {
        "hard_exclusion_clear": not hard_reject,
        "common_stock_only": common_stock,
        "entry_quote_known": quote_known,
        "trade_value_at_least_100b": trade_value_ok,
        "risk_at_most_6pct": risk is not None and risk <= dr["risk_rate_max"],
        "spread_at_most_0_10pct": spread_pct is not None and spread_pct <= dr["spread_pct_max"],
        "directness_at_least_14": directness is not None and directness >= dr["directness_min"],
        "freshness_at_least_7": freshness is not None and freshness >= dr["freshness_min"],
        "event_strength_at_least_2": event_strength is not None and event_strength >= dr["event_strength_min"],
        "change_rate_above3_to_10": _between(change_rate, dr["change_rate_min_exclusive_pct"], dr["change_rate_max_pct"], low_inclusive=False),
        "close_location_at_least_0_80": close_location is not None and close_location >= dr["close_location_min"],
        "upper_wick_at_most_0_20": upper_wick is not None and upper_wick <= dr["upper_wick_max"],
        "body_at_least_0_55": body_ratio is not None and body_ratio >= dr["body_ratio_min"],
        "digestion_0_20_to_0_80": _between(digestion, dr["digest_ratio_min"], dr["digest_ratio_max"]),
        "direct_evidence_audit": bool(audit["passed"]),
    }

    tc = V6_CONFIG[LANE_THEME_CONTINUATION]
    continuation_checks = {
        "hard_exclusion_clear": not hard_reject,
        "common_stock_only": common_stock,
        "entry_quote_known": quote_known,
        "trade_value_at_least_100b": trade_value_ok,
        "risk_at_most_5pct": risk is not None and risk <= tc["risk_rate_max"],
        "spread_at_most_0_10pct": spread_pct is not None and spread_pct <= tc["spread_pct_max"],
        "change_rate_4_to_9pct": _between(change_rate, tc["change_rate_min_pct"], tc["change_rate_max_pct"]),
        "digestion_0_30_to_0_60": _between(digestion, tc["digest_ratio_min"], tc["digest_ratio_max"]),
        "close_location_0_65_to_0_90": _between(close_location, tc["close_location_min"], tc["close_location_max"]),
        "upper_wick_0_10_to_0_35": _between(upper_wick, tc["upper_wick_min"], tc["upper_wick_max"]),
        "body_0_55_to_0_75": _between(body_ratio, tc["body_ratio_min"], tc["body_ratio_max"]),
        "theme_breadth_at_least_0_90": theme_breadth is not None and theme_breadth >= tc["theme_breadth_min"],
        "leader_rank_at_most_3": leader_rank is not None and leader_rank <= tc["leader_rank_max"],
        "one_strong_follower": follower_strong_count is not None and follower_strong_count >= tc["follower_strong_count_min"],
        "volume_ratio_at_least_1_20": volume_ratio is not None and volume_ratio >= tc["volume_ratio_min"],
    }

    lanes: list[str] = []
    if all(direct_reacted_checks.values()):
        lanes.append(LANE_DIRECT_REACTED)
    if all(momentum_checks.values()):
        lanes.append(LANE_MOMENTUM)
    if all(continuation_checks.values()):
        lanes.append(LANE_THEME_CONTINUATION)
    if all(direct_unreacted_checks.values()):
        lanes.append(LANE_DIRECT_UNREACTED)

    lane = "+".join(lanes) if lanes else LANE_NONE
    qualified = bool(lanes)
    if hard_reject or not common_stock:
        status = "BLOCK"
    elif qualified:
        status = "SHADOW_PASS"
    else:
        best_ratio = max(
            sum(momentum_checks.values()) / len(momentum_checks),
            sum(direct_unreacted_checks.values()) / len(direct_unreacted_checks),
            sum(direct_reacted_checks.values()) / len(direct_reacted_checks),
            sum(continuation_checks.values()) / len(continuation_checks),
        )
        status = "SHADOW_WATCH" if best_ratio >= 0.80 else "NONE"

    if risk is None:
        size_band = "NO_POSITION"
    elif risk <= 0.03:
        size_band = "BASE"
    elif risk <= 0.05:
        size_band = "HALF"
    elif risk <= 0.10:
        size_band = "QUARTER"
    else:
        size_band = "NO_POSITION"

    checks_by_lane = {
        LANE_MOMENTUM: momentum_checks,
        LANE_DIRECT_UNREACTED: direct_unreacted_checks,
        LANE_DIRECT_REACTED: direct_reacted_checks,
        LANE_THEME_CONTINUATION: continuation_checks,
    }
    if lanes:
        blockers: list[str] = []
    else:
        best_lane = max(checks_by_lane, key=lambda key: sum(checks_by_lane[key].values()) / len(checks_by_lane[key]))
        blockers = [key for key, passed in checks_by_lane[best_lane].items() if not passed]

    return {
        "version": V6_VERSION,
        "target_pct": TARGET_PCT,
        "research_only": True,
        "shadow_only": True,
        "status": status,
        "qualified": qualified,
        "eligible": qualified,
        "daily_pick": False,
        "daily_rank": None,
        "lane": lane,
        "lanes": lanes,
        "lane_priority": min((LANE_PRIORITY[value] for value in lanes), default=99),
        "spread_pct": None if spread_pct is None else round(spread_pct, 4),
        "risk_rate": risk,
        "trade_value": trade_value,
        "change_rate": change_rate,
        "digest_ratio": digestion,
        "close_location": close_location,
        "upper_wick": upper_wick,
        "body_ratio": body_ratio,
        "pattern_score": pattern_score,
        "volume_ratio": volume_ratio,
        "theme_breadth": theme_breadth,
        "leader_rank": leader_rank,
        "follower_strong_count": follower_strong_count,
        "directness_points": directness,
        "freshness_points": freshness,
        "event_strength": event_strength,
        "evidence_audit": audit,
        "size_band": size_band,
        "momentum_checks": momentum_checks,
        "direct_unreacted_checks": direct_unreacted_checks,
        "direct_reacted_checks": direct_reacted_checks,
        "theme_continuation_checks": continuation_checks,
        "blockers": blockers,
        "note": "v5를 대체하지 않는 v6 그림자 검증 규칙입니다.",
    }


def v6_sort_key(candidate: Mapping[str, Any]) -> tuple[Any, ...]:
    """Return a deterministic, lane-specific daily selection key.

    A direct-unreacted event is ranked by execution risk first.  Momentum is
    ranked by fresh participation first.  This avoids forcing unlike setups
    into one opaque composite score.
    """
    gate = _nested(candidate, "v6")
    lanes = [str(value) for value in gate.get("lanes") or []]
    primary = min(lanes, key=lambda value: LANE_PRIORITY.get(value, 99), default=LANE_NONE)
    priority = LANE_PRIORITY.get(primary, 99)

    freshness = num(gate.get("freshness_points")) or 0.0
    directness = num(gate.get("directness_points")) or 0.0
    event_strength = num(gate.get("event_strength")) or 0.0
    volume_ratio = min(num(gate.get("volume_ratio")) or 0.0, 5.0)
    close_location = num(gate.get("close_location")) or 0.0
    theme_breadth = num(gate.get("theme_breadth")) or 0.0
    leader_rank = num(gate.get("leader_rank"))
    risk = num(gate.get("risk_rate"))
    spread = num(gate.get("spread_pct"))
    turnover = num(gate.get("trade_value")) or num(candidate.get("trade_value")) or 0.0
    code = str(candidate.get("code") or "")

    risk_key = risk if risk is not None else math.inf
    spread_key = spread if spread is not None else math.inf
    leader_key = leader_rank if leader_rank is not None else math.inf

    if primary == LANE_DIRECT_REACTED:
        quality = (-freshness, -directness, -event_strength, -close_location)
    elif primary == LANE_MOMENTUM:
        quality = (-freshness, -volume_ratio, -close_location, -theme_breadth)
    elif primary == LANE_THEME_CONTINUATION:
        quality = (-theme_breadth, leader_key, -volume_ratio, -close_location)
    elif primary == LANE_DIRECT_UNREACTED:
        # The event has not yet been priced in.  Minimize overnight structural
        # and execution risk before considering turnover.
        quality = (risk_key, spread_key, -turnover, -freshness)
        return (priority, *quality, code)
    else:
        quality = ()

    return (priority, *quality, risk_key, spread_key, -turnover, code)


def apply_daily_v6_selection(candidates: Sequence[MutableMapping[str, Any]]) -> None:
    qualified: list[MutableMapping[str, Any]] = []
    for candidate in candidates:
        gate = candidate.get("v6") if isinstance(candidate.get("v6"), MutableMapping) else None
        if gate is None:
            continue
        gate["daily_pick"] = False
        gate["daily_rank"] = None
        if bool(gate.get("qualified")) and gate.get("status") != "BLOCK" and not gate.get("operational_blockers"):
            qualified.append(candidate)
        else:
            gate["eligible"] = False

    qualified.sort(key=v6_sort_key)
    for rank, candidate in enumerate(qualified, start=1):
        gate = candidate["v6"]
        gate["daily_rank"] = rank
        gate["daily_pick"] = rank == 1
        gate["eligible"] = rank == 1
        if rank == 1:
            gate["status"] = "SHADOW_PASS"
        else:
            gate["status"] = "SHADOW_ALTERNATE"
            gate["blockers"] = list(dict.fromkeys(list(gate.get("blockers") or []) + ["daily_one_pick_only"]))
