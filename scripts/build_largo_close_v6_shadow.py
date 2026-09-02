#!/usr/bin/env python3
"""Build the read-only Largo v6 shadow report from retained v5 material history."""
from __future__ import annotations

import argparse
import csv
import html
import json
import math
import statistics
import sys
from pathlib import Path
from typing import Any, Mapping, MutableMapping

from largo_close_v6 import (
    LANE_MOMENTUM,
    LANE_THEME_CONTINUATION,
    TARGET_PCT,
    V6_VERSION,
    apply_daily_v6_selection,
    num,
    v6_gate,
)


def load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"expected an object in {path}")
    return data


def fnum(value: Any) -> float | None:
    return num(value)


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def operational_blockers(signal: Mapping[str, Any], gate: Mapping[str, Any]) -> list[str]:
    blockers: list[str] = []
    if bool(signal.get("proxy")):
        blockers.append("과거 대용 신호")
    if signal.get("source_valid") is False:
        blockers.append("15:18 기준 후보 시각 불충족")
    if not bool(gate.get("qualified")):
        blockers.append("v6 조건 미통과")
    lanes = {str(value) for value in gate.get("lanes") or []}
    if lanes & {LANE_MOMENTUM, LANE_THEME_CONTINUATION} and signal.get("timely_theme") is False:
        blockers.append("테마 최종 캡처 지연")
    if gate.get("spread_pct") is None:
        blockers.append("15:18 매도·매수호가 미확인")
    return list(dict.fromkeys(blockers))


def result_map(history: Mapping[str, Any]) -> dict[tuple[str, str], Mapping[str, Any]]:
    output: dict[tuple[str, str], Mapping[str, Any]] = {}
    for row in history.get("results") or []:
        if not isinstance(row, Mapping):
            continue
        key = (str(row.get("signal_date") or ""), str(row.get("code") or "").zfill(6))
        output[key] = row
    return output


