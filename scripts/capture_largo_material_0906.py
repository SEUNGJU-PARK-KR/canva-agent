#!/usr/bin/env python3
"""Capture Largo material grades at 15:18 and validate executable returns before 09:06.

This is a deterministic research pipeline. It does not place orders and it does not
use a generative model. A score is an explanatory variable, not a buy signal.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import html
import json
import math
import statistics
import time
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from largo_material_0906 import (
    KST,
    NAVER_STOCK,
    VERSION,
    TARGET3_VERSION,
    atomic_json,
    entry_from_legacy,
    fetch_json,
    fetch_legacy_window,
    flatten_news,
    json_safe,
    load_json,
    num,
    outcome_before_0906,
    parse_datetime,
    score_candidate,
    theme_metrics_from_candidate,
    theme_metrics_from_payload,
    target3_gate,
)

DEFAULT_HISTORY: dict[str, Any] = {
    "version": VERSION,
    "generated_at": None,
    "theme_snapshots": [],
    "candidate_snapshots": [],
    "signals": [],
    "results": [],
    "events": [],
    "summary": {},
}


def now_kst(value: str | None = None) -> dt.datetime:
    if value:
        parsed = dt.datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=KST)
        return parsed.astimezone(KST)
    return dt.datetime.now(KST)


def source_timestamp(latest: Mapping[str, Any]) -> dt.datetime | None:
    values: list[dt.datetime] = []
    for key in ("generated_at", "updated_at", "market_at"):
        parsed = parse_datetime(latest.get(key))
        if parsed:
            values.append(parsed)
    for row in latest.get("candidates") or []:
        if not isinstance(row, Mapping):
            continue
        for key in ("updated_at", "generated_at", "source_at"):
            parsed = parse_datetime(row.get(key))
            if parsed:
                values.append(parsed)
    return max(values) if values else None


def ensure_history(value: Any) -> dict[str, Any]:
    result = dict(DEFAULT_HISTORY)
    if isinstance(value, Mapping):
        result.update({key: value.get(key, default) for key, default in DEFAULT_HISTORY.items()})
    for key in ("theme_snapshots", "candidate_snapshots", "signals", "results", "events"):
        if not isinstance(result.get(key), list):
            result[key] = []
    result["version"] = VERSION
    return result


def upsert(rows: list[dict[str, Any]], item: dict[str, Any], keys: Sequence[str]) -> None:
    signature = tuple(item.get(key) for key in keys)
    for index, row in enumerate(rows):
        if tuple(row.get(key) for key in keys) == signature:
            rows[index] = item
            return
    rows.append(item)


def merge_history(primary: Mapping[str, Any], bootstrap: Mapping[str, Any] | None) -> dict[str, Any]:
    result = ensure_history(primary)
    if not isinstance(bootstrap, Mapping):
        return result
    seed = ensure_history(bootstrap)
    definitions = {
        "theme_snapshots": ("date", "stage"),
        "candidate_snapshots": ("date", "stage"),
        "signals": ("signal_date",),
        "results": ("signal_date", "code"),
        "events": ("at", "stage"),
    }
    for key, signature in definitions.items():
        merged = [dict(row) for row in seed.get(key) or [] if isinstance(row, Mapping)]
        for row in result.get(key) or []:
            if isinstance(row, Mapping):
                upsert(merged, dict(row), signature)
        result[key] = merged
    return result



def target3_sort_key(candidate: Mapping[str, Any]) -> tuple[float, float, float, str]:
    target = candidate.get("target3") if isinstance(candidate.get("target3"), Mapping) else {}
    structure = candidate.get("structure") if isinstance(candidate.get("structure"), Mapping) else {}
    risk = num(target.get("risk_rate"))
    if risk is None:
        risk = num(structure.get("risk_rate"))
    spread = num(target.get("spread_pct"))
    turnover = num(candidate.get("trade_value")) or 0.0
    return (
        float(risk) if risk is not None else float("inf"),
        float(spread) if spread is not None else float("inf"),
        -float(turnover),
        str(candidate.get("name") or ""),
    )


def apply_daily_target3_selection(candidates: Sequence[Mapping[str, Any]]) -> None:
    """Keep at most one operationally valid v5 candidate for a signal date."""
    qualified: list[dict[str, Any]] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        target = candidate.get("target3") if isinstance(candidate.get("target3"), dict) else None
        if target is None:
            continue
        target["daily_pick"] = False
        target["daily_rank"] = None
        if bool(target.get("qualified")) and not target.get("operational_blockers") and target.get("status") != "BLOCK":
            qualified.append(candidate)
        else:
            target["eligible"] = False

    qualified.sort(key=target3_sort_key)
    for rank, candidate in enumerate(qualified, start=1):
        target = candidate["target3"]
        target["daily_rank"] = rank
        target["daily_pick"] = rank == 1
        if rank == 1:
            target["eligible"] = True
            target["status"] = "PASS"
            target["blockers"] = [x for x in target.get("blockers") or [] if x != "daily_one_pick_only"]
        else:
            target["eligible"] = False
            target["status"] = "ALTERNATE"
            target["blockers"] = list(dict.fromkeys(list(target.get("blockers") or []) + ["daily_one_pick_only"]))


def migrate_target3_history(history: dict[str, Any]) -> dict[str, Any]:
    """Backfill v5 gates and one-pick ranking into previously published history rows."""
    lookup: dict[tuple[str, str], Mapping[str, Any]] = {}
    for signal in history.get("signals") or []:
        if not isinstance(signal, Mapping):
            continue
        signal_date = str(signal.get("signal_date") or "")
        candidates = [row for row in signal.get("candidates") or [] if isinstance(row, dict)]
        for candidate in candidates:
            target3 = candidate.get("target3") if isinstance(candidate.get("target3"), Mapping) else None
            if not target3 or str(target3.get("version") or "") != TARGET3_VERSION:
                target3 = target3_gate(candidate, {
                    "entry_ask": candidate.get("entry_ask"),
                    "entry_bid": candidate.get("entry_bid"),
                })
                candidate["target3"] = target3
        apply_daily_target3_selection(candidates)
        for candidate in candidates:
            target3 = candidate.get("target3") if isinstance(candidate.get("target3"), Mapping) else {}
            lookup[(signal_date, str(candidate.get("code") or "").zfill(6))] = target3

    for result in history.get("results") or []:
        if not isinstance(result, dict):
            continue
        key = (str(result.get("signal_date") or ""), str(result.get("code") or "").zfill(6))
        target3 = lookup.get(key)
        if target3:
            result["target3"] = target3
            result["target3_version"] = target3.get("version")
            result["target3_status"] = target3.get("status")
            result["target3_lane"] = target3.get("lane")
            result["target3_eligible"] = bool(target3.get("eligible"))
            result["target3_qualified"] = bool(target3.get("qualified"))
            result["target3_daily_rank"] = target3.get("daily_rank")
            result["target3_size_band"] = target3.get("size_band")
            result["target3_spread_pct"] = target3.get("spread_pct")
        maximum = num(result.get("max_executable_return_pct"))
        result["hit_3_exec"] = None if maximum is None else maximum >= 3.0
    history["target3_version"] = TARGET3_VERSION
    return history

def candidate_theme_code(candidate: Mapping[str, Any]) -> str | None:
    theme = candidate.get("theme") if isinstance(candidate.get("theme"), Mapping) else {}
    value = theme.get("code")
    return str(value) if value not in (None, "") else None


def compact_theme(value: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "name", "code", "rising", "total", "breadth", "leader_rank",
        "follower_strong_count", "follower_turnover", "follower_turnover_ratio",
        "observed_breadth", "observed_leadership", "observed_followers",
    )
    return json_safe({key: value.get(key) for key in keys})


def collect_themes(candidates: Sequence[Mapping[str, Any]], *, timeout: int, delay: float) -> tuple[dict[str, Any], list[str]]:
    themes: dict[str, Any] = {}
    errors: list[str] = []
    representative: dict[str, Mapping[str, Any]] = {}
    for candidate in candidates:
        theme_code = candidate_theme_code(candidate)
        if theme_code and theme_code not in representative:
            representative[theme_code] = candidate
    for theme_code, candidate in representative.items():
        url = f"{NAVER_STOCK}/api/domestic/market/theme/{theme_code}/stocklist?marketType=ALL&orderType=priceTop&startIdx=0&pageSize=100"
        try:
            payload = fetch_json(url, timeout=timeout)
            metric = theme_metrics_from_payload(candidate, payload)
        except Exception as exc:  # best-effort public endpoint
            errors.append(f"theme {theme_code}: {type(exc).__name__}: {exc}")
            metric = theme_metrics_from_candidate(candidate)
        themes[theme_code] = compact_theme(metric)
        if delay:
            time.sleep(delay)
    return themes, errors


def fetch_news_box(code: str, *, timeout: int, delay: float) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    result: dict[str, Any] = {}
    paths = {
        "news": f"/api/domestic/detail/news?itemCode={code}&page=1&pageSize=30",
        "notice": f"/api/domestic/detail/notice?itemCode={code}&startIdx=0&pageSize=30",
    }
    for key, path in paths.items():
        try:
            result[key] = {"payload": fetch_json(NAVER_STOCK + path, timeout=timeout)}
        except Exception as exc:
            errors.append(f"{key} {code}: {type(exc).__name__}: {exc}")
            result[key] = {"payload": []}
        if delay:
            time.sleep(delay)
    return result, errors


def record_event(history: dict[str, Any], *, stage: str, at: dt.datetime, status: str, detail: str) -> None:
    upsert(history["events"], {
        "at": at.isoformat(), "stage": stage, "status": status, "detail": detail,
    }, ("at", "stage"))
    history["events"] = sorted(history["events"], key=lambda row: str(row.get("at") or ""))[-120:]


def append_snapshot(
    history: dict[str, Any],
    *,
    stage: str,
    at: dt.datetime,
    latest: Mapping[str, Any],
    themes: Mapping[str, Any],
    errors: Sequence[str],
    keep_candidates: bool,
) -> None:
    source_at = source_timestamp(latest)
    date_text = at.date().isoformat()
    theme_snapshot = {
        "date": date_text,
        "stage": stage,
        "at": at.isoformat(),
        "source_at": source_at.isoformat() if source_at else None,
        "themes": json_safe(themes),
        "errors": list(errors),
    }
    upsert(history["theme_snapshots"], theme_snapshot, ("date", "stage"))
    history["theme_snapshots"] = sorted(
        history["theme_snapshots"], key=lambda row: (str(row.get("date") or ""), str(row.get("stage") or ""))
    )[-180:]
    if keep_candidates:
        snapshot = {
            "date": date_text,
            "stage": stage,
            "at": at.isoformat(),
            "source_at": source_at.isoformat() if source_at else None,
            "candidates": json_safe([row for row in latest.get("candidates") or [] if isinstance(row, Mapping)]),
        }
        upsert(history["candidate_snapshots"], snapshot, ("date", "stage"))
        history["candidate_snapshots"] = sorted(
            history["candidate_snapshots"], key=lambda row: (str(row.get("date") or ""), str(row.get("stage") or ""))
        )[-90:]


def signal_basis(history: Mapping[str, Any], date_text: str, fallback: Mapping[str, Any]) -> tuple[list[Mapping[str, Any]], dt.datetime | None, str]:
    candidates = [
        row for row in history.get("candidate_snapshots") or []
        if isinstance(row, Mapping) and str(row.get("date")) == date_text
    ]
    if candidates:
        priority = {"15:10": 3, "14:53": 2, "15:18": 1}
        chosen = max(candidates, key=lambda row: (priority.get(str(row.get("stage")), 0), str(row.get("at") or "")))
        rows = [row for row in chosen.get("candidates") or [] if isinstance(row, Mapping)]
        return rows, parse_datetime(chosen.get("source_at")), f"저장된 {chosen.get('stage')} 후보 스냅샷"
    rows = [row for row in fallback.get("candidates") or [] if isinstance(row, Mapping)]
    return rows, source_timestamp(fallback), "실행 시점 최신 후보 스냅샷"


def same_date_theme_history(history: Mapping[str, Any], date_text: str) -> list[Mapping[str, Any]]:
    result = [
        row for row in history.get("theme_snapshots") or []
        if isinstance(row, Mapping) and str(row.get("date")) == date_text
    ]
    return sorted(result, key=lambda row: str(row.get("at") or ""))


def signal_stage(
    history: dict[str, Any],
    *,
    now: dt.datetime,
    latest: Mapping[str, Any],
    timeout: int,
    delay: float,
) -> None:
    date_text = now.date().isoformat()
    signal_at = dt.datetime.combine(now.date(), dt.time(15, 18), tzinfo=KST)
    basis_rows, basis_source_at, basis_label = signal_basis(history, date_text, latest)
    if not basis_rows:
        record_event(history, stage="15:18", at=now, status="NO_CANDIDATES", detail="후보 스냅샷이 없습니다.")
        return

    themes, theme_errors = collect_themes(basis_rows, timeout=timeout, delay=delay)
    append_snapshot(
        history, stage="15:18", at=now, latest={"candidates": basis_rows, "generated_at": basis_source_at.isoformat() if basis_source_at else None},
        themes=themes, errors=theme_errors, keep_candidates=False,
    )
    theme_history = same_date_theme_history(history, date_text)
    capture_lag = (now - signal_at).total_seconds() / 60
    source_age = (signal_at - basis_source_at).total_seconds() / 60 if basis_source_at else None
    source_valid = bool(
        basis_source_at
        and basis_source_at.date() == now.date()
        and -1 <= float(source_age) <= 35
    )
    timely_theme = capture_lag <= 5

    scored_rows: list[dict[str, Any]] = []
    errors = list(theme_errors)
    for index, candidate in enumerate(basis_rows, start=1):
        code = str(candidate.get("code") or "").zfill(6)
        news_box, news_errors = fetch_news_box(code, timeout=timeout, delay=delay)
        errors.extend(news_errors)
        items = flatten_news(news_box)
        theme_code = candidate_theme_code(candidate)
        theme_metric = themes.get(str(theme_code)) if theme_code else None
        scored = score_candidate(
            candidate,
            signal_at,
            items,
            theme_metrics=theme_metric or theme_metrics_from_candidate(candidate),
            theme_history=theme_history,
            proxy_note=None,
        )
        try:
            legacy = fetch_legacy_window(code, date_text.replace("-", "") + "151800", timeout=timeout)
            entry = entry_from_legacy(legacy)
        except Exception as exc:
            entry = {"entry_time": None, "entry_last": None, "entry_ask": None, "entry_bid": None}
            errors.append(f"15:18 quote {code}: {type(exc).__name__}: {exc}")
        production_score = scored.get("production_score")
        production_reasons: list[str] = []
        if not source_valid:
            production_score = None
            production_reasons.append("후보 기준 시각이 15:18 이전 35분 범위를 벗어남")
        if not timely_theme:
            production_score = None
            production_reasons.append("테마 최종 캡처가 15:23 이후 시작됨")
        if not entry.get("entry_ask"):
            production_score = None
            production_reasons.append("15:18 실행 기준 매도호가 미확인")

        target3 = target3_gate(scored, entry)
        target3_operational_blockers: list[str] = []
        if not source_valid:
            target3_operational_blockers.append("후보 기준 시각이 15:18 이전 35분 범위를 벗어남")
        if target3.get("lane") in {"MOMENTUM_DIGESTION", "BOTH"} and not timely_theme:
            target3_operational_blockers.append("테마 15:18 최종 캡처가 늦음")
        if not entry.get("entry_ask") or not entry.get("entry_bid"):
            target3_operational_blockers.append("15:18 최우선 매도·매수호가 미확인")
        if target3_operational_blockers:
            target3["eligible"] = False
            target3["status"] = "BLOCK"
            target3["operational_blockers"] = target3_operational_blockers
            target3["blockers"] = list(dict.fromkeys(list(target3.get("blockers") or []) + target3_operational_blockers))
        else:
            target3["operational_blockers"] = []

        scored_rows.append(json_safe({
            **scored,
            **entry,
            "target3": target3,
            "production_score": production_score,
            "production_eligible": production_score is not None,
            "production_blockers": production_reasons,
            "signal_basis": basis_label,
            "candidate_source_at": basis_source_at.isoformat() if basis_source_at else None,
            "source_age_minutes": round(source_age, 2) if source_age is not None else None,
            "theme_capture_lag_minutes": round(capture_lag, 2),
            "trade_value": (num(candidate.get("trade_value")) or num((candidate.get("metrics") or {}).get("trade_value"))) if isinstance(candidate.get("metrics"), Mapping) else num(candidate.get("trade_value")),
            "research_rank": index,
        }))

    apply_daily_target3_selection(scored_rows)
    scored_rows.sort(
        key=lambda row: (
            0 if bool((row.get("target3") or {}).get("eligible")) else
            1 if bool((row.get("target3") or {}).get("qualified")) else
            2 if str((row.get("target3") or {}).get("status") or "") == "WATCH" else 3,
            int((row.get("target3") or {}).get("daily_rank") or 999),
            *target3_sort_key(row),
        )
    )
    # Keep deterministic legacy ordering fields only as a final fallback.
    signal = {
        "signal_date": date_text,
        "signal_at": signal_at.isoformat(),
        "captured_at": now.isoformat(),
        "candidate_source_at": basis_source_at.isoformat() if basis_source_at else None,
        "signal_basis": basis_label,
        "source_valid": source_valid,
        "timely_theme": timely_theme,
        "candidates": scored_rows,
        "errors": errors,
        "proxy": False,
    }
    upsert(history["signals"], signal, ("signal_date",))
    history["signals"] = sorted(history["signals"], key=lambda row: str(row.get("signal_date") or ""))[-120:]
    record_event(
        history, stage="15:18", at=now, status="SIGNAL_CAPTURED",
        detail=(
            f"후보 {len(scored_rows)}개, v5 최종 1종목 "
            f"{sum(bool((row.get('target3') or {}).get('eligible')) for row in scored_rows)}개, "
            f"실전 점수 산출 {sum(row.get('production_score') is not None for row in scored_rows)}개"
        ),
    )


def evaluate_stage(history: dict[str, Any], *, now: dt.datetime, timeout: int, delay: float) -> None:
    completed = {(str(row.get("signal_date")), str(row.get("code"))) for row in history.get("results") or [] if isinstance(row, Mapping)}
    signals = [
        row for row in history.get("signals") or []
        if isinstance(row, Mapping) and str(row.get("signal_date") or "") < now.date().isoformat()
    ]
    signals.sort(key=lambda row: str(row.get("signal_date") or ""), reverse=True)
    target = next(
        (
            signal for signal in signals
            if any((str(signal.get("signal_date")), str(candidate.get("code"))) not in completed for candidate in signal.get("candidates") or [])
        ),
        None,
    )
    if target is None:
        record_event(history, stage="09:06", at=now, status="NO_PENDING_SIGNAL", detail="평가할 전일 신호가 없습니다.")
        return

    date_text = now.date().isoformat()
    observations = 0
    staged: list[dict[str, Any]] = []
    errors: list[str] = []
    for candidate in target.get("candidates") or []:
        if not isinstance(candidate, Mapping):
            continue
        code = str(candidate.get("code") or "").zfill(6)
        try:
            legacy = fetch_legacy_window(code, date_text.replace("-", "") + "090600", timeout=timeout)
            outcome = outcome_before_0906(
                legacy,
                entry_last=num(candidate.get("entry_last")),
                entry_ask=num(candidate.get("entry_ask")),
            )
        except Exception as exc:
            outcome = outcome_before_0906([], entry_last=None, entry_ask=None)
            errors.append(f"09:06 quote {code}: {type(exc).__name__}: {exc}")
        observations += int(outcome.get("open_observations") or 0)
        staged.append(json_safe({
            "signal_date": target.get("signal_date"),
            "next_date": date_text,
            "evaluated_at": now.isoformat(),
            "code": code,
            "name": candidate.get("name"),
            "score": candidate.get("comparable_score"),
            "raw_score": candidate.get("raw_score"),
            "production_score": candidate.get("production_score"),
            "coverage": candidate.get("coverage"),
            "grade": candidate.get("grade"),
            "grade_status": candidate.get("grade_status"),
            "target3": candidate.get("target3"),
            "target3_version": (candidate.get("target3") or {}).get("version") if isinstance(candidate.get("target3"), Mapping) else None,
            "target3_status": (candidate.get("target3") or {}).get("status") if isinstance(candidate.get("target3"), Mapping) else None,
            "target3_lane": (candidate.get("target3") or {}).get("lane") if isinstance(candidate.get("target3"), Mapping) else None,
            "target3_eligible": bool((candidate.get("target3") or {}).get("eligible")) if isinstance(candidate.get("target3"), Mapping) else False,
            "target3_size_band": (candidate.get("target3") or {}).get("size_band") if isinstance(candidate.get("target3"), Mapping) else None,
            "target3_spread_pct": (candidate.get("target3") or {}).get("spread_pct") if isinstance(candidate.get("target3"), Mapping) else None,
            "entry_time": candidate.get("entry_time"),
            "entry_last": candidate.get("entry_last"),
            "entry_ask": candidate.get("entry_ask"),
            "entry_bid": candidate.get("entry_bid"),
            **outcome,
            "proxy": bool(target.get("proxy")),
        }))
        if delay:
            time.sleep(delay)

    if observations == 0:
        record_event(
            history, stage="09:06", at=now, status="NO_MARKET_ROWS",
            detail=f"{target.get('signal_date')} 신호의 다음 거래일 09:00~09:05 자료가 없어 평가를 보류했습니다.",
        )
        return
    for item in staged:
        upsert(history["results"], item, ("signal_date", "code"))
    history["results"] = sorted(history["results"], key=lambda row: (str(row.get("signal_date") or ""), str(row.get("code") or "")))[-4000:]
    record_event(
        history, stage="09:06", at=now, status="EVALUATED",
        detail=f"{target.get('signal_date')} 신호 {len(staged)}개를 {date_text} 09:06 전에 평가했습니다. 오류 {len(errors)}건",
    )


def ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda index: values[index])
    output = [0.0] * len(values)
    cursor = 0
    while cursor < len(order):
        end = cursor
        value = values[order[cursor]]
        while end + 1 < len(order) and values[order[end + 1]] == value:
            end += 1
        rank = (cursor + end) / 2 + 1
        for position in range(cursor, end + 1):
            output[order[position]] = rank
        cursor = end + 1
    return output


def spearman(rows: Sequence[Mapping[str, Any]], x_key: str, y_key: str) -> float | None:
    pairs = [(num(row.get(x_key)), num(row.get(y_key))) for row in rows]
    pairs = [(x, y) for x, y in pairs if x is not None and y is not None]
    if len(pairs) < 3:
        return None
    x_rank = ranks([float(x) for x, _ in pairs])
    y_rank = ranks([float(y) for _, y in pairs])
    x_mean = statistics.fmean(x_rank)
    y_mean = statistics.fmean(y_rank)
    numerator = sum((x - x_mean) * (y - y_mean) for x, y in zip(x_rank, y_rank))
    denominator = math.sqrt(sum((x - x_mean) ** 2 for x in x_rank) * sum((y - y_mean) ** 2 for y in y_rank))
    return numerator / denominator if denominator else None


def stats(rows: Iterable[Mapping[str, Any]], *, score_key: str = "score") -> dict[str, Any]:
    items = [row for row in rows if num(row.get("max_executable_return_pct")) is not None]
    if not items:
        return {
            "n": 0, "avg_score": None, "avg_max_return_pct": None, "median_max_return_pct": None,
            "positive_rate": None, "hit_0_5_rate": None, "hit_1_rate": None,
            "hit_2_rate": None, "hit_3_rate": None, "avg_policy_3_return_pct": None,
            "policy_3_loss_rate": None,
        }
    returns = [float(row["max_executable_return_pct"]) for row in items]
    last_returns = [float(value) for row in items if (value := num(row.get("last_executable_return_pct"))) is not None]
    scores = [float(value) for row in items if (value := num(row.get(score_key))) is not None]
    policy3 = [3.0 if value >= 3.0 else float(num(row.get("last_executable_return_pct")) or 0.0) for row, value in zip(items, returns)]
    return {
        "n": len(items),
        "avg_score": round(statistics.fmean(scores), 4) if scores else None,
        "avg_max_return_pct": round(statistics.fmean(returns), 4),
        "median_max_return_pct": round(statistics.median(returns), 4),
        "positive_rate": round(sum(value > 0 for value in returns) / len(returns), 4),
        "hit_0_5_rate": round(sum(value >= 0.5 for value in returns) / len(returns), 4),
        "hit_1_rate": round(sum(value >= 1.0 for value in returns) / len(returns), 4),
        "hit_2_rate": round(sum(value >= 2.0 for value in returns) / len(returns), 4),
        "hit_3_rate": round(sum(value >= 3.0 for value in returns) / len(returns), 4),
        "avg_policy_3_return_pct": round(statistics.fmean(policy3), 4),
        "policy_3_loss_rate": round(sum(value < 0 for value in policy3) / len(policy3), 4),
        "avg_last_return_pct": round(statistics.fmean(last_returns), 4) if last_returns else None,
    }

def build_summary(history: Mapping[str, Any], now: dt.datetime) -> dict[str, Any]:
    rows = [
        row for row in history.get("results") or []
        if isinstance(row, Mapping) and num(row.get("max_executable_return_pct")) is not None
    ]
    score_rows = [row for row in rows if num(row.get("score")) is not None]
    target3_rows = [row for row in rows if bool(row.get("target3_eligible"))]
    dates = sorted({str(row.get("signal_date")) for row in rows}, reverse=True)
    top1: list[Mapping[str, Any]] = []
    top3: list[Mapping[str, Any]] = []
    daily: list[dict[str, Any]] = []
    for date_text in dates:
        day = [row for row in score_rows if str(row.get("signal_date")) == date_text]
        day.sort(key=lambda row: float(num(row.get("score")) or -1), reverse=True)
        target3_day = [row for row in target3_rows if str(row.get("signal_date")) == date_text]
        if day:
            top1.append(day[0])
            top3.extend(day[:3])
        daily.append({
            "signal_date": date_text,
            "n": len(day),
            "top_name": day[0].get("name") if day else None,
            "top_score": day[0].get("score") if day else None,
            "top_return_pct": day[0].get("max_executable_return_pct") if day else None,
            "top3": stats(day[:3]),
            "all": stats(day),
            "target3": stats(target3_day),
            "target3_names": [row.get("name") for row in target3_day],
            "proxy": all(bool(row.get("proxy")) for row in day) if day else True,
        })
    thresholds = []
    for threshold in range(40, 91, 5):
        item = stats(row for row in score_rows if float(row.get("score")) >= threshold)
        item["threshold"] = threshold
        thresholds.append(item)
    correlation = spearman(score_rows, "score", "max_executable_return_pct")
    high70 = stats(row for row in score_rows if float(row.get("score")) >= 70)
    production_rows = [row for row in rows if num(row.get("production_score")) is not None]
    completed_live_dates = sorted({str(row.get("signal_date")) for row in production_rows if not bool(row.get("proxy"))}, reverse=True)
    target3_stats = stats(target3_rows)
    if not rows:
        verdict = "완료된 평가가 없습니다. 오늘 15:18 신호부터 +3% 규칙을 고정해 누적합니다."
    elif target3_stats["n"] < 20:
        verdict = (
            "v5 종가베팅 규칙은 과거 6개 평가일 재검증 결과입니다. 20개 실제 신호 전에는 "
            "매수 기준으로 사용하지 않습니다."
        )
    elif (target3_stats.get("hit_3_rate") or 0) > (stats(rows).get("hit_3_rate") or 0):
        verdict = "v5 날짜별 1종목이 전체 후보보다 높은 정책수익을 보입니다. 조건을 고정해 전진검증합니다."
    else:
        verdict = "v5 규칙이 수익을 높이지 못했습니다. 조건을 자동 완화하지 않습니다."
    pending_dates = [
        str(signal.get("signal_date")) for signal in history.get("signals") or []
        if isinstance(signal, Mapping)
        and not any(str(row.get("signal_date")) == str(signal.get("signal_date")) for row in rows)
    ]
    return {
        "version": VERSION,
        "target3_version": TARGET3_VERSION,
        "generated_at": now.isoformat(),
        "definition": {
            "signal": "15:18 best ask with an observed best bid",
            "outcome_window": "09:00 <= time < 09:06 on the next observed trading session",
            "outcome": "maximum observed top bid",
            "target": "+3% versus the 15:18 best ask",
            "selection": "momentum-digestion or direct-event lane; risk <=10%; one lowest-risk pick per day",
        },
        "evaluated_rows": len(rows),
        "evaluated_dates": dates,
        "live_completed_dates": completed_live_dates,
        "pending_signal_dates": sorted(set(pending_dates), reverse=True),
        "overall": stats(score_rows),
        "target3_selected": target3_stats,
        "high_70": high70,
        "high_75": stats(row for row in score_rows if float(row.get("score")) >= 75),
        "high_80": stats(row for row in score_rows if float(row.get("score")) >= 80),
        "daily_top1": stats(top1),
        "daily_top3": stats(top3),
        "production_scored": stats(production_rows, score_key="production_score"),
        "correlation": None if correlation is None else round(correlation, 4),
        "daily": daily,
        "thresholds": thresholds,
        "verdict": verdict,
        "limitations": [
            "기존 공개 이력의 정확한 15:18 신호일은 제한적이며 v5는 오늘부터 별도 누적합니다.",
            "20거래일 재검증 중 성과 호가가 모두 남은 날은 6거래일뿐입니다.",
            "v5 조건은 같은 6개 평가일을 보며 선택했으므로 최소 20개 실제 신호의 고정 전진검증이 필요합니다.",
            "09:06 이전 성과는 09:00~09:05 분별 최우선 매수호가의 최대값이며 수량과 체결 지연은 반영하지 않습니다.",
            "생성형 AI를 쓰지 않고 뉴스·공시 사건, 발표 시각, 테마 확산, 순위와 15:18 호가 간격으로 판정합니다.",
        ],
    }

def fmt_pct(value: Any) -> str:
    parsed = num(value)
    return "-" if parsed is None else f"{parsed:+.2f}%"


def fmt_rate(value: Any) -> str:
    parsed = num(value)
    return "-" if parsed is None else f"{parsed * 100:.1f}%"


def esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


TARGET3_STATUS_LABELS = {
    "PASS": "오늘 최종 1종목",
    "ALTERNATE": "통과 후순위",
    "WATCH": "근접 관찰",
    "BLOCK": "안전 차단",
    "NONE": "미통과",
}
TARGET3_LANE_LABELS = {
    "MOMENTUM_DIGESTION": "추세·거래대금형",
    "DIRECT_EVENT": "직접 재료형",
    "BOTH": "두 경로 동시",
    "NONE": "-",
}
TARGET3_BLOCKER_LABELS = {
    "hard_exclusion_clear": "안전 제외 조건 통과 필요",
    "entry_quote_known": "15:18 최우선 매도·매수호가 확인 필요",
        "structural_risk_at_most_10pct": "구조 위험 10% 이내 필요",
                "spread_at_most_0_20pct": "15:18 호가 간격 0.20% 이하 필요",
    "directness_at_least_14": "직접 재료 점수 14점 이상 필요",
    "common_stock_only": "보통주만 허용",
    "digestion_at_least_0_10": "거래대금 소화 0.10 이상 필요",
    "change_rate_10_to_15pct": "당일 상승률 10~15% 필요",
    "digestion_at_most_1_00": "추세형 거래대금 소화 1.00 이하 필요",
    "digestion_at_most_1_50": "직접형 거래대금 소화 1.50 이하 필요",
    "daily_one_pick_only": "하루 한 종목 원칙에 따른 후순위",
    "freshness_at_least_3": "재료 신선도 3점 이상 필요",
    "spread_at_most_0_10pct": "15:18 호가 간격 0.10% 이하 필요",
}

def target3_blocker_text(value: Any) -> str:
    text = str(value or "")
    return TARGET3_BLOCKER_LABELS.get(text, text.replace("_", " "))

def render_report(history: Mapping[str, Any], summary: Mapping[str, Any]) -> str:
    latest_signal = max(
        (row for row in history.get("signals") or [] if isinstance(row, Mapping)),
        key=lambda row: str(row.get("signal_date") or ""),
        default=None,
    )
    signal_rows = ""
    if latest_signal:
        candidates = [row for row in latest_signal.get("candidates") or [] if isinstance(row, Mapping)]
        candidates.sort(
            key=lambda row: (
                bool((row.get("target3") or {}).get("eligible")) if isinstance(row.get("target3"), Mapping) else False,
                float(num(row.get("production_score")) or num(row.get("comparable_score")) or -1),
            ),
            reverse=True,
        )
        for row in candidates[:24]:
            evidence = row.get("evidence") if isinstance(row.get("evidence"), Mapping) else {}
            theme = row.get("theme") if isinstance(row.get("theme"), Mapping) else {}
            target3 = row.get("target3") if isinstance(row.get("target3"), Mapping) else {}
            signal_rows += (
                "<tr>"
                f"<td><b>{esc(row.get('name'))}</b><small>{esc(row.get('code'))}</small></td>"
                f"<td><b>{esc(TARGET3_STATUS_LABELS.get(str(target3.get('status') or ''), target3.get('status') or '-'))}</b><small>{esc(TARGET3_LANE_LABELS.get(str(target3.get('lane') or ''), target3.get('lane') or ''))}</small></td>"
                f"<td>추세 {esc(target3.get('theme_pass_count') or 0)}/{esc(target3.get('theme_check_count') or 0)}"
                f"<small>직접 {esc(target3.get('direct_pass_count') or 0)}/{esc(target3.get('direct_check_count') or 0)}</small></td>"
                f"<td>{esc(row.get('grade'))}<small>{esc(row.get('grade_status'))}</small></td>"
                f"<td>{esc(row.get('comparable_score'))}</td>"
                f"<td>{esc(theme.get('leader_rank'))}</td>"
                f"<td>{esc(theme.get('rising'))}/{esc(theme.get('total'))}<small>{fmt_rate(theme.get('breadth'))}</small></td>"
                f"<td>{esc(evidence.get('title') or '원문 미확인')}<small>{esc(evidence.get('at') or '')}</small></td>"
                f"<td>{esc(row.get('entry_ask') or '-')}<small>간격 {esc(target3.get('spread_pct') if target3.get('spread_pct') is not None else '-')}%</small></td>"
                f"<td>{'<br>'.join(esc(target3_blocker_text(x)) for x in target3.get('blockers') or []) or 'v5 최종 연구 후보'}</td>"
                "</tr>"
            )
    if not signal_rows:
        signal_rows = "<tr><td colspan='10'>아직 15:18 실시간 신호가 없습니다.</td></tr>"

    daily_rows = "".join(
        "<tr>"
        f"<td>{esc(row.get('signal_date'))}</td><td>{row.get('n')}</td>"
        f"<td>{esc(', '.join(str(x) for x in row.get('target3_names') or []) or '-')}</td>"
        f"<td>{(row.get('target3') or {}).get('n')}</td>"
        f"<td>{fmt_rate((row.get('target3') or {}).get('hit_3_rate'))}</td>"
        f"<td>{fmt_pct((row.get('target3') or {}).get('avg_max_return_pct'))}</td>"
        f"<td>{fmt_rate((row.get('all') or {}).get('hit_3_rate'))}</td>"
        f"<td>{'과거 대리값' if row.get('proxy') else '실시간 전진검증'}</td>"
        "</tr>"
        for row in summary.get("daily") or []
    ) or "<tr><td colspan='8'>완료된 날짜가 없습니다.</td></tr>"

    threshold_rows = "".join(
        "<tr>"
        f"<td>{row.get('threshold')}점 이상</td><td>{row.get('n')}</td>"
        f"<td>{fmt_pct(row.get('avg_max_return_pct'))}</td>"
        f"<td>{fmt_rate(row.get('hit_1_rate'))}</td>"
        f"<td>{fmt_rate(row.get('hit_2_rate'))}</td>"
        f"<td>{fmt_rate(row.get('hit_3_rate'))}</td>"
        f"<td>{fmt_pct(row.get('avg_policy_3_return_pct'))}</td>"
        "</tr>"
        for row in summary.get("thresholds") or []
    )
    overall = summary.get("overall") or {}
    target3 = summary.get("target3_selected") or {}
    latest_date = latest_signal.get("signal_date") if latest_signal else "-"
    pending = ", ".join(summary.get("pending_signal_dates") or []) or "없음"
    limitations = "".join(f"<li>{esc(item)}</li>" for item in summary.get("limitations") or [])
    return f"""<!doctype html><html lang='ko'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='referrer' content='no-referrer'><title>라르고 종가베팅 v5 검증</title><style>
