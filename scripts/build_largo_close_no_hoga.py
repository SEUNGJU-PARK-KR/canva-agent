#!/usr/bin/env python3
"""Build a self-contained Largo win-rate v4.1 research page."""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
from typing import Any, Mapping

from largo_winrate_v4 import (
    KST, VERSION, analyze_candidate, append_history, atomic_json, big_won, esc,
    load_history, pct_point, pct_ratio, phase, source_timestamp, won,
)

LABELS = {
    "CLOSE_ENTRY": "종가 보유 검토",
    "CLOSE_READY": "종가 보유 준비",
    "CLOSE_VALIDATED": "종가 통과 기록",
    "NEXT_DAY_CONFIRM": "익일 확인형",
    "WATCH": "관찰",
    "EXCLUDE": "제외",
}
RANK = {
    "CLOSE_ENTRY": 6, "CLOSE_READY": 5, "NEXT_DAY_CONFIRM": 4,
    "CLOSE_VALIDATED": 3, "WATCH": 2, "EXCLUDE": 0,
}


def rule_list(rules: list[Mapping[str, Any]]) -> str:
    return "".join(
        f"<li><b>{esc(item.get('name'))}</b><span class='{'PASS' if item.get('pass') else 'FAIL'}'>{'PASS' if item.get('pass') else 'FAIL'}</span><small>{esc(item.get('detail'))}</small></li>"
        for item in rules
    )


def check_list(checks: list[Mapping[str, Any]]) -> str:
    return "".join(
        f"<li><b>{esc(item.get('name'))}</b><span class='{esc('PENDING' if not item.get('active') else item.get('status'))}'>{esc('PENDING' if not item.get('active') else item.get('status'))}</span><small>{esc('관측 시간 전' if not item.get('active') else item.get('reason'))}</small></li>"
        for item in checks
    )


def issue_text(row: Mapping[str, Any]) -> str:
    if row.get("hard_failures"):
        return "<br>".join(f"{esc(item.get('name'))}: {esc(item.get('reason'))}" for item in row["hard_failures"])
    if row.get("hard_unresolved"):
        return "<br>".join(f"{esc(item.get('name'))}: {esc(item.get('reason'))}" for item in row["hard_unresolved"])
    failed_close = [item for item in row.get("close_rules") or [] if not item.get("pass")]
    if row.get("lane") == "NEXT_DAY_CONFIRM":
        return "종가 보유 조건은 부족합니다. 다음 날 전일 종가 회복을 확인하는 연구 경로입니다."
    if failed_close:
        return "<br>".join(f"{esc(item.get('name'))}: {esc(item.get('detail'))}" for item in failed_close[:4])
    return "명시한 조건을 통과했습니다."


