#!/usr/bin/env python3
from __future__ import annotations
from copy import deepcopy
from largo_close_v6_shadow import VERSION, apply_daily_selection, v6_shadow_gate


def fixture(**overrides):
    row = {
        "code": "123456", "name": "테스트기업", "market": "KOSPI", "hard_reject": False,
        "trade_value": 120_000_000_000,
        "evidence": {"negative": False, "title": "테스트기업, 1천억원 공급계약 체결", "body": "테스트기업이 직접 계약을 체결했다.", "direct_benefit": True, "event_strength": 2, "directness_points": 16, "freshness_points": 10, "observed": True},
        "theme": {"breadth": .75, "leader_rank": 1, "follower_strong_count": 1},
        "persistence": {"observed": True, "stable": True},
        "structure": {"change_rate": 7, "digest_ratio": .55, "risk_rate": .04, "close_location": .90, "upper_wick": .08, "body_ratio": .55},
    }
    for key, value in overrides.items():
        if key in row["structure"]: row["structure"][key] = value
        elif key in row["evidence"]: row["evidence"][key] = value
        elif key in row["theme"]: row["theme"][key] = value
        else: row[key] = value
    return row


def entry(ask=10000, bid=9990):
    return {"entry_ask": ask, "entry_bid": bid, "entry_last": ask, "entry_time": "15:18"}


def main():
    direct = v6_shadow_gate(fixture(), entry())
    assert direct["version"] == VERSION and direct["qualified"] and direct["lane"] == "DIRECT_CONFIRMED"
    mismatch = fixture(title="다른회사, 합병 비율 부담에 급락", body="다른 회사 관련 기사")
    blocked = v6_shadow_gate(mismatch, entry())
    assert not blocked["qualified"] and not blocked["evidence_audit"]["passed"] and blocked["evidence_audit"]["negative_context"]
    theme = fixture(title="", body="", direct_benefit=False, event_strength=0, directness_points=0, freshness_points=0, change_rate=12, digest_ratio=.50, close_location=.82, upper_wick=.12, body_ratio=.55, breadth=.85, leader_rank=1)
    theme["evidence"]["observed"] = False
    theme_gate = v6_shadow_gate(theme, entry())
    assert theme_gate["qualified"] and theme_gate["lane"] == "THEME_LEADER"
    weak = deepcopy(theme); weak["structure"]["digest_ratio"] = .20
    assert not v6_shadow_gate(weak, entry())["qualified"]
    first = fixture(code="111111", name="반복기업", title="반복기업, 공급계약 체결", body="반복기업 직접 수주")
    first_gate = v6_shadow_gate(first, entry())
    prior = [{"code": "111111", "signal_date": "2026-08-01", "v6_shadow": {"daily_pick": True, "event_hash": first_gate["event_hash"]}}]
    repeat = v6_shadow_gate(first, entry(), prior_signals=prior)
    assert repeat["duplicate_event"] and repeat["lane"] not in {"DIRECT_CONFIRMED", "DIRECT_UNPRICED"}
    a = fixture(code="000001", name="A기업", title="A기업, 공급계약 체결", body="A기업 직접 수주")
    b = fixture(code="000002", name="B기업", title="B기업, 공급계약 체결", body="B기업 직접 수주", risk_rate=.02)
    a["v6_shadow"] = v6_shadow_gate(a, entry()); b["v6_shadow"] = v6_shadow_gate(b, entry())
    apply_daily_selection([a, b])
    assert sum(row["v6_shadow"]["daily_pick"] for row in (a, b)) == 1
    assert sum(row["v6_shadow"]["eligible"] for row in (a, b)) == 1
    print({"version": VERSION, "tests": 7, "status": "PASS"})


if __name__ == "__main__":
    main()
