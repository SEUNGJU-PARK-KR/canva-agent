#!/usr/bin/env python3
"""Post-process a historical Largo backfill with the fixed v6 shadow gate.

Official rows require an observed 15:18 best ask/bid and next-session 09:00-09:05
best-bid observations. When those quotes are unavailable, the script also reports a
clearly separated daily-OHLC proxy. The proxy assumes entry at the signal-day close
and does not represent a pre-09:06 executable result.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import re
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping

import requests

from largo_close_v6_shadow import v6_shadow_gate

CHART_URL = "https://fchart.stock.naver.com/sise.nhn"
UA = "Mozilla/5.0 (compatible; LargoV6JuneBacktest/1.0; read-only)"


def num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(str(value).replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def truth(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().casefold() in {"1", "true", "yes", "pass"}


def as_iso_date(value: Any) -> str:
    text = re.sub(r"\D", "", str(value or ""))
    return f"{text[:4]}-{text[4:6]}-{text[6:8]}" if len(text) >= 8 else str(value or "")


def selection_key(record: Mapping[str, Any]) -> tuple[float, float, float, float, str]:
    shadow = record.get("shadow") if isinstance(record.get("shadow"), Mapping) else {}
    quality = num(shadow.get("quality")) or 0.0
    risk = num(shadow.get("risk_rate"))
    spread = num(shadow.get("spread_pct"))
    turnover = num(record.get("trade_value")) or 0.0
    return (
        -quality,
        risk if risk is not None else math.inf,
        spread if spread is not None else math.inf,
        -turnover,
        str(record.get("code") or ""),
    )


def qualified_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = [
        row for row in records
        if truth((row.get("shadow") or {}).get("qualified"))
        and str((row.get("shadow") or {}).get("status") or "") != "BLOCK"
    ]
    result.sort(key=selection_key)
    return result


def synthetic_entry(close: float | None) -> dict[str, Any]:
    # Only for the explicitly labelled daily-OHLC proxy. A zero spread is not an
    # observed historical quote and is never counted as an official result.
    return {"entry_ask": close, "entry_bid": close}


def chart_rows(code: str, *, timeout: int = 25) -> dict[str, dict[str, float]]:
    response = requests.get(
        CHART_URL,
        params={"symbol": code, "timeframe": "day", "count": 420, "requestType": 0},
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
    return result


def pct(value: float | None, base: float | None) -> float | None:
    if value is None or base is None or base <= 0:
        return None
    return round((value / base - 1.0) * 100.0, 4)


def mean(values: list[float | None]) -> float | None:
    clean = [float(x) for x in values if x is not None and math.isfinite(float(x))]
    return round(statistics.fmean(clean), 4) if clean else None


def median(values: list[float | None]) -> float | None:
    clean = [float(x) for x in values if x is not None and math.isfinite(float(x))]
    return round(statistics.median(clean), 4) if clean else None


def format_pct(value: Any) -> str:
    number = num(value)
    return "—" if number is None else f"{number:+.4f}%"


def format_price(value: Any) -> str:
    number = num(value)
    return "—" if number is None else f"{number:,.0f}원"


def build_markdown(summary: Mapping[str, Any], daily_rows: list[Mapping[str, Any]]) -> str:
    lines = [
        "# 라르고 v6 그림자 규칙 2026년 6월 백테스트",
        "",
        "정확한 호가 성과와 일봉 대용 성과를 분리했습니다. 일봉 대용은 신호일 종가 매수 후 다음 거래일 시가·고가·종가를 비교한 참고값이며 09시 06분 전 실행 수익률이 아닙니다.",
        "",
        "| 신호일 | 후보 | 경로 | 종가 대용 진입 | 다음 거래일 | 시가 | 장중 최고 | 장중 최저 | 종가 | +3% |",
        "|---|---|---|---:|---|---:|---:|---:|---:|---|",
    ]
    for row in daily_rows:
        if row.get("proxy_trade_action") != "BUY_PROXY":
            lines.append(f"| {row['signal_date']} | 매매 없음 | — | — | {row.get('next_date') or '—'} | — | — | — | — | — |")
            continue
        lines.append(
            "| {signal_date} | {name} ({code}) | {lane} | {entry} | {next_date} | {open_ret} | {high_ret} | {low_ret} | {close_ret} | {hit} |".format(
                signal_date=row.get("signal_date"),
                name=row.get("name"),
                code=row.get("code"),
                lane=row.get("proxy_lane"),
                entry=format_price(row.get("signal_close_proxy")),
                next_date=row.get("next_date") or "—",
                open_ret=format_pct(row.get("next_open_return_pct")),
                high_ret=format_pct(row.get("next_high_return_pct")),
                low_ret=format_pct(row.get("next_low_return_pct")),
                close_ret=format_pct(row.get("next_close_return_pct")),
                hit="도달" if truth(row.get("proxy_hit3_full_day")) else "미도달",
            )
        )
    lines.extend([
        "",
        "## 요약",
        "",
        f"- 검사 거래일: {summary.get('signal_days')}일",
        f"- 후보 행: {summary.get('candidate_rows')}건",
        f"- 정확한 15:18 진입호가 확보: {summary.get('exact_entry_quote_rows')}건",
        f"- 공식 v6 선택: {summary.get('official_selected_days')}건",
        f"- 일봉 대용 선택: {summary.get('proxy_selected_days')}건",
        f"- 일봉 대용 +3% 장중 도달: {summary.get('proxy_hit3_full_day_count')}건",
        f"- 일봉 대용 다음 날 상승 마감: {summary.get('proxy_positive_close_count')}건",
        f"- 일봉 대용 평균 시가 수익률: {format_pct(summary.get('mean_proxy_open_return_pct'))}",
        f"- 일봉 대용 평균 장중 최고 수익률: {format_pct(summary.get('mean_proxy_high_return_pct'))}",
        f"- 일봉 대용 평균 종가 수익률: {format_pct(summary.get('mean_proxy_close_return_pct'))}",
        "",
        "## 한계",
        "",
        "- 과거 후보군은 과거 일봉과 실행 시점의 상장·테마 목록으로 재구성했습니다.",
        "- 테마 구성은 당시 실제 구성의 완전한 시점 자료가 아닙니다.",
        "- 과거 뉴스·공시는 백테스트 실행 시점에 다시 수집한 자료입니다.",
        "- 정확한 15:18 호가가 없으면 호가 간격 조건을 검증할 수 없습니다.",
        "- 일봉 고가는 09시 06분 이후 움직임을 포함할 수 있습니다.",
        "- 수수료, 세금, 체결 지연과 호가 잔량은 반영하지 않았습니다.",
    ])
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--detail", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--month", default="2026-06")
    args = parser.parse_args()

    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    detail = json.loads(Path(args.detail).read_text(encoding="utf-8"))
    if not isinstance(detail, list):
        raise TypeError("detail JSON must be a list")

    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for item in detail:
        if not isinstance(item, Mapping):
            continue
        row = item.get("row") if isinstance(item.get("row"), Mapping) else {}
        date = str(row.get("signal_date") or "")
        if date.startswith(args.month):
            grouped[date].append(item)

    prior_official: list[dict[str, Any]] = []
    prior_proxy: list[dict[str, Any]] = []
    daily_rows: list[dict[str, Any]] = []
    selected_rows: list[dict[str, Any]] = []
    chart_cache: dict[str, dict[str, dict[str, float]]] = {}

    exact_entry_rows = 0
    exact_outcome_rows = 0

    for date in sorted(grouped):
        items = grouped[date]
        official_records: list[dict[str, Any]] = []
        proxy_records: list[dict[str, Any]] = []
        next_date = str((items[0].get("row") or {}).get("next_date") or "") if items else ""

        for item in items:
            row = item.get("row") if isinstance(item.get("row"), Mapping) else {}
            candidate = item.get("candidate") if isinstance(item.get("candidate"), Mapping) else {}
            scored = item.get("score") if isinstance(item.get("score"), Mapping) else {}
            outcome = item.get("outcome") if isinstance(item.get("outcome"), Mapping) else {}
            code = str(scored.get("code") or row.get("code") or candidate.get("code") or "").zfill(6)
            close = num(candidate.get("price")) or num(row.get("entry_last"))
            entry = {"entry_ask": row.get("entry_ask"), "entry_bid": row.get("entry_bid")}
            if num(entry.get("entry_ask")) is not None and num(entry.get("entry_bid")) is not None:
                exact_entry_rows += 1
            if num(outcome.get("max_executable_return_pct")) is not None:
                exact_outcome_rows += 1

            official_shadow = v6_shadow_gate(scored, entry, prior_signals=prior_official)
            proxy_shadow = v6_shadow_gate(scored, synthetic_entry(close), prior_signals=prior_proxy)
            base = {
                "item": item,
                "code": code,
                "name": str(scored.get("name") or candidate.get("name") or row.get("name") or ""),
                "trade_value": num(candidate.get("trade_value")) or num(row.get("trade_value")),
                "signal_close_proxy": close,
            }
            official_records.append({**base, "shadow": official_shadow})
            proxy_records.append({**base, "shadow": proxy_shadow})

        official_qualified = qualified_records(official_records)
        proxy_qualified = qualified_records(proxy_records)
        official_pick = official_qualified[0] if official_qualified else None
        proxy_pick = proxy_qualified[0] if proxy_qualified else None

        if official_pick:
            prior_official.append({"v6_shadow": official_pick["shadow"]})
        if proxy_pick:
            prior_proxy.append({"v6_shadow": proxy_pick["shadow"]})

        daily: dict[str, Any] = {
            "signal_date": date,
            "next_date": as_iso_date(next_date),
            "candidate_count": len(items),
            "official_qualified_count": len(official_qualified),
            "proxy_qualified_count": len(proxy_qualified),
            "official_trade_action": "NO_TRADE",
            "proxy_trade_action": "NO_TRADE",
            "code": "",
            "name": "",
            "official_lane": "",
            "proxy_lane": "",
            "quality": None,
            "risk_rate": None,
            "spread_pct": None,
            "change_rate": None,
            "digest_ratio": None,
            "close_location": None,
            "upper_wick": None,
            "body_ratio": None,
            "theme_breadth": None,
            "leader_rank": None,
            "directness_points": None,
            "freshness_points": None,
            "evidence_title": "",
            "entry_ask": None,
            "entry_bid": None,
            "max_executable_return_pct": None,
            "last_executable_return_pct": None,
            "official_policy_return_pct": None,
            "official_hit3": None,
            "signal_close_proxy": None,
            "next_open_proxy": None,
            "next_high_proxy": None,
            "next_low_proxy": None,
            "next_close_proxy": None,
            "next_open_return_pct": None,
            "next_high_return_pct": None,
            "next_low_return_pct": None,
            "next_close_return_pct": None,
            "proxy_full_day_policy_return_pct": None,
            "proxy_hit3_full_day": None,
            "proxy_status": "NO_ELIGIBLE",
        }

        if official_pick:
            item = official_pick["item"]
            row = item.get("row") or {}
            outcome = item.get("outcome") or {}
            maximum = num(outcome.get("max_executable_return_pct"))
            last = num(outcome.get("last_executable_return_pct"))
            policy = 3.0 if maximum is not None and maximum >= 3.0 else last
            daily.update({
                "official_trade_action": "BUY_OFFICIAL",
                "official_lane": official_pick["shadow"].get("lane"),
                "entry_ask": num(row.get("entry_ask")),
                "entry_bid": num(row.get("entry_bid")),
                "max_executable_return_pct": maximum,
                "last_executable_return_pct": last,
                "official_policy_return_pct": policy,
                "official_hit3": None if maximum is None else maximum >= 3.0,
            })

        if proxy_pick:
            item = proxy_pick["item"]
            row = item.get("row") or {}
            scored = item.get("score") or {}
            evidence = scored.get("evidence") if isinstance(scored.get("evidence"), Mapping) else {}
            code = proxy_pick["code"]
            close = proxy_pick["signal_close_proxy"]
            next_key = re.sub(r"\D", "", str(row.get("next_date") or next_date))
            if code not in chart_cache:
                try:
                    chart_cache[code] = chart_rows(code)
                except Exception:
                    chart_cache[code] = {}
            next_bar = chart_cache[code].get(next_key) if next_key else None
            open_px = num((next_bar or {}).get("open"))
            high_px = num((next_bar or {}).get("high"))
            low_px = num((next_bar or {}).get("low"))
            close_px = num((next_bar or {}).get("close"))
            open_ret, high_ret = pct(open_px, close), pct(high_px, close)
            low_ret, close_ret = pct(low_px, close), pct(close_px, close)
            hit3 = None if high_ret is None else high_ret >= 3.0
            policy = 3.0 if hit3 else close_ret
            daily.update({
                "proxy_trade_action": "BUY_PROXY",
                "proxy_status": "PROVISIONAL_NO_QUOTE" if daily["official_trade_action"] == "NO_TRADE" else "OFFICIAL_ALSO_AVAILABLE",
                "code": code,
                "name": proxy_pick["name"],
                "proxy_lane": proxy_pick["shadow"].get("lane"),
                "quality": num(proxy_pick["shadow"].get("quality")),
                "risk_rate": num(proxy_pick["shadow"].get("risk_rate")),
                "spread_pct": None,
                "change_rate": num(proxy_pick["shadow"].get("change_rate")),
                "digest_ratio": num(proxy_pick["shadow"].get("digest_ratio")),
                "close_location": num(proxy_pick["shadow"].get("close_location")),
                "upper_wick": num(proxy_pick["shadow"].get("upper_wick")),
                "body_ratio": num(proxy_pick["shadow"].get("body_ratio")),
                "theme_breadth": num(proxy_pick["shadow"].get("theme_breadth")),
                "leader_rank": num(proxy_pick["shadow"].get("leader_rank")),
                "directness_points": num(proxy_pick["shadow"].get("directness_points")),
                "freshness_points": num(proxy_pick["shadow"].get("freshness_points")),
                "evidence_title": str(evidence.get("title") or ""),
                "signal_close_proxy": close,
                "next_open_proxy": open_px,
                "next_high_proxy": high_px,
                "next_low_proxy": low_px,
                "next_close_proxy": close_px,
                "next_open_return_pct": open_ret,
                "next_high_return_pct": high_ret,
                "next_low_return_pct": low_ret,
                "next_close_return_pct": close_ret,
                "proxy_full_day_policy_return_pct": policy,
                "proxy_hit3_full_day": hit3,
            })
            selected_rows.append(dict(daily))

        daily_rows.append(daily)

    if not daily_rows:
        raise RuntimeError(f"no rows found for month {args.month}")

    fieldnames = list(daily_rows[0])
    with (output / "june-v6-daily.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader(); writer.writerows(daily_rows)
    with (output / "june-v6-selected.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader(); writer.writerows(selected_rows)

    official_evaluated = [r for r in daily_rows if num(r.get("official_policy_return_pct")) is not None]
    proxy_evaluated = [r for r in selected_rows if num(r.get("next_close_return_pct")) is not None]
    proxy_open = [num(r.get("next_open_return_pct")) for r in proxy_evaluated]
    proxy_high = [num(r.get("next_high_return_pct")) for r in proxy_evaluated]
    proxy_low = [num(r.get("next_low_return_pct")) for r in proxy_evaluated]
    proxy_close = [num(r.get("next_close_return_pct")) for r in proxy_evaluated]
    proxy_policy = [num(r.get("proxy_full_day_policy_return_pct")) for r in proxy_evaluated]

    summary = {
        "version": "largo-close-v6-shadow-june-2026-backtest-v1",
        "rule_version": "largo-close-v6-shadow-v1-unchanged",
        "month": args.month,
        "signal_days": len(daily_rows),
        "candidate_rows": sum(int(r.get("candidate_count") or 0) for r in daily_rows),
        "exact_entry_quote_rows": exact_entry_rows,
        "exact_outcome_quote_rows": exact_outcome_rows,
        "official_selected_days": sum(r.get("official_trade_action") == "BUY_OFFICIAL" for r in daily_rows),
        "official_evaluated_picks": len(official_evaluated),
        "official_positive_count": sum(float(r["official_policy_return_pct"]) > 0 for r in official_evaluated),
        "official_hit3_count": sum(truth(r.get("official_hit3")) for r in official_evaluated),
        "mean_official_policy_return_pct": mean([num(r.get("official_policy_return_pct")) for r in official_evaluated]),
        "proxy_selected_days": len(selected_rows),
        "proxy_no_trade_days": len(daily_rows) - len(selected_rows),
        "proxy_evaluated_picks": len(proxy_evaluated),
        "proxy_positive_open_count": sum((x or 0) > 0 for x in proxy_open),
        "proxy_positive_close_count": sum((x or 0) > 0 for x in proxy_close),
        "proxy_hit3_full_day_count": sum(truth(r.get("proxy_hit3_full_day")) for r in proxy_evaluated),
        "mean_proxy_open_return_pct": mean(proxy_open),
        "mean_proxy_high_return_pct": mean(proxy_high),
        "mean_proxy_low_return_pct": mean(proxy_low),
        "mean_proxy_close_return_pct": mean(proxy_close),
        "median_proxy_close_return_pct": median(proxy_close),
        "mean_proxy_full_day_policy_return_pct": mean(proxy_policy),
        "sum_proxy_full_day_policy_return_pct": round(sum(x for x in proxy_policy if x is not None), 4),
        "dates": [r["signal_date"] for r in daily_rows],
        "selected": selected_rows,
        "limitations": [
            "정확한 15:18 매도·매수호가와 다음 날 09:00~09:05 최우선 매수호가가 모두 있어야 공식 거래로 계산합니다.",
            "PROVISIONAL_NO_QUOTE는 호가 조건을 확인하지 못한 일봉 대용 후보입니다.",
            "일봉 장중 고가는 09:06 이후 움직임을 포함할 수 있습니다.",
            "후보와 테마는 과거 일봉 및 실행 시점의 목록을 사용한 재구성입니다.",
            "수수료, 세금, 호가 잔량과 주문 지연은 반영하지 않았습니다.",
        ],
    }
    (output / "june-v6-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "JUNE_V6_BACKTEST.md").write_text(build_markdown(summary, daily_rows), encoding="utf-8")
    print(json.dumps({
        "signal_days": summary["signal_days"],
        "candidate_rows": summary["candidate_rows"],
        "official_selected_days": summary["official_selected_days"],
        "proxy_selected_days": summary["proxy_selected_days"],
        "proxy_hit3_full_day_count": summary["proxy_hit3_full_day_count"],
        "mean_proxy_close_return_pct": summary["mean_proxy_close_return_pct"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
