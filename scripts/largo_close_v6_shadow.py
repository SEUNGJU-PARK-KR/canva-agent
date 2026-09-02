#!/usr/bin/env python3
"""Largo closing-bet v6 shadow gate.

Research-only shadow strategy. It never places orders and never replaces v5.
It consumes the evidence, theme, structure and exact quote fields frozen by the
15:18 capture pipeline. No historical news is re-fetched inside this module.
"""
from __future__ import annotations

import math
import re
from typing import Any, Mapping, Sequence

VERSION = "largo-close-v6-shadow"
TARGET_PCT = 3.0

NEGATIVE_CONTEXT = (
    "급락", "하락", "약세", "부담", "우려", "악화", "희석", "적자", "손실",
    "소송", "제재", "조사", "불확실", "철회", "해지", "거래정지", "상장폐지",
    "전환사채", "유상증자", "투자경고", "투자위험", "단기과열",
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


def normalize(value: Any) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]+", "", str(value or "")).casefold()


def is_common_stock_name(value: Any) -> bool:
    name = str(value or "")
    folded = name.casefold()
    if any(term.casefold() in folded for term in NON_COMMON_INSTRUMENT_TERMS):
        return False
    return re.search(r"(?:우|우B|우C|우선주)$", name) is None


def company_aliases(name: Any) -> set[str]:
    raw = str(name or "").strip()
    candidates = {
        raw,
        re.sub(r"\s*(?:주식회사|\(주\)|㈜)\s*", "", raw),
        re.sub(r"\s+", "", raw),
    }
    normalized = {normalize(value) for value in candidates if normalize(value)}
    return {value for value in normalized if len(value) >= 4}


def evidence_audit(scored: Mapping[str, Any]) -> dict[str, Any]:
    evidence = scored.get("evidence") if isinstance(scored.get("evidence"), Mapping) else {}
    title = str(evidence.get("title") or "")
    title_norm = normalize(title)
    aliases = company_aliases(scored.get("name"))
    exact_company = bool(aliases and any(alias in title_norm for alias in aliases))
    negative = bool(evidence.get("negative")) or any(
        term.casefold() in title.casefold() for term in NEGATIVE_CONTEXT
    )
    direct_benefit = bool(evidence.get("direct_benefit"))
    strength = num(evidence.get("event_strength"))
    directness = num(evidence.get("directness_points"))
    freshness = num(evidence.get("freshness_points"))
    observed = bool(evidence.get("observed"))
    passed = bool(
        observed
        and exact_company
        and direct_benefit
        and not negative
        and strength is not None
        and strength >= 2
        and directness is not None
        and freshness is not None
    )
    reasons: list[str] = []
    if not observed:
        reasons.append("frozen_evidence_missing")
    if not exact_company:
        reasons.append("candidate_company_not_in_evidence_title")
    if not direct_benefit:
        reasons.append("direct_benefit_not_confirmed")
    if negative:
        reasons.append("negative_context")
    if strength is None or strength < 2:
        reasons.append("strong_event_not_confirmed")
    return {
        "passed": passed,
        "exact_company": exact_company,
        "negative_context": negative,
        "direct_benefit": direct_benefit,
        "event_strength": strength,
        "directness_points": directness,
        "freshness_points": freshness,
        "title": title,
        "reasons": reasons,
    }


def _metrics(scored: Mapping[str, Any], entry: Mapping[str, Any]) -> dict[str, Any]:
    evidence = scored.get("evidence") if isinstance(scored.get("evidence"), Mapping) else {}
    theme = scored.get("theme") if isinstance(scored.get("theme"), Mapping) else {}
    structure = scored.get("structure") if isinstance(scored.get("structure"), Mapping) else {}
    ask = num(entry.get("entry_ask"))
    bid = num(entry.get("entry_bid"))
    spread = ((ask - bid) / ask * 100.0) if ask and bid and 0 < bid <= ask else None
    return {
        "ask": ask,
        "bid": bid,
        "spread_pct": spread,
        "hard_reject": bool(scored.get("hard_reject")),
        "common_stock": is_common_stock_name(scored.get("name")),
        "change_rate": num(structure.get("change_rate")),
        "digest_ratio": num(structure.get("digest_ratio")),
        "risk_rate": num(structure.get("risk_rate")),
        "close_location": num(structure.get("close_location")),
        "upper_wick": num(structure.get("upper_wick")),
        "body_ratio": num(structure.get("body_ratio")),
        "theme_breadth": num(theme.get("breadth")),
        "theme_leader_rank": num(theme.get("leader_rank")),
        "follower_strong_count": num(theme.get("follower_strong_count")),
        "follower_turnover": num(theme.get("follower_turnover")),
        "directness": num(evidence.get("directness_points")),
        "freshness": num(evidence.get("freshness_points")),
    }


