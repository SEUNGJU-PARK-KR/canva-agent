#!/usr/bin/env python3
"""Generate a self-contained Largo close-betting page without orderbook gates."""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
from pathlib import Path
from typing import Any, Mapping

KST = dt.timezone(dt.timedelta(hours=9))
HOGA_IDS = {"BEST_BID_HOLD_AUTO", "H_ABSORPTION", "H_LIQUIDITY"}
MAX_RISK = 0.06
RANK = {"STRICT": 2, "CONDITIONAL": 1, "EXCLUDE": 0}


def num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def won(value: Any) -> str:
    n = num(value)
    return "-" if n is None else f"{n:,.0f}원"


def pct(value: Any, digits: int = 2) -> str:
    n = num(value)
    return "-" if n is None else f"{n * 100:.{digits}f}%"


def big_won(value: Any) -> str:
    n = num(value)
    if n is None:
        return "-"
    if abs(n) >= 1_0000_0000_0000:
        return f"{n / 1_0000_0000_0000:.2f}조원"
    if abs(n) >= 1_0000_0000:
        return f"{n / 1_0000_0000:.0f}억원"
    return won(n)


def source_timestamp(rows: list[Mapping[str, Any]]) -> str | None:
    values: list[str] = []
    for row in rows:
        automation = row.get("automation_356")
        if not isinstance(automation, Mapping):
            continue
        best_bid = automation.get("best_bid")
        if not isinstance(best_bid, Mapping):
            continue
        for snap in best_bid.get("snapshots") or []:
            if isinstance(snap, Mapping) and snap.get("source_at"):
                values.append(str(snap["source_at"]))
    return max(values) if values else None


def phase(source_at: str | None, built_at: dt.datetime) -> tuple[str, str, str]:
    if not source_at:
        return "UNKNOWN", "기준 시각 확인 불가", "기준 시각을 확인하지 못했습니다."
    try:
        parsed = dt.datetime.fromisoformat(source_at)
    except ValueError:
        return "UNKNOWN", "기준 시각 확인 불가", "기준 시각을 확인하지 못했습니다."
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=KST)
    local = parsed.astimezone(KST)
    shown = local.strftime("%Y-%m-%d %H:%M")
    if local.date() < built_at.date():
        return "HISTORICAL_CLOSE", f"{local:%Y-%m-%d} 종가 스냅샷", f"{shown} 기준 과거 종가 스냅샷입니다. 오늘 추천이 아닙니다."
    if local.time() >= dt.time(15, 30):
        return "FINAL_CLOSE", f"{local:%Y-%m-%d} 종가 확정", f"{shown} 종가 확정 데이터를 판정했습니다."
    return "INTRADAY_PROVISIONAL", f"{shown} 장중 잠정", f"{shown} 장중 잠정 판정입니다. 15:30 종가 확정 전에는 추천으로 보지 않습니다."


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
        price = num(item.get("price"))
        if entry and price and 0 < price < entry:
            supports.append((price, str(item.get("name") or "구조선")))
    if not entry or not supports:
        return {"status": "FAIL", "entry": entry, "stop": None, "stop_source": "구조선", "rate": None, "one_r": None, "reason": "유효한 진입가 아래 구조선을 확인하지 못했습니다."}
    stop, name = max(supports)
    rate = (entry - stop) / entry
    return {"status": "PASS" if rate <= MAX_RISK else "FAIL", "entry": entry, "stop": stop, "stop_source": name, "rate": rate, "one_r": entry + (entry - stop), "reason": f"가장 가까운 구조선까지 위험거리는 {rate:.2%}입니다."}