:root{{--bg:#edf2f7;--paper:#fff;--ink:#142137;--muted:#68788d;--line:#d8e1eb;--blue:#1e65c8;--green:#14764c;--amber:#9a6200;--red:#b93645}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font-family:Arial,'Malgun Gothic',sans-serif;line-height:1.55}}main{{max-width:1550px;margin:auto;padding:20px}}.hero{{background:linear-gradient(135deg,#0b2c56,#1763a8);color:#fff;border-radius:24px;padding:28px}}.hero h1{{margin:8px 0}}.hero p{{max-width:1080px;color:#dcecff}}.cards{{display:grid;grid-template-columns:repeat(5,1fr);gap:10px;margin:16px 0}}.card,.section{{background:#fff;border:1px solid var(--line);border-radius:18px;padding:16px;margin-bottom:16px}}.card b{{display:block;font-size:27px}}.card span,small{{color:var(--muted)}}.verdict{{border-left:6px solid var(--amber)}}.table{{overflow:auto}}table{{width:100%;border-collapse:collapse;font-size:12px}}th,td{{padding:9px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}td small{{display:block}}.rules{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}}.rules>div{{border:1px solid var(--line);border-radius:13px;padding:12px}}.status{{display:inline-block;background:#edf4ff;color:#174f96;padding:5px 8px;border-radius:999px}}@media(max-width:1000px){{.cards{{grid-template-columns:repeat(2,1fr)}}.rules{{grid-template-columns:1fr}}}}@media(max-width:560px){{main{{padding:10px}}.cards{{grid-template-columns:1fr}}.hero{{padding:20px}}}}
</style></head><body><main><section class='hero'><span>읽기 전용 연구 · 자동주문 없음 · 생성형 AI 미사용</span><h1>라르고 종가베팅 v5</h1><p>기존 합산 점수를 매수 게이트에서 뺐습니다. 당일 강한 추세와 거래대금 소화가 맞는 종목, 또는 강한 직접 재료가 확인된 종목을 분리해서 검사합니다. 구조 위험 10%를 넘으면 차단하고 하루 한 종목만 남깁니다.</p></section>
<section class='cards'><div class='card'><b>{summary.get('evaluated_rows')}</b><span>평가 후보-거래일</span></div><div class='card'><b>{len(summary.get('evaluated_dates') or [])}</b><span>평가 날짜</span></div><div class='card'><b>{target3.get('n')}</b><span>v5 최종 1종목</span></div><div class='card'><b>{fmt_rate(target3.get('hit_3_rate'))}</b><span>v5 +3% 도달</span></div><div class='card'><b>{fmt_rate(overall.get('hit_3_rate'))}</b><span>전체 +3% 도달</span></div></section>
<section class='section verdict'><h2>판정</h2><p>{esc(summary.get('verdict'))}</p><p>최근 신호일 {esc(latest_date)} · 결과 대기 {esc(pending)}</p></section>
<section class='section'><h2>최근 15:18 v5 연구 후보</h2><p class='status'>오늘 최종 1종목만 연구 후보입니다. 통과 후순위, 근접 관찰, 안전 차단은 진입 대상이 아닙니다.</p><div class='table'><table><thead><tr><th>종목</th><th>v5 상태</th><th>게이트</th><th>재료등급</th><th>기존점수</th><th>순위</th><th>테마 확산</th><th>확인 재료</th><th>15:18 호가</th><th>미통과 사유</th></tr></thead><tbody>{signal_rows}</tbody></table></div></section>
<section class='section'><h2>고정 전진검증</h2><div class='table'><table><thead><tr><th>신호일</th><th>전체 후보</th><th>v5 최종 종목</th><th>선택 수</th><th>선택군 +3%</th><th>선택군 평균 최대</th><th>전체 +3%</th><th>자료 구분</th></tr></thead><tbody>{daily_rows}</tbody></table></div></section>
<section class='section'><h2>v5 통과 규칙</h2><div class='rules'><div><b>A · 추세·거래대금형</b><p>당일 상승률 10~15%, 최근 60거래일 최대 거래대금 대비 0.10~1.00, 15:18 호가 간격 0.20% 이하.</p></div><div><b>B · 직접 재료형</b><p>직접성 14점 이상, 신선도 3점 이상, 거래대금 비율 0.10~1.50, 15:18 호가 간격 0.10% 이하.</p></div><div><b>공통</b><p>위험 종목과 보통주가 아닌 상품을 제외합니다. 구조 위험은 10% 이하로 제한합니다. 여러 종목이 통과하면 위험거리, 호가 간격, 거래대금 순서로 한 종목만 남깁니다.</p></div></div></section>
<section class='section'><h2>기존 점수 임계값 참고</h2><div class='table'><table><thead><tr><th>임계값</th><th>건수</th><th>평균 최대</th><th>+1%</th><th>+2%</th><th>+3%</th><th>+3% 정책 평균</th></tr></thead><tbody>{threshold_rows}</tbody></table></div></section>
<section class='section'><h2>검증 제한</h2><ul>{limitations}</ul></section>
</main></body></html>"""

def write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8-sig")
        return
    keys: list[str] = []
    for row in rows:
        for key in row:
            if key not in keys:
                keys.append(key)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in keys})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=("bootstrap", "14:53", "15:10", "15:18", "09:06"), required=True)
    parser.add_argument("--history", type=Path, required=True)
    parser.add_argument("--bootstrap", type=Path, default=None)
    parser.add_argument("--latest", type=Path, default=None)
    parser.add_argument("--output-history", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--csv", type=Path, required=True)
    parser.add_argument("--now", default=None)
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--delay", type=float, default=0.12)
    args = parser.parse_args()

    current = ensure_history(load_json(args.history, DEFAULT_HISTORY))
    bootstrap = load_json(args.bootstrap, {}) if args.bootstrap else None
    history = migrate_target3_history(merge_history(current, bootstrap))
    now = now_kst(args.now)
    latest = load_json(args.latest, {}) if args.latest else {}

    if args.stage in {"14:53", "15:10"}:
        candidates = [row for row in latest.get("candidates") or [] if isinstance(row, Mapping)]
        themes, errors = collect_themes(candidates, timeout=args.timeout, delay=args.delay)
        append_snapshot(
            history, stage=args.stage, at=now, latest=latest, themes=themes, errors=errors,
            keep_candidates=args.stage == "15:10",
        )
        record_event(
            history, stage=args.stage, at=now,
            status="SNAPSHOT_CAPTURED" if candidates else "NO_CANDIDATES",
            detail=f"후보 {len(candidates)}개, 테마 {len(themes)}개, 오류 {len(errors)}건",
        )
    elif args.stage == "15:18":
        signal_stage(history, now=now, latest=latest, timeout=args.timeout, delay=args.delay)
    elif args.stage == "09:06":
        evaluate_stage(history, now=now, timeout=args.timeout, delay=args.delay)
    else:
        record_event(history, stage="bootstrap", at=now, status="BOOTSTRAP", detail="과거 대리 검증 자료로 보고서를 초기화했습니다.")

    summary = build_summary(history, now)
    history["summary"] = summary
    history["generated_at"] = now.isoformat()
    atomic_json(args.output_history, json_safe(history))
    atomic_json(args.summary, json_safe(summary))
    result_rows = [row for row in history.get("results") or [] if isinstance(row, Mapping)]
    write_csv(args.csv, result_rows)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(render_report(history, summary), encoding="utf-8")
    print(json.dumps({
        "version": VERSION,
        "stage": args.stage,
        "generated_at": now.isoformat(),
        "signals": len(history.get("signals") or []),
        "results": len(result_rows),
        "evaluated_dates": summary.get("evaluated_dates"),
        "pending": summary.get("pending_signal_dates"),
        "correlation": summary.get("correlation"),
        "verdict": summary.get("verdict"),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