def _between(value: float | None, low: float, high: float) -> bool:
    return value is not None and low <= value <= high


def _at_least(value: float | None, threshold: float) -> bool:
    return value is not None and value >= threshold


def _at_most(value: float | None, threshold: float) -> bool:
    return value is not None and value <= threshold


def target6_shadow_gate(scored: Mapping[str, Any], entry: Mapping[str, Any]) -> dict[str, Any]:
    """Return a v6 shadow classification without changing the v5 decision."""
    metrics = _metrics(scored, entry)
    audit = evidence_audit(scored)

    common = {
        "hard_exclusion_clear": not metrics["hard_reject"],
        "common_stock_only": bool(metrics["common_stock"]),
        "exact_1518_quote": metrics["ask"] is not None and metrics["bid"] is not None and metrics["spread_pct"] is not None,
        "structural_risk_at_most_10pct": _at_most(metrics["risk_rate"], 0.10),
        "digestion_at_least_0_10": _at_least(metrics["digest_ratio"], 0.10),
    }

    theme_momentum = {
        **common,
        "change_rate_10_to_15pct": _between(metrics["change_rate"], 10.0, 15.0),
        "digestion_0_30_to_1_00": _between(metrics["digest_ratio"], 0.30, 1.00),
        "theme_breadth_at_least_0_80": _at_least(metrics["theme_breadth"], 0.80),
        "theme_rank_at_most_3": metrics["theme_leader_rank"] is not None and metrics["theme_leader_rank"] <= 3,
        "close_location_at_least_0_70": _at_least(metrics["close_location"], 0.70),
        "upper_wick_at_most_0_20": _at_most(metrics["upper_wick"], 0.20),
        "spread_at_most_0_20pct": _at_most(metrics["spread_pct"], 0.20),
    }

    close_strength = {
        **common,
        "change_rate_7_to_15pct": _between(metrics["change_rate"], 7.0, 15.0),
        "digestion_0_30_to_1_00": _between(metrics["digest_ratio"], 0.30, 1.00),
        "close_location_at_least_0_90": _at_least(metrics["close_location"], 0.90),
        "upper_wick_at_most_0_10": _at_most(metrics["upper_wick"], 0.10),
        "body_ratio_at_least_0_45": _at_least(metrics["body_ratio"], 0.45),
        "theme_or_direct_confirmation": _at_least(metrics["theme_breadth"], 0.55) or _at_least(metrics["directness"], 12.0),
        "risk_at_most_0_08": _at_most(metrics["risk_rate"], 0.08),
        "spread_at_most_0_15pct": _at_most(metrics["spread_pct"], 0.15),
    }

    direct_fresh = {
        **common,
        "frozen_evidence_audit": bool(audit["passed"]),
        "directness_at_least_14": _at_least(metrics["directness"], 14.0),
        "freshness_at_least_7": _at_least(metrics["freshness"], 7.0),
        "change_rate_at_most_10pct": metrics["change_rate"] is not None and metrics["change_rate"] <= 10.0,
        "digestion_0_10_to_1_00": _between(metrics["digest_ratio"], 0.10, 1.00),
        "close_location_at_least_0_60": _at_least(metrics["close_location"], 0.60),
        "upper_wick_at_most_0_25": _at_most(metrics["upper_wick"], 0.25),
        "risk_at_most_0_06": _at_most(metrics["risk_rate"], 0.06),
        "spread_at_most_0_10pct": _at_most(metrics["spread_pct"], 0.10),
    }

    direct_continuation = {
        **common,
        "frozen_evidence_audit": bool(audit["passed"]),
        "directness_at_least_15": _at_least(metrics["directness"], 15.0),
        "freshness_at_least_3": _at_least(metrics["freshness"], 3.0),
        "change_rate_0_to_8pct": _between(metrics["change_rate"], 0.0, 8.0),
        "digestion_0_30_to_0_90": _between(metrics["digest_ratio"], 0.30, 0.90),
        "close_location_at_least_0_75": _at_least(metrics["close_location"], 0.75),
        "body_ratio_at_least_0_25": _at_least(metrics["body_ratio"], 0.25),
        "risk_at_most_0_06": _at_most(metrics["risk_rate"], 0.06),
        "spread_at_most_0_10pct": _at_most(metrics["spread_pct"], 0.10),
    }

    checks_by_lane = {
        "THEME_MOMENTUM": theme_momentum,
        "CLOSE_STRENGTH": close_strength,
        "DIRECT_FRESH": direct_fresh,
        "DIRECT_CONTINUATION": direct_continuation,
    }
    passed = [lane for lane, checks in checks_by_lane.items() if all(checks.values())]

    if "DIRECT_FRESH" in passed and "THEME_MOMENTUM" in passed:
        lane = "DIRECT_AND_THEME"
    elif passed:
        priority = ("DIRECT_FRESH", "THEME_MOMENTUM", "CLOSE_STRENGTH", "DIRECT_CONTINUATION")
        lane = next(value for value in priority if value in passed)
    else:
        lane = "NONE"

    eligible = lane != "NONE"
    if metrics["hard_reject"] or not metrics["common_stock"] or (metrics["risk_rate"] is not None and metrics["risk_rate"] > 0.10):
        status = "BLOCK"
    elif eligible:
        status = "SHADOW_PASS"
    else:
        status = "WATCH"

    confirmation = 0
    confirmation += 3 if lane in {"DIRECT_FRESH", "DIRECT_AND_THEME"} else 0
    confirmation += 2 if lane in {"THEME_MOMENTUM", "DIRECT_AND_THEME"} else 0
    confirmation += 1 if lane == "CLOSE_STRENGTH" else 0
    confirmation += 1 if audit["passed"] else 0
    confirmation += 1 if _at_least(metrics["theme_breadth"], 0.80) else 0
    confirmation += 1 if _at_least(metrics["close_location"], 0.90) else 0

    if metrics["risk_rate"] is None:
        size_band = "NO_POSITION"
    elif metrics["risk_rate"] <= 0.04:
        size_band = "BASE"
    elif metrics["risk_rate"] <= 0.06:
        size_band = "HALF"
    elif metrics["risk_rate"] <= 0.10:
        size_band = "QUARTER"
    else:
        size_band = "NO_POSITION"

    if lane == "DIRECT_AND_THEME":
        blockers: list[str] = []
    elif lane in checks_by_lane:
        blockers = [key for key, passed_check in checks_by_lane[lane].items() if not passed_check]
    else:
        alternatives = [
            [key for key, passed_check in checks.items() if not passed_check]
            for checks in checks_by_lane.values()
        ]
        blockers = min(alternatives, key=len)

    return {
        "version": VERSION,
        "target_pct": TARGET_PCT,
        "research_only": True,
        "shadow_only": True,
        "status": status,
        "qualified": eligible,
        "eligible": eligible,
        "lane": lane,
        "passed_lanes": passed,
        "confirmation_count": confirmation,
        "spread_pct": None if metrics["spread_pct"] is None else round(float(metrics["spread_pct"]), 4),
        "risk_rate": metrics["risk_rate"],
        "digest_ratio": metrics["digest_ratio"],
        "change_rate": metrics["change_rate"],
        "theme_breadth": metrics["theme_breadth"],
        "theme_leader_rank": metrics["theme_leader_rank"],
        "evidence_audit": audit,
        "checks": checks_by_lane,
        "blockers": blockers,
        "size_band": size_band,
        "daily_pick": False,
        "daily_rank": None,
        "note": "v5를 대체하지 않는 그림자 규칙입니다. 7월은 학습용 대용표본, 8월은 실행가능 검증표본으로 분리합니다.",
    }