def classify(row: Mapping[str, Any]) -> dict[str, Any]:
    checks: list[dict[str, str]] = []
    for item in row.get("checks") or []:
        if not isinstance(item, Mapping):
            continue
        check_id = str(item.get("id") or "")
        if check_id in HOGA_IDS:
            continue
        checks.append({
            "id": check_id,
            "name": str(item.get("name") or check_id or "조건"),
            "role": str(item.get("role") or "required"),
            "status": str(item.get("status") or "MISSING").upper(),
            "reason": str(item.get("reason") or item.get("rule") or "세부 사유 없음"),
        })
    required = [c for c in checks if c["role"] == "required"]
    failures = [c for c in required if c["status"] == "FAIL"]
    warnings = [c for c in required if c["status"] in {"WARN", "MISSING", "UNKNOWN"}]
    risk = risk_plan(row)
    if risk["status"] != "PASS":
        failures.append({"id": "RISK_DISTANCE_STRICT", "name": "위험거리 6% 이내", "role": "required", "status": "FAIL", "reason": risk["reason"]})
    status = "EXCLUDE" if failures else "CONDITIONAL" if warnings else "STRICT"
    pattern = row.get("pattern") if isinstance(row.get("pattern"), Mapping) else {}
    catalyst = row.get("catalyst") if isinstance(row.get("catalyst"), Mapping) else {}
    return {
        "status": status,
        "code": str(row.get("code") or ""),
        "name": str(row.get("name") or row.get("code") or "종목"),
        "price": num(row.get("price")),
        "change_rate": num(row.get("change_rate")),
        "quality": num(row.get("quality_score")) or 0,
        "trade_value": num(row.get("trade_value")),
        "market_cap": num(row.get("market_cap")),
        "source_status": str(row.get("final_status") or "UNKNOWN"),
        "catalyst_grade": str(catalyst.get("grade") or "-"),
        "catalyst_reason": str(catalyst.get("reason") or "확인 정보 없음"),
        "pattern_id": str(pattern.get("id") or "-"),
        "pattern_name": str(pattern.get("name") or "미분류"),
        "pattern_score": num(pattern.get("score")) or 0,
        "initial_size": str((row.get("plan") or {}).get("initial_size") if isinstance(row.get("plan"), Mapping) else pattern.get("initial") or "-"),
        "risk": risk,
        "checks": required,
        "failures": failures,
        "warnings": warnings,
    }


def card(row: Mapping[str, Any], provisional: bool) -> str:
    risk = row["risk"]
    issues = row["failures"] if row["status"] == "EXCLUDE" else row["warnings"]
    issue_text = "<br>".join(f"{esc(x['name'])}: {esc(x['reason'])}" for x in issues) or "호가를 제외한 모든 필수 게이트를 통과했습니다."
    label = {"STRICT": "잠정 엄격 후보" if provisional else "엄격 추천", "CONDITIONAL": "잠정 조건부" if provisional else "조건부 관찰", "EXCLUDE": "제외"}[row["status"]]
    change = "-" if row["change_rate"] is None else f"{row['change_rate']:+.2f}%"
    one_r = risk["one_r"]
    next_plan = "유효한 진입·손절 계획 없음"
    if risk["status"] == "PASS" and risk["entry"] and risk["stop"]:
        entry = risk["entry"]
        stop = risk["stop"]
        next_plan = f"갭상승 추격 금지 · 보합 시 {entry:,.0f}원 재지지 확인 · 갭하락 시 {entry:,.0f}원 회복 실패 또는 {stop:,.0f}원 이탈 시 종료"
    checks = "".join(f"<li><b>{esc(x['name'])}</b><span class='{esc(x['status'])}'>{esc(x['status'])}</span><small>{esc(x['reason'])}</small></li>" for x in row["checks"])
    return f"""
<article class="candidate {esc(row['status'])}" data-text="{esc(row['name'])} {esc(row['code'])} {esc(row['catalyst_reason'])} {esc(row['pattern_name'])}">
  <header><div><h3>{esc(row['name'])}</h3><p>{esc(row['code'])} · 원래 상태 {esc(row['source_status'])}</p></div><span class="badge {esc(row['status'])}">{esc(label)}</span></header>
  <div class="metrics"><div><b>{won(row['price'])}</b><span>기준 가격</span></div><div><b>{esc(change)}</b><span>등락률</span></div><div><b>{row['quality']:.1f}</b><span>품질</span></div><div><b>{row['pattern_score']:.0f}</b><span>패턴</span></div></div>
  <div class="tags"><span>재료 {esc(row['catalyst_grade'])} · {esc(row['catalyst_reason'])}</span><span>{esc(row['pattern_id'])} {esc(row['pattern_name'])}</span><span>거래대금 {esc(big_won(row['trade_value']))}</span><span>초기 비중 {esc(row['initial_size'])}</span></div>
  <p class="issue">{issue_text}</p>
  <div class="risk"><div><b>{won(risk['entry'])}</b><span>진입 기준</span></div><div><b>{won(risk['stop'])}</b><span>{esc(risk['stop_source'])}</span></div><div><b>{pct(risk['rate'])}</b><span>위험거리</span></div><div><b>{won(one_r)}</b><span>1R</span></div></div>
  <details><summary>필수 조건과 익일 가격 계획</summary><ul class="checks">{checks}</ul><p class="next">{esc(next_plan)}</p></details>
</article>"""


