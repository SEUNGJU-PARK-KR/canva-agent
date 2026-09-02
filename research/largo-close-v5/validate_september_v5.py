#!/usr/bin/env python3
"""Reconstruct September 2026 Largo v5 signals from retained closing snapshots.

Research-only. No order placement. Each candidate is scored with the canonical
largo_material_0906 module, using the exact 15:18 legacy quote when available.
Outcomes use the next session's top bid from 09:00 through 09:05.
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
import urllib.request
from pathlib import Path
from typing import Any, Mapping

from largo_material_0906 import (
    KST,
    NAVER_STOCK,
    entry_from_legacy,
    fetch_json,
    fetch_legacy_window,
    flatten_news,
    json_safe,
    outcome_before_0906,
    score_candidate,
    target3_gate,
    theme_metrics_from_candidate,
)

SNAPSHOTS = {
    "2026-09-01": "https://raw.githubusercontent.com/SEUNGJU-PARK-KR/canva-agent/e6fcdcdd2d34dc13bc78b8e70447fa33f234c5be/data/latest.json",
    "2026-09-02": "https://raw.githubusercontent.com/SEUNGJU-PARK-KR/canva-agent/839f9d1467dd7f995cbdf0ad64e618de1fc71724/data/latest.json",
}
NEXT_SESSION = {
    "2026-09-01": "2026-09-02",
    "2026-09-02": "2026-09-03",
}


def num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def fetch_snapshot(url: str, timeout: int) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": "LargoSeptemberV5/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("candidates"), list):
        raise ValueError(f"invalid snapshot: {url}")
    return data


def fetch_news_box(code: str, timeout: int) -> tuple[list[dict[str, Any]], list[str]]:
    errors: list[str] = []
    box: dict[str, Any] = {}
    for key, path in {
        "news": f"/api/domestic/detail/news?itemCode={code}&page=1&pageSize=30",
        "notice": f"/api/domestic/detail/notice?itemCode={code}&startIdx=0&pageSize=30",
    }.items():
        try:
            box[key] = {"payload": fetch_json(NAVER_STOCK + path, timeout=timeout)}
        except Exception as exc:
            box[key] = {"payload": []}
            errors.append(f"{key}:{type(exc).__name__}:{exc}")
    return flatten_news(box), errors


def evaluate_candidate(candidate: Mapping[str, Any], signal_date: str, timeout: int, delay: float) -> dict[str, Any]:
    code = str(candidate.get("code") or "").zfill(6)
    signal_at = dt.datetime.fromisoformat(signal_date + "T15:18:00+09:00").astimezone(KST)
    items, errors = fetch_news_box(code, timeout)
    scored = score_candidate(
        candidate,
        signal_at,
        items,
        theme_metrics=theme_metrics_from_candidate(candidate),
        theme_history=[],
        proxy_note="retained post-close snapshot; exact 15:18 quote; current snapshot theme fields",
    )
    try:
        legacy = fetch_legacy_window(code, signal_date.replace("-", "") + "151800", timeout=timeout)
        entry = entry_from_legacy(legacy)
    except Exception as exc:
        entry = {"entry_time": None, "entry_last": None, "entry_ask": None, "entry_bid": None}
        errors.append(f"entry:{type(exc).__name__}:{exc}")
    gate = target3_gate(scored, entry)
    if delay:
        time.sleep(delay)
    structure = scored.get("structure") if isinstance(scored.get("structure"), Mapping) else {}
    evidence = scored.get("evidence") if isinstance(scored.get("evidence"), Mapping) else {}
    return json_safe({
        "signal_date": signal_date,
        "code": code,
        "name": candidate.get("name"),
        "market": candidate.get("market"),
        "snapshot_generated_at": candidate.get("updated_at"),
        "snapshot_price": candidate.get("price"),
        "trade_value": candidate.get("trade_value"),
        "hard_reject": scored.get("hard_reject"),
        "failed_ids": scored.get("failed_ids"),
        "grade": scored.get("grade"),
        "grade_status": scored.get("grade_status"),
        "directness_points": evidence.get("directness_points"),
        "freshness_points": evidence.get("freshness_points"),
        "evidence_title": evidence.get("title"),
        "evidence_at": evidence.get("at"),
        "change_rate": structure.get("change_rate"),
        "digest_ratio": structure.get("digest_ratio"),
        "risk_rate": structure.get("risk_rate"),
        "entry_time": entry.get("entry_time"),
        "entry_last": entry.get("entry_last"),
        "entry_ask": entry.get("entry_ask"),
        "entry_bid": entry.get("entry_bid"),
        "spread_pct": gate.get("spread_pct"),
        "v5_status": gate.get("status"),
        "v5_eligible": gate.get("eligible"),
        "v5_lane": gate.get("lane"),
        "v5_size_band": gate.get("size_band"),
        "v5_blockers": gate.get("blockers"),
        "daily_pick": False,
        "daily_rank": None,
        "errors": errors,
    })


def selection_key(row: Mapping[str, Any]) -> tuple[float, float, float, str]:
    risk = num(row.get("risk_rate"))
    spread = num(row.get("spread_pct"))
    turnover = num(row.get("trade_value"))
    return (
        risk if risk is not None else math.inf,
        spread if spread is not None else math.inf,
        -(turnover if turnover is not None else 0.0),
        str(row.get("code") or ""),
    )


def add_outcome(row: dict[str, Any], next_date: str, timeout: int) -> None:
    row["next_date"] = next_date
    entry_ask = num(row.get("entry_ask"))
    entry_last = num(row.get("entry_last"))
    if not entry_ask or not entry_last:
        row["outcome_status"] = "NO_ENTRY_QUOTE"
        return
    try:
        legacy = fetch_legacy_window(str(row.get("code")), next_date.replace("-", "") + "090600", timeout=timeout)
        outcome = outcome_before_0906(legacy, entry_last=entry_last, entry_ask=entry_ask)
    except Exception as exc:
        row["outcome_status"] = "ERROR"
        row.setdefault("errors", []).append(f"outcome:{type(exc).__name__}:{exc}")
        return
    row.update(json_safe(outcome))
    row["outcome_status"] = "EVALUATED" if outcome.get("open_observations") else "NO_MARKET_ROWS"
    max_ret = num(outcome.get("max_executable_return_pct"))
    last_ret = num(outcome.get("last_executable_return_pct"))
    row["policy_return_3pct"] = 3.0 if max_ret is not None and max_ret >= 3.0 else last_ret


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            flat = {}
            for key, value in row.items():
                if isinstance(value, (dict, list)):
                    flat[key] = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
                else:
                    flat[key] = value
            writer.writerow(flat)


def fmt_pct(value: Any, digits: int = 4) -> str:
    v = num(value)
    return "—" if v is None else f"{v:+.{digits}f}%"


def fmt_price(value: Any) -> str:
    v = num(value)
    return "—" if v is None else f"{v:,.0f}원"


def build_report(summary: Mapping[str, Any], selected: list[dict[str, Any]]) -> str:
    selected_by_date = {str(row.get("signal_date")): row for row in selected}
    cards = [
        ("검사일", str(summary["signal_days"])),
        ("검사 후보", str(summary["candidate_rows"])),
        ("v5 통과", str(summary["eligible_rows"])),
        ("날짜별 선택", str(summary["selected_rows"])),
        ("결과 완료", str(summary["evaluated_picks"])),
    ]
    cards_html = "".join(f"<div class='card'><b>{html.escape(v)}</b><span>{html.escape(k)}</span></div>" for k, v in cards)
    day_rows = []
    for date_text in summary["dates"]:
        row = selected_by_date.get(date_text)
        if not row:
            day_rows.append(f"<tr><td>{date_text}</td><td>통과 종목 없음</td><td>—</td><td>—</td><td>매매 없음</td></tr>")
            continue
        result = "결과 대기"
        if row.get("outcome_status") == "EVALUATED":
            result = "+3% 도달" if row.get("hit_3_exec") else f"09:05 {fmt_pct(row.get('last_executable_return_pct'))}"
        risk_pct = (num(row.get("risk_rate")) or 0) * 100
        day_rows.append(
            "<tr>"
            f"<td>{date_text}</td>"
            f"<td>{html.escape(str(row.get('name') or ''))}<small>{html.escape(str(row.get('code') or ''))} · {html.escape(str(row.get('v5_lane') or ''))}</small></td>"
            f"<td>{fmt_price(row.get('entry_ask'))}<small>위험 {fmt_pct(risk_pct,2)} · 호가차 {fmt_pct(row.get('spread_pct'),4)}</small></td>"
            f"<td>{fmt_pct(row.get('max_executable_return_pct'))}</td>"
            f"<td>{result}</td>"
            "</tr>"
        )
    warnings = "".join(f"<li>{html.escape(str(item))}</li>" for item in summary["limitations"])
    return f"""<!doctype html><html lang='ko'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>라르고 v5 2026년 9월 검증</title><style>
