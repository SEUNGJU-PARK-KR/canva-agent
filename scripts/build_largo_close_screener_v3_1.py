from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import build_largo_close_screener_v3 as impl

ORIGINAL_DEMO_ROWS = impl.base.demo_rows


def demo_rows_v3() -> list[dict[str, Any]]:
    rows = ORIGINAL_DEMO_ROWS()
    defaults = [
        (
            "CLASSIC_WICK_BREAKOUT",
            "고전형 윗꼬리 장대양봉",
            "종가 1차. 익일 갭이 과도하지 않고 기준봉 종가선을 지지하면 첫 1파 청산.",
        ),
        (
            "AFTER_CLOSE_REVIEW",
            "장마감 후보선정형",
            "관심종목에만 저장. 다음 날 호가·차트에서 대장과 물량소화를 다시 확인.",
        ),
    ]
    for index, row in enumerate(rows):
        pattern, label, plan = defaults[min(index, len(defaults) - 1)]
        high = row.get("high")
        low = row.get("low")
        op = row.get("open")
        price = row.get("price")
        candle_range = high - low if all(value is not None for value in (high, low)) else None
        body_ratio = (
            max(0.0, (price - op) / candle_range)
            if price is not None and op is not None and candle_range and candle_range > 0
            else None
        )
        row.update(
            {
                "pattern": pattern,
                "pattern_label": label,
                "pattern_data": {"demo": True},
                "body_ratio": body_ratio,
                "ma10": None,
                "ma60": None,
                "ma120": None,
                "ma_convergence": None,
                "ma_breakout": pattern == "CLASSIC_WICK_BREAKOUT",
                "entry_plan": plan,
            }
        )
        row.setdefault("checks", {})["wick_role"] = {
            "status": "PASS" if pattern == "CLASSIC_WICK_BREAKOUT" else "WARN",
            "value": {"upper_wick": row.get("upper_wick"), "pattern": pattern},
            "reason": "데모용 패턴 역할 판정",
            "role": "required" if pattern == "CLASSIC_WICK_BREAKOUT" else "supporting",
        }
        row["checks"]["pattern_fit"] = {
            "status": "PASS" if pattern == "CLASSIC_WICK_BREAKOUT" else "WARN",
            "value": pattern,
            "reason": "데모용 패턴 적합성",
            "role": "required" if pattern == "CLASSIC_WICK_BREAKOUT" else "supporting",
        }
    return rows


impl.base.demo_rows = demo_rows_v3


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="site-output")
    parser.add_argument("--history", default="largo-close-screener/data/history.json")
    parser.add_argument("--candidate-limit", type=int, default=36)
    args = parser.parse_args()
    meta = impl.base.build(
        Path(args.output),
        Path(args.history),
        max(5, min(args.candidate_limit, 60)),
    )
    print(json.dumps(meta, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