def build(latest_path: Path, methodology_path: Path, output_path: Path) -> dict[str, Any]:
    latest = json.loads(latest_path.read_text(encoding="utf-8"))
    methodology = json.loads(methodology_path.read_text(encoding="utf-8"))
    raw_rows = [x for x in (latest.get("candidates") or []) if isinstance(x, Mapping)]
    built_at = dt.datetime.now(KST)
    source_at = source_timestamp(raw_rows)
    phase_id, phase_label, warning = phase(source_at, built_at)
    rows = [classify(x) for x in raw_rows]
    rows.sort(key=lambda x: (RANK[x["status"]], x["quality"], x["pattern_score"], -(x["risk"]["rate"] or 99)), reverse=True)
    counts = {key: sum(1 for x in rows if x["status"] == key) for key in RANK}
    provisional = phase_id == "INTRADAY_PROVISIONAL"
    strict_cards = "".join(card(x, provisional) for x in rows if x["status"] == "STRICT") or "<div class='empty'><b>엄격 추천 종목이 없습니다.</b><p>경고를 임의로 통과시키지 않았습니다.</p></div>"
    conditional_cards = "".join(card(x, provisional) for x in rows if x["status"] == "CONDITIONAL") or "<div class='empty'><b>조건부 관찰 종목이 없습니다.</b></div>"
    table_rows = "".join(
        f"<tr data-status='{esc(x['status'])}' data-text='{esc(x['name'])} {esc(x['code'])} {esc(x['catalyst_reason'])}'><td><span class='badge {esc(x['status'])}'>{esc(x['status'])}</span></td><td><b>{esc(x['name'])}</b><small>{esc(x['code'])}</small></td><td>{won(x['price'])}</td><td>{x['quality']:.1f}</td><td>{esc(x['catalyst_grade'])} · {esc(x['catalyst_reason'])}</td><td>{esc(x['pattern_id'])} {esc(x['pattern_name'])}</td><td>{won(x['risk']['entry'])}</td><td>{won(x['risk']['stop'])}</td><td>{pct(x['risk']['rate'])}</td><td>{esc((x['failures'] or x['warnings'] or [{'reason':'통과'}])[0]['reason'])}</td></tr>"
        for x in rows
    )
    workflow = [x for x in (methodology.get("workflow") or []) if isinstance(x, Mapping) and str(x.get("id")) != "H"]
    stages = "".join(f"<div><i>{esc(x.get('id'))}</i><b>{esc(x.get('name'))}</b><span>{esc(x.get('rule'))}</span></div>" for x in workflow)
    title_status = "잠정 후보" if provisional else "엄격 추천"
    source_text = source_at or str(latest.get("market_date") or "-")
    html_text = f"""<!doctype html><html lang='ko'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='referrer' content='no-referrer'><title>라르고 종가베팅 {title_status} · 호가 조건 제외</title><style>
:root{{--bg:#eef2f7;--paper:#fff;--ink:#132039;--muted:#68778d;--line:#d9e2ed;--blue:#175fc2;--green:#117448;--amber:#9a6200;--red:#b43242}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font-family:Arial,'Malgun Gothic',sans-serif;line-height:1.55}}.shell{{max-width:1500px;margin:auto;padding:20px}}.hero{{background:linear-gradient(135deg,#0d274d,#164a87);color:#fff;border-radius:24px;padding:28px}}.hero h1{{margin:10px 0;font-size:34px}}.hero p{{color:#d8e7fb}}.meta,.stats,.metrics,.risk{{display:grid;grid-template-columns:repeat(4,1fr);gap:9px}}.meta div{{background:#ffffff18;border:1px solid #ffffff25;border-radius:12px;padding:10px}}.meta b,.meta span,.metrics b,.metrics span,.risk b,.risk span{{display:block}}.notice{{margin:16px 0;padding:13px 15px;background:#fff7e8;border:1px solid #e7c77d;border-radius:14px;color:#674600}}.stats>div,.section{{background:#fff;border:1px solid var(--line);border-radius:18px;padding:16px;margin-bottom:16px}}.stats b{{font-size:30px;display:block}}.toolbar{{position:sticky;top:0;z-index:5;background:#fffffff2;padding:10px;border:1px solid var(--line);border-radius:14px;display:flex;gap:8px;flex-wrap:wrap;margin-bottom:16px}}button,input{{font:inherit;padding:9px 11px;border:1px solid var(--line);border-radius:10px;background:#fff}}input{{flex:1;min-width:220px}}button.active{{background:var(--blue);color:#fff}}.grid{{display:grid;grid-template-columns:repeat(2,1fr);gap:12px}}.candidate{{border:1px solid var(--line);border-radius:16px;overflow:hidden}}.candidate header{{display:flex;justify-content:space-between;padding:14px;border-bottom:1px solid var(--line)}}.candidate h3{{margin:0}}.candidate header p{{margin:3px 0 0;color:var(--muted);font-size:11px}}.badge{{padding:5px 8px;border-radius:999px;font-size:10px;font-weight:800}}.STRICT{{border-color:#9fd6bc}}.badge.STRICT,.PASS{{background:#e8f7ef;color:var(--green)}}.CONDITIONAL{{border-color:#e9c36c}}.badge.CONDITIONAL,.WARN,.MISSING,.UNKNOWN{{background:#fff5df;color:var(--amber)}}.EXCLUDE{{border-color:#e8bcc3}}.badge.EXCLUDE,.FAIL{{background:#ffedf0;color:var(--red)}}.metrics,.risk{{padding:12px 14px}}.metrics div,.risk div{{background:#f7f9fc;border-radius:10px;padding:8px}}.metrics span,.risk span{{font-size:9px;color:var(--muted)}}.tags{{display:flex;gap:6px;flex-wrap:wrap;padding:0 14px}}.tags span{{background:#eff3f8;border-radius:999px;padding:5px 7px;font-size:10px}}.issue,.next{{margin:11px 14px;padding:10px;background:#fff7e8;border-radius:10px;font-size:11px}}details{{margin:10px 14px 14px}}summary{{cursor:pointer;font-weight:800}}.checks{{list-style:none;padding:0}}.checks li{{display:grid;grid-template-columns:1fr auto;gap:6px;border-bottom:1px solid var(--line);padding:7px 0}}.checks small{{grid-column:1/-1;color:var(--muted)}}.empty{{padding:30px;text-align:center;border:1px dashed #b9c7d9;border-radius:14px}}table{{width:100%;border-collapse:collapse;font-size:11px}}th,td{{border-bottom:1px solid var(--line);padding:8px;text-align:left}}td small{{display:block;color:var(--muted)}}.tablewrap{{overflow:auto}}.stages{{display:grid;grid-template-columns:repeat(9,1fr);gap:7px}}.stages div{{border:1px solid var(--line);border-radius:12px;padding:9px}}.stages i{{display:grid;width:24px;height:24px;place-items:center;border-radius:50%;background:var(--blue);color:#fff;font-style:normal}}.stages b,.stages span{{display:block}}.stages span{{font-size:10px;color:var(--muted)}}@media(max-width:850px){{.grid{{grid-template-columns:1fr}}.meta,.stats,.metrics,.risk{{grid-template-columns:repeat(2,1fr)}}.stages{{grid-template-columns:repeat(2,1fr)}}.hero h1{{font-size:27px}}}}
</style></head><body><main class='shell'><section class='hero'><span>읽기 전용 · 주문 기능 없음 · 호가 판정 미사용</span><h1>라르고 종가베팅 {title_status}</h1><p>호가 관련 세 조건만 제거했습니다. 종목 자격·재료·대장·일봉·매집 소화·종가 구조·진입가·구조 손절 게이트는 그대로 적용합니다.</p><div class='meta'><div><b>전략</b><span>{esc(latest.get('strategy_version'))}</span></div><div><b>기준 시각</b><span>{esc(source_text)}</span></div><div><b>판정 상태</b><span>{esc(phase_label)}</span></div><div><b>위험 한도</b><span>진입가 대비 6%</span></div></div></section><div class='notice'><b>기준 확인:</b> {esc(warning)} 주문·계좌 기능은 없습니다.</div><section class='stats'><div><b>{counts['STRICT']}</b><span>{'잠정 엄격 후보' if provisional else '엄격 추천'}</span></div><div><b>{counts['CONDITIONAL']}</b><span>{'잠정 조건부' if provisional else '조건부 관찰'}</span></div><div><b>{counts['EXCLUDE']}</b><span>제외</span></div><div><b>3</b><span>제외한 호가 조건</span></div></section><nav class='toolbar'><input id='q' placeholder='종목명·코드·재료 검색'><button class='active' data-filter='ALL'>전체</button><button data-filter='STRICT'>엄격</button><button data-filter='CONDITIONAL'>조건부</button><button data-filter='EXCLUDE'>제외</button></nav><section class='section'><h2>{'잠정 엄격 후보' if provisional else '엄격 추천'}</h2><div class='grid'>{strict_cards}</div></section><section class='section'><h2>{'잠정 조건부 후보' if provisional else '조건부 관찰'}</h2><div class='grid'>{conditional_cards}</div></section><section class='section'><h2>전체 엄격 판정표</h2><div class='tablewrap'><table><thead><tr><th>판정</th><th>종목</th><th>가격</th><th>품질</th><th>재료</th><th>패턴</th><th>진입</th><th>손절</th><th>위험</th><th>핵심 사유</th></tr></thead><tbody id='rows'>{table_rows}</tbody></table></div></section><section class='section'><h2>적용한 기존 전략</h2><p>H 단계와 호가 체크 세 개만 제외했습니다.</p><div class='stages'>{stages}</div></section></main><script>
const q=document.getElementById('q');let filter='ALL';function apply(){{const s=q.value.toLowerCase();document.querySelectorAll('article.candidate,tbody tr').forEach(el=>{{const ok=(filter==='ALL'||el.classList.contains(filter)||el.dataset.status===filter)&&(!s||(el.dataset.text||'').toLowerCase().includes(s));el.hidden=!ok}})}}q.addEventListener('input',apply);document.querySelectorAll('[data-filter]').forEach(b=>b.addEventListener('click',()=>{{filter=b.dataset.filter;document.querySelectorAll('[data-filter]').forEach(x=>x.classList.toggle('active',x===b));apply()}}));
</script></body></html>"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html_text, encoding="utf-8")
    result = {"output": str(output_path), "bytes": output_path.stat().st_size, "counts": counts, "source_at": source_at, "phase": phase_id, "phase_label": phase_label, "strict": [x["name"] for x in rows if x["status"] == "STRICT"], "conditional": [x["name"] for x in rows if x["status"] == "CONDITIONAL"]}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--latest", type=Path, required=True)
    parser.add_argument("--methodology", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    build(args.latest, args.methodology, args.output)


if __name__ == "__main__":
    main()
