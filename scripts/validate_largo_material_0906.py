#!/usr/bin/env python3
"""Offline validation for the deterministic Largo material and 09:06 pipeline."""
from __future__ import annotations

import datetime as dt

from largo_material_0906 import (
    KST,
    catalyst_evidence,
    entry_from_legacy,
    event_significance,
    outcome_before_0906,
    score_candidate,
    theme_metrics_from_members,
)


def fixture_candidate() -> dict:
    return {
        "code": "123456",
        "name": "테스트종목",
        "price": 10000,
        "theme": {"code": "T001", "name": "로봇", "leader_rank": 1},
        "catalyst": {"grade": "S", "reason": "로봇 관련주 6/8 상승", "positive_titles": ["테스트종목 공급계약 체결"]},
        "metrics": {"close_location": 0.93, "upper_wick": 0.04, "body_ratio": 0.71, "digest_ratio": 1.1},
        "pattern": {"id": "C1", "score": 92},
        "plan": {"stop_distance": 0.02},
        "checks": [
            {"id": "X_RISK", "status": "PASS"},
            {"id": "X_TYPE", "status": "PASS"},
            {"id": "C_SEQUENCE", "status": "PASS"},
        ],
    }


def main() -> None:
    signal_at = dt.datetime(2026, 9, 1, 15, 18, tzinfo=KST)
    significance = event_significance("테스트종목 500억원 공급계약, 최근 매출액 대비 12.5%")
    assert significance["magnitude"] == "HIGH"
    assert significance["revenue_ratio_pct"] == 12.5
    assert significance["amount_krw"] == 50_000_000_000

    candidate = fixture_candidate()
    items = [{
        "title": "테스트종목 500억원 공급계약 체결",
        "body": "최근 매출액 대비 12.5%",
        "at": signal_at - dt.timedelta(hours=2),
        "source": "notice",
    }]
    evidence = catalyst_evidence(candidate, signal_at, items)
    assert evidence["direct_benefit"] is True
    assert evidence["directness_points"] == 18
    assert evidence["freshness_points"] == 10
    assert evidence["magnitude"] == "HIGH"

    future = catalyst_evidence(candidate, signal_at, [{**items[0], "at": signal_at + dt.timedelta(hours=1)}])
    assert future["title"] is None

    negative = catalyst_evidence(candidate, signal_at, [{
        "title": "테스트종목 유상증자 결정",
        "body": "",
        "at": signal_at - dt.timedelta(hours=1),
        "source": "notice",
    }])
    assert negative["negative"] is True

    members = [
        {"code": "123456", "name": "테스트종목", "change_rate": 12.0, "trade_value": 80_000_000_000},
        {"code": "111111", "name": "후속1", "change_rate": 8.0, "trade_value": 40_000_000_000},
        {"code": "222222", "name": "후속2", "change_rate": 5.0, "trade_value": 30_000_000_000},
        {"code": "333333", "name": "후속3", "change_rate": 2.0, "trade_value": 8_000_000_000},
        {"code": "444444", "name": "소폭상승", "change_rate": 0.5, "trade_value": 4_000_000_000},
        {"code": "555555", "name": "하락", "change_rate": -1.0, "trade_value": 4_000_000_000},
    ]
    theme = theme_metrics_from_members(candidate, members)
    assert theme["leader_rank"] == 1
    assert theme["follower_strong_count"] == 2
    assert theme["follower_turnover"] == 70_000_000_000
    assert abs(theme["breadth"] - 5 / 6) < 1e-9

    history = [
        {"stage": "14:53", "at": "2026-09-01T14:53:00+09:00", "themes": {"T001": {"breadth": 0.67}}},
        {"stage": "15:10", "at": "2026-09-01T15:10:00+09:00", "themes": {"T001": {"breadth": 0.72}}},
    ]
    scored = score_candidate(candidate, signal_at, items, theme_metrics=theme, theme_history=history)
    assert scored["grade"] == "S"
    assert scored["grade_status"] == "CONFIRMED"
    assert scored["coverage"] == 1.0
    assert scored["production_score"] is not None

    signal_rows = [["15:18", "10,000", "0", "10,010", "10,000", "100", "20"]]
    entry = entry_from_legacy(signal_rows)
    assert entry["entry_ask"] == 10010
    open_rows = [
        ["09:05", "10,080", "0", "10,090", "10,080", "200", "20"],
        ["09:04", "10,130", "0", "10,140", "10,130", "180", "20"],
        ["09:06", "10,500", "0", "10,510", "10,500", "220", "20"],
    ]
    result = outcome_before_0906(open_rows, entry_last=entry["entry_last"], entry_ask=entry["entry_ask"])
    assert result["max_bid"] == 10130
    assert result["last_time"] == "09:05"
    assert result["open_observations"] == 2
    assert result["hit_1_exec"] is True

    print({
        "status": "PASS",
        "grade": scored["grade"],
        "score": scored["production_score"],
        "coverage": scored["coverage"],
        "max_exec_return_pct": result["max_executable_return_pct"],
    })


if __name__ == "__main__":
    main()
