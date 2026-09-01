#!/usr/bin/env python3
"""Offline checks for the Largo closing-bet v5 research rule."""
from __future__ import annotations

import datetime as dt

from capture_largo_material_0906 import apply_daily_target3_selection
from largo_material_0906 import (
    KST,
    TARGET3_VERSION,
    catalyst_evidence,
    entry_from_legacy,
    outcome_before_0906,
    score_candidate,
    target3_gate,
    theme_metrics_from_members,
)


def candidate(name: str = "테스트종목", risk: float = 0.05) -> dict:
    return {
        "code": "123456",
        "name": name,
        "price": 10000,
        "trade_value": 80_000_000_000,
        "theme": {"code": "T001", "name": "로봇", "leader_rank": 1},
        "catalyst": {"grade": "S", "reason": "공급계약", "positive_titles": [f"{name} 공급계약 체결"]},
        "metrics": {
            "close_location": 0.80,
            "upper_wick": 0.15,
            "body_ratio": 0.55,
            "digest_ratio": 0.80,
            "change_rate": 12.0,
            "trade_value": 80_000_000_000,
        },
        "pattern": {"id": "C1", "score": 80},
        "plan": {"stop_distance": risk},
        "checks": [
            {"id": "X_RISK", "status": "PASS"},
            {"id": "X_TYPE", "status": "PASS"},
            {"id": "C_SEQUENCE", "status": "PASS"},
        ],
    }


def scored_fixture(name: str = "테스트종목", risk: float = 0.05) -> dict:
    signal_at = dt.datetime(2026, 9, 1, 15, 18, tzinfo=KST)
    item = {
        "title": f"{name} 500억원 공급계약 체결",
        "body": "최근 매출액 대비 12.5%",
        "at": signal_at - dt.timedelta(hours=2),
        "source": "notice",
    }
    members = [
        {"code": "123456", "name": name, "change_rate": 12.0, "trade_value": 80_000_000_000},
        {"code": "111111", "name": "후속1", "change_rate": 5.0, "trade_value": 40_000_000_000},
        {"code": "222222", "name": "후속2", "change_rate": 3.0, "trade_value": 30_000_000_000},
        {"code": "333333", "name": "하락", "change_rate": -1.0, "trade_value": 5_000_000_000},
    ]
    source = candidate(name=name, risk=risk)
    theme = theme_metrics_from_members(source, members)
    return score_candidate(source, signal_at, [item], theme_metrics=theme, theme_history=[])


def main() -> None:
    entry = entry_from_legacy([["15:18", "10,000", "0", "10,010", "10,000", "100", "20"]])
    scored = scored_fixture()
    assert scored["structure"]["change_rate"] == 12.0
    gate = target3_gate(scored, entry)
    assert gate["version"] == TARGET3_VERSION == "largo-close-v5"
    assert gate["qualified"] is True
    assert gate["eligible"] is True
    assert gate["lane"] == "BOTH"
    assert gate["size_band"] == "HALF"

    risky = target3_gate(scored_fixture(risk=0.11), entry)
    assert risky["qualified"] is False
    assert risky["status"] == "BLOCK"

    fund = target3_gate(scored_fixture(name="RISE 200"), entry)
    assert fund["qualified"] is False
    assert fund["status"] == "BLOCK"

    first = {**scored, "name": "낮은위험", "trade_value": 50_000_000_000, "target3": target3_gate(scored, entry)}
    second_scored = scored_fixture(name="높은위험", risk=0.08)
    second = {**second_scored, "trade_value": 100_000_000_000, "target3": target3_gate(second_scored, entry)}
    apply_daily_target3_selection([second, first])
    assert first["target3"]["daily_pick"] is True
    assert first["target3"]["eligible"] is True
    assert second["target3"]["daily_pick"] is False
    assert second["target3"]["status"] == "ALTERNATE"

    open_rows = [
        ["09:05", "10,240", "0", "10,250", "10,240", "200", "20"],
        ["09:04", "10,350", "0", "10,360", "10,350", "180", "20"],
        ["09:06", "10,600", "0", "10,610", "10,600", "220", "20"],
    ]
    outcome = outcome_before_0906(open_rows, entry_last=entry["entry_last"], entry_ask=entry["entry_ask"])
    assert outcome["max_bid"] == 10350
    assert outcome["last_time"] == "09:05"
    assert outcome["open_observations"] == 2
    assert outcome["max_executable_return_pct"] > 3.0

    evidence = catalyst_evidence(candidate(), dt.datetime(2026, 9, 1, 15, 18, tzinfo=KST), [{
        "title": "테스트종목 500억원 공급계약 체결",
        "body": "최근 매출액 대비 12.5%",
        "at": dt.datetime(2026, 9, 1, 13, 18, tzinfo=KST),
        "source": "notice",
    }])
    assert evidence["directness_points"] >= 14
    print({"status": "PASS", "version": TARGET3_VERSION, "lane": gate["lane"], "daily_pick": first["name"]})


if __name__ == "__main__":
    main()
