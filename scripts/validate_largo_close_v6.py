#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from largo_close_v6 import (
    LANE_DIRECT_UNREACTED,
    LANE_MOMENTUM,
    V6_VERSION,
    apply_daily_v6_selection,
    audit_direct_evidence,
    v6_gate,
)


def fixture_direct() -> dict:
    return {
        "code": "012450",
        "name": "한화에어로스페이스",
        "hard_reject": False,
        "trade_value": 300_000_000_000,
        "metrics": {"change_rate": 2.0},
        "structure": {
            "change_rate": 2.0,
            "digest_ratio": 0.60,
            "risk_rate": 0.03,
            "close_location": 0.20,
            "upper_wick": 0.70,
            "body_ratio": 0.10,
            "pattern_score": 80,
        },
        "evidence": {
            "title": "한화에어로스페이스(주) (정정)단일판매ㆍ공급계약체결",
            "directness_points": 15,
            "freshness_points": 7,
            "event_strength": 2,
        },
        "theme": {},
    }


def fixture_momentum(name: str, freshness: float, volume_ratio: float) -> dict:
    return {
        "code": "000720" if name == "현대건설" else "034020",
        "name": name,
        "hard_reject": False,
        "trade_value": 300_000_000_000,
        "metrics": {"volume_ratio": volume_ratio},
        "structure": {
            "change_rate": 12.0,
            "digest_ratio": 0.50,
            "risk_rate": 0.06,
            "close_location": 0.85,
            "upper_wick": 0.10,
            "body_ratio": 0.70,
            "pattern_score": 80,
        },
        "evidence": {"directness_points": 10, "freshness_points": freshness, "event_strength": 1},
        "theme": {"breadth": 0.90, "leader_rank": 2, "follower_strong_count": 2},
    }


def run_unit_checks() -> dict:
    direct = fixture_direct()
    audit = audit_direct_evidence(direct, change_rate=2.0)
    assert audit["passed"] is True
    gate = v6_gate(direct, {"entry_ask": 100_000, "entry_bid": 99_950})
    assert gate["version"] == V6_VERSION
    assert LANE_DIRECT_UNREACTED in gate["lanes"]

    mismatch = fixture_direct()
    mismatch["evidence"] = dict(mismatch["evidence"], title="SKIET, 합병 비율 부담에 이틀째 급락")
    blocked = audit_direct_evidence(mismatch, change_rate=2.0)
    assert blocked["passed"] is False
    assert "company_entity_mismatch" in blocked["blockers"]
    assert "negative_or_ambiguous_context" in blocked["blockers"]

    fresh = fixture_momentum("현대건설", 10, 2.0)
    stale = fixture_momentum("두산에너빌리티", 3, 1.2)
    for row in (fresh, stale):
        row["v6"] = v6_gate(row, {"entry_ask": 100_000, "entry_bid": 99_900})
        assert LANE_MOMENTUM in row["v6"]["lanes"]
    apply_daily_v6_selection([stale, fresh])
    assert fresh["v6"]["eligible"] is True
    assert stale["v6"]["eligible"] is False

    return {"version": V6_VERSION, "unit_checks": "PASS"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--unit-only", action="store_true")
    parser.add_argument("--output")
    args = parser.parse_args()
    result = run_unit_checks()
    if args.output:
        Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