def card(row: Mapping[str, Any]) -> str:
    lane = str(row.get("lane") or "WATCH")
    features = row.get("features") or {}
    risk = row.get("risk") or {}
    fresh = row.get("freshness") or {}
    persist = row.get("persistence") or {}
    catalyst = row.get("catalyst") or {}
    plan = row.get("plans") or {}
    selected_plan = plan.get("close_entry") if lane in {"CLOSE_ENTRY", "CLOSE_READY", "CLOSE_VALIDATED"} else plan.get("next_day_confirm")
    selected_plan = selected_plan or {}
    if lane == "NEXT_DAY_CONFIRM":
        plan_text = (
            f"전일 종가 {won(selected_plan.get('reference_close'))} 회복과 09:05·09:15 유지 확인 · "
            f"갭 +{selected_plan.get('no_chase_gap_pct', 3):.0f}% 이상 추격 금지 · "
            f"1차 +1% 50% · 2차 +2% 30% · 구조선 {won(selected_plan.get('cancel_below'))} 아래 취소"
        )
    else:
        plan_text = (
            f"진입 기준 {won(selected_plan.get('entry'))} · 구조선 {won(selected_plan.get('stop'))} · "
            f"+1% {won(selected_plan.get('target1'))}에서 50% · +2% {won(selected_plan.get('target2'))}에서 30% · "
            f"잔여 20% 추적"
        )
    fresh_label = {"FRESH": "24시간 이내", "RECENT": "72시간 이내", "STALE": "72시간 초과", "UNKNOWN": "시각 미확인"}.get(str(fresh.get("status")), "시각 미확인")
    variant_label = {"CORE": "핵심형", "ELITE": "초강한 마감형"}.get(str(row.get("close_variant") or ""), "-")
    breadth = features.get("theme_breadth")
    details = rule_list(list(row.get("close_rules") or [])) if lane in {"CLOSE_ENTRY", "CLOSE_READY", "CLOSE_VALIDATED"} else rule_list(list(row.get("confirm_rules") or []))
    return f"""
<article class="candidate {esc(lane)}" data-lane="{esc(lane)}" data-text="{esc(row.get('name'))} {esc(row.get('code'))} {esc(catalyst.get('reason'))} {esc(features.get('pattern_name'))}">
<header><div><h3>{esc(row.get('name'))}</h3><p>{esc(row.get('code'))} · {esc(features.get('pattern_id'))} {esc(features.get('pattern_name'))}</p></div><span class="badge">{esc(LABELS.get(lane, lane))}</span></header>
<div class="score"><b>{row.get('close_rule_passes')}/{row.get('close_rule_count')}</b><span>종가 보유 조건</span><small>익일 확인 조건 {row.get('confirm_rule_passes')}/{row.get('confirm_rule_count')} · 연속 {persist.get('consecutive_count', 0)}회</small></div>
<div class="metrics"><div><b>{won(row.get('price'))}</b><span>기준가</span></div><div><b>{pct_point(row.get('change_rate'))}</b><span>당일 등락</span></div><div><b>{features.get('close_location') if features.get('close_location') is not None else '-'}</b><span>종가 위치</span></div><div><b>{pct_ratio(risk.get('rate'))}</b><span>구조 위험</span></div></div>
<div class="tags"><span>재료 {esc(features.get('grade'))} · {esc(catalyst.get('reason') or '확인 정보 없음')}</span><span>종가 경로 {esc(variant_label)}</span><span>신선도 {esc(fresh_label)}</span><span>테마 확산 {esc(f"{breadth:.0%}" if breadth is not None else '-')}</span><span>주도 순위 {esc(features.get('rank') if features.get('rank') is not None else '-')}</span><span>매물 소화 {esc(f"{features.get('digest_ratio'):.2f}" if features.get('digest_ratio') is not None else '-')}</span><span>거래대금 {esc(big_won(row.get('trade_value')))}</span></div>
<p class="issue">{issue_text(row)}</p>
<p class="plan">{esc(plan_text)}</p>
<details><summary>적용 조건 확인</summary><ul class="checks">{details}</ul></details>
<details><summary>원래 필수 게이트</summary><ul class="checks">{check_list(list(row.get('checks') or []))}</ul></details>
</article>"""


def empty(text: str) -> str:
    return f"<div class='empty'>{esc(text)}</div>"


