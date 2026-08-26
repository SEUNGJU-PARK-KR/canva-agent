from __future__ import annotations

import argparse
import html
import json
import statistics
from datetime import datetime
from pathlib import Path
from typing import Any

import build_largo_close_screener_v2 as base

ORIGINAL_DAILY_METRICS = base.daily_metrics
ORIGINAL_EVALUATE = base.evaluate
ORIGINAL_RENDER_SITE = base.render_site
ORIGINAL_TIME_SERIES = base.time_series

ENTRY_PATTERNS = {"CLASSIC_WICK_BREAKOUT", "STRUCTURAL_BREAKOUT_SPLIT"}
PATTERN_LABELS = {
    "CLASSIC_WICK_BREAKOUT": "고전형 윗꼬리 장대양봉",
    "STRUCTURAL_BREAKOUT_SPLIT": "추세·전고점 돌파 분할형",
    "ACCUMULATION_WICK_WATCH": "긴 윗꼬리 매집 관찰형",
    "LIMIT_UP_NEXT_DAY_REVIEW": "상한가 익일전략형",
    "AFTER_CLOSE_REVIEW": "장마감 후보선정형",
}


def extended_time_series(client: Any, code: str, kind: str, page_size: int) -> list[dict[str, Any]]:
    if kind == "siseDay":
        page_size = max(160, page_size)
    return ORIGINAL_TIME_SERIES(client, code, kind, page_size)


def extended_daily_metrics(rows: list[dict[str, Any]], current: dict[str, Any]) -> dict[str, Any]:
    result = ORIGINAL_DAILY_METRICS(rows, current)
    series = [row for row in rows if row.get("price") is not None]
    closes = [float(row["price"]) for row in series]

    def moving_average(period: int) -> float | None:
        return statistics.mean(closes[:period]) if len(closes) >= period else None

    ma_values = {
        "ma5": moving_average(5),
        "ma10": moving_average(10),
        "ma20": moving_average(20),
        "ma60": moving_average(60),
        "ma120": moving_average(120),
    }
    result.update(ma_values)
    available_mas = [value for value in ma_values.values() if value and value > 0]
    price = current.get("price")
    op = current.get("open")
    high = current.get("high")
    low = current.get("low")
    previous_close = closes[1] if len(closes) >= 2 else None
    ma_ceiling = max(available_mas) if available_mas else None
    ma_floor = min(available_mas) if available_mas else None
    ma_convergence = (
        (ma_ceiling - ma_floor) / price
        if price and ma_ceiling and ma_floor and price > 0
        else None
    )
    ma_breakout = bool(
        price
        and ma_ceiling
        and price >= ma_ceiling
        and (
            (op is not None and op <= ma_ceiling * 1.02)
            or (previous_close is not None and previous_close <= ma_ceiling * 1.02)
        )
    )
    candle_range = high - low if all(value is not None for value in (high, low)) else None
    body_ratio = (
        max(0.0, (price - op) / candle_range)
        if price is not None and op is not None and candle_range and candle_range > 0
        else None
    )
    result.update(
        {
            "ma_ceiling": ma_ceiling,
            "ma_floor": ma_floor,
            "ma_convergence": ma_convergence,
            "ma_breakout": ma_breakout,
            "body_ratio": body_ratio,
            "previous_close": previous_close,
        }
    )
    return result


def merge_market_data(candidate: dict[str, Any], detail: dict[str, Any]) -> dict[str, Any]:
    data = dict(candidate)
    for key, value in detail.items():
        if value not in (None, "", 0) or not data.get(key):
            data[key] = value
    return data


