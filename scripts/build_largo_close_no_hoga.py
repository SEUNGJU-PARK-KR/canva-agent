#!/usr/bin/env python3
"""Generate a phase-aware, self-contained Largo close-betting page without orderbook gates.

The strategy gates are not weakened. Time-dependent close-structure checks are marked
pending until their observation window opens, allowing traders to prepare candidates
before the closing auction while keeping final readiness strict.
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

KST = dt.timezone(dt.timedelta(hours=9))
HOGA_IDS = {"BEST_BID_HOLD_AUTO", "H_ABSORPTION", "H_LIQUIDITY"}
LATE_GATE_TIMES: dict[str, dt.time] = {
    "C_LOCATION": dt.time(14, 35),
    "C_WICK": dt.time(14, 35),
    "C_SEQUENCE": dt.time(15, 5),
}
MAX_RISK = 0.06
STATUS_RANK = {
    "AUCTION_READY": 6,
    "ENTRY_READY": 5,
    "PREPARE": 4,
    "EARLY_WATCH": 3,
    "CONDITIONAL": 2,
    "STRICT": 6,
    "EXCLUDE": 0,
}
STATUS_LABELS = {
    "AUCTION_READY": "종가 단일가 검토",
    "ENTRY_READY": "진입 준비",
    "PREPARE": "우선 준비",
    "EARLY_WATCH": "조기 감시",
    "CONDITIONAL": "조건부 관찰",
    "STRICT": "종가 확정 통과",
    "EXCLUDE": "제외",
}
REFRESH_TIMES = [
    dt.time(13, 38), dt.time(13, 53), dt.time(14, 8), dt.time(14, 23),
    dt.time(14, 33), dt.time(14, 40), dt.time(14, 53), dt.time(15, 0),
    dt.time(15, 4), dt.time(15, 10), dt.time(15, 18), dt.time(15, 23),
    dt.time(15, 28), dt.time(15, 35),
]


def num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def won(value: Any) -> str:
    value_num = num(value)
    return "-" if value_num is None else f"{value_num:,.0f}원"


def pct(value: Any, digits: int = 2) -> str:
    value_num = num(value)
    return "-" if value_num is None else f"{value_num * 100:.{digits}f}%"


def big_won(value: Any) -> str:
    value_num = num(value)
    if value_num is None:
        return "-"
    if abs(value_num) >= 1_0000_0000_0000:
        return f"{value_num / 1_0000_0000_0000:.2f}조원"
    if abs(value_num) >= 1_0000_0000:
        return f"{value_num / 1_0000_0000:.0f}억원"
    return won(value_num)


def parse_datetime(value: Any) -> dt.datetime | None:
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=KST)
    return parsed.astimezone(KST)


def source_timestamp(latest: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]) -> dt.datetime | None:
    values: list[dt.datetime] = []
    for key in ("generated_at", "updated_at"):
        parsed = parse_datetime(latest.get(key))
        if parsed:
            values.append(parsed)
    for row in rows:
        parsed = parse_datetime(row.get("updated_at"))
        if parsed:
            values.append(parsed)
    return max(values) if values else None


def phase(source_at: dt.datetime | None, built_at: dt.datetime) -> dict[str, Any]:
    if source_at is None:
        return {
            "id": "UNKNOWN",
            "label": "기준 시각 확인 불가",
            "title": "기준 시각 확인 필요",
            "warning": "데이터 기준 시각을 확인하지 못했습니다. 신규 진입 판단에 사용하지 마세요.",
            "action": "원본 데이터 생성 시각을 먼저 확인합니다.",
        }
    shown = source_at.strftime("%Y-%m-%d %H:%M")
    if source_at.date() < built_at.date():
        return {
            "id": "HISTORICAL_CLOSE",
            "label": f"{source_at:%Y-%m-%d} 과거 스냅샷",
            "title": "과거 종가 기록",
            "warning": f"{shown} 기준 과거 스냅샷입니다. 오늘 후보가 아닙니다.",
            "action": "최신 자동 갱신 뒤 다시 확인합니다.",
        }
    current = source_at.time()
    if current < dt.time(14, 0):
        phase_id, title, action = "EARLY_SCAN", "1차 조기 탐색", "재료·대장·유동성·차트 자격을 먼저 고정합니다."
    elif current < dt.time(14, 35):
        phase_id, title, action = "EARLY_WATCH", "조기 감시 후보", "상위 후보 차트와 구조 손절선을 미리 준비합니다."
    elif current < dt.time(15, 5):
        phase_id, title, action = "PRE_CLOSE", "마감 전 우선 준비", "고가권 유지와 윗꼬리 변화를 보며 후보를 줄입니다."
    elif current < dt.time(15, 12):
        phase_id, title, action = "ENTRY_PREP", "종가 진입 준비", "진입가·구조 손절을 기록하고 15시 10분 이후 유지 여부를 확인합니다."
    elif current < dt.time(15, 20):
        phase_id, title, action = "ENTRY_CONFIRM", "마감 유지 확인", "연속 스냅샷에서 후보가 유지되는지 확인합니다."
    elif current < dt.time(15, 30):
        phase_id, title, action = "CLOSING_AUCTION", "종가 단일가 검토", "준비된 후보만 15시 20분~30분 종가 단일가에서 검토합니다."
    else:
        phase_id, title, action = "FINAL_CLOSE", "종가 확정 검증", "종가 확정 뒤 전략 적합 여부를 기록합니다."
    return {
        "id": phase_id,
        "label": f"{shown} · {title}",
        "title": title,
        "warning": f"{shown} 기준 판정입니다. 단계 이름을 확인하고 확정 추천과 혼동하지 마세요.",
        "action": action,
    }


def next_refresh(source_at: dt.datetime | None) -> str:
    if source_at is None:
        return "다음 자동 실행 시각 확인 필요"
    for refresh_time in REFRESH_TIMES:
        candidate = dt.datetime.combine(source_at.date(), refresh_time, tzinfo=KST)
        if candidate > source_at:
            return candidate.strftime("%H:%M 예정")
    return "오늘 자동 갱신 종료"


def history_stats(history: Mapping[str, Any], code: str, pattern_id: str, market_date: dt.date | None) -> dict[str, Any]:
    snapshots = history.get("snapshots") if isinstance(history, Mapping) else []
    observations: list[tuple[dt.datetime, Mapping[str, Any] | None]] = []
    for snapshot in snapshots or []:
        if not isinstance(snapshot, Mapping):
            continue
        observed_at = parse_datetime(snapshot.get("at"))
        if observed_at is None or (market_date and observed_at.date() != market_date):
            continue
        items = snapshot.get("items")
        item = items.get(code) if isinstance(items, Mapping) else None
        observations.append((observed_at, item if isinstance(item, Mapping) else None))
    observations.sort(key=lambda item: item[0])
    appearances = [(observed_at, item) for observed_at, item in observations if item is not None]
    consecutive = 0
    for _, item in reversed(observations):
        if item is None:
            break
        consecutive += 1
    pattern_matches = sum(1 for _, item in appearances if str(item.get("pattern") or "") == pattern_id)
    return {
        "snapshot_count": len(observations),
        "appearance_count": len(appearances),
        "consecutive_count": consecutive,
        "pattern_match_count": pattern_matches,
        "first_seen": appearances[0][0].strftime("%H:%M") if appearances else None,
        "last_seen": appearances[-1][0].strftime("%H:%M") if appearances else None,
    }


def risk_plan(row: Mapping[str, Any]) -> dict[str, Any]:
    automation = row.get("automation_356")
    if isinstance(automation, Mapping):
        risk = automation.get("risk_plan")
        if isinstance(risk, Mapping) and risk:
            rate = num(risk.get("risk_rate"))
            valid = str(risk.get("status") or "").upper() == "PASS" and rate is not None and rate <= MAX_RISK
            return {
                "status": "PASS" if valid else "FAIL",
                "entry": num(risk.get("entry_price")),
                "stop": num(risk.get("stop_price")),
                "stop_source": str(risk.get("stop_source") or "구조선"),
                "rate": rate,
                "one_r": num(risk.get("one_r_price")),
                "reason": str(risk.get("reason") or ""),
            }
    entry = num(row.get("price"))
    plan = row.get("plan") if isinstance(row.get("plan"), Mapping) else {}
    supports: list[tuple[float, str]] = []
    for item in plan.get("supports") or []:
        if not isinstance(item, Mapping):
            continue
        support_price = num(item.get("price"))
        if entry and support_price and 0 < support_price < entry:
            supports.append((support_price, str(item.get("name") or "구조선")))
    invalidation = num(plan.get("invalidation"))
    if entry and invalidation and 0 < invalidation < entry:
        supports.append((invalidation, "무효화선"))
    if not entry or not supports:
        return {
            "status": "FAIL", "entry": entry, "stop": None, "stop_source": "구조선",
            "rate": None, "one_r": None, "reason": "유효한 진입가 아래 구조선을 확인하지 못했습니다.",
        }
    stop, name = max(supports)
    rate = (entry - stop) / entry
    return {
        "status": "PASS" if rate <= MAX_RISK else "FAIL",
        "entry": entry,
        "stop": stop,
        "stop_source": name,
        "rate": rate,
        "one_r": entry + (entry - stop),
        "reason": f"가장 가까운 구조선까지 위험거리는 {rate:.2%}입니다.",
    }


def is_gate_active(check_id: str, source_at: dt.datetime | None) -> bool:
    gate_time = LATE_GATE_TIMES.get(check_id)
    if gate_time is None or source_at is None:
        return True
    return source_at.time() >= gate_time


def readiness_score(
    quality: float,
    pattern_score: float,
    checks: Sequence[Mapping[str, Any]],
    risk: Mapping[str, Any],
    persistence: Mapping[str, Any],
) -> float:
    active = [check for check in checks if check.get("active")]
    pass_ratio = sum(1 for check in active if check.get("status") == "PASS") / max(len(active), 1)
    warning_count = sum(1 for check in active if check.get("status") in {"WARN", "MISSING", "UNKNOWN"})
    risk_rate = num(risk.get("rate"))
    risk_score = 0.0 if risk_rate is None else max(0.0, min(1.0, (MAX_RISK - risk_rate) / MAX_RISK))
    consecutive = min(int(persistence.get("consecutive_count") or 0), 3) / 3
    pattern_consistency = min(int(persistence.get("pattern_match_count") or 0), 3) / 3
    score = (
        min(max(quality, 0), 100) * 0.20
        + min(max(pattern_score, 0), 100) * 0.15
        + pass_ratio * 35
        + risk_score * 10
        + consecutive * 12
        + pattern_consistency * 8
        - warning_count * 2.5
    )
    return round(max(0.0, min(100.0, score)), 1)


def classify(
    row: Mapping[str, Any],
    source_at: dt.datetime | None,
    phase_id: str,
    history: Mapping[str, Any],
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    for item in row.get("checks") or []:
        if not isinstance(item, Mapping):
            continue
        check_id = str(item.get("id") or "")
        if check_id in HOGA_IDS:
            continue
        role = str(item.get("role") or "required")
        if role != "required":
            continue
        active = is_gate_active(check_id, source_at)
        checks.append({
            "id": check_id,
            "name": str(item.get("name") or check_id or "조건"),
            "role": role,
            "status": str(item.get("status") or "MISSING").upper(),
            "reason": str(item.get("reason") or item.get("rule") or "세부 사유 없음"),
            "active": active,
        })
    risk = risk_plan(row)
    active_failures = [check for check in checks if check["active"] and check["status"] == "FAIL"]
    active_warnings = [check for check in checks if check["active"] and check["status"] in {"WARN", "MISSING", "UNKNOWN"}]
    pending = [check for check in checks if not check["active"]]
    if risk["status"] != "PASS":
        active_failures.append({
            "id": "RISK_DISTANCE_STRICT", "name": "위험거리 6% 이내", "role": "required",
            "status": "FAIL", "reason": risk["reason"], "active": True,
        })
    pattern = row.get("pattern") if isinstance(row.get("pattern"), Mapping) else {}
    catalyst = row.get("catalyst") if isinstance(row.get("catalyst"), Mapping) else {}
    code = str(row.get("code") or "")
    pattern_id = str(pattern.get("id") or "-")
    persistence = history_stats(history, code, pattern_id, source_at.date() if source_at else None)

    if active_failures:
        status = "EXCLUDE"
    elif phase_id in {"EARLY_SCAN", "EARLY_WATCH", "UNKNOWN"}:
        status = "EARLY_WATCH"
    elif phase_id == "PRE_CLOSE":
        status = "PREPARE" if not active_warnings else "CONDITIONAL"
    elif phase_id in {"ENTRY_PREP", "ENTRY_CONFIRM"}:
        enough_history = phase_id == "ENTRY_PREP" or int(persistence.get("consecutive_count") or 0) >= 2
        status = "ENTRY_READY" if not active_warnings and enough_history else "CONDITIONAL"
    elif phase_id == "CLOSING_AUCTION":
        status = "AUCTION_READY" if not active_warnings and int(persistence.get("consecutive_count") or 0) >= 2 else "CONDITIONAL"
    elif phase_id == "FINAL_CLOSE":
        status = "STRICT" if not active_warnings else "CONDITIONAL"
    else:
        status = "CONDITIONAL"

    quality = num(row.get("quality_score")) or 0.0
    pattern_score = num(pattern.get("score")) or 0.0
    score = readiness_score(quality, pattern_score, checks, risk, persistence)
    return {
        "status": status,
        "code": code,
        "name": str(row.get("name") or code or "종목"),
        "price": num(row.get("price")),
        "change_rate": num(row.get("change_rate")),
        "quality": quality,
        "trade_value": num(row.get("trade_value")),
        "market_cap": num(row.get("market_cap")),
        "source_status": str(row.get("final_status") or "UNKNOWN"),
        "catalyst_grade": str(catalyst.get("grade") or "-"),
        "catalyst_reason": str(catalyst.get("reason") or "확인 정보 없음"),
        "pattern_id": pattern_id,
        "pattern_name": str(pattern.get("name") or "미분류"),
        "pattern_score": pattern_score,
        "initial_size": str((row.get("plan") or {}).get("initial_size") if isinstance(row.get("plan"), Mapping) else pattern.get("initial") or "-"),
        "risk": risk,
        "checks": checks,
        "failures": active_failures,
        "warnings": active_warnings,
        "pending": pending,
        "persistence": persistence,
        "readiness": score,
    }


def status_label(status: str, historical: bool) -> str:
    if historical and status != "EXCLUDE":
        return f"과거 {STATUS_LABELS.get(status, status)}"
    return STATUS_LABELS.get(status, status)


def issue_lines(row: Mapping[str, Any]) -> str:
    issues = row["failures"] if row["status"] == "EXCLUDE" else row["warnings"]
    lines = [f"{esc(item['name'])}: {esc(item['reason'])}" for item in issues]
    if row["pending"]:
        lines.extend(f"{esc(item['name'])}: 관측 시간 전" for item in row["pending"])
    return "<br>".join(lines) or "호가를 제외한 활성 필수 게이트를 통과했습니다."


def card(row: Mapping[str, Any], historical: bool) -> str:
    risk = row["risk"]
    change = "-" if row["change_rate"] is None else f"{row['change_rate']:+.2f}%"
    persistence = row["persistence"]
    next_plan = "유효한 진입·손절 계획 없음"
    if risk["status"] == "PASS" and risk["entry"] and risk["stop"]:
        entry = risk["entry"]
        stop = risk["stop"]
        next_plan = (
            f"갭상승 추격 금지 · 보합 시 {entry:,.0f}원 재지지 확인 · "
            f"갭하락 시 {entry:,.0f}원 회복 실패 또는 {stop:,.0f}원 이탈 시 종료"
        )
    check_rows = []
    for item in row["checks"]:
        shown_status = item["status"] if item["active"] else "PENDING"
        reason = item["reason"] if item["active"] else "해당 관측 시간이 아직 열리지 않았습니다."
        check_rows.append(
            f"<li><b>{esc(item['name'])}</b><span class='{esc(shown_status)}'>{esc(shown_status)}</span>"
            f"<small>{esc(reason)}</small></li>"
        )
    persistence_text = (
        f"당일 {persistence['appearance_count']}회 등장 · 연속 {persistence['consecutive_count']}회"
        + (f" · 최초 {persistence['first_seen']}" if persistence.get("first_seen") else "")
    )
    return f"""
