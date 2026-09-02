#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise SystemExit(f"missing patch marker: {label}")
    return text.replace(old, new, 1)


root = Path(__file__).resolve().parents[2]
script = root / "research/largo-close-v5/validate_september_v5.py"
text = script.read_text(encoding="utf-8")

text = replace_once(text, "import html\nimport json\n", "import html\nimport hashlib\nimport json\n", "hashlib import")

next_session = '''NEXT_SESSION = {
    "2026-09-01": "2026-09-02",
    "2026-09-02": "2026-09-03",
}
'''
variants = next_session + '''
KNOWN_RECONSTRUCTION_VARIANTS = [
    {
        "run_id": 33619084738,
        "generated_at": "2026-09-02T19:24:37+09:00",
        "signal_date": "2026-09-01",
        "selected_code": "096770",
        "selected_name": "SK이노베이션",
        "lane": "DIRECT_EVENT",
        "entry_ask": 135400.0,
        "max_executable_return_pct": 0.0739,
        "policy_return_3pct": -0.9601,
        "result": "LOSS",
    },
    {
        "run_id": 33620235904,
        "generated_at": "2026-09-02T19:38:12+09:00",
        "signal_date": "2026-09-01",
        "selected_code": "047770",
        "selected_name": "코데즈컴바인",
        "lane": "MOMENTUM_DIGESTION",
        "entry_ask": 3910.0,
        "max_executable_return_pct": 3.4527,
        "policy_return_3pct": 3.0,
        "result": "HIT_3",
    },
]
'''
text = replace_once(text, next_session, variants, "known variants")

text = replace_once(
    text,
    '''    items, errors = fetch_news_box(code, timeout)
    scored = score_candidate(
''',
    '''    items, errors = fetch_news_box(code, timeout)
    evidence_payload = [
        {
            "title": str(item.get("title") or ""),
            "body": str(item.get("body") or ""),
            "at": item.get("at").isoformat() if isinstance(item.get("at"), dt.datetime) else None,
            "source": str(item.get("source") or ""),
        }
        for item in items
    ]
    news_input_hash = hashlib.sha256(
        json.dumps(evidence_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    scored = score_candidate(
''',
    "news input hash",
)
text = replace_once(
    text,
    '''        "evidence_at": evidence.get("at"),
        "change_rate": structure.get("change_rate"),
''',
    '''        "evidence_at": evidence.get("at"),
        "news_input_hash": news_input_hash,
        "news_item_count": len(evidence_payload),
        "change_rate": structure.get("change_rate"),
''',
    "hash output",
)

text = replace_once(
    text,
    '''    cards = [
        ("검사일", str(summary["signal_days"])),
        ("검사 후보", str(summary["candidate_rows"])),
        ("v5 통과", str(summary["eligible_rows"])),
        ("날짜별 선택", str(summary["selected_rows"])),
        ("결과 완료", str(summary["evaluated_picks"])),
    ]
''',
    '''    cards = [
        ("검사일", str(summary["signal_days"])),
        ("검사 후보", str(summary["candidate_rows"])),
        ("최신 재구성 통과", str(summary["eligible_rows"])),
        ("알려진 재구성 변형", str(len(summary["known_reconstruction_variants"]))),
        ("정식 완료", str(summary["official_evaluated_picks"])),
    ]
''',
    "report cards",
)

text = replace_once(
    text,
    '''    warnings = "".join(f"<li>{html.escape(str(item))}</li>" for item in summary["limitations"])
    return f"""<!doctype html><html lang='ko'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
''',
    '''    variant_rows = "".join(
        "<tr>"
        f"<td>{item['run_id']}</td>"
        f"<td>{html.escape(item['selected_name'])}<small>{html.escape(item['selected_code'])} · {html.escape(item['lane'])}</small></td>"
        f"<td>{fmt_price(item['entry_ask'])}</td>"
        f"<td>{fmt_pct(item['max_executable_return_pct'])}</td>"
        f"<td>{fmt_pct(item['policy_return_3pct'])}</td>"
        "</tr>"
        for item in summary["known_reconstruction_variants"]
    )
    warnings = "".join(f"<li>{html.escape(str(item))}</li>" for item in summary["limitations"])
    return f"""<!doctype html><html lang='ko'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
''',
    "variant rows",
)