def evaluate(history: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    results = result_map(history)
    selected_rows: list[dict[str, Any]] = []
    all_rows: list[dict[str, Any]] = []

    for signal in sorted(
        (row for row in history.get("signals") or [] if isinstance(row, Mapping)),
        key=lambda row: str(row.get("signal_date") or ""),
    ):
        candidates: list[MutableMapping[str, Any]] = []
        for source in signal.get("candidates") or []:
            if not isinstance(source, Mapping):
                continue
            candidate: MutableMapping[str, Any] = dict(source)
            entry = {
                "entry_ask": candidate.get("entry_ask"),
                "entry_bid": candidate.get("entry_bid"),
            }
            gate = v6_gate(candidate, entry)
            gate["operational_blockers"] = operational_blockers(signal, gate)
            if gate["operational_blockers"]:
                gate["eligible"] = False
                if gate.get("qualified"):
                    gate["status"] = "SHADOW_BLOCK"
            candidate["v6"] = gate
            candidate["signal_date"] = str(signal.get("signal_date") or "")
            candidate["signal_proxy"] = bool(signal.get("proxy"))
            candidates.append(candidate)

        apply_daily_v6_selection(candidates)
        for candidate in candidates:
            gate = candidate.get("v6") if isinstance(candidate.get("v6"), Mapping) else {}
            code = str(candidate.get("code") or "").zfill(6)
            date_text = str(signal.get("signal_date") or "")
            result = results.get((date_text, code), {})
            maximum = fnum(result.get("max_executable_return_pct"))
            last = fnum(result.get("last_executable_return_pct"))
            policy = TARGET_PCT if maximum is not None and maximum >= TARGET_PCT else last
            row = {
                "signal_date": date_text,
                "code": code,
                "name": candidate.get("name"),
                "lane": gate.get("lane"),
                "status": gate.get("status"),
                "qualified": bool(gate.get("qualified")),
                "eligible": bool(gate.get("eligible")),
                "daily_rank": gate.get("daily_rank"),
                "entry_ask": candidate.get("entry_ask"),
                "entry_bid": candidate.get("entry_bid"),
                "spread_pct": gate.get("spread_pct"),
                "risk_rate": gate.get("risk_rate"),
                "trade_value": gate.get("trade_value"),
                "change_rate": gate.get("change_rate"),
                "digest_ratio": gate.get("digest_ratio"),
                "close_location": gate.get("close_location"),
                "upper_wick": gate.get("upper_wick"),
                "body_ratio": gate.get("body_ratio"),
                "theme_breadth": gate.get("theme_breadth"),
                "leader_rank": gate.get("leader_rank"),
                "follower_strong_count": gate.get("follower_strong_count"),
                "directness_points": gate.get("directness_points"),
                "freshness_points": gate.get("freshness_points"),
                "evidence_title": (candidate.get("evidence") or {}).get("title") if isinstance(candidate.get("evidence"), Mapping) else None,
                "evidence_audit_pass": (gate.get("evidence_audit") or {}).get("passed") if isinstance(gate.get("evidence_audit"), Mapping) else None,
                "operational_blockers": " | ".join(gate.get("operational_blockers") or []),
                "next_date": result.get("next_date"),
                "max_executable_return_pct": maximum,
                "last_executable_return_pct": last,
                "policy_return_pct": policy,
                "hit_3pct": None if maximum is None else maximum >= TARGET_PCT,
                "signal_proxy": bool(signal.get("proxy")),
            }
            all_rows.append(row)
            if row["eligible"]:
                selected_rows.append(row)
    return all_rows, selected_rows


def metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    evaluated = [row for row in rows if fnum(row.get("policy_return_pct")) is not None]
    returns = [float(row["policy_return_pct"]) for row in evaluated]
    return {
        "selected": len(rows),
        "evaluated": len(evaluated),
        "positive": sum(value > 0 for value in returns),
        "losses": sum(value < 0 for value in returns),
        "hit3": sum(bool(row.get("hit_3pct")) for row in evaluated),
        "mean_policy_return_pct": round(statistics.fmean(returns), 4) if returns else None,
        "median_policy_return_pct": round(statistics.median(returns), 4) if returns else None,
        "sum_policy_return_pct": round(sum(returns), 4) if returns else None,
    }


def pct(value: Any) -> str:
    number = fnum(value)
    return "—" if number is None else f"{number:+.4f}%"


def price(value: Any) -> str:
    number = fnum(value)
    return "—" if number is None else f"{number:,.0f}원"


def report(summary: Mapping[str, Any], rows: list[dict[str, Any]], benchmark: Mapping[str, Any]) -> str:
    esc = lambda value: html.escape(str(value if value is not None else ""))
    displayed = sorted(rows, key=lambda row: str(row.get("signal_date") or ""), reverse=True)
    body_rows = []
    for row in displayed:
        result_text = "결과 대기" if row.get("policy_return_pct") is None else pct(row.get("policy_return_pct"))
        body_rows.append(
            "<tr>"
            f"<td>{esc(row['signal_date'])}</td>"
            f"<td>{esc(row['name'])}<small>{esc(row['code'])} · {esc(row['lane'])}</small></td>"
            f"<td>{price(row['entry_ask'])}<small>호가차 {pct(row['spread_pct'])}</small></td>"
            f"<td>{pct(row['max_executable_return_pct'])}</td>"
            f"<td>{result_text}</td>"
            "</tr>"
        )
    if not body_rows:
        body_rows.append("<tr><td colspan='5'>완전한 15:18 입력을 갖춘 v6 그림자 신호가 아직 없습니다.</td></tr>")

    history = benchmark.get("august_exact") or {}
    m = summary.get("metrics") or {}
    return f"""<!doctype html><html lang='ko'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>라르고 종가베팅 v6 그림자 신호</title><style>
:root{{--bg:#edf2f7;--paper:#fff;--ink:#17243a;--muted:#68778b;--line:#d5dee9;--navy:#0b315d;--blue:#286aae;--green:#137354;--amber:#8a5a00}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font-family:Arial,'Malgun Gothic',sans-serif;line-height:1.55}}main{{max-width:1180px;margin:auto;padding:20px}}.hero{{padding:28px;border-radius:22px;background:linear-gradient(135deg,var(--navy),var(--blue));color:#fff}}.hero p{{max-width:900px;color:#e4effb}}.cards{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:16px 0}}.card,.section{{background:#fff;border:1px solid var(--line);border-radius:17px;padding:17px;margin-bottom:16px}}.card b{{display:block;font-size:27px}}.card span,small{{color:var(--muted)}}.warning{{border-left:6px solid var(--amber)}}table{{width:100%;border-collapse:collapse;font-size:14px}}th,td{{padding:10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}td small{{display:block}}@media(max-width:820px){{.cards{{grid-template-columns:repeat(2,1fr)}}}}@media(max-width:560px){{main{{padding:10px}}.cards{{grid-template-columns:1fr}}.hero{{padding:20px}}}}
</style></head><body><main><section class='hero'><span>연구용 그림자 신호 · 주문 기능 없음</span><h1>라르고 종가베팅 v6</h1><p>v5는 비교 기준으로 유지합니다. v6는 마감 구조·테마 확산·직접 재료 의미 검사를 강화해 별도로 누적합니다.</p></section>
<section class='cards'><div class='card'><b>{m.get('selected',0)}</b><span>전진 그림자 신호</span></div><div class='card'><b>{m.get('evaluated',0)}</b><span>결과 완료</span></div><div class='card'><b>{m.get('hit3',0)}</b><span>09:06 전 +3%</span></div><div class='card'><b>{history.get('hit3',0)}/{history.get('trades',0)}</b><span>8월 연구표본 +3%</span></div></section>
<section class='section'><h2>전진 그림자 결과</h2><div style='overflow:auto'><table><thead><tr><th>신호일</th><th>종목</th><th>15:18 가상 진입</th><th>09:06 전 최고</th><th>정책수익</th></tr></thead><tbody>{''.join(body_rows)}</tbody></table></div></section>
<section class='section'><h2>v6 핵심 조건</h2><p>추세 확인형, 직접 재료 미반영형, 직접 재료 반영형, 테마 연속형을 따로 계산합니다. 모든 경로는 거래대금 1,000억원 이상과 정확한 15시 18분 매도·매수호가를 요구합니다. 직접 재료는 회사명 일치와 부정 문맥 차단을 거칩니다.</p></section>
<section class='section warning'><h2>승격 전 제한</h2><p>v6는 매수 지시가 아닙니다. 40거래일과 완료 신호 20건을 모두 채우기 전에는 v5를 대체하지 않습니다. 수수료·세금·호가 잔량·주문 지연도 실제 운용에서 별도로 반영해야 합니다.</p></section>
</main></body></html>"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--history", required=True)
    parser.add_argument("--benchmark-summary", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    history = load_json(Path(args.history))
    benchmark = load_json(Path(args.benchmark_summary))
    all_rows, selected = evaluate(history)
    summary = {
        "version": V6_VERSION,
        "history_generated_at": history.get("generated_at"),
        "signal_dates": sorted({row["signal_date"] for row in all_rows}),
        "candidate_rows": len(all_rows),
        "selected_rows": len(selected),
        "metrics": metrics(selected),
        "selected": selected,
        "research_only": True,
        "shadow_only": True,
    }

    output = Path(args.output_dir)
    data = output / "data"
    data.mkdir(parents=True, exist_ok=True)
    write_csv(data / "largo-close-v6-shadow-all.csv", all_rows)
    write_csv(data / "largo-close-v6-shadow-selected.csv", selected)
    (data / "largo-close-v6-shadow-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "close-v6.html").write_text(report(summary, selected, benchmark), encoding="utf-8")
    print(json.dumps({"candidates": len(all_rows), "selected": len(selected), "metrics": summary["metrics"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