<article class="candidate {esc(row['status'])}" data-status="{esc(row['status'])}" data-text="{esc(row['name'])} {esc(row['code'])} {esc(row['catalyst_reason'])} {esc(row['pattern_name'])}">
  <header><div><h3>{esc(row['name'])}</h3><p>{esc(row['code'])} · 원래 상태 {esc(row['source_status'])}</p></div><span class="badge {esc(row['status'])}">{esc(status_label(row['status'], historical))}</span></header>
  <div class="readiness"><b>{row['readiness']:.1f}</b><span>준비도</span><small>{esc(persistence_text)}</small></div>
  <div class="metrics"><div><b>{won(row['price'])}</b><span>기준 가격</span></div><div><b>{esc(change)}</b><span>등락률</span></div><div><b>{row['quality']:.1f}</b><span>품질</span></div><div><b>{row['pattern_score']:.0f}</b><span>패턴</span></div></div>
  <div class="tags"><span>재료 {esc(row['catalyst_grade'])} · {esc(row['catalyst_reason'])}</span><span>{esc(row['pattern_id'])} {esc(row['pattern_name'])}</span><span>거래대금 {esc(big_won(row['trade_value']))}</span><span>초기 비중 {esc(row['initial_size'])}</span></div>
  <p class="issue">{issue_lines(row)}</p>
  <div class="risk"><div><b>{won(risk['entry'])}</b><span>진입 기준</span></div><div><b>{won(risk['stop'])}</b><span>{esc(risk['stop_source'])}</span></div><div><b>{pct(risk['rate'])}</b><span>위험거리</span></div><div><b>{won(risk['one_r'])}</b><span>1R</span></div></div>
  <details><summary>필수 조건과 익일 가격 계획</summary><ul class="checks">{''.join(check_rows)}</ul><p class="next">{esc(next_plan)}</p></details>