text = replace_once(
    text,
    '''</style></head><body><main><section class='hero'><span>읽기 전용 · 주문 기능 없음</span><h1>라르고 종가베팅 v5 9월 재구성 검증</h1><p>보존된 장 마감 후보 스냅샷을 v5 임계값에 그대로 넣고, 15시 18분 최우선 매도호가와 다음 거래일 09시 06분 전 최우선 매수호가를 다시 조회했습니다.</p></section>
<section class='cards'>{cards_html}</section>
<section class='section'><h2>날짜별 최종 선택</h2>''',
    '''</style></head><body><main><section class='hero'><span>읽기 전용 · 주문 기능 없음</span><h1>라르고 종가베팅 v5 9월 안정성 검증</h1><p>시장 스냅샷과 15시 18분 호가는 고정됐지만 과거 뉴스·공시 입력을 실행 때마다 다시 조회해 9월 1일 선택 종목이 달라졌습니다. 9월 공식 성과는 아직 0건입니다.</p></section>
<section class='cards'>{cards_html}</section>
<section class='section warning'><h2>9월 1일 재현성 실패</h2><p>동일한 종가 후보 스냅샷으로 두 번 실행했지만 첫 실행은 SK이노베이션을 골라 -0.9601%, 두 번째 실행은 코데즈컴바인을 골라 +3.0000%로 계산했습니다. 변한 입력은 과거 뉴스·공시 응답입니다. 어느 한쪽도 공식 승패로 확정하지 않습니다.</p><div style='overflow:auto'><table><thead><tr><th>실행</th><th>선택 종목</th><th>15:18 진입</th><th>09:06 전 최고</th><th>정책수익</th></tr></thead><tbody>{variant_rows}</tbody></table></div></section>
<section class='section'><h2>최신 재구성 선택</h2>''',
    "stability report section",
)

text = replace_once(text, '"version": "largo-close-v5-september-reconstruction-v1",', '"version": "largo-close-v5-september-reconstruction-v2",', "version")
text = replace_once(
    text,
    '''        "evaluated_picks": len(evaluated),
        "positive_picks": sum((num(row.get("policy_return_3pct")) or 0) > 0 for row in evaluated),
        "hit3_picks": sum(bool(row.get("hit_3_exec")) for row in evaluated),
        "mean_policy_return_pct": round(statistics.fmean(policy_returns), 4) if policy_returns else None,
        "sum_policy_return_pct": round(sum(policy_returns), 4) if policy_returns else None,
        "snapshots": snapshot_meta,
''',
    '''        "latest_reconstruction_evaluated_picks": len(evaluated),
        "latest_reconstruction_positive_picks": sum((num(row.get("policy_return_3pct")) or 0) > 0 for row in evaluated),
        "latest_reconstruction_hit3_picks": sum(bool(row.get("hit_3_exec")) for row in evaluated),
        "latest_reconstruction_mean_policy_return_pct": round(statistics.fmean(policy_returns), 4) if policy_returns else None,
        "latest_reconstruction_sum_policy_return_pct": round(sum(policy_returns), 4) if policy_returns else None,
        "official_status": "EXCLUDED_UNSTABLE_HISTORICAL_EVIDENCE_INPUT",
        "official_evaluated_picks": 0,
        "official_positive_picks": 0,
        "official_hit3_picks": 0,
        "official_mean_policy_return_pct": None,
        "official_sum_policy_return_pct": None,
        "known_reconstruction_variants": KNOWN_RECONSTRUCTION_VARIANTS,
        "snapshots": snapshot_meta,
''',
    "official metrics",
)
text = replace_once(
    text,
    '''        "limitations": [
            "9월 1일과 2일의 보존 스냅샷은 장 마감 뒤 생성됐습니다. 정확한 15:18 후보 화면을 복원한 전진검증이 아니라 사후 재구성입니다.",
''',
    '''        "limitations": [
            "동일한 시장 스냅샷으로 두 번 실행했지만 과거 뉴스·공시 응답이 달라 2026-09-01 선택 종목이 바뀌었습니다. 9월 공식 성과는 0건입니다.",
            "9월 1일과 2일의 보존 스냅샷은 장 마감 뒤 생성됐습니다. 정확한 15:18 후보 화면을 복원한 전진검증이 아니라 사후 재구성입니다.",
''',
    "stability limitation",
)
text = replace_once(
    text,
    '''        "selected": [{key: row.get(key) for key in ("signal_date", "code", "name", "v5_lane", "entry_ask", "outcome_status", "max_executable_return_pct", "policy_return_3pct")} for row in selected],
''',
    '''        "official_status": summary["official_status"],
        "selected": [{key: row.get(key) for key in ("signal_date", "code", "name", "v5_lane", "entry_ask", "outcome_status", "max_executable_return_pct", "policy_return_3pct")} for row in selected],
''',
    "print official status",
)
script.write_text(text, encoding="utf-8")

