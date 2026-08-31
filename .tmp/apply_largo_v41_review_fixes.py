#!/usr/bin/env python3
from pathlib import Path


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    if new in text:
        return
    if old not in text:
        raise SystemExit(f"{label}: target block not found")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


capture = Path("scripts/capture_largo_forward_v4.py")
replace_once(
    capture,
    """        target = reference * 1.01 if reference else None
        target2 = reference * 1.02 if reference else None
""",
    """        target = reference * 1.01 if reference is not None and reference > 0 else None
        target2 = reference * 1.02 if reference is not None and reference > 0 else None
""",
    "capture targets",
)
replace_once(
    capture,
    """        if reference:
            observation["open_gap_pct"] = ((data["open"] / reference) - 1) * 100 if data.get("open") else None
            observation["current_return_pct"] = ((data["price"] / reference) - 1) * 100 if data.get("price") else None
            observation["reclaimed_reference"] = bool(data.get("high") and data["high"] >= reference)
            observation["target1_seen"] = bool(data.get("high") and target and data["high"] >= target)
            observation["target2_seen"] = bool(data.get("high") and target2 and data["high"] >= target2)
            observation["stop_seen"] = bool(data.get("low") and stop and data["low"] <= stop)
            observation["no_chase_gap"] = bool(observation.get("open_gap_pct") is not None and observation["open_gap_pct"] >= 3.0)
            observation["above_reference"] = bool(data.get("price") and data["price"] >= reference)
""",
    """        if reference is not None and reference > 0:
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
""",
    "capture observations",
)

backtest = Path("scripts/backtest_winrate_v4.py")
replace_once(
    backtest,
    """    mean = lambda key: statistics.mean(row[key] for row in evaluated if row.get(key) is not None)
    return {
""",
    """    def safe_mean(key: str) -> float | None:
        values = [row[key] for row in evaluated if row.get(key) is not None]
        return statistics.mean(values) if values else None

    return {
""",
    "safe mean helper",
)
text = backtest.read_text(encoding="utf-8")
text = text.replace(
    '        "avg_open_gap": mean("open_gap_pct"), "avg_close": mean("next_close_return_pct"),\n'
    '        "avg_mfe": mean("mfe_pct"), "avg_mae": mean("mae_pct"),\n',
    '        "avg_open_gap": safe_mean("open_gap_pct"), "avg_close": safe_mean("next_close_return_pct"),\n'
    '        "avg_mfe": safe_mean("mfe_pct"), "avg_mae": safe_mean("mae_pct"),\n',
)
if 'safe_mean("open_gap_pct")' not in text:
    raise SystemExit("safe mean calls not applied")
backtest.write_text(text, encoding="utf-8")

rules = Path("scripts/largo_winrate_v4.py")
text = rules.read_text(encoding="utf-8")
text = text.replace("import time\n", "")
text = text.replace(
    "from typing import Any, Iterable, Mapping, Sequence",
    "from typing import Any, Mapping, Sequence",
)
rules.write_text(text, encoding="utf-8")

validator = Path("scripts/validate_largo_winrate_v41.py")
validator.write_text(
    validator.read_text(encoding="utf-8").replace("import copy\n", ""),
    encoding="utf-8",
)

print("review fixes applied")
