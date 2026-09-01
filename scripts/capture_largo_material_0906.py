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
        scored_rows.append(json_safe({
            **scored,
            **entry,
            "production_score": production_score,
            "production_eligible": production_score is not None,
            "production_blockers": production_reasons,
            "signal_basis": basis_label,
            "candidate_source_at": basis_source_at.isoformat() if basis_source_at else None,
            "source_age_minutes": round(source_age, 2) if source_age is not None else None,
            "theme_capture_lag_minutes": round(capture_lag, 2),
            "research_rank": index,
        }))

    scored_rows.sort(
        key=lambda row: (
            row.get("production_score") is not None,
            num(row.get("production_score")) or -1,
            num(row.get("comparable_score")) or -1,
        ),
        reverse=True,
    )
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
        detail=f"후보 {len(scored_rows)}개, 실전 점수 산출 {sum(row.get('production_score') is not None for row in scored_rows)}개",
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
            "positive_rate": None, "hit_0_5_rate": None, "hit_1_rate": None, "hit_2_rate": None,
        }
    returns = [float(row["max_executable_return_pct"]) for row in items]
    scores = [float(value) for row in items if (value := num(row.get(score_key))) is not None]
    return {
        "n": len(items),
        "avg_score": round(statistics.fmean(scores), 4) if scores else None,
        "avg_max_return_pct": round(statistics.fmean(returns), 4),
        "median_max_return_pct": round(statistics.median(returns), 4),
        "positive_rate": round(sum(value > 0 for value in returns) / len(returns), 4),
        "hit_0_5_rate": round(sum(value >= 0.5 for value in returns) / len(returns), 4),
        "hit_1_rate": round(sum(value >= 1.0 for value in returns) / len(returns), 4),
        "hit_2_rate": round(sum(value >= 2.0 for value in returns) / len(returns), 4),
    }