</article>"""


def build(latest_path: Path, methodology_path: Path, output_path: Path, history_path: Path | None = None) -> dict[str, Any]:
    latest = json.loads(latest_path.read_text(encoding="utf-8"))
    methodology = json.loads(methodology_path.read_text(encoding="utf-8"))
    history: Mapping[str, Any] = {}
    if history_path and history_path.exists() and history_path.stat().st_size:
        loaded_history = json.loads(history_path.read_text(encoding="utf-8"))
        history = loaded_history if isinstance(loaded_history, Mapping) else {}
    raw_rows = [row for row in (latest.get("candidates") or []) if isinstance(row, Mapping)]
    built_at = dt.datetime.now(KST)
    source_at = source_timestamp(latest, raw_rows)
    phase_info = phase(source_at, built_at)
    rows = [classify(row, source_at, phase_info["id"], history) for row in raw_rows]
    rows.sort(
        key=lambda row: (
            STATUS_RANK.get(row["status"], 0), row["readiness"], row["quality"],
            row["pattern_score"], -(row["risk"]["rate"] if row["risk"]["rate"] is not None else 99),
        ),
        reverse=True,
    )
    counts = {status: sum(1 for row in rows if row["status"] == status) for status in STATUS_RANK}
    non_excluded = [row for row in rows if row["status"] != "EXCLUDE"]
    top_rows = non_excluded[:6]
    historical = phase_info["id"] == "HISTORICAL_CLOSE"
    top_cards = "".join(card(row, historical) for row in top_rows) or "<div class='empty'><b>준비할 후보가 없습니다.</b><p>필수 게이트 실패 종목만 남았습니다.</p></div>"
    strict_statuses = {"ENTRY_READY", "AUCTION_READY", "STRICT"}
    strict_rows = [row for row in rows if row["status"] in strict_statuses]
    strict_cards = "".join(card(row, historical) for row in strict_rows) or "<div class='empty'><b>실행 단계 후보가 없습니다.</b><p>조기 감시 목록에서 다음 갱신을 확인하세요.</p></div>"
    conditional_rows = [row for row in rows if row["status"] in {"EARLY_WATCH", "PREPARE", "CONDITIONAL"}]
    conditional_cards = "".join(card(row, historical) for row in conditional_rows) or "<div class='empty'><b>감시 후보가 없습니다.</b></div>"
    table_rows = "".join(
        f"<tr data-status='{esc(row['status'])}' data-text='{esc(row['name'])} {esc(row['code'])} {esc(row['catalyst_reason'])}'>"
        f"<td><span class='badge {esc(row['status'])}'>{esc(status_label(row['status'], historical))}</span></td>"
        f"<td><b>{esc(row['name'])}</b><small>{esc(row['code'])}</small></td><td>{row['readiness']:.1f}</td>"
        f"<td>{won(row['price'])}</td><td>{row['quality']:.1f}</td><td>{esc(row['catalyst_grade'])} · {esc(row['catalyst_reason'])}</td>"
        f"<td>{esc(row['pattern_id'])} {esc(row['pattern_name'])}</td><td>{won(row['risk']['entry'])}</td>"
        f"<td>{won(row['risk']['stop'])}</td><td>{pct(row['risk']['rate'])}</td>"
        f"<td>{esc((row['failures'] or row['warnings'] or row['pending'] or [{'reason':'통과'}])[0]['reason'])}</td></tr>"
        for row in rows
    )
    workflow = [
        item for item in (methodology.get("workflow") or [])
        if isinstance(item, Mapping) and str(item.get("id")) != "H"
    ]
    stages = "".join(
        f"<div><i>{esc(item.get('id'))}</i><b>{esc(item.get('name'))}</b><span>{esc(item.get('rule'))}</span></div>"
        for item in workflow
    )
    source_text = source_at.isoformat() if source_at else str(latest.get("market_date") or "-")
    total_ready = sum(counts.get(status, 0) for status in strict_statuses)
    watch_count = sum(counts.get(status, 0) for status in {"EARLY_WATCH", "PREPARE", "CONDITIONAL"})
    phase_steps = [
        ("13:38", "조기 탐색", "재료·대장·유동성"),
        ("14:08", "감시 시작", "차트·패턴·위험선"),
        ("14:33", "후보 압축", "고가권·윗꼬리 관측"),
        ("14:53", "우선 준비", "진입·손절 기록"),
        ("15:04", "진입 준비", "마감 과정 관측 시작"),
        ("15:10/18", "유지 확인", "연속 등장과 구조 유지"),
        ("15:20~29", "종가 단일가", "준비 후보만 검토"),
    ]
    phase_timeline = "".join(
        f"<div><b>{esc(time_text)}</b><span>{esc(name)}</span><small>{esc(description)}</small></div>"
        for time_text, name, description in phase_steps
    )
    html_text = f"""<!doctype html><html lang='ko'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='referrer' content='no-referrer'><title>라르고 종가베팅 조기 후보 · 호가 조건 제외</title><style>
