#!/usr/bin/env python3
"""Append the fixed pre-09:06 +3% research gate to a pinned scoring module."""
from __future__ import annotations

import sys
from pathlib import Path

PATCH = r'''

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
'''


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: patch_target3_module.py <module-path>")
    path = Path(sys.argv[1])
    text = path.read_text(encoding="utf-8")
    if "def target3_gate(" not in text:
        path.write_text(text.rstrip() + "\n" + PATCH, encoding="utf-8")
    patched = path.read_text(encoding="utf-8")
    assert 'TARGET3_VERSION = "largo-target3-v1"' in patched
    assert "def target3_gate(" in patched
    print({"path": str(path), "bytes": len(patched.encode("utf-8"))})


if __name__ == "__main__":
    main()