def classify_pattern(row: dict[str, Any], day: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    change_rate = row.get("change_rate") or 0
    close_location = row.get("close_location")
    upper_wick = row.get("upper_wick")
    body_ratio = day.get("body_ratio")
    volume_ratio = row.get("volume_ratio") or 0
    trade_value = row.get("trade_value") or 0
    catalyst = row.get("catalyst") or {}
    leader_check = row.get("checks", {}).get("leader_or_catalyst", {})
    leader_or_material = leader_check.get("status") == "PASS" or catalyst.get("strong")
    structure_pass = row.get("checks", {}).get("structure_hold", {}).get("status") != "FAIL"
    near_high = (day.get("high_proximity") or 0) >= 0.98
    turnover_pass = trade_value >= 50e9 or volume_ratio >= 1.8
    ma_breakout = bool(day.get("ma_breakout"))
    ma_converged = day.get("ma_convergence") is not None and day["ma_convergence"] <= 0.10
    slight_wick = upper_wick is not None and 0.03 <= upper_wick <= 0.35
    long_wick = upper_wick is not None and 0.25 <= upper_wick <= 0.65
    bullish_body = body_ratio is not None and body_ratio >= 0.45
    limit_up = change_rate >= 28.0 and close_location is not None and close_location >= 0.90

    classic = all(
        [
            turnover_pass,
            bullish_body,
            slight_wick,
            close_location is not None and close_location >= 0.60,
            ma_breakout,
            ma_converged,
            leader_or_material,
        ]
    )
    structural = all(
        [
            turnover_pass,
            body_ratio is not None and body_ratio >= 0.35,
            close_location is not None and close_location >= 0.65,
            near_high,
            structure_pass,
            leader_or_material,
        ]
    )
    accumulation = all(
        [
            trade_value >= 30e9 or volume_ratio >= 1.30,
            long_wick,
            close_location is not None and close_location >= 0.40,
            (ma_breakout or near_high or row.get("reference_candle") is not None),
            structure_pass,
            leader_or_material,
        ]
    )

    if limit_up:
        pattern = "LIMIT_UP_NEXT_DAY_REVIEW"
    elif classic:
        pattern = "CLASSIC_WICK_BREAKOUT"
    elif structural:
        pattern = "STRUCTURAL_BREAKOUT_SPLIT"
    elif accumulation:
        pattern = "ACCUMULATION_WICK_WATCH"
    else:
        pattern = "AFTER_CLOSE_REVIEW"

    return pattern, {
        "turnover_pass": turnover_pass,
        "body_ratio": body_ratio,
        "slight_wick": slight_wick,
        "long_wick": long_wick,
        "ma_breakout": ma_breakout,
        "ma_converged": ma_converged,
        "near_high": near_high,
        "leader_or_material": leader_or_material,
        "limit_up": limit_up,
    }


def check(status: str, value: Any, reason: str, role: str = "required") -> dict[str, Any]:
    return {"status": status, "value": value, "reason": reason, "role": role}


def evaluate_v3(
    candidate: dict[str, Any],
    detail: dict[str, Any],
    daily: list[dict[str, Any]],
    ticks: list[dict[str, Any]],
    news: list[dict[str, str]],
    hoga: dict[str, Any],
    excluded: set[str],
    history: dict[str, Any],
    now: datetime,
) -> dict[str, Any]:
    row = ORIGINAL_EVALUATE(candidate, detail, daily, ticks, news, hoga, excluded, history, now)
    data = merge_market_data(candidate, detail)
    day = extended_daily_metrics(daily, data)
    pattern, pattern_data = classify_pattern(row, day)
    checks = row["checks"]

    checks.pop("no_distribution_wick", None)
    upper_wick = row.get("upper_wick")
    close_location = row.get("close_location")
    body_ratio = day.get("body_ratio")

    if pattern == "CLASSIC_WICK_BREAKOUT":
        wick_status = "PASS"
        wick_reason = "살짝 윗꼬리가 달린 장대양봉: 당일 차익 물량이 일부 나온 고전형"
        high_threshold = 0.60
    elif pattern == "STRUCTURAL_BREAKOUT_SPLIT":
        wick_status = "PASS" if upper_wick is not None and upper_wick <= 0.45 else "WARN"
        wick_reason = "윗꼬리 길이보다 전고점·추세저항 돌파와 지지선 분할 계획을 우선"
        high_threshold = 0.65
    elif pattern == "ACCUMULATION_WICK_WATCH":
        wick_status = "PASS"
        wick_reason = "긴 윗꼬리·유성형을 매집 관찰형으로 분리; 낮은 비중과 다단계 분할만 허용"
        high_threshold = 0.40
    elif pattern == "LIMIT_UP_NEXT_DAY_REVIEW":
        wick_status = "PASS"
        wick_reason = "상한가 종목은 윗꼬리보다 분봉 잠김·재잠김 패턴을 수동 확인"
        high_threshold = 0.90
    else:
        if upper_wick is None:
            wick_status = "WARN"
            wick_reason = "윗꼬리 역할 미확인"
        elif upper_wick > 0.65:
            wick_status = "FAIL"
            wick_reason = "극단적 상승폭 반납이며 매집 패턴 근거도 부족"
        elif upper_wick < 0.03:
            wick_status = "WARN"
            wick_reason = "윗꼬리가 거의 없어 다음 날 차익 매물 출회 가능성을 추가 확인"
        else:
            wick_status = "WARN"
            wick_reason = "캔들 모양만으로 종가 진입 패턴을 확정할 수 없음"
        high_threshold = 0.65

    checks["wick_role"] = check(
        wick_status,
        {"upper_wick": upper_wick, "body_ratio": body_ratio, "pattern": pattern},
        wick_reason,
        "required" if pattern in ENTRY_PATTERNS else "supporting",
    )
    high_status = (
        "PASS"
        if close_location is not None and close_location >= high_threshold
        else "WARN"
        if close_location is not None and close_location >= max(0.35, high_threshold - 0.10)
        else "FAIL"
    )
    checks["high_close"] = check(
        high_status,
        {"close_location": close_location, "pattern_threshold": high_threshold},
        "패턴별 종가 위치 기준. 고전형은 살짝 종가 반납을 허용",
        "required" if pattern in ENTRY_PATTERNS else "supporting",
    )
    ma_status = (
        "PASS"
        if day.get("ma_breakout") and (day.get("ma_convergence") or 1) <= 0.10
        else "WARN"
        if day.get("ma_breakout") or pattern != "CLASSIC_WICK_BREAKOUT"
        else "FAIL"
    )
    checks["ma_cluster_breakout"] = check(
        ma_status,
        {
            "ma5": day.get("ma5"),
            "ma10": day.get("ma10"),
            "ma20": day.get("ma20"),
            "ma60": day.get("ma60"),
            "ma120": day.get("ma120"),
            "convergence": day.get("ma_convergence"),
            "breakout": day.get("ma_breakout"),
        },
        "고전형 핵심: 이평선 수렴·장기 저항 구간을 거래대금과 함께 돌파",
        "required" if pattern == "CLASSIC_WICK_BREAKOUT" else "supporting",
    )

    catalyst = row.get("catalyst") or {}
    leader_status = checks.get("leader_or_catalyst", {}).get("status")
    if catalyst.get("strong") and leader_status == "PASS":
        material_status = "PASS"
        material_reason = "직접 재료와 섹터·대장성이 함께 확인됨"
    elif catalyst.get("strong"):
        material_status = "PASS"
        material_reason = "강한 직접 재료 확인; 관련주 확산과 대장성 추가 점검"
    elif leader_status == "PASS" and catalyst.get("titles"):
        material_status = "WARN"
        material_reason = "섹터 주도성은 있으나 재료 연속성을 사람이 확인해야 함"
    else:
        material_status = "FAIL"
        material_reason = "다음 날 수급 명분이 되는 재료의 연속성을 확인하지 못함"
    checks["material_continuity"] = check(
        material_status,
        {"positive": catalyst.get("positive"), "titles": catalyst.get("titles")},
        material_reason,
    )

    pattern_status = "PASS" if pattern in ENTRY_PATTERNS or pattern == "ACCUMULATION_WICK_WATCH" else "WARN"
    checks["pattern_fit"] = check(
        pattern_status,
        {"pattern": pattern, **pattern_data},
        {
            "CLASSIC_WICK_BREAKOUT": "고전형 4조건 충족",
            "STRUCTURAL_BREAKOUT_SPLIT": "과거 매집·전고점 돌파·지지선 분할 구조",
            "ACCUMULATION_WICK_WATCH": "매집 미완성 가능성: 종가 소액 후 여러 지지선 분할 관찰",
            "LIMIT_UP_NEXT_DAY_REVIEW": "상한가 분봉 1·2·3 패턴과 앞 매물대를 익일 수동 확인",
            "AFTER_CLOSE_REVIEW": "장마감 후보선정 조건은 있으나 당일 종가 진입 패턴은 미완성",
        }[pattern],
        "required" if pattern in ENTRY_PATTERNS else "supporting",
    )

    if pattern not in ENTRY_PATTERNS:
        for key_name in ("absorption", "reclaim", "high_close"):
            if key_name in checks:
                checks[key_name]["role"] = "supporting"

    hard_required = [value for value in checks.values() if value.get("role") == "required"]
    fail_count = sum(value.get("status") == "FAIL" for value in hard_required)
    warn_count = sum(value.get("status") == "WARN" for value in hard_required)
    entry_ready_now = pattern in ENTRY_PATTERNS and fail_count == 0 and warn_count == 0

    rank_history = [
        snap
        for snap in history.get("snapshots", [])
        if snap.get("date") == now.date().isoformat() and row["code"] in snap.get("stocks", {})
    ]
    snapshot_labels = {
        snap.get("label"): snap.get("stocks", {}).get(row["code"], {}).get("late_pass")
        for snap in rank_history
    }
    clock = now.hour * 60 + now.minute
    current_label = (
        "15:10"
        if 15 * 60 + 7 <= clock < 15 * 60 + 15
        else "15:18"
        if 15 * 60 + 15 <= clock < 15 * 60 + 23
        else "other"
    )
    if current_label in {"15:10", "15:18"}:
        snapshot_labels[current_label] = entry_ready_now
    consecutive = snapshot_labels.get("15:10") is True and snapshot_labels.get("15:18") is True

    critical_fail_keys = {"management", "trade_value", "adverse_material", "chart_qualification", "stop_plan"}
    critical_fail = any(checks.get(key, {}).get("status") == "FAIL" for key in critical_fail_keys)
    if critical_fail or (pattern in ENTRY_PATTERNS and fail_count):
        state = "EXCLUDE"
    elif pattern in ENTRY_PATTERNS and consecutive and warn_count == 0:
        state = "READY"
    else:
        state = "WATCH"

    pattern_base_score = {
        "CLASSIC_WICK_BREAKOUT": 28,
        "STRUCTURAL_BREAKOUT_SPLIT": 25,
        "ACCUMULATION_WICK_WATCH": 18,
        "LIMIT_UP_NEXT_DAY_REVIEW": 16,
        "AFTER_CLOSE_REVIEW": 8,
    }[pattern]
    pass_count = sum(value.get("status") == "PASS" for value in checks.values() if value.get("role") == "required")
    total_required = max(1, sum(value.get("role") == "required" for value in checks.values()))
    row["score_parts"]["pattern"] = pattern_base_score
    row["score_parts"]["required_gate_quality"] = 22 * pass_count / total_required
    row["score"] = round(min(100.0, sum(row["score_parts"].values())), 1)
    row.update(
        {
            "pattern": pattern,
            "pattern_label": PATTERN_LABELS[pattern],
            "pattern_data": pattern_data,
            "ma10": day.get("ma10"),
            "ma60": day.get("ma60"),
            "ma120": day.get("ma120"),
            "ma_convergence": day.get("ma_convergence"),
            "ma_breakout": day.get("ma_breakout"),
            "body_ratio": body_ratio,
            "checks": checks,
            "fail_count": fail_count,
            "warn_count": warn_count,
            "late_pass": entry_ready_now,
            "consecutive_late_pass": consecutive,
            "state": state,
        }
    )
    row["entry_plan"] = {
        "CLASSIC_WICK_BREAKOUT": "종가 1차. 익일 갭이 과도하지 않고 기준봉 종가선을 지지하면 첫 1파 청산.",
        "STRUCTURAL_BREAKOUT_SPLIT": "종가 1차, 돌파선·단기추세 지지 2차. 반등 또는 첫 1파에서 분할 청산.",
        "ACCUMULATION_WICK_WATCH": "낮은 초기 비중만 허용. 5·10·20일선 또는 박스 지지까지 여러 번 나눠 접근.",
        "LIMIT_UP_NEXT_DAY_REVIEW": "종가 자동진입 아님. 상한가 잠김 패턴과 앞 매물대를 확인한 뒤 익일 시초가 전략으로 전환.",
        "AFTER_CLOSE_REVIEW": "관심종목에만 저장. 다음 날 호가·차트에서 대장과 물량소화를 다시 확인.",
    }[pattern]
    return row


def fmt_money(value: float | None) -> str:
    return "-" if value is None else f"{value / 1e8:,.0f}억"


def fmt_percent(value: float | None, scale: float = 1.0) -> str:
    return "-" if value is None else f"{value * scale:,.1f}%"


def render_site_v3(results: list[dict[str, Any]], meta: dict[str, Any], strict: dict[str, Any]) -> str:
    embedded = json.dumps({"results": results, "meta": meta, "strict": strict}, ensure_ascii=False).replace("</", "<\\/")
    rows = []
    for row in results:
        rows.append(
            "<tr data-state='{state}' data-pattern='{pattern}' data-search='{search}' onclick=\"openDetail('{code}')\">"
            "<td><b>{name}</b><small>{code} · {industry}</small></td>"
            "<td><span class='pattern'>{pattern_label}</span></td>"
            "<td>{rank}</td><td>{trade_value}</td><td>{change}</td>"
            "<td>{body}</td><td>{wick}</td><td>{close_location}</td>"
            "<td>{volume_ratio}</td><td>{stop}</td><td><b>{score}</b></td>"
            "<td><span class='state {state_lower}'>{state}</span></td></tr>".format(
                state=html.escape(row["state"]),
                pattern=html.escape(row["pattern"]),
                search=html.escape((row["name"] + " " + row["code"] + " " + str(row.get("industry") or "")).casefold()),
                code=html.escape(row["code"]),
                name=html.escape(row["name"]),
                industry=html.escape(str(row.get("industry") or "미분류")),
                pattern_label=html.escape(row.get("pattern_label") or "-"),
                rank=row.get("trade_value_rank") or "-",
                trade_value=fmt_money(row.get("trade_value")),
                change=fmt_percent(row.get("change_rate"), 1.0),
                body=fmt_percent(row.get("body_ratio"), 100.0),
                wick=fmt_percent(row.get("upper_wick"), 100.0),
                close_location=fmt_percent(row.get("close_location"), 100.0),
                volume_ratio="-" if row.get("volume_ratio") is None else f"{row['volume_ratio']:.2f}x",
                stop=fmt_percent(row.get("stop_distance"), 100.0),
                score=row["score"],
                state_lower=row["state"].lower(),
            )
        )
    pattern_options = "".join(
        f"<option value='{key}'>{html.escape(label)}</option>" for key, label in PATTERN_LABELS.items()
    )
    return f"""<!doctype html><html lang='ko'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='color-scheme' content='light'><title>라르고 종가매매 패턴 스크리너 v3</title><style>
:root{{--bg:#edf2f7;--paper:#fff;--line:#d9e2ec;--ink:#172337;--muted:#68788c;--blue:#1766d6;--green:#10804e;--amber:#a86c00;--red:#b53341;--shadow:0 14px 36px rgba(34,52,78,.09)}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);font-family:Arial,'Malgun Gothic',sans-serif;color:var(--ink)}}main{{max-width:1700px;margin:20px auto;padding:0 18px 44px}}header{{background:linear-gradient(135deg,#fff,#f1f6ff);border:1px solid var(--line);border-radius:20px;padding:24px;box-shadow:var(--shadow)}}h1{{margin:0 0 8px;font-size:30px}}header p{{margin:0;color:var(--muted);line-height:1.7}}.stats{{display:grid;grid-template-columns:repeat(6,1fr);gap:10px;margin-top:18px}}.stat,.box{{background:#fff;border:1px solid var(--line);border-radius:14px;padding:13px}}.stat b{{display:block;font-size:22px;color:var(--blue)}}.stat span{{font-size:11px;color:var(--muted)}}.correction{{margin-top:14px;background:#fff8e8;border:1px solid #efd49a;border-radius:14px;padding:14px;line-height:1.65;color:#745000}}.patterns{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:16px 0}}.box h3{{margin:0 0 7px;font-size:15px}}.box p{{margin:0;color:var(--muted);font-size:12px;line-height:1.6}}.toolbar{{display:flex;gap:9px;flex-wrap:wrap;margin:14px 0}}input,select{{border:1px solid var(--line);border-radius:10px;padding:9px 11px;background:#fff}}input{{min-width:280px;flex:1}}.tablewrap{{background:#fff;border:1px solid var(--line);border-radius:16px;overflow:auto;box-shadow:var(--shadow)}}table{{border-collapse:collapse;width:100%;min-width:1320px;font-size:12px}}th,td{{padding:10px 9px;border-bottom:1px solid #edf1f5;text-align:right;white-space:nowrap}}th{{position:sticky;top:0;background:#f3f7fb;color:#46566b;z-index:1}}th:first-child,td:first-child,th:nth-child(2),td:nth-child(2){{text-align:left}}tbody tr{{cursor:pointer}}tbody tr:hover{{background:#f7faff}}td small{{display:block;color:var(--muted);margin-top:4px}}.pattern{{display:inline-block;max-width:190px;overflow:hidden;text-overflow:ellipsis}}.state{{display:inline-flex;padding:5px 9px;border-radius:999px;font-weight:800}}.state.ready{{background:#e8f7ef;color:var(--green)}}.state.watch{{background:#fff5de;color:var(--amber)}}.state.exclude{{background:#ffedf0;color:var(--red)}}dialog{{width:min(1100px,94vw);border:0;border-radius:18px;padding:0;box-shadow:0 30px 90px rgba(0,0,0,.28)}}dialog::backdrop{{background:rgba(14,24,38,.55)}}.modalhead{{padding:18px 20px;border-bottom:1px solid var(--line);display:flex;justify-content:space-between;align-items:center}}.modalbody{{padding:18px 20px;max-height:76vh;overflow:auto}}button{{border:1px solid var(--line);border-radius:10px;background:#fff;padding:8px 11px;cursor:pointer}}.checks{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}}.check{{border:1px solid var(--line);border-radius:12px;padding:11px}}.check.pass{{border-color:#bde6ce;background:#f2fbf6}}.check.warn{{border-color:#ecd49f;background:#fffaf0}}.check.fail{{border-color:#efc1c7;background:#fff4f5}}.check b,.check span{{display:block}}.check span{{margin-top:5px;color:var(--muted);font-size:11px;line-height:1.5}}.detailgrid{{display:grid;grid-template-columns:repeat(4,1fr);gap:9px;margin-bottom:14px}}.detailgrid div{{border:1px solid var(--line);border-radius:12px;padding:10px}}.detailgrid b,.detailgrid span{{display:block}}.detailgrid span{{font-size:11px;color:var(--muted);margin-top:3px}}.method{{margin-top:18px;background:#fff;border:1px solid var(--line);border-radius:16px;padding:18px}}.method li{{margin:8px 0;line-height:1.55;color:#45566b}}@media(max-width:1050px){{.stats{{grid-template-columns:repeat(2,1fr)}}.patterns{{grid-template-columns:1fr 1fr}}.checks{{grid-template-columns:1fr 1fr}}.detailgrid{{grid-template-columns:1fr 1fr}}}}@media(max-width:650px){{h1{{font-size:23px}}.patterns,.checks{{grid-template-columns:1fr}}input{{min-width:100%}}}}
</style></head><body><main><header><h1>라르고 종가매매 패턴 스크리너 v3</h1><p>OpenBot 원본 분석 10편을 종가 진입 교육 5편과 장마감 후보선정 교육 5편으로 분리했습니다. 필수 게이트와 패턴 적합성을 먼저 보고 점수는 통과 종목의 우선순위에만 씁니다.</p><div class='stats'><div class='stat'><b>10편</b><span>원본 전체 분석</span></div><div class='stat'><b>5+5</b><span>종가진입 / 후보선정</span></div><div class='stat'><b>25,192</b><span>분석 프레임</span></div><div class='stat'><b>{meta.get('candidate_count',0)}</b><span>현재 후보</span></div><div class='stat'><b>{meta.get('mode','-')}</b><span>데이터 모드</span></div><div class='stat'><b>{meta.get('generated_at','-')}</b><span>생성 시각</span></div></div><div class='correction'><b>중요 정정</b><br>윗꼬리는 무조건 악재가 아닙니다. 고전형 종가매매에서는 살짝 윗꼬리가 달린 장대양봉이 핵심 조건입니다. 긴 윗꼬리·유성형은 매집 구조가 맞을 때 별도의 낮은 비중 분할 관찰형으로 분리합니다.</div></header><section class='patterns'><div class='box'><h3>고전형 윗꼬리 장대양봉</h3><p>거래대금 · 살짝 윗꼬리 · 이평선 수렴 돌파 · 재료 연속성. 익일 첫 파동을 노립니다.</p></div><div class='box'><h3>추세·전고점 돌파 분할형</h3><p>과거 매집과 물량소화 뒤 저항을 돌파합니다. 종가 1차 후 돌파선·추세선에 나눠 접근합니다.</p></div><div class='box'><h3>긴 윗꼬리 매집 관찰형</h3><p>긴 윗꼬리를 무조건 버리지 않습니다. 매집 미완성 가능성 때문에 초기 비중을 낮추고 여러 지지선을 씁니다.</p></div><div class='box'><h3>상한가 익일전략형</h3><p>당일 종가 자동매수가 아닙니다. 분봉 잠김 패턴과 앞 매물대를 확인해 익일 시초가·스윙 전략으로 넘깁니다.</p></div></section><div class='toolbar'><input id='search' placeholder='종목명·코드·테마 검색'><select id='stateFilter'><option value='ALL'>모든 상태</option><option>READY</option><option>WATCH</option><option>EXCLUDE</option></select><select id='patternFilter'><option value='ALL'>모든 패턴</option>{pattern_options}</select></div><div class='tablewrap'><table><thead><tr><th>종목</th><th>패턴</th><th>거래대금순위</th><th>거래대금</th><th>등락률</th><th>몸통</th><th>윗꼬리</th><th>종가위치</th><th>거래량배수</th><th>손절거리</th><th>점수</th><th>상태</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div><section class='method'><h2>시간순 판정</h2><ol><li>등락률·거래대금 상위에서 특징주를 넓게 모읍니다.</li><li>뉴스로 재료를 확인하고 같은 섹터끼리 묶은 뒤 S/A/B 등급과 대장주를 정합니다.</li><li>상한가 종목은 분봉의 잠김·재잠김·반복해제 패턴을 먼저 분류합니다.</li><li>일봉·주봉·월봉에서 기준봉, 전고점, 이평선 수렴과 매물대를 확인합니다.</li><li>종가 진입형을 고전형, 구조돌파형, 매집관찰형으로 분류합니다.</li><li>종가 1차와 추가 분할 지지선, 익일 청산·손절 계획을 미리 만듭니다.</li><li>마지막으로 실제 HTS에서 호가·체결과 대장주 유지를 확인합니다.</li></ol></section></main><dialog id='detail'><div class='modalhead'><div><b id='detailTitle'></b><div id='detailSub' style='color:var(--muted);font-size:12px;margin-top:4px'></div></div><button onclick='detail.close()'>닫기</button></div><div class='modalbody'><div class='detailgrid' id='detailMetrics'></div><p id='entryPlan' class='correction'></p><div class='checks' id='checkGrid'></div><p><a id='naverLink' target='_blank' rel='noopener'>네이버증권에서 확인</a></p></div></dialog><script>const DATA={embedded};const rows=[...document.querySelectorAll('tbody tr')];const search=document.getElementById('search');const stateFilter=document.getElementById('stateFilter');const patternFilter=document.getElementById('patternFilter');function apply(){{const q=search.value.trim().toLowerCase();rows.forEach(r=>{{r.hidden=!((!q||r.dataset.search.includes(q))&&(stateFilter.value==='ALL'||r.dataset.state===stateFilter.value)&&(patternFilter.value==='ALL'||r.dataset.pattern===patternFilter.value))}})}}search.oninput=apply;stateFilter.onchange=apply;patternFilter.onchange=apply;const detail=document.getElementById('detail');function valueText(v){{if(v===null||v===undefined)return '-';if(typeof v==='object')return JSON.stringify(v);return String(v)}}function openDetail(code){{const r=DATA.results.find(x=>x.code===code);if(!r)return;document.getElementById('detailTitle').textContent=r.name+' · '+r.pattern_label;document.getElementById('detailSub').textContent=r.code+' · '+r.industry+' · '+r.state;document.getElementById('detailMetrics').innerHTML=[['현재가',r.price?.toLocaleString()],['거래대금',(r.trade_value/1e8).toLocaleString()+'억'],['종가위치',((r.close_location||0)*100).toFixed(1)+'%'],['윗꼬리',((r.upper_wick||0)*100).toFixed(1)+'%'],['몸통',((r.body_ratio||0)*100).toFixed(1)+'%'],['거래량배수',r.volume_ratio?.toFixed(2)+'x'],['구조손절',r.support?.toLocaleString()],['점수',r.score]].map(x=>'<div><b>'+valueText(x[1])+'</b><span>'+x[0]+'</span></div>').join('');document.getElementById('entryPlan').textContent=r.entry_plan;document.getElementById('checkGrid').innerHTML=Object.entries(r.checks).map(([k,v])=>'<div class="check '+v.status.toLowerCase()+'"><b>'+v.status+' · '+k+'</b><span>'+v.reason+'</span><span>'+valueText(v.value)+'</span></div>').join('');document.getElementById('naverLink').href=r.naver_url;detail.showModal()}}</script></body></html>"""


base.time_series = extended_time_series
base.daily_metrics = extended_daily_metrics
base.evaluate = evaluate_v3
base.render_site = render_site_v3


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="site-output")
    parser.add_argument("--history", default="largo-close-screener/data/history.json")
    parser.add_argument("--candidate-limit", type=int, default=36)
    args = parser.parse_args()
    meta = base.build(Path(args.output), Path(args.history), max(5, min(args.candidate_limit, 60)))
    print(json.dumps(meta, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