:root{{--bg:#eef2f7;--paper:#fff;--ink:#132039;--muted:#68778d;--line:#d9e2ed;--blue:#175fc2;--green:#117448;--amber:#9a6200;--red:#b43242;--purple:#6847a6}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font-family:Arial,'Malgun Gothic',sans-serif;line-height:1.55}}.shell{{max-width:1500px;margin:auto;padding:20px}}.hero{{background:linear-gradient(135deg,#0d274d,#164a87);color:#fff;border-radius:24px;padding:28px}}.hero h1{{margin:10px 0;font-size:34px}}.hero p{{color:#d8e7fb;max-width:980px}}.meta,.stats,.metrics,.risk{{display:grid;grid-template-columns:repeat(4,1fr);gap:9px}}.meta div{{background:#ffffff18;border:1px solid #ffffff25;border-radius:12px;padding:10px}}.meta b,.meta span,.metrics b,.metrics span,.risk b,.risk span{{display:block}}.phase{{margin:16px 0;background:#fff;border:1px solid var(--line);border-radius:18px;padding:17px;display:grid;grid-template-columns:1fr auto;gap:15px;align-items:center}}.phase h2{{margin:0 0 5px}}.phase p{{margin:0;color:var(--muted)}}.next-refresh{{background:#eaf2ff;color:#184f9c;border-radius:12px;padding:11px;text-align:center;font-weight:800}}.timeline{{display:grid;grid-template-columns:repeat(7,1fr);gap:7px;margin-bottom:16px}}.timeline div{{background:#fff;border:1px solid var(--line);border-radius:13px;padding:10px}}.timeline b,.timeline span,.timeline small{{display:block}}.timeline span{{font-weight:800}}.timeline small{{color:var(--muted);font-size:10px}}.notice{{margin:16px 0;padding:13px 15px;background:#fff7e8;border:1px solid #e7c77d;border-radius:14px;color:#674600}}.stats>div,.section{{background:#fff;border:1px solid var(--line);border-radius:18px;padding:16px;margin-bottom:16px}}.stats b{{font-size:30px;display:block}}.toolbar{{position:sticky;top:0;z-index:5;background:#fffffff2;padding:10px;border:1px solid var(--line);border-radius:14px;display:flex;gap:8px;flex-wrap:wrap;margin-bottom:16px}}button,input{{font:inherit;padding:9px 11px;border:1px solid var(--line);border-radius:10px;background:#fff}}input{{flex:1;min-width:220px}}button.active{{background:var(--blue);color:#fff}}.grid{{display:grid;grid-template-columns:repeat(2,1fr);gap:12px}}.candidate{{position:relative;border:1px solid var(--line);border-radius:16px;overflow:hidden;background:#fff}}.candidate header{{display:flex;justify-content:space-between;padding:14px;border-bottom:1px solid var(--line);gap:10px}}.candidate h3{{margin:0}}.candidate header p{{margin:3px 0 0;color:var(--muted);font-size:11px}}.badge{{display:inline-flex;padding:5px 8px;border-radius:999px;font-size:10px;font-weight:800;white-space:nowrap}}.AUCTION_READY,.STRICT{{border-color:#78bf9e}}.badge.AUCTION_READY,.badge.STRICT,.badge.ENTRY_READY,.PASS{{background:#e8f7ef;color:var(--green)}}.ENTRY_READY{{border-color:#9fd6bc}}.PREPARE{{border-color:#9bbce9}}.badge.PREPARE{{background:#eaf2ff;color:#1b58a5}}.EARLY_WATCH{{border-color:#b7a4dc}}.badge.EARLY_WATCH{{background:#f0ebfa;color:var(--purple)}}.CONDITIONAL{{border-color:#e9c36c}}.badge.CONDITIONAL,.WARN,.MISSING,.UNKNOWN,.PENDING{{background:#fff5df;color:var(--amber)}}.EXCLUDE{{border-color:#e8bcc3}}.badge.EXCLUDE,.FAIL{{background:#ffedf0;color:var(--red)}}.readiness{{display:grid;grid-template-columns:auto 1fr;gap:4px 9px;padding:11px 14px;background:#f7f9fc}}.readiness b{{font-size:26px;color:var(--blue);grid-row:1/3}}.readiness span{{font-weight:800}}.readiness small{{color:var(--muted)}}.metrics,.risk{{padding:12px 14px}}.metrics div,.risk div{{background:#f7f9fc;border-radius:10px;padding:8px}}.metrics span,.risk span{{font-size:9px;color:var(--muted)}}.tags{{display:flex;gap:6px;flex-wrap:wrap;padding:0 14px}}.tags span{{background:#eff3f8;border-radius:999px;padding:5px 7px;font-size:10px}}.issue,.next{{margin:11px 14px;padding:10px;background:#fff7e8;border-radius:10px;font-size:11px}}details{{margin:10px 14px 14px}}summary{{cursor:pointer;font-weight:800}}.checks{{list-style:none;padding:0}}.checks li{{display:grid;grid-template-columns:1fr auto;gap:6px;border-bottom:1px solid var(--line);padding:7px 0}}.checks small{{grid-column:1/-1;color:var(--muted)}}.empty{{padding:30px;text-align:center;border:1px dashed #b9c7d9;border-radius:14px}}table{{width:100%;border-collapse:collapse;font-size:11px}}th,td{{border-bottom:1px solid var(--line);padding:8px;text-align:left}}td small{{display:block;color:var(--muted)}}.tablewrap{{overflow:auto}}.stages{{display:grid;grid-template-columns:repeat(9,1fr);gap:7px}}.stages div{{border:1px solid var(--line);border-radius:12px;padding:9px}}.stages i{{display:grid;width:24px;height:24px;place-items:center;border-radius:50%;background:var(--blue);color:#fff;font-style:normal}}.stages b,.stages span{{display:block}}.stages span{{font-size:10px;color:var(--muted)}}@media(max-width:1000px){{.timeline{{grid-template-columns:repeat(4,1fr)}}}}@media(max-width:850px){{.grid{{grid-template-columns:1fr}}.meta,.stats,.metrics,.risk{{grid-template-columns:repeat(2,1fr)}}.stages,.timeline{{grid-template-columns:repeat(2,1fr)}}.hero h1{{font-size:27px}}.phase{{grid-template-columns:1fr}}}}
</style></head><body><main class='shell'><section class='hero'><span>읽기 전용 · 주문 기능 없음 · 호가 판정 미사용</span><h1>라르고 종가베팅 조기 후보</h1><p>후보를 종가 확정 뒤가 아니라 장 마감 1시간 50분 전부터 단계적으로 보여줍니다. 기존 필수 게이트는 유지하고, 아직 관측 시간이 오지 않은 마감 조건만 대기로 표시합니다.</p><div class='meta'><div><b>전략</b><span>{esc(latest.get('strategy_version'))}</span></div><div><b>기준 시각</b><span>{esc(source_text)}</span></div><div><b>판정 단계</b><span>{esc(phase_info['title'])}</span></div><div><b>위험 한도</b><span>진입가 대비 6%</span></div></div></section><section class='phase'><div><h2>{esc(phase_info['title'])}</h2><p>{esc(phase_info['action'])}</p></div><div class='next-refresh'>다음 갱신<br>{esc(next_refresh(source_at))}</div></section><section class='timeline'>{phase_timeline}</section><div class='notice'><b>기준 확인:</b> {esc(phase_info['warning'])} 조기 감시는 매수 신호가 아니며 주문·계좌 기능은 없습니다.</div><section class='stats'><div><b>{len(top_rows)}</b><span>지금 준비할 상위 후보</span></div><div><b>{total_ready}</b><span>실행 단계 후보</span></div><div><b>{watch_count}</b><span>감시·조건부</span></div><div><b>{counts.get('EXCLUDE',0)}</b><span>제외</span></div></section><nav class='toolbar'><input id='q' placeholder='종목명·코드·재료 검색'><button class='active' data-filter='ALL'>전체</button><button data-filter='READY'>실행 단계</button><button data-filter='WATCH'>감시 단계</button><button data-filter='EXCLUDE'>제외</button></nav><section class='section'><h2>지금 준비할 상위 후보</h2><p>후보가 일찍 보이도록 준비도와 당일 반복 등장 횟수로 정렬했습니다. 필수 실패 종목은 포함하지 않습니다.</p><div class='grid'>{top_cards}</div></section><section class='section'><h2>실행 단계 후보</h2><div class='grid'>{strict_cards}</div></section><section class='section'><h2>감시·조건부 후보</h2><div class='grid'>{conditional_cards}</div></section><section class='section'><h2>전체 엄격 판정표</h2><div class='tablewrap'><table><thead><tr><th>단계</th><th>종목</th><th>준비도</th><th>가격</th><th>품질</th><th>재료</th><th>패턴</th><th>진입</th><th>손절</th><th>위험</th><th>핵심 사유</th></tr></thead><tbody id='rows'>{table_rows}</tbody></table></div></section><section class='section'><h2>적용한 기존 전략</h2><p>H 단계와 호가 체크 세 개만 제외했습니다. 마감 조건은 삭제하지 않고 관측 가능 시각 전까지 대기로 둡니다.</p><div class='stages'>{stages}</div></section></main><script>
const q=document.getElementById('q');let filter='ALL';const ready=new Set(['ENTRY_READY','AUCTION_READY','STRICT']);const watch=new Set(['EARLY_WATCH','PREPARE','CONDITIONAL']);function showStatus(s){{if(filter==='ALL')return true;if(filter==='READY')return ready.has(s);if(filter==='WATCH')return watch.has(s);return s===filter}}function apply(){{const search=q.value.toLowerCase();document.querySelectorAll('article.candidate,tbody tr').forEach(el=>{{const status=el.dataset.status||'';const text=(el.dataset.text||'').toLowerCase();el.hidden=!(showStatus(status)&&text.includes(search))}})}}q.addEventListener('input',apply);document.querySelectorAll('[data-filter]').forEach(button=>button.addEventListener('click',()=>{{filter=button.dataset.filter;document.querySelectorAll('[data-filter]').forEach(item=>item.classList.toggle('active',item===button));apply()}}));
</script></body></html>"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html_text, encoding="utf-8")
    return {
        "status": "PASS",
        "phase": phase_info["id"],
        "source_at": source_text,
        "counts": counts,
        "top_candidates": [row["name"] for row in top_rows],
        "bytes": len(html_text.encode("utf-8")),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--latest", required=True, type=Path)
    parser.add_argument("--methodology", required=True, type=Path)
    parser.add_argument("--history", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = build(args.latest, args.methodology, args.output, args.history)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
