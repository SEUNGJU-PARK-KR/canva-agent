#!/usr/bin/env python3
"""Add catalyst timestamps and repair deterministic data-quality gates for v4."""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any, Mapping

from largo_winrate_v4 import (
    VERSION, atomic_json, enrich_news_for_candidate, metric_value, num,
    risk_plan, source_timestamp,
)


def update_check(row: dict[str, Any], check_id: str, status: str, reason: str) -> None:
    for check in row.get("checks") or []:
        if isinstance(check, dict) and str(check.get("id")) == check_id:
            check.setdefault("original_status", check.get("status"))
            check.setdefault("original_reason", check.get("reason"))
            check["status"] = status
            check["reason"] = reason
            return


def enrich(latest: dict[str, Any], payloads: Mapping[str, Any] | None, timeout: int, delay: float) -> dict[str, Any]:
    rows = [row for row in latest.get("candidates") or [] if isinstance(row, dict)]
    source_at = source_timestamp(latest, rows)
    errors: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        code = str(row.get("code") or "")
        offline = isinstance(payloads, Mapping)
        payload = payloads.get(code, {}) if offline else None
        news = enrich_news_for_candidate(row, source_at, timeout=timeout, payload=payload if offline else None)
        fresh = news.get("catalyst_freshness") or {}
        metrics = row.get("metrics") if isinstance(row.get("metrics"), Mapping) else {}
        historical = any(num(metrics.get(key)) is not None for key in ("ma5", "ma10", "ma20", "ma60", "prev20_high"))
        wick = metric_value(row, "upper_wick_ratio", "upper_wick")
        body = metric_value(row, "body_ratio")
        risk = risk_plan(row)
        if historical:
            update_check(row, "X_IPO", "PASS", "과거 일봉과 이동평균이 있어 당일 신규 상장이 아닙니다.")
        if wick is not None and body is not None and wick <= 0.20 and body >= 0.40:
            update_check(row, "C_WICK", "PASS", f"윗꼬리 {wick:.2f} · 몸통 {body:.2f}")
        if risk.get("status") == "PASS":
            update_check(row, "R_PLAN", "PASS", f"구조 손절 위험거리 {float(risk['rate']):.2%}")
            update_check(row, "ENTRY_STOP_AUTO", "PASS", "진입가와 구조 손절선이 계산되었습니다.")
        row["winrate_v4"] = {
            "version": VERSION,
            "enriched_at": source_at.isoformat() if source_at else None,
            "prior_history_confirmed": historical,
            "catalyst_freshness": fresh,
            "matched_catalyst_items": news.get("matched_items") or [],
            "after_signal_items": news.get("after_signal_items") or [],
            "source_errors": news.get("errors") or [],
        }
        if news.get("errors"):
            errors.append({"code": code, "errors": news["errors"]})
        if payloads is None and delay > 0 and index + 1 < len(rows):
            time.sleep(delay)
    latest["winrate_v4"] = {
        "version": VERSION,
        "source_at": source_at.isoformat() if source_at else None,
        "candidates": len(rows),
        "source_error_count": len(errors),
        "source_errors": errors,
        "notes": [
            "상장일 미확인은 과거 일봉 존재 여부로 보완합니다.",
            "윗꼬리 0에 가까운 강한 양봉을 경고로 두던 오류를 수정합니다.",
            "재료 제목과 뉴스·공시 제목을 대조해 발표 시각을 기록합니다.",
        ],
    }
    return latest


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("--data", type=Path, required=True)
    result.add_argument("--output", type=Path)
    result.add_argument("--payload-file", type=Path)
    result.add_argument("--timeout", type=int, default=15)
    result.add_argument("--delay", type=float, default=0.10)
    return result


def main() -> None:
    args = parser().parse_args()
    latest = json.loads(args.data.read_text(encoding="utf-8"))
    payloads = None
    if args.payload_file and args.payload_file.exists():
        payloads = json.loads(args.payload_file.read_text(encoding="utf-8"))
    output = enrich(latest, payloads, args.timeout, args.delay)
    target = args.output or args.data
    atomic_json(target, output)
    print(json.dumps(output.get("winrate_v4"), ensure_ascii=False))


if __name__ == "__main__":
    main()
