#!/usr/bin/env python3
"""Build cumulative v6 shadow signals from the canonical v5 history."""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import html
import json
import statistics
from pathlib import Path
from typing import Any, Mapping, Sequence

from largo_close_v6_shadow import VERSION, apply_daily_selection, num, v6_shadow_gate

KST = dt.timezone(dt.timedelta(hours=9))
HISTORY_VERSION = "largo-close-v6-shadow-history-v1"


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def clone(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def prior_for_code(rows: Sequence[Mapping[str, Any]], code: str, signal_date: str) -> list[Mapping[str, Any]]:
    matched = [row for row in rows if str(row.get("code") or "").zfill(6) == code and str(row.get("signal_date") or "") < signal_date and bool(mapping(row.get("v6_shadow")).get("daily_pick"))]
    matched.sort(key=lambda row: str(row.get("signal_date") or ""))
    return matched[-5:]


def result_index(material: Mapping[str, Any]) -> dict[tuple[str, str], Mapping[str, Any]]:
    return {(str(row.get("signal_date") or ""), str(row.get("code") or "").zfill(6)): row for row in material.get("results") or [] if isinstance(row, Mapping)}


def outcome(result: Mapping[str, Any] | None) -> dict[str, Any]:
    if not result:
        return {"outcome_status": "PENDING", "next_date": None, "max_executable_return_pct": None, "last_executable_return_pct": None, "policy_return_pct": None, "hit_3_exec": None}
    maximum, last = num(result.get("max_executable_return_pct")), num(result.get("last_executable_return_pct"))
    policy = 3.0 if maximum is not None and maximum >= 3.0 else last
    return {"outcome_status": "EVALUATED" if maximum is not None and last is not None else "NO_0906_QUOTE", "next_date": result.get("next_date"), "max_executable_return_pct": maximum, "last_executable_return_pct": last, "policy_return_pct": policy, "hit_3_exec": None if maximum is None else maximum >= 3.0}


def stats(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    values = [num(row.get("policy_return_pct")) for row in rows]
    values = [value for value in values if value is not None]
    return {"n": len(rows), "positive": sum(value > 0 for value in values), "loss": sum(value < 0 for value in values), "hit3": sum(bool(row.get("hit_3_exec")) for row in rows), "positive_rate": sum(value > 0 for value in values) / len(values) if values else None, "hit3_rate": sum(bool(row.get("hit_3_exec")) for row in rows) / len(rows) if rows else None, "mean_policy_return_pct": statistics.fmean(values) if values else None, "median_policy_return_pct": statistics.median(values) if values else None, "sum_policy_return_pct": sum(values) if values else None, "worst_policy_return_pct": min(values) if values else None}


def build_history(material: Mapping[str, Any], previous: Mapping[str, Any], now: dt.datetime) -> dict[str, Any]:
    results = result_index(material)
    previous_rows = [row for row in previous.get("signals") or [] if isinstance(row, Mapping)]
    output: list[dict[str, Any]] = []
    signals = sorted((row for row in material.get("signals") or [] if isinstance(row, Mapping)), key=lambda row: str(row.get("signal_date") or ""))
    for signal in signals:
        signal_date = str(signal.get("signal_date") or "")
        candidates: list[dict[str, Any]] = []
        for source in signal.get("candidates") or []:
            if not isinstance(source, Mapping):
                continue
            row = clone(source)
            code = str(row.get("code") or "").zfill(6)
            entry = {"entry_ask": row.get("entry_ask"), "entry_bid": row.get("entry_bid"), "entry_last": row.get("entry_last"), "entry_time": row.get("entry_time")}
            row["v6_shadow"] = v6_shadow_gate(row, entry, prior_signals=prior_for_code([*previous_rows, *output], code, signal_date))
            candidates.append(row)
        apply_daily_selection(candidates)
        for row in candidates:
            shadow = mapping(row.get("v6_shadow"))
            if not shadow.get("daily_pick"):
                continue
            code = str(row.get("code") or "").zfill(6)
            official = bool(not signal.get("proxy") and signal.get("source_valid", True) and signal.get("timely_theme", True))
            output.append({"signal_date": signal_date, "signal_at": signal.get("signal_at"), "captured_at": signal.get("captured_at"), "proxy": bool(signal.get("proxy")), "official_source": official, "code": code, "name": row.get("name"), "market": row.get("market"), "trade_value": row.get("trade_value"), "entry_time": row.get("entry_time"), "entry_last": row.get("entry_last"), "entry_ask": row.get("entry_ask"), "entry_bid": row.get("entry_bid"), "v5": row.get("target3"), "v6_shadow": shadow, "evidence": row.get("evidence"), "theme": row.get("theme"), "structure": row.get("structure"), **outcome(results.get((signal_date, code)))})
    completed = [row for row in output if row.get("outcome_status") == "EVALUATED"]
    official = [row for row in completed if row.get("official_source")]
    proxy = [row for row in completed if not row.get("official_source")]
    return {"version": HISTORY_VERSION, "gate_version": VERSION, "generated_at": now.isoformat(), "research_only": True, "signals": output, "summary": {"signal_days": len(output), "completed": len(completed), "pending": sum(row.get("outcome_status") == "PENDING" for row in output), "official": stats(official), "proxy": stats(proxy), "all_completed": stats(completed), "limitations": ["v5는 정본 비교 기준으로 유지하고 v6는 그림자 신호로만 기록합니다.", "공식 성과는 정확한 15:18 후보·재료·테마·매도·매수호가가 함께 보존된 신호만 포함합니다.", "과거 프록시 신호는 조건 연구용이며 정식 승률에 포함하지 않습니다.", "정책수익은 09:06 전 +3% 도달 시 +3%, 미도달 시 09:05 마지막 최우선 매수호가입니다.", "수수료, 세금, 잔량과 주문 지연은 포함하지 않습니다."]}}


def fmt_pct(value: Any, digits: int = 4) -> str:
    value = num(value)
    return "—" if value is None else f"{value:+.{digits}f}%"


def fmt_price(value: Any) -> str:
    value = num(value)
    return "—" if value is None else f"{value:,.0f}원"


def build_report(history: Mapping[str, Any]) -> str:
    summary = mapping(history.get("summary")); official = mapping(summary.get("official")); all_completed = mapping(summary.get("all_completed"))
    body = []
    for row in sorted((row for row in history.get("signals") or [] if isinstance(row, Mapping)), key=lambda row: str(row.get("signal_date") or ""), reverse=True):
        shadow = mapping(row.get("v6_shadow")); audit = mapping(shadow.get("evidence_audit"))
        result = "결과 대기" if row.get("outcome_status") != "EVALUATED" else "+3% 도달" if row.get("hit_3_exec") else f"09:05 {fmt_pct(row.get('last_executable_return_pct'))}"
        body.append("<tr>" + f"<td>{html.escape(str(row.get('signal_date') or ''))}<small>{'공식' if row.get('official_source') else '프록시'}</small></td>" + f"<td>{html.escape(str(row.get('name') or ''))}<small>{html.escape(str(row.get('code') or ''))}</small></td>" + f"<td>{html.escape(str(shadow.get('lane') or ''))}<small>품질 {shadow.get('quality','—')}</small></td>" + f"<td>{fmt_price(row.get('entry_ask'))}<small>호가차 {fmt_pct(shadow.get('spread_pct'))}</small></td>" + f"<td>{fmt_pct(row.get('max_executable_return_pct'))}</td><td>{result}</td>" + f"<td>{html.escape(str(audit.get('title') or '재료 없음'))}<small>감사 {'통과' if audit.get('passed') else '실패'}</small></td></tr>")
    if not body:
        body.append("<tr><td colspan='7'>아직 v6 그림자 신호가 없습니다.</td></tr>")
    limits = "".join(f"<li>{html.escape(str(item))}</li>" for item in summary.get("limitations") or [])
    return f"""<!doctype html><html lang='ko'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>라르고 종가베팅 v6 그림자 검증</title><style>:root{{--bg:#edf2f7;--paper:#fff;--ink:#16243a;--muted:#66758a;--line:#d7e0ea;--navy:#0c315a;--blue:#286aaa;--amber:#9a6200}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font-family:Arial,'Malgun Gothic',sans-serif;line-height:1.55}}main{{max-width:1320px;margin:auto;padding:20px}}.hero{{padding:28px;border-radius:22px;background:linear-gradient(135deg,var(--navy),var(--blue));color:#fff}}.hero p{{color:#deebf8;max-width:930px}}.cards{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:16px 0}}.card,.section{{background:var(--paper);border:1px solid var(--line);border-radius:18px;padding:16px;margin-bottom:16px}}.card b{{font-size:27px;display:block}}.card span,small{{display:block;color:var(--muted)}}table{{width:100%;border-collapse:collapse;font-size:14px}}th,td{{padding:10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}.warning{{border-left:6px solid var(--amber)}}@media(max-width:900px){{.cards{{grid-template-columns:repeat(2,1fr)}}}}@media(max-width:560px){{main{{padding:10px}}.cards{{grid-template-columns:1fr}}.hero{{padding:20px}}}}</style></head><body><main><section class='hero'><span>읽기 전용 · 주문 기능 없음 · v5 유지</span><h1>라르고 종가베팅 v6 그림자 검증</h1><p>v5 후보를 네 개의 강화 경로로 다시 심사합니다. v6는 전진검증 자료가 쌓일 때까지 매수 기준이 아닙니다.</p></section><section class='cards'><div class='card'><b>{summary.get('signal_days',0)}</b><span>그림자 신호일</span></div><div class='card'><b>{summary.get('completed',0)}</b><span>결과 완료</span></div><div class='card'><b>{official.get('n',0)}</b><span>공식 완료 신호</span></div><div class='card'><b>{fmt_pct(all_completed.get('sum_policy_return_pct'))}</b><span>전체 완료 단순 합계</span></div></section><section class='section'><h2>날짜별 v6 그림자 선택</h2><div style='overflow:auto'><table><thead><tr><th>신호일</th><th>종목</th><th>경로</th><th>15:18 가상 진입</th><th>09:06 전 최고</th><th>정책 결과</th><th>재료 감사</th></tr></thead><tbody>{''.join(body)}</tbody></table></div></section><section class='section'><h2>수정한 조건</h2><p>추세형은 거래대금 소화율 0.30 이상, 테마 확산률 80% 이상, 테마 1·2위, 고가권 마감과 작은 윗꼬리를 함께 요구합니다. 직접 재료형은 후보 회사명과 기사 주체의 일치, 긍정 문맥, 신선도, 당일 과반영 여부를 확인합니다. 같은 종목의 같은 재료는 최근 5개 신호 안에서 다시 선택하지 않습니다.</p></section><section class='section warning'><h2>해석 제한</h2><ul>{limits}</ul></section></main></body></html>"""


def write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    fields = ["signal_date", "next_date", "official_source", "code", "name", "lane", "quality", "entry_ask", "entry_bid", "spread_pct", "risk_rate", "change_rate", "digest_ratio", "theme_breadth", "leader_rank", "directness_points", "freshness_points", "evidence_audit_pass", "evidence_title", "max_executable_return_pct", "last_executable_return_pct", "policy_return_pct", "hit_3_exec", "outcome_status"]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader()
        for row in rows:
            shadow, audit = mapping(row.get("v6_shadow")), mapping(mapping(row.get("v6_shadow")).get("evidence_audit"))
            writer.writerow({"signal_date": row.get("signal_date"), "next_date": row.get("next_date"), "official_source": row.get("official_source"), "code": row.get("code"), "name": row.get("name"), "lane": shadow.get("lane"), "quality": shadow.get("quality"), "entry_ask": row.get("entry_ask"), "entry_bid": row.get("entry_bid"), "spread_pct": shadow.get("spread_pct"), "risk_rate": shadow.get("risk_rate"), "change_rate": shadow.get("change_rate"), "digest_ratio": shadow.get("digest_ratio"), "theme_breadth": shadow.get("theme_breadth"), "leader_rank": shadow.get("leader_rank"), "directness_points": shadow.get("directness_points"), "freshness_points": shadow.get("freshness_points"), "evidence_audit_pass": audit.get("passed"), "evidence_title": audit.get("title"), "max_executable_return_pct": row.get("max_executable_return_pct"), "last_executable_return_pct": row.get("last_executable_return_pct"), "policy_return_pct": row.get("policy_return_pct"), "hit_3_exec": row.get("hit_3_exec"), "outcome_status": row.get("outcome_status")})


def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("--material-history", required=True); parser.add_argument("--previous-history"); parser.add_argument("--output-history", required=True); parser.add_argument("--output-summary", required=True); parser.add_argument("--output-csv", required=True); parser.add_argument("--output-report", required=True); parser.add_argument("--now"); args = parser.parse_args()
    now = dt.datetime.fromisoformat(args.now).astimezone(KST) if args.now else dt.datetime.now(KST)
    history = build_history(load_json(Path(args.material_history), {}), load_json(Path(args.previous_history), {}) if args.previous_history else {}, now)
    for path, value in ((Path(args.output_history), history), (Path(args.output_summary), history.get("summary") or {})):
        path.parent.mkdir(parents=True, exist_ok=True); path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csv(Path(args.output_csv), history.get("signals") or [])
    Path(args.output_report).parent.mkdir(parents=True, exist_ok=True); Path(args.output_report).write_text(build_report(history), encoding="utf-8")
    print(json.dumps({"version": history.get("version"), "gate": history.get("gate_version"), "signals": len(history.get("signals") or []), "summary": history.get("summary")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
