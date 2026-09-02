#!/usr/bin/env python3
"""Reconstruct July 2026 Largo close-v5 candidates with the canonical rule engine.

This is research-only. Exact v5 performance is recorded only when both the 15:18
entry ask/bid and the next-session 09:00-09:05 executable bid are available.
When those old minute quotes are unavailable, a candidate that passes every
non-quote v5 condition is labelled PROVISIONAL_NO_QUOTE and is accompanied only
by daily open/high/close proxies from the signal-day close.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlencode

import requests

ROOT = Path(__file__).resolve().parents[2]
CANONICAL_DIR = ROOT / "scripts"
BACKFILL_SOURCE = ROOT / "research/largo-target3-20day/source/backfill_largo_target3_20day.py"
sys.path.insert(0, str(CANONICAL_DIR))
import largo_material_0906 as canonical  # noqa: E402

assert canonical.TARGET3_VERSION == "largo-close-v5"

CHART_URL = "https://fchart.stock.naver.com/sise.nhn"
UA = "Mozilla/5.0 (compatible; LargoJulyV5Canonical/1.0; read-only)"


def num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(str(value).replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def truth(value: Any) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes"}


def run_backfill(raw_dir: Path, workers: int) -> None:
    raw_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="largo-july-v5-") as temp_dir:
        runner = Path(temp_dir) / "backfill_largo_v5_july.py"
        shutil.copy2(BACKFILL_SOURCE, runner)
        env = os.environ.copy()
        env["PYTHONPATH"] = str(CANONICAL_DIR)
        command = [
            sys.executable,
            str(runner),
            "--output-dir", str(raw_dir),
            "--end-date", "2026-07-31",
            "--days", "22",
            "--preselect", "80",
            "--workers", str(workers),
        ]
        subprocess.run(command, cwd=ROOT, env=env, check=True)


def fnum(value: Any) -> float | None:
    match = re.search(r"-?\d+(?:\.\d+)?", str(value or "").replace(",", ""))
    return float(match.group()) if match else None


def chart_rows(code: str, timeout: int) -> list[dict[str, float | str]]:
    query = urlencode({"symbol": code, "timeframe": "day", "count": 380, "requestType": 0})
    last_error: Exception | None = None
    for attempt in range(4):
        try:
            response = requests.get(
                CHART_URL + "?" + query,
                headers={"User-Agent": UA, "Referer": "https://finance.naver.com/"},
                timeout=timeout,
            )
            response.raise_for_status()
            text = response.content.decode("utf-8", errors="replace")
            rows: list[dict[str, float | str]] = []
            for packed in re.findall(r'data="([^"]+)"', text):
                parts = packed.split("|")
                if len(parts) < 6 or not re.fullmatch(r"20\d{6}", parts[0]):
                    continue
                values = [fnum(value) for value in parts[1:6]]
                if any(value is None for value in values):
                    continue
                o, h, l, c, v = [float(value) for value in values]
                rows.append({"date": parts[0], "o": o, "h": h, "l": l, "c": c, "v": v})
            return sorted({str(row["date"]): row for row in rows}.values(), key=lambda row: str(row["date"]))
        except Exception as exc:
            last_error = exc
            time.sleep(0.5 * (attempt + 1))
    raise RuntimeError(f"chart fetch failed for {code}: {last_error}")


def lane_without_quotes(target: Mapping[str, Any]) -> str | None:
    momentum = target.get("momentum_checks") if isinstance(target.get("momentum_checks"), Mapping) else {}
    direct = target.get("direct_checks") if isinstance(target.get("direct_checks"), Mapping) else {}

    def passed(checks: Mapping[str, Any], spread_key: str) -> bool:
        if not checks:
            return False
        ignored = {"entry_quote_known", spread_key}
        return all(bool(value) for key, value in checks.items() if key not in ignored)

    momentum_pass = passed(momentum, "spread_at_most_0_20pct")
    direct_pass = passed(direct, "spread_at_most_0_10pct")
    if momentum_pass and direct_pass:
        return "BOTH_NO_QUOTE"
    if momentum_pass:
        return "MOMENTUM_DIGESTION_NO_QUOTE"
    if direct_pass:
        return "DIRECT_EVENT_NO_QUOTE"
    return None


def selection_key(detail: Mapping[str, Any]) -> tuple[float, float, float, str]:
    row = detail.get("row") if isinstance(detail.get("row"), Mapping) else {}
    risk = num(row.get("risk_rate"))
    spread = num(row.get("spread_pct"))
    turnover = num(row.get("trade_value"))
    return (
        risk if risk is not None else math.inf,
        spread if spread is not None else math.inf,
        -(turnover or 0.0),
        str(row.get("code") or ""),
    )


def proxy_outcome(detail: Mapping[str, Any], timeout: int) -> dict[str, Any]:
    row = detail.get("row") if isinstance(detail.get("row"), Mapping) else {}
    candidate = detail.get("candidate") if isinstance(detail.get("candidate"), Mapping) else {}
    code = str(row.get("code") or "").zfill(6)
    signal_close = num(candidate.get("price")) or num(row.get("entry_last"))
    next_date = str(row.get("next_date") or "").replace("-", "")
    result: dict[str, Any] = {
        "signal_close_proxy": signal_close,
        "next_open_proxy": None,
        "next_high_proxy": None,
        "next_close_proxy": None,
        "next_open_return_from_close_pct": None,
        "next_high_return_from_close_pct": None,
        "next_close_return_from_close_pct": None,
        "proxy_error": None,
    }
    if not signal_close or not next_date:
        result["proxy_error"] = "signal close or next date missing"
        return result
    try:
        bars = chart_rows(code, timeout)
        bar = next((item for item in bars if str(item.get("date")) == next_date), None)
        if not bar:
            raise RuntimeError("next daily bar missing")
        for field in ("open", "high", "close"):
            price = num(bar[{"open": "o", "high": "h", "close": "c"}[field]])
            result[f"next_{field}_proxy"] = price
            result[f"next_{field}_return_from_close_pct"] = round((price / signal_close - 1) * 100, 4) if price else None
    except Exception as exc:
        result["proxy_error"] = f"{type(exc).__name__}: {exc}"
    return result


def flat_selected(detail: Mapping[str, Any], status: str, lane: str, timeout: int) -> dict[str, Any]:
    row = detail.get("row") if isinstance(detail.get("row"), Mapping) else {}
    score = detail.get("score") if isinstance(detail.get("score"), Mapping) else {}
    structure = score.get("structure") if isinstance(score.get("structure"), Mapping) else {}
    evidence = score.get("evidence") if isinstance(score.get("evidence"), Mapping) else {}
    max_return = num(row.get("max_executable_return_pct"))
    last_return = num(row.get("last_executable_return_pct"))
    official_policy = 3.0 if max_return is not None and max_return >= 3.0 else last_return
    result = {
        "signal_date": row.get("signal_date"),
        "next_date": row.get("next_date"),
        "selection_status": status,
        "code": str(row.get("code") or "").zfill(6),
        "name": row.get("name"),
        "lane": lane,
        "entry_time": row.get("entry_time"),
        "entry_ask": row.get("entry_ask"),
        "entry_bid": row.get("entry_bid"),
        "spread_pct": row.get("spread_pct"),
        "change_rate": structure.get("change_rate"),
        "digest_ratio": structure.get("digest_ratio"),
        "risk_rate": structure.get("risk_rate"),
        "trade_value": row.get("trade_value"),
        "directness_points": evidence.get("directness_points"),
        "freshness_points": evidence.get("freshness_points"),
        "evidence_title": evidence.get("title"),
        "max_executable_return_pct": max_return,
        "last_executable_return_pct": last_return,
        "policy_return_pct": official_policy,
        "hit_3_exec": max_return is not None and max_return >= 3.0,
    }
    result.update(proxy_outcome(detail, timeout))
    return result


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fields is None:
        fields = []
        for row in rows:
            for key in row:
                if key not in fields:
                    fields.append(key)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--workers", type=int, default=24)
    parser.add_argument("--timeout", type=int, default=25)
    args = parser.parse_args()

    output = Path(args.output_dir)
    raw_dir = output / "raw"
    result_dir = output / "output"
    result_dir.mkdir(parents=True, exist_ok=True)
    run_backfill(raw_dir, args.workers)

    details = json.loads((raw_dir / "target3_20day_detail.json").read_text(encoding="utf-8"))
    details = [item for item in details if str(item.get("row", {}).get("signal_date", "")).startswith("2026-07")]
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in details:
        grouped.setdefault(str(item["row"]["signal_date"]), []).append(item)

    daily: list[dict[str, Any]] = []
    selected: list[dict[str, Any]] = []
    for signal_date in sorted(grouped):
        day = grouped[signal_date]
        official = sorted(
            [item for item in day if bool(item.get("target3", {}).get("eligible"))],
            key=selection_key,
        )
        provisional_pairs = [
            (item, lane)
            for item in day
            if not bool(item.get("target3", {}).get("eligible"))
            and (lane := lane_without_quotes(item.get("target3", {}))) is not None
        ]
        provisional_pairs.sort(key=lambda pair: selection_key(pair[0]))
        if official:
            chosen = flat_selected(official[0], "OFFICIAL", str(official[0]["target3"].get("lane") or ""), args.timeout)
        elif provisional_pairs:
            chosen = flat_selected(provisional_pairs[0][0], "PROVISIONAL_NO_QUOTE", provisional_pairs[0][1], args.timeout)
        else:
            sample_row = day[0].get("row", {}) if day else {}
            chosen = {
                "signal_date": signal_date,
                "next_date": sample_row.get("next_date"),
                "selection_status": "NO_CANDIDATE",
                "code": "",
                "name": "",
                "lane": "",
                "entry_time": None,
                "entry_ask": None,
                "entry_bid": None,
                "spread_pct": None,
                "change_rate": None,
                "digest_ratio": None,
                "risk_rate": None,
                "trade_value": None,
                "directness_points": None,
                "freshness_points": None,
                "evidence_title": None,
                "max_executable_return_pct": None,
                "last_executable_return_pct": None,
                "policy_return_pct": None,
                "hit_3_exec": None,
                "signal_close_proxy": None,
                "next_open_proxy": None,
                "next_high_proxy": None,
                "next_close_proxy": None,
                "next_open_return_from_close_pct": None,
                "next_high_return_from_close_pct": None,
                "next_close_return_from_close_pct": None,
                "proxy_error": None,
            }
        chosen["official_eligible_count"] = len(official)
        chosen["provisional_no_quote_count"] = len(provisional_pairs)
        chosen["candidate_count"] = len(day)
        daily.append(chosen)
        if chosen["selection_status"] != "NO_CANDIDATE":
            selected.append(chosen)

    official_evaluated = [row for row in selected if row["selection_status"] == "OFFICIAL" and num(row.get("policy_return_pct")) is not None]
    official_returns = [num(row["policy_return_pct"]) for row in official_evaluated]
    official_returns = [value for value in official_returns if value is not None]
    provisional = [row for row in selected if row["selection_status"] == "PROVISIONAL_NO_QUOTE"]
    proxy_open = [num(row.get("next_open_return_from_close_pct")) for row in provisional]
    proxy_open = [value for value in proxy_open if value is not None]
    proxy_high = [num(row.get("next_high_return_from_close_pct")) for row in provisional]
    proxy_high = [value for value in proxy_high if value is not None]

    summary = {
        "version": "largo-close-v5-july-2026-canonical-v1",
        "rule_version": canonical.TARGET3_VERSION,
        "signal_days": len(daily),
        "candidate_rows": len(details),
        "entry_quote_rows": sum(num(item.get("row", {}).get("entry_ask")) is not None for item in details),
        "outcome_quote_rows": sum(num(item.get("row", {}).get("max_executable_return_pct")) is not None for item in details),
        "official_eligible_rows": sum(int(row["official_eligible_count"]) for row in daily),
        "official_selected_days": sum(row["selection_status"] == "OFFICIAL" for row in daily),
        "provisional_no_quote_rows": sum(int(row["provisional_no_quote_count"]) for row in daily),
        "provisional_selected_days": len(provisional),
        "no_candidate_days": sum(row["selection_status"] == "NO_CANDIDATE" for row in daily),
        "evaluated_official_picks": len(official_evaluated),
        "positive_official_picks": sum((num(row["policy_return_pct"]) or 0) > 0 for row in official_evaluated),
        "loss_official_picks": sum((num(row["policy_return_pct"]) or 0) < 0 for row in official_evaluated),
        "hit3_official_picks": sum(bool(row.get("hit_3_exec")) for row in official_evaluated),
        "mean_official_policy_return_pct": round(statistics.fmean(official_returns), 4) if official_returns else None,
        "proxy_open_observations": len(proxy_open),
        "proxy_positive_open_count": sum(value > 0 for value in proxy_open),
        "proxy_hit3_daily_high_count": sum(value >= 3 for value in proxy_high),
        "mean_proxy_open_return_pct": round(statistics.fmean(proxy_open), 4) if proxy_open else None,
        "mean_proxy_daily_high_return_pct": round(statistics.fmean(proxy_high), 4) if proxy_high else None,
        "dates": [row["signal_date"] for row in daily],
        "selected": selected,
        "limitations": [
            "정확한 15:18 매도·매수호가와 다음 날 09:00~09:05 매수호가가 모두 있어야 정식 v5 거래로 계산합니다.",
            "PROVISIONAL_NO_QUOTE는 호가 조건을 제외한 v5 조건을 통과한 참고 후보이며 실제 매수 신호가 아닙니다.",
            "일봉 참고 수익은 신호일 종가와 다음 날 시가·고가·종가를 비교한 값입니다. 09:06 전 수익률이 아닙니다.",
            "후보군은 과거 일봉과 현재 상장·테마 목록으로 재구성했습니다.",
            "수수료, 세금, 호가 잔량과 주문 지연은 반영하지 않았습니다.",
        ],
    }

    fields = list(daily[0]) if daily else []
    write_csv(result_dir / "july-v5-daily.csv", daily, fields)
    write_csv(result_dir / "july-v5-selected.csv", selected, fields)
    (result_dir / "july-v5-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