workflow = root / ".github/workflows/largo-v5-september-validation.yml"
yml = workflow.read_text(encoding="utf-8")
yml = yml.replace('      - "research/largo-close-v5/forward/2026-09/**"\n', '')
yml = replace_once(yml, "assert summary['version']=='largo-close-v5-september-reconstruction-v1'", "assert summary['version']=='largo-close-v5-september-reconstruction-v2'", "workflow version")
yml = replace_once(
    yml,
    "          assert summary['dates']==['2026-09-01','2026-09-02']\n",
    "          assert summary['dates']==['2026-09-01','2026-09-02']\n          assert summary['official_status']=='EXCLUDED_UNSTABLE_HISTORICAL_EVIDENCE_INPUT'\n          assert summary['official_evaluated_picks']==0\n          assert len(summary['known_reconstruction_variants'])==2\n",
    "workflow stability assertions",
)
workflow.write_text(yml, encoding="utf-8")

forward = root / "research/largo-close-v5/forward/2026-09"
forward.mkdir(parents=True, exist_ok=True)
(forward / "README.md").write_text('''# 라르고 종가베팅 v5 2026년 9월 검증

## 판정

9월 1일은 공식 성과에서 제외합니다. 같은 종가 후보 스냅샷과 같은 15시 18분 호가를 사용했지만, 실행 시점마다 다시 조회한 과거 뉴스·공시 응답이 달라 최종 선택이 바뀌었습니다.

- 실행 33619084738은 SK이노베이션을 선택했습니다. 정책수익은 -0.9601%였습니다.
- 실행 33620235904는 코데즈컴바인을 선택했습니다. 09시 06분 전 +3.4527%에 도달해 정책수익은 +3.0000%였습니다.

어느 한쪽도 공식 승패로 확정하지 않습니다. 9월 1일의 안정적인 정답을 사후에 고르면 결과 선택 편향이 생깁니다.

9월 2일 최신 재구성에서는 흥구석유가 선택됐습니다. 15시 18분 가상 진입가는 11,550원입니다. 다음 거래일인 9월 3일 결과는 대기 상태입니다. 이 후보 역시 장 마감 뒤 스냅샷을 사용했으므로 공식 전진검증 신호로 세지 않습니다.

## 정식 표본 인정 조건

15시 18분 후보 목록, 당시 뉴스·공시 원문과 시각, 테마 입력, 최우선 매도·매수호가를 같은 실행에서 저장합니다. 입력 묶음의 해시가 남고 다음 거래일 09시 06분 결과까지 연결된 거래만 공식 성과에 넣습니다.

v5 임계값은 이번 결과를 보고 바꾸지 않았습니다. 주문, 자동매수와 계좌 연결 기능도 없습니다.
''', encoding="utf-8")

summary = {
    "version": "largo-close-v5-september-stability-v1",
    "as_of": "2026-09-02",
    "strategy_version": "largo-close-v5",
    "strategy_thresholds_changed": False,
    "official_status": "EXCLUDED_UNSTABLE_HISTORICAL_EVIDENCE_INPUT",
    "official_evaluated_picks": 0,
    "official_positive_picks": 0,
    "official_hit3_picks": 0,
    "known_reconstruction_variants": [
        {
            "run_id": 33619084738,
            "signal_date": "2026-09-01",
            "code": "096770",
            "name": "SK이노베이션",
            "entry_ask": 135400.0,
            "max_executable_return_pct": 0.0739,
            "policy_return_3pct": -0.9601,
        },
        {
            "run_id": 33620235904,
            "signal_date": "2026-09-01",
            "code": "047770",
            "name": "코데즈컴바인",
            "entry_ask": 3910.0,
            "max_executable_return_pct": 3.4527,
            "policy_return_3pct": 3.0,
        },
    ],
    "pending_reconstruction": {
        "signal_date": "2026-09-02",
        "next_date": "2026-09-03",
        "code": "024060",
        "name": "흥구석유",
        "lane": "MOMENTUM_DIGESTION",
        "entry_ask": 11550.0,
        "entry_bid": 11530.0,
        "spread_pct": 0.1732,
        "outcome_status": "PENDING",
        "official_forward_signal": False,
    },
}
(forward / "september-v5-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
(forward / "september-v5-selected.csv").write_text(
    "run_id,signal_date,next_date,code,name,v5_lane,entry_ask,max_executable_return_pct,policy_return_3pct,outcome_status,official\n"
    "33619084738,2026-09-01,2026-09-02,096770,SK이노베이션,DIRECT_EVENT,135400,0.0739,-0.9601,EVALUATED,False\n"
    "33620235904,2026-09-01,2026-09-02,047770,코데즈컴바인,MOMENTUM_DIGESTION,3910,3.4527,3.0,EVALUATED,False\n"
    ",2026-09-02,2026-09-03,024060,흥구석유,MOMENTUM_DIGESTION,11550,,,PENDING,False\n",
    encoding="utf-8-sig",
)

print({"status": "patched", "version": "largo-close-v5-september-reconstruction-v2"})