def build_summary(history: Mapping[str, Any], now: dt.datetime) -> dict[str, Any]:
    rows = [row for row in history.get("results") or [] if isinstance(row, Mapping) and num(row.get("max_executable_return_pct")) is not None]
    score_rows = [row for row in rows if num(row.get("score")) is not None]
    dates = sorted({str(row.get("signal_date")) for row in rows}, reverse=True)
    top1: list[Mapping[str, Any]] = []
    top3: list[Mapping[str, Any]] = []
    daily: list[dict[str, Any]] = []
    for date_text in dates:
        day = [row for row in score_rows if str(row.get("signal_date")) == date_text]
        day.sort(key=lambda row: float(num(row.get("score")) or -1), reverse=True)
        if not day:
            continue
        top1.append(day[0])
        top3.extend(day[:3])
        daily.append({
            "signal_date": date_text,
            "n": len(day),
            "top_name": day[0].get("name"),
            "top_score": day[0].get("score"),
            "top_return_pct": day[0].get("max_executable_return_pct"),
            "top3": stats(day[:3]),
            "all": stats(day),
            "proxy": all(bool(row.get("proxy")) for row in day),
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
    if not rows:
        verdict = "완료된 평가가 없습니다. 오늘 15:18 신호부터 누적합니다."
    elif high70["n"] < 10:
        verdict = "70점 이상 표본이 10건 미만입니다. 높은 점수와 09:06 이전 수익의 관계를 확정하지 않습니다."
    elif correlation is not None and correlation >= 0.20 and (high70.get("hit_1_rate") or 0) > (stats(score_rows).get("hit_1_rate") or 0):
        verdict = "고득점 집단이 전체보다 나은 초기 성과를 보였습니다. 20거래일 전진검증 전에는 매수 기준으로 쓰지 않습니다."
    else:
        verdict = "높은 점수가 09:06 이전 수익을 안정적으로 설명하지 못했습니다. 점수는 연구 순위로만 사용합니다."
    pending_dates = [
        str(signal.get("signal_date")) for signal in history.get("signals") or []
        if isinstance(signal, Mapping)
        and not any(str(row.get("signal_date")) == str(signal.get("signal_date")) for row in rows)
    ]
    return {
        "version": VERSION,
        "generated_at": now.isoformat(),
        "definition": {
            "signal": "15:18 ask, with a pre-signal candidate snapshot",
            "outcome_window": "09:00 <= time < 09:06 on the next observed trading session",
            "outcome": "maximum observed top bid; minute snapshots, not intraminute high",
            "score": "deterministic 100-point material and closing-structure framework",
        },
        "evaluated_rows": len(rows),
        "evaluated_dates": dates,
        "live_completed_dates": completed_live_dates,
        "pending_signal_dates": sorted(set(pending_dates), reverse=True),
        "overall": stats(score_rows),
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
            "과거 보관분은 2026-08-27, 2026-08-28, 2026-08-31 세 신호일뿐입니다.",
            "8월 27일과 28일은 15:30 구조를, 8월 31일은 14:47 구조를 15:18 대리값으로 사용했습니다.",
            "09:06 이전 성과는 09:00~09:05 분별 최우선 매수호가의 최대값이며 수량과 체결 지연은 반영하지 않습니다.",
            "생성형 AI를 쓰지 않고 뉴스·공시 사건, 발표 시각, 테마 확산, 대장 순위, 2·3등주 거래대금과 확산 유지로 계산합니다.",
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


def render_report(history: Mapping[str, Any], summary: Mapping[str, Any]) -> str:
    latest_signal = max(
        (row for row in history.get("signals") or [] if isinstance(row, Mapping)),
        key=lambda row: str(row.get("signal_date") or ""),
        default=None,
    )
    signal_rows = ""
    if latest_signal:
        candidates = [row for row in latest_signal.get("candidates") or [] if isinstance(row, Mapping)]
        candidates.sort(key=lambda row: float(num(row.get("production_score")) or num(row.get("comparable_score")) or -1), reverse=True)
        for row in candidates[:24]:
            evidence = row.get("evidence") if isinstance(row.get("evidence"), Mapping) else {}
            theme = row.get("theme") if isinstance(row.get("theme"), Mapping) else {}
            signal_rows += (
                "<tr>"
                f"<td><b>{esc(row.get('name'))}</b><small>{esc(row.get('code'))}</small></td>"
                f"<td>{esc(row.get('grade'))}<small>{esc(row.get('grade_status'))}</small></td>"
                f"<td>{esc(row.get('production_score') if row.get('production_score') is not None else '-')}</td>"
                f"<td>{esc(row.get('comparable_score'))}</td>"
                f"<td>{fmt_rate(row.get('coverage'))}</td>"
                f"<td>{esc(theme.get('leader_rank'))}</td>"
                f"<td>{esc(theme.get('rising'))}/{esc(theme.get('total'))}<small>{fmt_rate(theme.get('breadth'))}</small></td>"
                f"<td>{esc(evidence.get('title') or '원문 미확인')}<small>{esc(evidence.get('at') or '')}</small></td>"
                f"<td>{esc(row.get('entry_ask') or '-')}</td>"
                f"<td>{'<br>'.join(esc(x) for x in row.get('production_blockers') or []) or '연구 점수 산출'}</td>"
                "</tr>"
            )
    if not signal_rows:
        signal_rows = "<tr><td colspan='10'>아직 15:18 실시간 신호가 없습니다.</td></tr>"

    daily_rows = "".join(
        "<tr>"
        f"<td>{esc(row.get('signal_date'))}</td><td>{row.get('n')}</td>"
        f"<td><b>{esc(row.get('top_name'))}</b><small>{esc(row.get('top_score'))}점</small></td>"
        f"<td>{fmt_pct(row.get('top_return_pct'))}</td>"
        f"<td>{fmt_pct((row.get('top3') or {}).get('avg_max_return_pct'))}</td>"
        f"<td>{fmt_rate((row.get('all') or {}).get('hit_1_rate'))}</td>"
        f"<td>{'과거 대리값' if row.get('proxy') else '실시간 전진검증'}</td>"
        "</tr>"
        for row in summary.get("daily") or []
    ) or "<tr><td colspan='7'>완료된 날짜가 없습니다.</td></tr>"

    threshold_rows = "".join(
        "<tr>"
        f"<td>{row.get('threshold')}점 이상</td><td>{row.get('n')}</td>"
        f"<td>{fmt_pct(row.get('avg_max_return_pct'))}</td>"
        f"<td>{fmt_rate(row.get('positive_rate'))}</td>"
        f"<td>{fmt_rate(row.get('hit_0_5_rate'))}</td>"
        f"<td>{fmt_rate(row.get('hit_1_rate'))}</td>"
        f"<td>{fmt_rate(row.get('hit_2_rate'))}</td>"
        "</tr>"
        for row in summary.get("thresholds") or []
    )
    overall = summary.get("overall") or {}
    high = summary.get("high_70") or {}
    latest_date = latest_signal.get("signal_date") if latest_signal else "-"
    pending = ", ".join(summary.get("pending_signal_dates") or []) or "없음"
    limitations = "".join(f"<li>{esc(item)}</li>" for item in summary.get("limitations") or [])
    return f"""<!doctype html><html lang='ko'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='referrer' content='no-referrer'><title>라르고 재료 등급·09:06 검증</title><style>
:root{{--bg:#edf2f7;--paper:#fff;--ink:#142137;--muted:#68788d;--line:#d8e1eb;--blue:#1e65c8;--green:#14764c;--amber:#9a6200;--red:#b93645}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font-family:Arial,'Malgun Gothic',sans-serif;line-height:1.55}}main{{max-width:1550px;margin:auto;padding:20px}}.hero{{background:linear-gradient(135deg,#0b2c56,#1763a8);color:#fff;border-radius:24px;padding:28px}}.hero h1{{margin:8px 0}}.hero p{{max-width:1080px;color:#dcecff}}.cards{{display:grid;grid-template-columns:repeat(5,1fr);gap:10px;margin:16px 0}}.card,.section{{background:#fff;border:1px solid var(--line);border-radius:18px;padding:16px;margin-bottom:16px}}.card b{{display:block;font-size:27px}}.card span,small{{color:var(--muted)}}.verdict{{border-left:6px solid var(--amber)}}.table{{overflow:auto}}table{{width:100%;border-collapse:collapse;font-size:12px}}th,td{{padding:9px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}td small{{display:block}}.rules{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}}.rules>div{{border:1px solid var(--line);border-radius:13px;padding:12px}}.status{{display:inline-block;background:#edf4ff;color:#174f96;padding:5px 8px;border-radius:999px}}@media(max-width:1000px){{.cards{{grid-template-columns:repeat(2,1fr)}}.rules{{grid-template-columns:1fr}}}}@media(max-width:560px){{main{{padding:10px}}.cards{{grid-template-columns:1fr}}.hero{{padding:20px}}}}
</style></head><body><main><section class='hero'><span>읽기 전용 연구 · 자동주문 없음 · 생성형 AI 미사용</span><h1>재료 등급과 다음 날 09:06 이전 수익 검증</h1><p>14:53·15:10·15:18의 테마 확산과 2·3등주 거래대금을 저장합니다. 15:18 매도호가를 진입 기준으로 두고 다음 거래일 09:00~09:05의 최우선 매수호가 최대값을 검증합니다.</p></section>
<section class='cards'><div class='card'><b>{summary.get('evaluated_rows')}</b><span>평가 후보-거래일</span></div><div class='card'><b>{len(summary.get('evaluated_dates') or [])}</b><span>평가 날짜</span></div><div class='card'><b>{summary.get('correlation') if summary.get('correlation') is not None else '-'}</b><span>점수-수익 순위상관</span></div><div class='card'><b>{fmt_rate(high.get('hit_1_rate'))}</b><span>70점 이상 +1% 도달</span></div><div class='card'><b>{fmt_rate(overall.get('hit_1_rate'))}</b><span>전체 +1% 도달</span></div></section>
<section class='section verdict'><h2>판정</h2><p>{esc(summary.get('verdict'))}</p><p>최근 신호일 {esc(latest_date)} · 결과 대기 {esc(pending)}</p></section>
<section class='section'><h2>최근 15:18 재료 점수</h2><p class='status'>S·A·B는 사건 직접성, 발표 시각, 테마 확산, 대장 순위, 2·3등주 거래대금과 확산 유지로 계산합니다.</p><div class='table'><table><thead><tr><th>종목</th><th>등급</th><th>실전점수</th><th>비교점수</th><th>자료충족</th><th>순위</th><th>테마 확산</th><th>확인 재료</th><th>15:18 매도호가</th><th>차단 사유</th></tr></thead><tbody>{signal_rows}</tbody></table></div></section>
<section class='section'><h2>오늘부터 역순 검증</h2><div class='table'><table><thead><tr><th>신호일</th><th>후보</th><th>최고점 종목</th><th>09:06 전 최대</th><th>상위 3 평균</th><th>전체 +1%</th><th>자료 구분</th></tr></thead><tbody>{daily_rows}</tbody></table></div></section>
<section class='section'><h2>점수 임계값별 성과</h2><div class='table'><table><thead><tr><th>임계값</th><th>건수</th><th>평균 최대수익</th><th>양수</th><th>+0.5%</th><th>+1%</th><th>+2%</th></tr></thead><tbody>{threshold_rows}</tbody></table></div></section>
<section class='section'><h2>계산식</h2><div class='rules'><div><b>재료 65점</b><p>직접성 18, 발표 신선도 10, 테마 확산 14, 대장 순위 8, 2·3등주 거래대금 5, 확산 유지 10.</p></div><div><b>종가 구조 35점</b><p>종가 위치 9, 윗꼬리 7, 몸통 6, 마감 과정 4, 패턴 4, 매물 소화 3, 구조 위험 2.</p></div><div><b>등급</b><p>S는 넓은 확산과 3위 안 주도력, 강한 후속 종목과 확산 유지가 필요합니다. A는 신선한 직접 사건이나 1·2위 중심의 확산입니다. 나머지는 B입니다.</p></div></div></section>
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
    history = merge_history(current, bootstrap)
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
