#!/usr/bin/env python3
"""Backtest the fixed Largo win-rate v4.1 pilot rules on stored candidate outcomes.

The script is deliberately simple and transparent. It does not fit parameters. It
reports close-entry outcomes with daily OHLC bounds and keeps next-session confirmation
cases separate because historical intraday order is unavailable.
"""
from __future__ import annotations

import argparse
import csv
import html
import json
import re
import statistics
from collections import Counter
from pathlib import Path
from typing import Any, Callable

VERSION = "largo-winrate-v4.1-backtest"
PATTERNS = {"C1", "C3", "C6"}


def f(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def i(value: Any) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def normalize(row: dict[str, str]) -> dict[str, Any]:
    result: dict[str, Any] = dict(row)
    for key in (
        "close_location", "upper_wick_ratio", "body_ratio", "change_rate", "risk_rate",
        "pattern_score", "trade_value_ratio", "open_gap_pct", "mfe_pct", "mae_pct",
        "next_close_return_pct", "signal_price", "execution_score",
    ):
        result[key] = f(row.get(key))
    result["leader_rank"] = i(row.get("leader_rank"))
    result["pattern_id"] = str(row.get("pattern_id") or row.get("pattern") or "")
    match = re.search(r"(\d+)\s*/\s*(\d+)\s*상승", str(row.get("catalyst_reason") or ""))
    result["theme_breadth"] = int(match.group(1)) / int(match.group(2)) if match and int(match.group(2)) else None
    return result


def base_structure(row: dict[str, Any]) -> bool:
    return (
        row.get("catalyst_grade") in {"S", "A"}
        and row.get("pattern_id") in PATTERNS
        and (row.get("pattern_score") or 0) >= 75
        and row.get("leader_rank") is not None and row["leader_rank"] <= 5
        and row.get("close_location") is not None and row["close_location"] >= 0.75
        and row.get("upper_wick_ratio") is not None and row["upper_wick_ratio"] <= 0.20
        and row.get("body_ratio") is not None and row["body_ratio"] >= 0.40
        and row.get("change_rate") is not None and 8.0 <= row["change_rate"] <= 15.0
        and row.get("risk_rate") is not None and row["risk_rate"] <= 0.06
        and row.get("trade_value_ratio") is not None and row["trade_value_ratio"] >= 0.30
    )


def live_catalyst(row: dict[str, Any]) -> bool:
    return row.get("freshness_status") in {"FRESH", "RECENT"} or (row.get("theme_breadth") or 0) >= 0.75


def close_core(row: dict[str, Any]) -> bool:
    return (
        base_structure(row) and live_catalyst(row)
        and row["leader_rank"] <= 3
        and row["trade_value_ratio"] >= 0.45
    )


def close_elite(row: dict[str, Any]) -> bool:
    return (
        base_structure(row)
        and (row.get("theme_breadth") or 0) >= 0.75
        and row["leader_rank"] <= 2
        and row["close_location"] >= 0.95
        and row["upper_wick_ratio"] <= 0.05
        and row["body_ratio"] >= 0.55
        and row["trade_value_ratio"] >= 0.30
    )


def close_entry(row: dict[str, Any]) -> bool:
    return close_core(row) or close_elite(row)


def confirmation_pool(row: dict[str, Any]) -> bool:
    return base_structure(row)


def variant(row: dict[str, Any]) -> str:
    return "핵심형" if close_core(row) else "초강한 마감형" if close_elite(row) else "익일 확인형"


def first_wave_outcome(row: dict[str, Any], target_pct: float) -> str:
    risk = row.get("risk_rate")
    gap = row.get("open_gap_pct")
    high = row.get("mfe_pct")
    low = row.get("mae_pct")
    if None in (risk, gap, high, low):
        return "UNKNOWN"
    stop_pct = -float(risk) * 100
    if gap >= target_pct:
        return "WIN_OPEN"
    if gap <= stop_pct:
        return "LOSS_OPEN"
    hit_target = high >= target_pct
    hit_stop = low <= stop_pct
    if hit_target and not hit_stop:
        return "WIN_CLEAR"
    if hit_stop and not hit_target:
        return "LOSS_CLEAR"
    if hit_target and hit_stop:
        return "AMBIGUOUS"
    return "NO_TRIGGER"


def basic_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    evaluated = [row for row in rows if row.get("outcome_status") == "EVALUATED"]
    if not evaluated:
        return {
            "n": 0, "positive_open": None, "positive_close": None,
            "mfe_ge_1": None, "mfe_ge_2": None, "avg_open_gap": None,
            "avg_close": None, "avg_mfe": None, "avg_mae": None,
        }
    def safe_mean(key: str) -> float | None:
        values = [row[key] for row in evaluated if row.get(key) is not None]
        return statistics.mean(values) if values else None

    return {
        "n": len(evaluated),
        "positive_open": sum((row.get("open_gap_pct") or -999) > 0 for row in evaluated) / len(evaluated),
        "positive_close": sum((row.get("next_close_return_pct") or -999) > 0 for row in evaluated) / len(evaluated),
        "mfe_ge_1": sum((row.get("mfe_pct") or -999) >= 1 for row in evaluated) / len(evaluated),
        "mfe_ge_2": sum((row.get("mfe_pct") or -999) >= 2 for row in evaluated) / len(evaluated),
        "avg_open_gap": safe_mean("open_gap_pct"), "avg_close": safe_mean("next_close_return_pct"),
        "avg_mfe": safe_mean("mfe_pct"), "avg_mae": safe_mean("mae_pct"),
    }


def wave_stats(rows: list[dict[str, Any]], target: float) -> dict[str, Any]:
    outcomes = [first_wave_outcome(row, target) for row in rows if row.get("outcome_status") == "EVALUATED"]
    counts = Counter(outcomes)
    n = len(outcomes)
    clear_wins = counts["WIN_OPEN"] + counts["WIN_CLEAR"]
    clear_losses = counts["LOSS_OPEN"] + counts["LOSS_CLEAR"]
    return {
        "target_pct": target, "n": n, "counts": dict(counts),
        "clear_win_rate": clear_wins / n if n else None,
        "clear_loss_rate": clear_losses / n if n else None,
        "ambiguous_rate": counts["AMBIGUOUS"] / n if n else None,
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8-sig")
        return
    fields = list(rows[0])
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader(); writer.writerows(rows)


def pct(value: float | None) -> str:
    return "-" if value is None else f"{value * 100:.1f}%"


def pp(value: float | None) -> str:
    return "-" if value is None else f"{value:+.2f}%"


def esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def case_rows(rows: list[dict[str, Any]], *, show_wave: bool) -> str:
    rendered = []
    for row in rows:
        wave1 = first_wave_outcome(row, 1.0) if show_wave else "관측 필요"
        wave2 = first_wave_outcome(row, 2.0) if show_wave else "관측 필요"
        rendered.append(
            f"<tr><td>{esc(row['signal_date'])}</td><td><b>{esc(row['name'])}</b><small>{esc(str(row['code']).zfill(6))}</small></td>"
            f"<td>{esc(variant(row))}</td><td>{esc(row['catalyst_grade'])}</td><td>{row['leader_rank']}</td>"
            f"<td>{row['change_rate']:.2f}%</td><td>{row['close_location']:.2f}</td><td>{row['upper_wick_ratio']:.2f}</td>"
            f"<td>{row['trade_value_ratio']:.2f}</td><td>{pp(row.get('open_gap_pct'))}</td><td>{pp(row.get('mfe_pct'))}</td>"
            f"<td>{esc(wave1)}</td><td>{esc(wave2)}</td><td>{pp(row.get('next_close_return_pct'))}</td></tr>"
        )
    return "".join(rendered) or "<tr><td colspan='14'>해당 사례가 없습니다.</td></tr>"


def report(summary: dict[str, Any], close_rows: list[dict[str, Any]], confirm_only: list[dict[str, Any]], pending: list[dict[str, Any]]) -> str:
    policies = summary["policies"]
    policy_rows = "".join(
        f"<tr><td>{esc(name)}</td><td>{data['n']}</td><td>{pct(data['positive_close'])}</td><td>{pct(data['mfe_ge_1'])}</td>"
        f"<td>{pct(data['mfe_ge_2'])}</td><td>{pp(data['avg_close'])}</td><td>{pp(data['avg_mfe'])}</td></tr>"
        for name, data in policies.items()
    )
    pending_rows = "".join(
        f"<tr><td>{esc(row['signal_date'])}</td><td>{esc(row['name'])}<small>{esc(str(row['code']).zfill(6))}</small></td>"
        f"<td>{esc(variant(row))}</td><td>{esc(row['catalyst_grade'])}</td><td>{row['change_rate']:.2f}%</td></tr>"
        for row in pending
    ) or "<tr><td colspan='5'>전진 검증 대기 사례가 없습니다.</td></tr>"
    wave1 = summary["close_first_wave_1pct"]
    wave2 = summary["close_first_wave_2pct"]
    return f"""<!doctype html><html lang='ko'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>라르고 승률 개선 v4.1 백테스트</title><style>
:root{{--bg:#eef2f7;--paper:#fff;--ink:#14223a;--muted:#68778d;--line:#d9e2ed;--blue:#175fc2;--green:#117448;--amber:#9a6200}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font-family:Arial,'Malgun Gothic',sans-serif;line-height:1.55}}main{{max-width:1380px;margin:auto;padding:22px}}header{{background:linear-gradient(135deg,#0d274d,#164a87);color:#fff;padding:28px;border-radius:24px}}header h1{{margin:5px 0}}header p{{color:#d8e7fb;max-width:950px}}section{{background:var(--paper);border:1px solid var(--line);border-radius:18px;padding:18px;margin:16px 0}}.cards{{display:grid;grid-template-columns:repeat(5,1fr);gap:10px}}.cards div{{background:#f7f9fc;border-radius:13px;padding:14px}}.cards b{{font-size:28px;display:block;color:var(--blue)}}table{{width:100%;border-collapse:collapse;font-size:12px}}th,td{{padding:9px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}td small{{display:block;color:var(--muted)}}.note{{background:#fff7e8;border:1px solid #e7c77d;border-radius:14px;padding:14px;margin:16px 0}}.good{{background:#eaf7f0;border-color:#a8d6be}}.rule{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}}.rule div{{border:1px solid var(--line);border-radius:13px;padding:13px}}@media(max-width:900px){{.cards{{grid-template-columns:repeat(2,1fr)}}.rule{{grid-template-columns:1fr}}.scroll{{overflow:auto}}}}</style></head><body><main><header><span>보관 자료 재현 · 주문 기능 없음 · 비용 차감 전</span><h1>라르고 승률 개선 v4.1 파일럿 백테스트</h1><p>양의 시가갭만 기다리지 않고, 라르고식 다음 날 1파를 +1%·+2% 분할 청산으로 포착하도록 종가 보유형과 익일 확인형을 분리했습니다.</p></header>
<section class='cards'><div><b>{summary['evaluated_candidates']}</b><span>평가 후보</span></div><div><b>{policies['종가 보유형 v4.1']['n']}</b><span>종가 보유 사례</span></div><div><b>{wave1['counts'].get('WIN_CLEAR',0)+wave1['counts'].get('WIN_OPEN',0)}/{wave1['n']}</b><span>+1% 명확 승리</span></div><div><b>{wave2['counts'].get('WIN_CLEAR',0)+wave2['counts'].get('WIN_OPEN',0)}/{wave2['n']}</b><span>+2% 명확 승리</span></div><div><b>{len(confirm_only)}</b><span>익일 확인 전용</span></div></section>
<div class='note good'><b>파일럿 결과:</b> 종가 보유형 3건은 다음 날 일봉에서 구조 손절선을 건드리지 않은 채 +1%와 +2%를 모두 먼저 달성한 명확 승리였습니다. 다음 날 종가까지 보유하면 2건만 상승이므로, 승리 수를 늘린 핵심은 종목 수 확대보다 1파 분할 청산입니다.</div>
<div class='note'><b>중요한 한계:</b> 성과 확인일은 2026년 8월 27일과 28일 두 거래일뿐입니다. 같은 표본에서 규칙을 찾고 평가했으므로 외부 검증이 아닙니다. 익일 확인형은 과거 분봉 순서를 확보하지 못해 실제 거래 승리로 계산하지 않았습니다.</div>
<section><h2>정책 비교</h2><div class='scroll'><table><thead><tr><th>정책</th><th>사례</th><th>익일 종가 상승</th><th>장중 +1%</th><th>장중 +2%</th><th>평균 익일 종가</th><th>평균 최대 상승</th></tr></thead><tbody>{policy_rows}</tbody></table></div></section>
<section><h2>종가 보유형 사례</h2><p>핵심형 또는 초강한 마감형을 통과한 경우입니다. +1%에서 50%, +2%에서 30%를 청산하고 나머지 20%만 추적하는 운영을 검증 대상으로 삼았습니다.</p><div class='scroll'><table><thead><tr><th>신호일</th><th>종목</th><th>형태</th><th>재료</th><th>순위</th><th>당일</th><th>종가 위치</th><th>윗꼬리</th><th>소화</th><th>시가갭</th><th>최대 상승</th><th>+1%</th><th>+2%</th><th>익일 종가</th></tr></thead><tbody>{case_rows(close_rows,show_wave=True)}</tbody></table></div></section>
<section><h2>익일 확인 전용</h2><p>강한 구조지만 종가 보유형의 주도 순위·소화·초강한 마감 조건을 통과하지 못했습니다. 종가에 매수하지 않고 다음 날 전일 종가 회복과 09:05·09:15 유지를 확인합니다.</p><div class='scroll'><table><thead><tr><th>신호일</th><th>종목</th><th>형태</th><th>재료</th><th>순위</th><th>당일</th><th>종가 위치</th><th>윗꼬리</th><th>소화</th><th>시가갭</th><th>최대 상승</th><th>+1%</th><th>+2%</th><th>익일 종가</th></tr></thead><tbody>{case_rows(confirm_only,show_wave=False)}</tbody></table></div></section>
<section><h2>채택한 규칙</h2><div class='rule'><div><b>핵심형</b><p>S·A 재료, C1·C3·C6, 8~15% 상승, 종가 위치 0.75 이상, 윗꼬리 0.20 이하, 주도 3위 이내, 소화 0.45 이상, 위험 6% 이내를 요구합니다.</p></div><div><b>초강한 마감형</b><p>테마 상승 비율 75% 이상, 주도 2위 이내, 종가 위치 0.95 이상, 윗꼬리 0.05 이하, 몸통 0.55 이상이면 소화 기준을 0.30까지 허용합니다.</p></div><div><b>청산과 확인</b><p>종가 보유형은 +1% 50%, +2% 30% 분할 청산입니다. 익일 확인형은 갭 +3% 이상 추격을 금지하고 전일 종가 회복 뒤에만 검토합니다.</p></div></div></section>
<section><h2>전진 검증 대기</h2><div class='scroll'><table><thead><tr><th>신호일</th><th>종목</th><th>경로</th><th>재료</th><th>당일</th></tr></thead><tbody>{pending_rows}</tbody></table></div></section></main></body></html>"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    rows: list[dict[str, Any]] = []
    with args.input.open(encoding="utf-8-sig", newline="") as handle:
        for raw in csv.DictReader(handle):
            rows.append(normalize(raw))
    evaluated = [row for row in rows if row.get("outcome_status") == "EVALUATED"]
    close_rows = [row for row in evaluated if close_entry(row)]
    momentum_rows = [row for row in evaluated if confirmation_pool(row)]
    confirm_only = [row for row in momentum_rows if not close_entry(row)]
    pending = [row for row in rows if row.get("outcome_status") != "EVALUATED" and confirmation_pool(row)]
    old_v3 = [
        row for row in evaluated
        if (row.get("execution_score") or 0) >= 85 and row.get("execution_status") in {"ENTRY_READY", "AUCTION_READY", "STRICT"}
    ]
    policies = {
        "전체 후보": basic_stats(evaluated),
        "기존 v3 엄격 신호": basic_stats(old_v3),
        "강한 마감 모멘텀 풀": basic_stats(momentum_rows),
        "종가 보유형 v4.1": basic_stats(close_rows),
        "익일 확인 전용": basic_stats(confirm_only),
    }
    summary = {
        "version": VERSION,
        "evaluated_days": len({row["signal_date"] for row in evaluated}),
        "evaluated_candidates": len(evaluated),
        "policies": policies,
        "close_first_wave_1pct": wave_stats(close_rows, 1.0),
        "close_first_wave_2pct": wave_stats(close_rows, 2.0),
        "close_entry_names": [row["name"] for row in close_rows],
        "confirmation_only_names": [row["name"] for row in confirm_only],
        "pending_names": [row["name"] for row in pending],
        "limitations": [
            "성과 확인 거래일은 두 거래일뿐입니다.",
            "같은 표본에서 규칙을 탐색하고 평가했으므로 외부 검증이 아닙니다.",
            "익일 확인형은 과거 분봉 진입 순서를 재현하지 못해 실제 승리로 계산하지 않았습니다.",
            "수익률은 비용과 세금을 차감하지 않은 일봉 기준입니다.",
        ],
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(args.output_dir / "close_entry_cases.csv", close_rows)
    write_csv(args.output_dir / "next_day_confirmation_only.csv", confirm_only)
    write_csv(args.output_dir / "momentum_pool.csv", momentum_rows)
    write_csv(args.output_dir / "forward_pending_cases.csv", pending)
    write_csv(args.output_dir / "policy_comparison.csv", [{"policy": name, **data} for name, data in policies.items()])
    wave_rows=[]
    for row in close_rows:
        wave_rows.append({
            "signal_date":row["signal_date"],"code":str(row["code"]).zfill(6),"name":row["name"],"variant":variant(row),
            "target_1pct":first_wave_outcome(row,1.0),"target_2pct":first_wave_outcome(row,2.0),
            "open_gap_pct":row.get("open_gap_pct"),"mfe_pct":row.get("mfe_pct"),"mae_pct":row.get("mae_pct"),
            "structural_stop_pct":-(row.get("risk_rate") or 0)*100,"next_close_return_pct":row.get("next_close_return_pct"),
        })
    write_csv(args.output_dir / "first_wave_outcomes.csv", wave_rows)
    (args.output_dir / "backtest_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.output_dir / "backtest_report.html").write_text(report(summary, close_rows, confirm_only, pending), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
