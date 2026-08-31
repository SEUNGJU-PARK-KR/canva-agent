#!/usr/bin/env python3
"""Deterministic smoke tests for Largo win-rate v4.1 rules."""
from __future__ import annotations

import datetime as dt

from largo_winrate_v4 import KST, VERSION, analyze_candidate, event_item_matches, theme_breadth


def candidate(*, code: str = "000001", rank: int = 3, digest: float = 0.60,
              loc: float = 0.90, wick: float = 0.10, body: float = 0.70,
              reason: str = "전력설비 관련주 30/35 상승") -> dict:
    checks = [
        {"id": "X_MARKET_CAP", "name": "시총", "role": "required", "status": "PASS", "reason": "통과"},
        {"id": "X_TRADE_VALUE", "name": "거래대금", "role": "required", "status": "PASS", "reason": "통과"},
        {"id": "X_RISK", "name": "위험", "role": "required", "status": "PASS", "reason": "통과"},
        {"id": "X_TYPE", "name": "보통주", "role": "required", "status": "PASS", "reason": "통과"},
        {"id": "X_IPO", "name": "상장 경과", "role": "required", "status": "PASS", "reason": "통과"},
        {"id": "X_POLITICAL", "name": "정치 제외", "role": "required", "status": "PASS", "reason": "통과"},
        {"id": "X_PENNY", "name": "동전주", "role": "required", "status": "PASS", "reason": "통과"},
        {"id": "C_SEQUENCE", "name": "마감 과정", "role": "required", "status": "PASS", "reason": "통과"},
        {"id": "C_LOCATION", "name": "종가 위치", "role": "required", "status": "PASS", "reason": "통과"},
        {"id": "C_WICK", "name": "윗꼬리", "role": "required", "status": "PASS", "reason": "통과"},
    ]
    return {
        "code": code, "name": "테스트종목", "price": 10000, "change_rate": 10.0,
        "trade_value": 100_000_000_000, "market_cap": 1_000_000_000_000,
        "quality_score": 90,
        "catalyst": {"grade": "S", "reason": reason, "positive_titles": ["테스트종목 공급계약 체결"]},
        "pattern": {"id": "C1", "name": "이평 수렴 돌파", "score": 85},
        "metrics": {
            "close_location": loc, "upper_wick_ratio": wick, "body_ratio": body,
            "digest_ratio": digest, "ma20": 9500, "ma60": 9000,
        },
        "plan": {"initial_size": "NORMAL"}, "checks": checks,
        "automation_356": {
            "risk_plan": {"status": "PASS", "entry_price": 10000, "stop_price": 9600,
                          "stop_source": "구조선", "risk_rate": 0.04, "one_r_price": 10400},
            "next_day_plan": {"gap_up": "추격 금지"},
        },
        "theme": {"rank": rank},
    }


def history(code: str) -> dict:
    return {
        "snapshots": [
            {"at": "2026-09-01T15:10:00+09:00", "items": {code: {"pattern": "C1"}}},
            {"at": "2026-09-01T15:18:00+09:00", "items": {code: {"pattern": "C1"}}},
        ]
    }


def main() -> None:
    assert VERSION == "largo-winrate-v4.1"
    source_at = dt.datetime(2026, 9, 1, 15, 18, tzinfo=KST)

    core = candidate()
    result = analyze_candidate(core, source_at, history(core["code"]))
    assert result["lane"] == "CLOSE_ENTRY" and result["close_variant"] == "CORE", result

    elite = candidate(code="000002", rank=2, digest=0.34, loc=1.0, wick=0.0, body=0.60)
    result = analyze_candidate(elite, source_at, history(elite["code"]))
    assert result["lane"] == "CLOSE_ENTRY" and result["close_variant"] == "ELITE", result

    confirmation = candidate(code="000003", rank=5, digest=0.80, loc=0.91, wick=0.09, body=0.73,
                             reason="전력설비 관련주 24/34 상승")
    result = analyze_candidate(confirmation, source_at, history(confirmation["code"]))
    assert result["lane"] == "NEXT_DAY_CONFIRM" and not result["close_fit"], result

    unresolved = candidate(code="000004")
    unresolved["metrics"] = {"close_location": 0.90, "upper_wick_ratio": 0.10, "body_ratio": 0.70, "digest_ratio": 0.60}
    for check in unresolved["checks"]:
        if check["id"] == "X_IPO":
            check["status"] = "WARN"
    result = analyze_candidate(unresolved, source_at, history(unresolved["code"]))
    assert result["lane"] == "EXCLUDE" and result["hard_unresolved"], result

    negative = candidate(code="000005")
    negative["catalyst"] = {"grade": "S", "reason": "대규모 유상증자", "positive_titles": ["테스트종목 유상증자"]}
    result = analyze_candidate(negative, source_at, history(negative["code"]))
    assert result["lane"] == "EXCLUDE", result

    assert abs((theme_breadth(core) or 0) - (30 / 35)) < 1e-9
    assert event_item_matches(core, {"title": "테스트종목 공급계약 체결", "at": source_at.isoformat()})
    assert not event_item_matches(core, {"title": "다른회사 공급계약 체결", "at": source_at.isoformat()})
    assert not event_item_matches(core, {"title": "테스트종목 주식선물 가격제한폭 확대", "at": source_at.isoformat()})
    print({"version": VERSION, "core": "PASS", "elite": "PASS", "confirmation": "PASS", "safety": "PASS"})


if __name__ == "__main__":
    main()