:root{{--bg:#eef3f8;--paper:#fff;--ink:#17243a;--muted:#65758a;--line:#d5dee9;--navy:#0b315d;--blue:#2367ad;--amber:#9a6200}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font-family:Arial,'Malgun Gothic',sans-serif;line-height:1.55}}main{{max-width:1240px;margin:auto;padding:20px}}.hero{{padding:28px;border-radius:22px;background:linear-gradient(135deg,var(--navy),var(--blue));color:white}}.hero p{{color:#deecfb;max-width:900px}}.cards{{display:grid;grid-template-columns:repeat(5,1fr);gap:10px;margin:16px 0}}.card,.section{{background:var(--paper);border:1px solid var(--line);border-radius:17px;padding:16px;margin-bottom:16px}}.card b{{font-size:26px;display:block}}.card span,small{{color:var(--muted)}}.warning{{border-left:6px solid var(--amber)}}table{{border-collapse:collapse;width:100%;font-size:14px}}th,td{{padding:10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}td small{{display:block}}@media(max-width:850px){{.cards{{grid-template-columns:repeat(2,1fr)}}}}@media(max-width:560px){{main{{padding:10px}}.cards{{grid-template-columns:1fr}}.hero{{padding:20px}}}}
</style></head><body><main><section class='hero'><span>읽기 전용 · 주문 기능 없음</span><h1>라르고 종가베팅 v5 9월 재구성 검증</h1><p>보존된 장 마감 후보 스냅샷을 v5 임계값에 그대로 넣고, 15시 18분 최우선 매도호가와 다음 거래일 09시 06분 전 최우선 매수호가를 다시 조회했습니다.</p></section>
<section class='cards'>{cards_html}</section>
<section class='section'><h2>날짜별 최종 선택</h2><div style='overflow:auto'><table><thead><tr><th>신호일</th><th>종목</th><th>15:18 가상 진입</th><th>09:06 전 최고</th><th>정책 결과</th></tr></thead><tbody>{''.join(day_rows)}</tbody></table></div></section>
<section class='section warning'><h2>해석 제한</h2><ul>{warnings}</ul></section>
<section class='section'><h2>고정 규칙</h2><p>추세·거래대금형은 당일 상승률 10~15%, 거래대금 소화 0.10~1.00, 호가 간격 0.20% 이하입니다. 직접 재료형은 직접성 14점 이상, 신선도 3점 이상, 소화 0.10~1.50, 호가 간격 0.10% 이하입니다. 공통으로 하드 제외 없음, 보통주, 구조 위험 10% 이하를 요구합니다.</p></section>
</main></body></html>"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="out")
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--delay", type=float, default=0.05)
    parser.add_argument("--today", default=dt.datetime.now(KST).date().isoformat())
    args = parser.parse_args()
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    today = dt.date.fromisoformat(args.today)

    all_rows: list[dict[str, Any]] = []
    selected: list[dict[str, Any]] = []
    snapshot_meta: list[dict[str, Any]] = []
    for signal_date, url in SNAPSHOTS.items():
        snapshot = fetch_snapshot(url, args.timeout)
        snapshot_meta.append({
            "signal_date": signal_date,
            "url": url,
            "market_date": snapshot.get("market_date"),
            "generated_at": snapshot.get("generated_at"),
            "candidate_count": len(snapshot.get("candidates") or []),
        })
        day_rows = [evaluate_candidate(candidate, signal_date, args.timeout, args.delay) for candidate in snapshot.get("candidates") or []]
        eligible = sorted((row for row in day_rows if row.get("v5_eligible")), key=selection_key)
        for rank, row in enumerate(eligible, 1):
            row["daily_rank"] = rank
        if eligible:
            pick = eligible[0]
            pick["daily_pick"] = True
            next_date = NEXT_SESSION[signal_date]
            if dt.date.fromisoformat(next_date) <= today:
                add_outcome(pick, next_date, args.timeout)
            else:
                pick["next_date"] = next_date
                pick["outcome_status"] = "PENDING"
            selected.append(dict(pick))
        all_rows.extend(day_rows)

    evaluated = [row for row in selected if row.get("outcome_status") == "EVALUATED"]
    policy_returns = [num(row.get("policy_return_3pct")) for row in evaluated]
    policy_returns = [value for value in policy_returns if value is not None]
    summary = {
        "version": "largo-close-v5-september-reconstruction-v1",
        "generated_at": dt.datetime.now(KST).isoformat(),
        "today": args.today,
        "dates": list(SNAPSHOTS),
        "signal_days": len(SNAPSHOTS),
        "candidate_rows": len(all_rows),
        "eligible_rows": sum(bool(row.get("v5_eligible")) for row in all_rows),
        "selected_rows": len(selected),
        "evaluated_picks": len(evaluated),
        "positive_picks": sum((num(row.get("policy_return_3pct")) or 0) > 0 for row in evaluated),
        "hit3_picks": sum(bool(row.get("hit_3_exec")) for row in evaluated),
        "mean_policy_return_pct": round(statistics.fmean(policy_returns), 4) if policy_returns else None,
        "sum_policy_return_pct": round(sum(policy_returns), 4) if policy_returns else None,
        "snapshots": snapshot_meta,
        "selected": selected,
        "limitations": [
            "9월 1일과 2일의 보존 스냅샷은 장 마감 뒤 생성됐습니다. 정확한 15:18 후보 화면을 복원한 전진검증이 아니라 사후 재구성입니다.",
            "진입 가격은 정확한 15:18 최우선 매도호가를 사용합니다. 결과는 다음 거래일 09:00~09:05 분별 최우선 매수호가입니다.",
            "테마 구성과 마감 구조 일부는 보존 스냅샷 값을 사용합니다.",
            "수수료, 세금, 호가 잔량과 주문 지연은 반영하지 않습니다.",
            "v5 임계값은 변경하지 않았습니다. 9월 자료를 보고 다시 맞추지 않습니다.",
        ],
    }
    write_csv(out / "september-v5-all.csv", all_rows)
    write_csv(out / "september-v5-selected.csv", selected)
    (out / "september-v5-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "september-v5-report.html").write_text(build_report(summary, selected), encoding="utf-8")
    print(json.dumps({
        "dates": summary["dates"],
        "candidates": summary["candidate_rows"],
        "eligible": summary["eligible_rows"],
        "selected": [{key: row.get(key) for key in ("signal_date", "code", "name", "v5_lane", "entry_ask", "outcome_status", "max_executable_return_pct", "policy_return_3pct")} for row in selected],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
