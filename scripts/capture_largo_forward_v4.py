#!/usr/bin/env python3
"""Capture read-only next-session observations for Largo v4 forward testing."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import time
from pathlib import Path
from typing import Any, Mapping

from largo_winrate_v4 import BASE, KST, UA, VERSION, atomic_json, fetch_json, num


def rows_of(payload: Any) -> list[Mapping[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, Mapping)]
    if isinstance(payload, Mapping):
        for key in ("items", "content", "datas", "data", "result", "stocks"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, Mapping)]
    return []


def first_number(payload: Any, keys: tuple[str, ...]) -> float | None:
    if isinstance(payload, Mapping):
        for key in keys:
            result = num(payload.get(key))
            if result is not None:
                return result
        for value in payload.values():
            if isinstance(value, (Mapping, list, tuple)):
                result = first_number(value, keys)
                if result is not None:
                    return result
    elif isinstance(payload, (list, tuple)):
        for value in payload[:100]:
            result = first_number(value, keys)
            if result is not None:
                return result
    return None


def quote(code: str, timeout: int) -> dict[str, Any]:
    payload = fetch_json(f"/api/domestic/detail/{code}/price", timeout=timeout)
    return {
        "price": first_number(payload, ("closePrice", "nowPrice", "currentPrice", "price")),
        "open": first_number(payload, ("openPrice", "open")),
        "high": first_number(payload, ("highPrice", "high")),
        "low": first_number(payload, ("lowPrice", "low")),
        "change_rate": first_number(payload, ("fluctuationsRatio", "changeRate", "change_rate")),
        "raw_available": bool(payload),
    }


def load_json(path: Path, fallback: Any) -> Any:
    if not path.exists() or not path.stat().st_size:
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback


def capture(signals: Mapping[str, Any], history: Mapping[str, Any], stage: str, timeout: int, delay: float) -> dict[str, Any]:
    now = dt.datetime.now(KST)
    signal_at = str(signals.get("signal_at") or "")
    records = [dict(item) for item in history.get("observations", []) if isinstance(item, Mapping)]
    current: list[dict[str, Any]] = []
    for index, candidate in enumerate(signals.get("candidates") or []):
        if not isinstance(candidate, Mapping):
            continue
        code = str(candidate.get("code") or "")
        if not code:
            continue
        error = None
        try:
            data = quote(code, timeout)
        except Exception as exc:
            data = {"price": None, "open": None, "high": None, "low": None, "change_rate": None, "raw_available": False}
            error = f"{type(exc).__name__}: {exc}"
        reference = num(candidate.get("signal_price"))
        risk = candidate.get("risk") if isinstance(candidate.get("risk"), Mapping) else {}
        stop = num(risk.get("stop"))
        target = reference * 1.01 if reference is not None and reference > 0 else None
        target2 = reference * 1.02 if reference is not None and reference > 0 else None
        observation = {
            "observed_at": now.isoformat(), "stage": stage, "signal_at": signal_at,
            "market_date": signals.get("market_date"), "code": code, "name": candidate.get("name"),
            "lane": candidate.get("lane"), "close_variant": candidate.get("close_variant"),
            "reference_close": reference, "stop": stop, "target1": target, "target2": target2,
            **data, "error": error,
        }
        if reference is not None and reference > 0:
            open_price = data.get("open")
            current_price = data.get("price")
            high_price = data.get("high")
            low_price = data.get("low")
            observation["open_gap_pct"] = ((open_price / reference) - 1) * 100 if open_price is not None else None
            observation["current_return_pct"] = ((current_price / reference) - 1) * 100 if current_price is not None else None
            observation["reclaimed_reference"] = bool(high_price is not None and high_price >= reference)
            observation["target1_seen"] = bool(high_price is not None and target is not None and high_price >= target)
            observation["target2_seen"] = bool(high_price is not None and target2 is not None and high_price >= target2)
            observation["stop_seen"] = bool(low_price is not None and stop is not None and low_price <= stop)
            observation["no_chase_gap"] = bool(observation.get("open_gap_pct") is not None and observation["open_gap_pct"] >= 3.0)
            observation["above_reference"] = bool(current_price is not None and current_price >= reference)
            observation["confirmation_ready"] = bool(
                candidate.get("lane") == "NEXT_DAY_CONFIRM"
                and not observation["no_chase_gap"]
                and observation["reclaimed_reference"]
                and observation["above_reference"]
                and not observation["stop_seen"]
                and stage in {"09:05", "09:15", "10:30"}
            )
            if observation["target1_seen"] and not observation["stop_seen"]:
                observation["bounded_outcome_1pct"] = "WIN_CLEAR"
            elif observation["stop_seen"] and not observation["target1_seen"]:
                observation["bounded_outcome_1pct"] = "LOSS_CLEAR"
            elif observation["stop_seen"] and observation["target1_seen"]:
                observation["bounded_outcome_1pct"] = "AMBIGUOUS"
            else:
                observation["bounded_outcome_1pct"] = "OPEN"
        records.append(observation)
        current.append(observation)
        if delay > 0 and index + 1 < len(signals.get("candidates") or []):
            time.sleep(delay)
    records.sort(key=lambda item: str(item.get("observed_at") or ""))
    return {
        "version": VERSION, "updated_at": now.isoformat(), "latest_stage": stage,
        "signal_at": signal_at, "observations": records[-5000:], "latest": current,
        "notes": [
            "일봉·현재가 관찰 기록이며 자동 주문을 수행하지 않습니다.",
            "목표와 구조선이 모두 관측된 날은 분봉 순서를 확인하기 전까지 결과를 확정하지 않습니다.",
            "종가 보유형은 +1% 50%, +2% 30%, 잔여 20% 추적을 전진 검증합니다.",
            "익일 확인형은 09:05·09:15 전일 종가 회복 유지와 갭 +3% 미만을 요구합니다.",
        ],
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("--signals", type=Path, required=True)
    result.add_argument("--history", type=Path, required=True)
    result.add_argument("--output", type=Path, required=True)
    result.add_argument("--stage", required=True)
    result.add_argument("--timeout", type=int, default=15)
    result.add_argument("--delay", type=float, default=0.15)
    return result


def main() -> None:
    args = parser().parse_args()
    signals = load_json(args.signals, {"candidates": []})
    history = load_json(args.history, {"observations": []})
    result = capture(signals, history, args.stage, args.timeout, args.delay)
    atomic_json(args.output, result)
    print(json.dumps({"stage": args.stage, "count": len(result["latest"]), "updated_at": result["updated_at"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
