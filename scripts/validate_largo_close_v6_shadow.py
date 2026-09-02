#!/usr/bin/env python3
from largo_close_v6_shadow import evidence_audit, target6_shadow_gate


def fixture() -> dict:
    return {
        "code": "096770",
        "name": "SK이노베이션",
        "hard_reject": False,
        "trade_value": 100_000_000_000,
        "evidence": {
            "observed": True,
            "title": "SK이노베이션, 신규 대형 공급계약 체결",
            "direct_benefit": True,
            "negative": False,
            "event_strength": 2,
            "directness_points": 15,
            "freshness_points": 10,
        },
        "theme": {"breadth": 0.85, "leader_rank": 1},
        "structure": {
            "change_rate": 7.0,
            "digest_ratio": 0.45,
            "risk_rate": 0.04,
            "close_location": 0.92,
            "upper_wick": 0.08,
            "body_ratio": 0.55,
        },
    }


def main() -> None:
    entry = {"entry_ask": 10000, "entry_bid": 9990}

    good = fixture()
    good_gate = target6_shadow_gate(good, entry)
    assert good_gate["eligible"], good_gate
    assert good_gate["evidence_audit"]["passed"]

    mismatch = fixture()
    mismatch["evidence"]["title"] = "SKIET, 합병 비율 부담에 이틀째 급락"
    audit = evidence_audit(mismatch)
    assert not audit["passed"], audit
    mismatch_gate = target6_shadow_gate(mismatch, entry)
    assert mismatch_gate["lane"] not in {
        "DIRECT_FRESH",
        "DIRECT_CONTINUATION",
        "DIRECT_AND_THEME",
    }

    overreflected = fixture()
    overreflected["structure"]["change_rate"] = 12.0
    overreflected["theme"]["breadth"] = 0.40
    overreflected["structure"]["close_location"] = 0.70
    overreflected["structure"]["upper_wick"] = 0.20
    over_gate = target6_shadow_gate(overreflected, entry)
    assert over_gate["lane"] not in {
        "DIRECT_FRESH",
        "DIRECT_CONTINUATION",
        "DIRECT_AND_THEME",
    }

    theme = fixture()
    theme["name"] = "테마주"
    theme["evidence"] = {"observed": False}
    theme["structure"].update(
        {"change_rate": 12.0, "digest_ratio": 0.50, "close_location": 0.80, "upper_wick": 0.15}
    )
    theme["theme"] = {"breadth": 0.86, "leader_rank": 2}
    theme_gate = target6_shadow_gate(theme, entry)
    assert theme_gate["lane"] == "THEME_MOMENTUM", theme_gate

    missing_quote = target6_shadow_gate(fixture(), {"entry_ask": None, "entry_bid": None})
    assert not missing_quote["eligible"]
    print({"status": "ok", "version": good_gate["version"]})


if __name__ == "__main__":
    main()
