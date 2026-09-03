#!/usr/bin/env python3
"""Export next-session daily OHLC outcomes for every reconstructed June candidate.

This script is research-only. It does not place orders. Historical 15:18 quotes are
not available for June, so returns use the signal-day close as a proxy entry and the
next trading day's daily OHLC. The daily high can occur after 09:06.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import csv
import json
import math
import re
import statistics
import time
from pathlib import Path
from typing import Any, Mapping

import requests

CHART_URL = "https://fchart.stock.naver.com/sise.nhn"
UA = "Mozilla/5.0 (compatible; LargoJuneOutcomeExport/1.0; read-only)"
NEGATIVE_TERMS = (
    "급락", "하락", "약세", "부담", "우려", "희석", "적자", "철회", "해지",
    "리콜", "소송", "조사", "압수수색", "부진", "쇼크", "실망", "매도",
    "유상증자", "전환사채", "신주인수권", "횡령", "배임", "거래정지",
    "상장폐지", "관리종목", "감사의견", "단기과열", "투자경고", "투자위험",
)


def num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(str(value).replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def truth(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().casefold() in {"1", "true", "yes", "pass"}


def normalize(value: Any) -> str:
    text = str(value or "").casefold()
    for term in ("주식회사", "(주)", "㈜", "홀딩스", "그룹", "corporation", "corp"):
        text = text.replace(term.casefold(), "")
    return re.sub(r"[^0-9a-z가-힣]+", "", text)


def chart_rows(code: str, timeout: int = 25, retries: int = 4) -> tuple[str, dict[str, dict[str, float]], str | None]:
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            response = requests.get(
                CHART_URL,
                params={"symbol": code, "timeframe": "day", "count": 460, "requestType": 0},
                headers={"User-Agent": UA, "Referer": "https://finance.naver.com/"},
                timeout=timeout,
            )
            response.raise_for_status()
            result: dict[str, dict[str, float]] = {}
            for packed in re.findall(r'data="([^"]+)"', response.text):
                parts = packed.split("|")
                if len(parts) < 6 or not re.fullmatch(r"20\d{6}", parts[0]):
                    continue
                try:
                    o, h, l, c, v = [float(x) for x in parts[1:6]]
                except ValueError:
                    continue
                if min(o, h, l, c) <= 0 or h < l:
                    continue
                result[parts[0]] = {"open": o, "high": h, "low": l, "close": c, "volume": v}
            return code, result, None
        except Exception as exc:
            last_error = exc
            time.sleep(min(4.0, 0.5 * (2**attempt)))
    return code, {}, f"{type(last_error).__name__}: {last_error}"


def pct(value: float | None, base: float | None) -> float | None:
    if value is None or base is None or base <= 0:
        return None
    return round((value / base - 1.0) * 100.0, 6)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--detail", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--month", default="2026-06")
    parser.add_argument("--workers", type=int, default=18)
    args = parser.parse_args()

    detail = json.loads(Path(args.detail).read_text(encoding="utf-8"))
    if not isinstance(detail, list):
        raise TypeError("detail must be a list")

    filtered: list[Mapping[str, Any]] = []
    codes: set[str] = set()
    for item in detail:
        if not isinstance(item, Mapping):
            continue
        row = item.get("row") if isinstance(item.get("row"), Mapping) else {}
        if not str(row.get("signal_date") or "").startswith(args.month):
            continue
        filtered.append(item)
        code = str(row.get("code") or "").zfill(6)
        if code:
            codes.add(code)

    charts: dict[str, dict[str, dict[str, float]]] = {}
    errors: list[str] = []
    with cf.ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = {executor.submit(chart_rows, code): code for code in sorted(codes)}
        for index, future in enumerate(cf.as_completed(futures), 1):
            code, values, error = future.result()
            charts[code] = values
            if error:
                errors.append(f"{code}: {error}")
            if index % 50 == 0:
                print({"charts": index, "total": len(codes), "errors": len(errors)}, flush=True)

    rows: list[dict[str, Any]] = []
    for item in filtered:
        row = item.get("row") if isinstance(item.get("row"), Mapping) else {}
        candidate = item.get("candidate") if isinstance(item.get("candidate"), Mapping) else {}
        score = item.get("score") if isinstance(item.get("score"), Mapping) else {}
        structure = score.get("structure") if isinstance(score.get("structure"), Mapping) else {}
        evidence = score.get("evidence") if isinstance(score.get("evidence"), Mapping) else {}
        theme = score.get("theme") if isinstance(score.get("theme"), Mapping) else {}
        metrics = candidate.get("metrics") if isinstance(candidate.get("metrics"), Mapping) else {}

        signal_date = str(row.get("signal_date") or "")
        next_date = str(row.get("next_date") or "")
        signal_key = re.sub(r"\D", "", signal_date)
        next_key = re.sub(r"\D", "", next_date)
        code = str(row.get("code") or score.get("code") or candidate.get("code") or "").zfill(6)
        signal_bar = charts.get(code, {}).get(signal_key, {})
        next_bar = charts.get(code, {}).get(next_key, {})
        signal_close = num(signal_bar.get("close")) or num(candidate.get("price")) or num(row.get("entry_last"))

        evidence_title = str(evidence.get("title") or row.get("evidence_title") or "")
        company = normalize(score.get("name") or row.get("name") or candidate.get("name"))
        company_match = bool(company and company in normalize(evidence_title))
        evidence_negative = bool(evidence.get("negative")) or any(term in evidence_title for term in NEGATIVE_TERMS)
        direct_benefit = truth(evidence.get("direct_benefit"))
        event_strength = num(evidence.get("event_strength"))
        evidence_audit_pass = bool(
            evidence_title and company_match and direct_benefit and not evidence_negative
            and event_strength is not None and event_strength >= 1
        )

        next_open = num(next_bar.get("open"))
        next_high = num(next_bar.get("high"))
        next_low = num(next_bar.get("low"))
        next_close = num(next_bar.get("close"))
        high_return = pct(next_high, signal_close)
        close_return = pct(next_close, signal_close)
        policy3 = 3.0 if high_return is not None and high_return >= 3.0 else close_return

        rows.append({
            "signal_date": signal_date,
            "next_date": next_date,
            "code": code,
            "name": str(score.get("name") or row.get("name") or candidate.get("name") or ""),
            "market": row.get("market"),
            "hard_reject": truth(score.get("hard_reject")),
            "signal_close": signal_close,
            "trade_value": num(candidate.get("trade_value")) or num(row.get("trade_value")),
            "market_cap_proxy": num(row.get("market_cap_proxy")) or num(candidate.get("market_cap")),
            "change_rate": num(structure.get("change_rate")) if num(structure.get("change_rate")) is not None else num(metrics.get("change_rate")),
            "digest_ratio": num(structure.get("digest_ratio")) if num(structure.get("digest_ratio")) is not None else num(metrics.get("digest_ratio")),
            "risk_rate": num(structure.get("risk_rate")) if num(structure.get("risk_rate")) is not None else num((candidate.get("plan") or {}).get("stop_distance")),
            "close_location": num(structure.get("close_location")) if num(structure.get("close_location")) is not None else num(metrics.get("close_location")),
            "upper_wick": num(structure.get("upper_wick")) if num(structure.get("upper_wick")) is not None else num(metrics.get("upper_wick")),
            "body_ratio": num(structure.get("body_ratio")) if num(structure.get("body_ratio")) is not None else num(metrics.get("body_ratio")),
            "pattern_score": num(structure.get("pattern_score")) or num((candidate.get("pattern") or {}).get("score")),
            "theme_name": theme.get("name") or row.get("theme_name"),
            "theme_breadth": num(theme.get("breadth")) if num(theme.get("breadth")) is not None else num(row.get("theme_breadth")),
            "leader_rank": num(theme.get("leader_rank")) if num(theme.get("leader_rank")) is not None else num(row.get("leader_rank")),
            "follower_strong_count": num(theme.get("follower_strong_count")) if num(theme.get("follower_strong_count")) is not None else num(row.get("follower_strong_count")),
            "follower_turnover": num(theme.get("follower_turnover")) if num(theme.get("follower_turnover")) is not None else num(row.get("follower_turnover")),
            "directness_points": num(evidence.get("directness_points")) if num(evidence.get("directness_points")) is not None else num(row.get("points_directness")),
            "freshness_points": num(evidence.get("freshness_points")) if num(evidence.get("freshness_points")) is not None else num(row.get("points_freshness")),
            "event_strength": event_strength,
            "evidence_title": evidence_title,
            "evidence_company_match": company_match,
            "evidence_negative": evidence_negative,
            "evidence_direct_benefit": direct_benefit,
            "evidence_audit_pass": evidence_audit_pass,
            "next_open": next_open,
            "next_high": next_high,
            "next_low": next_low,
            "next_close": next_close,
            "next_open_return_pct": pct(next_open, signal_close),
            "next_high_return_pct": high_return,
            "next_low_return_pct": pct(next_low, signal_close),
            "next_close_return_pct": close_return,
            "policy3_return_pct": policy3,
            "hit3_full_day": None if high_return is None else high_return >= 3.0,
            "chart_outcome_known": all(value is not None for value in (signal_close, next_open, next_high, next_low, next_close)),
        })

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0]) if rows else []
    with output.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    known = [row for row in rows if row["chart_outcome_known"]]
    summary = {
        "month": args.month,
        "rows": len(rows),
        "dates": sorted({row["signal_date"] for row in rows}),
        "codes": len(codes),
        "known_outcomes": len(known),
        "chart_errors": errors,
        "baseline_hit3_rate": sum(bool(row["hit3_full_day"]) for row in known) / len(known) if known else None,
        "baseline_mean_policy3_pct": statistics.fmean(
            float(row["policy3_return_pct"]) for row in known if row["policy3_return_pct"] is not None
        ) if known else None,
        "limitations": [
            "신호일 종가를 대용 진입가로 사용했습니다.",
            "다음 날 일봉 고가는 09시 06분 이후 움직임을 포함할 수 있습니다.",
            "수수료, 세금, 호가 잔량과 주문 지연을 반영하지 않았습니다.",
        ],
    }
    output.with_suffix(".summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