LANE_PRIORITY = {
    "DIRECT_AND_THEME": 0,
    "DIRECT_FRESH": 1,
    "THEME_MOMENTUM": 2,
    "CLOSE_STRENGTH": 3,
    "DIRECT_CONTINUATION": 4,
}


def shadow_sort_key(candidate: Mapping[str, Any]) -> tuple[float, ...]:
    gate = candidate.get("target6_shadow") if isinstance(candidate.get("target6_shadow"), Mapping) else {}
    structure = candidate.get("structure") if isinstance(candidate.get("structure"), Mapping) else {}
    risk = num(gate.get("risk_rate"))
    spread = num(gate.get("spread_pct"))
    confirmation = num(gate.get("confirmation_count")) or 0.0
    trade_value = num(candidate.get("trade_value")) or 0.0
    close_location = num(structure.get("close_location")) or 0.0
    return (
        float(LANE_PRIORITY.get(str(gate.get("lane")), 99)),
        -float(confirmation),
        float(risk) if risk is not None else math.inf,
        float(spread) if spread is not None else math.inf,
        -float(close_location),
        -float(trade_value),
        str(candidate.get("code") or ""),
    )


def apply_daily_shadow_selection(candidates: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    qualified: list[dict[str, Any]] = []
    for row in candidates:
        if not isinstance(row, dict):
            continue
        gate = row.get("target6_shadow")
        if not isinstance(gate, dict):
            gate = target6_shadow_gate(row, row)
            row["target6_shadow"] = gate
        gate["daily_pick"] = False
        gate["daily_rank"] = None
        if gate.get("eligible") and gate.get("status") == "SHADOW_PASS":
            qualified.append(row)
    qualified.sort(key=shadow_sort_key)
    for rank, row in enumerate(qualified, 1):
        gate = row["target6_shadow"]
        gate["daily_rank"] = rank
        gate["daily_pick"] = rank == 1
        if rank > 1:
            gate["status"] = "SHADOW_ALTERNATE"
    return qualified