def render(latest: Mapping[str, Any], rows: list[dict[str, Any]], source_at: dt.datetime | None, methodology: Mapping[str, Any]) -> str:
    phase_info = phase(source_at)
    close_rows = [row for row in rows if row["lane"] in {"CLOSE_ENTRY", "CLOSE_READY", "CLOSE_VALIDATED"}]
    confirm_rows = [row for row in rows if row["lane"] == "NEXT_DAY_CONFIRM"]
    other_rows = [row for row in rows if row["lane"] in {"WATCH", "EXCLUDE"}]
    close_cards = "".join(card(row) for row in close_rows) or empty("종가 보유 조건을 통과한 후보가 없습니다.")
    confirm_cards = "".join(card(row) for row in confirm_rows) or empty("익일 확인형 후보가 없습니다.")
    table_rows = "".join(
        f"<tr data-lane='{esc(row['lane'])}' data-text='{esc(row['name'])} {esc(row['code'])} {esc((row.get('catalyst') or {}).get('reason'))}'><td><span class='mini {esc(row['lane'])}'>{esc(LABELS.get(row['lane'], row['lane']))}</span></td><td><b>{esc(row['name'])}</b><small>{esc(row['code'])}</small></td><td>{won(row['price'])}</td><td>{row['close_rule_passes']}/{row['close_rule_count']}</td><td>{row['confirm_rule_passes']}/{row['confirm_rule_count']}</td><td>{esc((row.get('freshness') or {}).get('status'))}</td><td>{esc((row.get('features') or {}).get('rank'))}</td><td>{esc((row.get('features') or {}).get('pattern_id'))}</td><td>{pct_ratio((row.get('risk') or {}).get('rate'))}</td><td>{issue_text(row)}</td></tr>"
        for row in rows
    )
    principle = str(methodology.get("principle") or "필수 게이트를 통과한 종목끼리만 우선순위를 비교합니다.")
    source_text = source_at.isoformat() if source_at else str(latest.get("generated_at") or "-")
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="referrer" content="no-referrer"><title>라르고 종가베팅 승률 개선 v4.1 · 호가 조건 제외</title><style>
:root{{--bg:#eef2f7;--paper:#fff;--ink:#132039;--muted:#68778d;--line:#d9e2ed;--blue:#175fc2;--green:#117448;--amber:#9a6200;--red:#b43242;--purple:#6847a6}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font-family:Arial,'Malgun Gothic',sans-serif;line-height:1.55}}.shell{{max-width:1500px;margin:auto;padding:20px}}.hero{{background:linear-gradient(135deg,#0d274d,#164a87);color:#fff;border-radius:24px;padding:28px}}.hero h1{{margin:8px 0;font-size:34px}}.hero p{{max-width:1050px;color:#d8e7fb}}.meta,.stats,.metrics{{display:grid;grid-template-columns:repeat(4,1fr);gap:9px}}.meta div{{background:#ffffff18;border:1px solid #ffffff25;border-radius:12px;padding:10px}}.meta b,.meta span,.metrics b,.metrics span{{display:block}}.notice{{margin:16px 0;padding:14px 16px;background:#fff7e8;border:1px solid #e7c77d;border-radius:14px;color:#674600}}.notice.red{{background:#fff0f2;border-color:#e5b6bd;color:#7b2430}}.stats>div,.section{{background:#fff;border:1px solid var(--line);border-radius:18px;padding:16px;margin-bottom:16px}}.stats b{{font-size:30px;display:block}}.toolbar{{position:sticky;top:0;z-index:5;background:#fffffff2;padding:10px;border:1px solid var(--line);border-radius:14px;display:flex;gap:8px;flex-wrap:wrap;margin-bottom:16px}}button,input{{font:inherit;padding:9px 11px;border:1px solid var(--line);border-radius:10px;background:#fff}}input{{flex:1;min-width:220px}}button.active{{background:var(--blue);color:#fff}}.grid{{display:grid;grid-template-columns:repeat(2,1fr);gap:12px}}.candidate{{border:1px solid var(--line);border-radius:16px;overflow:hidden;background:#fff}}.candidate header{{display:flex;justify-content:space-between;gap:12px;padding:14px;border-bottom:1px solid var(--line)}}.candidate h3{{margin:0}}.candidate header p{{margin:3px 0 0;color:var(--muted);font-size:11px}}.badge,.mini{{display:inline-flex;padding:5px 8px;border-radius:999px;font-size:10px;font-weight:800;white-space:nowrap}}.CLOSE_ENTRY{{border-color:#78bf9e}}.CLOSE_ENTRY .badge,.mini.CLOSE_ENTRY{{background:#e8f7ef;color:var(--green)}}.CLOSE_READY{{border-color:#9bbce9}}.CLOSE_READY .badge,.mini.CLOSE_READY{{background:#eaf2ff;color:#1b58a5}}.NEXT_DAY_CONFIRM{{border-color:#b7a4dc}}.NEXT_DAY_CONFIRM .badge,.mini.NEXT_DAY_CONFIRM{{background:#f0ebfa;color:var(--purple)}}.WATCH .badge,.CLOSE_VALIDATED .badge,.mini.WATCH,.mini.CLOSE_VALIDATED{{background:#fff5df;color:var(--amber)}}.EXCLUDE{{border-color:#e8bcc3}}.EXCLUDE .badge,.mini.EXCLUDE{{background:#ffedf0;color:var(--red)}}.score{{display:grid;grid-template-columns:auto 1fr;gap:3px 10px;padding:11px 14px;background:#f7f9fc}}.score b{{font-size:25px;color:var(--blue);grid-row:1/3}}.score span{{font-weight:800}}.score small{{color:var(--muted)}}.metrics{{padding:12px 14px}}.metrics div{{background:#f7f9fc;border-radius:10px;padding:8px}}.metrics span{{font-size:9px;color:var(--muted)}}.tags{{display:flex;gap:6px;flex-wrap:wrap;padding:0 14px}}.tags span{{background:#eff3f8;border-radius:999px;padding:5px 7px;font-size:10px}}.issue,.plan{{margin:11px 14px;padding:10px;border-radius:10px;font-size:11px}}.issue{{background:#fff7e8}}.plan{{background:#eef5ff}}details{{margin:10px 14px 14px}}summary{{cursor:pointer;font-weight:800}}.checks{{list-style:none;padding:0}}.checks li{{display:grid;grid-template-columns:1fr auto;gap:6px;border-bottom:1px solid var(--line);padding:7px 0}}.checks small{{grid-column:1/-1;color:var(--muted)}}.PASS{{color:var(--green)}}.FAIL{{color:var(--red)}}.WARN,.MISSING,.UNKNOWN,.PENDING{{color:var(--amber)}}.empty{{padding:30px;text-align:center;border:1px dashed #b9c7d9;border-radius:14px;color:var(--muted)}}.tablewrap{{overflow:auto}}table{{width:100%;border-collapse:collapse;font-size:11px}}th,td{{border-bottom:1px solid var(--line);padding:8px;text-align:left;vertical-align:top}}td small{{display:block;color:var(--muted)}}.method{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}}.method div{{border:1px solid var(--line);border-radius:13px;padding:12px}}.method p{{font-size:11px;color:var(--muted)}}@media(max-width:850px){{.grid{{grid-template-columns:1fr}}.meta,.stats,.metrics{{grid-template-columns:repeat(2,1fr)}}.method{{grid-template-columns:1fr}}.hero h1{{font-size:27px}}}}
</style></head><body><main class="shell"><section class="hero"><span>읽기 전용 · 주문 기능 없음 · 호가 판정 미사용</span><h1>라르고 종가베팅 승률 개선 v4.1</h1><p>승리 거래를 늘리기 위해 종목 수보다 청산 구조를 먼저 바꿨습니다. 종가 보유형은 핵심형과 초강한 마감형으로 제한하고, 더 넓은 강한 종목군은 다음 날 전일 종가 회복을 확인합니다. 보유형은 다음 날 1파에서 +1% 50%, +2% 30%를 분할 청산합니다.</p><div class="meta"><div><b>기준 시각</b><span>{esc(source_text)}</span></div><div><b>판정 단계</b><span>{esc(phase_info['title'])}</span></div><div><b>핵심형</b><span>주도 3위 + 소화 0.45</span></div><div><b>초강한 마감형</b><span>테마 75% + 최상단 종가</span></div></div></section>
<div class="notice red"><b>기준 확인:</b> 주문·계좌 기능은 없습니다. 파일럿 2거래일 결과는 확률이 아닙니다. 종가 보유 조건을 통과하지 못한 종목은 점수가 높아도 종가 매수 대상으로 표시하지 않습니다.</div><div class="notice"><b>운영 원칙:</b> {esc(principle)} 종가 보유형은 +1%와 +2%에서 분할 청산합니다. 익일 확인형은 갭 +3% 이상을 추격하지 않으며, 전일 종가 회복과 09:05·09:15 유지가 확인되지 않으면 거래하지 않습니다.</div>
<section class="stats"><div><b>{sum(1 for row in rows if row['lane']=='CLOSE_ENTRY')}</b><span>종가 보유 검토</span></div><div><b>{sum(1 for row in rows if row['lane'] in {'CLOSE_READY','CLOSE_VALIDATED'})}</b><span>보유 준비·검증</span></div><div><b>{len(confirm_rows)}</b><span>익일 확인형</span></div><div><b>{sum(1 for row in rows if row['lane']=='EXCLUDE')}</b><span>제외</span></div></section>
<nav class="toolbar"><input id="q" placeholder="종목명·코드·재료 검색"><button class="active" data-filter="ALL">전체</button><button data-filter="CLOSE">종가 보유</button><button data-filter="CONFIRM">익일 확인</button><button data-filter="WATCH">관찰</button><button data-filter="EXCLUDE">제외</button></nav>
<section class="section"><h2>종가 보유형</h2><p>15시 18분 이후 반복 유지까지 확인된 핵심형 또는 초강한 마감형만 종가 단일가에서 검토합니다. 다음 날 +1%와 +2%를 1파 청산선으로 사용합니다.</p><div class="grid">{close_cards}</div></section>
<section class="section"><h2>익일 확인형</h2><p>강한 마감 구조지만 종가 보유 기준은 부족한 종목입니다. 종가에 보유하지 않고 다음 날 전일 종가 회복과 09:05·09:15 유지를 확인합니다.</p><div class="grid">{confirm_cards}</div></section>
<section class="section"><h2>전체 판정표</h2><div class="tablewrap"><table><thead><tr><th>경로</th><th>종목</th><th>가격</th><th>종가 조건</th><th>확인 조건</th><th>신선도</th><th>주도 순위</th><th>패턴</th><th>위험</th><th>핵심 사유</th></tr></thead><tbody>{table_rows}</tbody></table></div></section>
<section class="section"><h2>승리 거래를 늘리는 구조</h2><div class="method"><div><b>1파를 승리로 잠금</b><p>종가 보유형은 +1%에서 50%, +2%에서 30%를 청산합니다. 종가까지 보유하며 장중 수익을 반납하는 문제를 줄입니다.</p></div><div><b>두 개의 보유 경로</b><p>핵심형은 주도 3위·소화 0.45를 요구합니다. 초강한 마감형은 테마 확산 75%·주도 2위·종가 위치 0.95로 소화 0.30 예외를 허용합니다.</p></div><div><b>확인형으로 기회 확대</b><p>강하지만 보유 기준이 부족한 종목은 다음 날 전일 종가 회복 뒤에만 검토합니다. 갭 +3% 이상은 추격하지 않습니다.</p></div></div></section>
</main><script>const q=document.getElementById('q');let filter='ALL';function apply(){{const term=q.value.toLowerCase();document.querySelectorAll('article.candidate,tbody tr').forEach(el=>{{const lane=el.dataset.lane||'';const text=(el.dataset.text||'').toLowerCase();let ok=!term||text.includes(term);if(filter==='CLOSE')ok=ok&&['CLOSE_ENTRY','CLOSE_READY','CLOSE_VALIDATED'].includes(lane);if(filter==='CONFIRM')ok=ok&&lane==='NEXT_DAY_CONFIRM';if(filter==='WATCH')ok=ok&&['WATCH','CLOSE_VALIDATED'].includes(lane);if(filter==='EXCLUDE')ok=ok&&lane==='EXCLUDE';el.hidden=!ok;}})}}q.addEventListener('input',apply);document.querySelectorAll('button[data-filter]').forEach(btn=>btn.addEventListener('click',()=>{{document.querySelectorAll('button[data-filter]').forEach(x=>x.classList.remove('active'));btn.classList.add('active');filter=btn.dataset.filter;apply();}}));</script></body></html>"""


def build(args: argparse.Namespace) -> dict[str, Any]:
    latest = json.loads(args.latest.read_text(encoding="utf-8"))
    methodology = json.loads(args.methodology.read_text(encoding="utf-8"))
    raw_rows = [item for item in latest.get("candidates") or [] if isinstance(item, Mapping)]
    source_at = source_timestamp(latest, raw_rows)
    history = load_history(args.history)
    rows = [analyze_candidate(item, source_at, history) for item in raw_rows]
    rows.sort(key=lambda row: (RANK.get(row["lane"], 0), row["close_rule_passes"], row["confirm_rule_passes"], row["quality"]), reverse=True)
    result = {
        "version": VERSION, "strategy_version": latest.get("strategy_version"),
        "generated_at": dt.datetime.now(KST).isoformat(),
        "source_at": source_at.isoformat() if source_at else None,
        "phase": phase(source_at),
        "counts": {lane: sum(1 for row in rows if row["lane"] == lane) for lane in LABELS},
        "candidates": rows,
        "pilot_evidence": {
            "evaluated_days": 2,
            "close_lane_examples": 3,
            "close_lane_clear_1pct_wins": 3,
            "close_lane_clear_2pct_wins": 3,
            "close_lane_positive_next_close": 2,
            "confirmation_only_examples": 1,
            "warning": "같은 2거래일 표본에서 규칙을 탐색하고 평가한 파일럿이며 외부 검증이 아닙니다.",
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render(latest, rows, source_at, methodology), encoding="utf-8")
    if args.json_out:
        atomic_json(args.json_out, result)
    updated_history = append_history(history, source_at, rows)
    if args.history_out:
        atomic_json(args.history_out, updated_history)
    if args.signals_out:
        signals = {
            "version": VERSION, "signal_at": source_at.isoformat() if source_at else None,
            "market_date": source_at.date().isoformat() if source_at else None,
            "candidates": [
                {
                    "code": row["code"], "name": row["name"], "lane": row["lane"],
                    "close_variant": row.get("close_variant"),
                    "signal_price": row["price"], "risk": row["risk"], "plans": row["plans"],
                    "features": row["features"], "freshness": row["freshness"],
                }
                for row in rows if row["lane"] in {"CLOSE_ENTRY", "CLOSE_READY", "NEXT_DAY_CONFIRM"}
            ],
        }
        atomic_json(args.signals_out, signals)
    return result


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("--latest", type=Path, required=True)
    result.add_argument("--methodology", type=Path, required=True)
    result.add_argument("--history", type=Path)
    result.add_argument("--output", type=Path, required=True)
    result.add_argument("--json-out", type=Path)
    result.add_argument("--history-out", type=Path)
    result.add_argument("--signals-out", type=Path)
    return result


if __name__ == "__main__":
    result = build(parser().parse_args())
    print(json.dumps({"counts": result["counts"], "source_at": result["source_at"]}, ensure_ascii=False))
