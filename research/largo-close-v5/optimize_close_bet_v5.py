#!/usr/bin/env python3
"""Revalidate and optimize a read-only Largo-style closing-bet research gate.

Inputs are the retained 20-session historical reconstruction generated on 2026-09-01.
The script never sends orders and never connects to a brokerage account.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import re
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

TARGET_PCT = 3.0
VERSION = "largo-close-v5"
DEVELOPMENT_DATES = {"2026-08-24", "2026-08-25", "2026-08-26"}
VALIDATION_DATES = {"2026-08-27", "2026-08-28", "2026-08-31"}

NON_COMMON_TERMS = (
    "Reg.S", "스팩", "SPAC", "ETN", "ETF", "RISE ", "KODEX ", "TIGER ",
    "ACE ", "PLUS ", "HANARO ", "SOL ", "KOSEF ", "KBSTAR ", "ARIRANG ",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_common_stock_name(value: Any) -> bool:
    name = str(value or "")
    lower = name.casefold()
    if any(term.casefold() in lower for term in NON_COMMON_TERMS):
        return False
    if re.search(r"(?:우|우B|우C|우선주)$", name):
        return False
    return True


def load_detail(path: Path) -> pd.DataFrame:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("detail JSON must be a list")
    records: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        row = dict(item.get("row") or {})
        candidate = item.get("candidate") if isinstance(item.get("candidate"), dict) else {}
        metrics = candidate.get("metrics") if isinstance(candidate.get("metrics"), dict) else {}
        evidence = (item.get("score") or {}).get("evidence") if isinstance(item.get("score"), dict) else {}
        evidence = evidence if isinstance(evidence, dict) else {}
        for key, value in metrics.items():
            row[f"metric_{key}"] = value
        for key, value in evidence.items():
            row[f"evidence_{key}"] = value
        records.append(row)
    frame = pd.DataFrame(records)
    required = {
        "signal_date", "name", "code", "hard_reject", "entry_ask", "entry_bid",
        "spread_pct", "digest_ratio", "risk_rate", "points_directness",
        "points_freshness", "max_executable_return_pct", "last_executable_return_pct",
        "metric_change_rate", "trade_value",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"missing required fields: {missing}")
    return frame


def policy_return(frame: pd.DataFrame, target: float = TARGET_PCT) -> pd.Series:
    return pd.Series(
        np.where(
            frame["max_executable_return_pct"].astype(float) >= target,
            target,
            frame["last_executable_return_pct"].astype(float),
        ),
        index=frame.index,
        dtype=float,
    )


def apply_v5(frame: pd.DataFrame, *, risk_cap: float = 0.10) -> pd.DataFrame:
    result = frame.copy()
    result["outcome_available"] = result["last_executable_return_pct"].notna()
    result["common_stock"] = result["name"].map(is_common_stock_name)
    result["safety_clear"] = (~result["hard_reject"].fillna(False)) & result["common_stock"]
    result["exact_entry"] = result["entry_ask"].notna() & result["entry_bid"].notna() & result["spread_pct"].notna()
    result["risk_clear_v5"] = result["risk_rate"].notna() & (result["risk_rate"] <= risk_cap)

    result["momentum_lane_v5"] = (
        result["safety_clear"]
        & result["exact_entry"]
        & result["risk_clear_v5"]
        & result["metric_change_rate"].between(10.0, 15.0, inclusive="both")
        & result["digest_ratio"].between(0.10, 1.00, inclusive="both")
        & (result["spread_pct"] <= 0.20)
    )
    result["direct_lane_v5"] = (
        result["safety_clear"]
        & result["exact_entry"]
        & result["risk_clear_v5"]
        & (result["points_directness"] >= 14.0)
        & (result["points_freshness"] >= 3.0)
        & result["digest_ratio"].between(0.10, 1.50, inclusive="both")
        & (result["spread_pct"] <= 0.10)
    )
    result["v5_qualified"] = result["momentum_lane_v5"] | result["direct_lane_v5"]
    result["v5_lane"] = np.select(
        [result["momentum_lane_v5"] & result["direct_lane_v5"], result["momentum_lane_v5"], result["direct_lane_v5"]],
        ["BOTH", "MOMENTUM_DIGESTION", "DIRECT_EVENT"],
        default="NONE",
    )
    result["v5_size_band"] = np.select(
        [result["risk_rate"] <= 0.04, result["risk_rate"] <= 0.06, result["risk_rate"] <= 0.10],
        ["BASE", "HALF", "QUARTER"],
        default="NO_POSITION",
    )
    result["v5_daily_pick"] = False
    result["v5_daily_rank"] = pd.NA

    qualified = result[result["v5_qualified"]].copy()
    if not qualified.empty:
        qualified = qualified.sort_values(
            ["signal_date", "risk_rate", "spread_pct", "trade_value", "name"],
            ascending=[True, True, True, False, True],
        )
        ranks = qualified.groupby("signal_date").cumcount() + 1
        result.loc[qualified.index, "v5_daily_rank"] = ranks.astype("Int64")
        result.loc[qualified.index[ranks.eq(1)], "v5_daily_pick"] = True

    result["policy_return_3pct"] = np.nan
    outcome_mask = result["outcome_available"]
    result.loc[outcome_mask, "policy_return_3pct"] = policy_return(result.loc[outcome_mask])
    result["hit_3pct"] = result["max_executable_return_pct"].ge(TARGET_PCT)
    result["positive_policy"] = result["policy_return_3pct"].gt(0)
    return result


def summarize(frame: pd.DataFrame, label: str) -> dict[str, Any]:
    rows = frame[frame["outcome_available"]].copy()
    if rows.empty:
        return {
            "label": label, "trades": 0, "positive": 0, "losses": 0, "hit3": 0,
            "positive_rate": None, "loss_rate": None, "hit3_rate": None,
            "mean_policy_return_pct": None, "median_policy_return_pct": None,
            "sum_policy_return_pct": None, "compound_return_pct": None,
            "mean_last_return_pct": None, "mean_max_return_pct": None,
        }
    returns = rows["policy_return_3pct"].astype(float)
    compound = (np.prod(1.0 + returns.to_numpy() / 100.0) - 1.0) * 100.0
    return {
        "label": label,
        "trades": int(len(rows)),
        "positive": int((returns > 0).sum()),
        "losses": int((returns < 0).sum()),
        "hit3": int(rows["hit_3pct"].sum()),
        "positive_rate": round(float((returns > 0).mean()), 6),
        "loss_rate": round(float((returns < 0).mean()), 6),
        "hit3_rate": round(float(rows["hit_3pct"].mean()), 6),
        "mean_policy_return_pct": round(float(returns.mean()), 6),
        "median_policy_return_pct": round(float(returns.median()), 6),
        "sum_policy_return_pct": round(float(returns.sum()), 6),
        "compound_return_pct": round(float(compound), 6),
        "mean_last_return_pct": round(float(rows["last_executable_return_pct"].mean()), 6),
        "mean_max_return_pct": round(float(rows["max_executable_return_pct"].mean()), 6),
    }


def scenario(frame: pd.DataFrame, **overrides: float) -> tuple[pd.DataFrame, dict[str, Any]]:
    risk_cap = overrides.get("risk_cap", 0.10)
    base = frame.copy()
    clean = (~base["hard_reject"].fillna(False)) & base["name"].map(is_common_stock_name)
    exact = base["entry_ask"].notna() & base["entry_bid"].notna() & base["spread_pct"].notna()
    risk = base["risk_rate"].notna() & (base["risk_rate"] <= risk_cap)
    momentum = (
        clean & exact & risk
        & base["metric_change_rate"].between(overrides.get("change_low", 10.0), overrides.get("change_high", 15.0), inclusive="both")
        & base["digest_ratio"].between(overrides.get("momentum_digest_low", 0.10), overrides.get("momentum_digest_high", 1.00), inclusive="both")
        & (base["spread_pct"] <= overrides.get("momentum_spread", 0.20))
    )
    direct = (
        clean & exact & risk
        & (base["points_directness"] >= overrides.get("directness", 14.0))
        & (base["points_freshness"] >= overrides.get("freshness", 3.0))
        & base["digest_ratio"].between(overrides.get("direct_digest_low", 0.10), overrides.get("direct_digest_high", 1.50), inclusive="both")
        & (base["spread_pct"] <= overrides.get("direct_spread", 0.10))
    )
    chosen = base[momentum | direct].sort_values(
        ["signal_date", "risk_rate", "spread_pct", "trade_value", "name"],
        ascending=[True, True, True, False, True],
    ).groupby("signal_date", as_index=False).head(1)
    chosen = chosen.copy()
    chosen["outcome_available"] = chosen["last_executable_return_pct"].notna()
    chosen["policy_return_3pct"] = np.nan
    available = chosen["outcome_available"]
    if available.any():
        chosen.loc[available, "policy_return_3pct"] = policy_return(chosen.loc[available])
    chosen["hit_3pct"] = chosen["max_executable_return_pct"].ge(TARGET_PCT)
    return chosen, summarize(chosen, "scenario")


def sensitivity_table(frame: pd.DataFrame) -> pd.DataFrame:
    tests: list[tuple[str, dict[str, float]]] = [
        ("기준 v5", {}),
        ("위험 상한 6%", {"risk_cap": 0.06}),
        ("위험 상한 8%", {"risk_cap": 0.08}),
        ("위험 상한 12%", {"risk_cap": 0.12}),
        ("위험 상한 15%", {"risk_cap": 0.15}),
        ("추세 상승 9~15%", {"change_low": 9.0}),
        ("추세 상승 10~16%", {"change_high": 16.0}),
        ("추세 상승 11~15%", {"change_low": 11.0}),
        ("추세 거래대금 상한 0.8", {"momentum_digest_high": 0.80}),
        ("추세 거래대금 상한 1.2", {"momentum_digest_high": 1.20}),
        ("추세 스프레드 0.15%", {"momentum_spread": 0.15}),
        ("추세 스프레드 0.25%", {"momentum_spread": 0.25}),
        ("직접성 12점", {"directness": 12.0}),
        ("직접성 15점", {"directness": 15.0}),
        ("신선도 7점", {"freshness": 7.0}),
        ("직접형 거래대금 상한 1.0", {"direct_digest_high": 1.00}),
        ("직접형 거래대금 상한 2.0", {"direct_digest_high": 2.00}),
        ("직접형 스프레드 0.08%", {"direct_spread": 0.08}),
        ("직접형 스프레드 0.12%", {"direct_spread": 0.12}),
    ]
    rows: list[dict[str, Any]] = []
    for name, overrides in tests:
        picks, stats = scenario(frame, **overrides)
        dev = summarize(picks[picks["signal_date"].isin(DEVELOPMENT_DATES)], "development")
        val = summarize(picks[picks["signal_date"].isin(VALIDATION_DATES)], "validation")
        rows.append({
            "scenario": name,
            "trades": stats["trades"],
            "mean_return_pct": stats["mean_policy_return_pct"],
            "sum_return_pct": stats["sum_policy_return_pct"],
            "loss_rate": stats["loss_rate"],
            "hit3_rate": stats["hit3_rate"],
            "development_trades": dev["trades"],
            "development_mean_pct": dev["mean_policy_return_pct"],
            "validation_trades": val["trades"],
            "validation_mean_pct": val["mean_policy_return_pct"],
            "selected_names": ", ".join(picks["name"].astype(str).tolist()),
        })
    return pd.DataFrame(rows)


def condition_effects(frame: pd.DataFrame) -> pd.DataFrame:
    available = frame[frame["outcome_available"]].copy()
    conditions: list[tuple[str, pd.Series]] = [
        ("안전 제외 통과", available["safety_clear"]),
        ("보통주만", available["common_stock"]),
        ("위험거리 10% 이하", available["risk_clear_v5"]),
        ("당일 상승 10~15%", available["metric_change_rate"].between(10.0, 15.0, inclusive="both")),
        ("거래대금 소화 0.10~1.00", available["digest_ratio"].between(0.10, 1.00, inclusive="both")),
        ("직접성 14점 이상", available["points_directness"] >= 14.0),
        ("신선도 3점 이상", available["points_freshness"] >= 3.0),
        ("스프레드 0.20% 이하", available["spread_pct"] <= 0.20),
        ("스프레드 0.10% 이하", available["spread_pct"] <= 0.10),
        ("v5 두 경로 통과", available["v5_qualified"]),
        ("v5 날짜별 최종 1종목", available["v5_daily_pick"]),
    ]
    rows: list[dict[str, Any]] = []
    for name, mask in conditions:
        passed = available[mask.fillna(False)]
        failed = available[~mask.fillna(False)]
        p = summarize(passed, "pass")
        f = summarize(failed, "fail")
        rows.append({
            "condition": name,
            "pass_n": p["trades"],
            "pass_mean_pct": p["mean_policy_return_pct"],
            "pass_loss_rate": p["loss_rate"],
            "pass_hit3_rate": p["hit3_rate"],
            "fail_n": f["trades"],
            "fail_mean_pct": f["mean_policy_return_pct"],
            "mean_diff_pct_point": None if p["mean_policy_return_pct"] is None or f["mean_policy_return_pct"] is None else round(p["mean_policy_return_pct"] - f["mean_policy_return_pct"], 6),
        })
    return pd.DataFrame(rows)


def friction_table(selected: pd.DataFrame) -> pd.DataFrame:
    rows = []
    raw = selected["policy_return_3pct"].dropna().astype(float).to_numpy()
    for friction in (0.0, 0.2, 0.3, 0.5):
        adjusted = raw - friction
        compound = (np.prod(1 + adjusted / 100) - 1) * 100 if len(adjusted) else np.nan
        rows.append({
            "round_trip_friction_pct": friction,
            "trades": int(len(adjusted)),
            "mean_net_return_pct": round(float(adjusted.mean()), 6) if len(adjusted) else None,
            "sum_net_return_pct": round(float(adjusted.sum()), 6) if len(adjusted) else None,
            "compound_net_return_pct": round(float(compound), 6) if len(adjusted) else None,
            "positive_rate": round(float((adjusted > 0).mean()), 6) if len(adjusted) else None,
        })
    return pd.DataFrame(rows)


def pct(value: Any, digits: int = 2) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "-"
    return f"{float(value):.{digits}f}%"


def rate(value: Any, digits: int = 1) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "-"
    return f"{float(value) * 100:.{digits}f}%"


def money(value: Any) -> str:
    try:
        return f"{float(value):,.0f}원"
    except (TypeError, ValueError):
        return "-"


def html_table(frame: pd.DataFrame, columns: Iterable[tuple[str, str]]) -> str:
    cols = list(columns)
    header = "".join(f"<th>{html.escape(label)}</th>" for _, label in cols)
    body = []
    for _, row in frame.iterrows():
        cells = []
        for key, _ in cols:
            value = row.get(key)
            cells.append(f"<td>{html.escape(str(value if pd.notna(value) else '-'))}</td>")
        body.append("<tr>" + "".join(cells) + "</tr>")
    return f"<div class='table'><table><thead><tr>{header}</tr></thead><tbody>{''.join(body)}</tbody></table></div>"


def render_report(
    summary: dict[str, Any],
    selected: pd.DataFrame,
    sensitivity: pd.DataFrame,
    condition: pd.DataFrame,
    friction: pd.DataFrame,
) -> str:
    old = summary["old_rule"]
    new = summary["v5_daily"]
    development = summary["development"]
    validation = summary["validation"]

    selected_view = selected.copy()
    selected_view["lane_ko"] = selected_view["v5_lane"].map({
        "MOMENTUM_DIGESTION": "추세·거래대금형",
        "DIRECT_EVENT": "직접 재료형",
        "BOTH": "두 경로 동시",
    })
    selected_view["entry_ask_text"] = selected_view["entry_ask"].map(money)
    selected_view["max_text"] = selected_view["max_executable_return_pct"].map(pct)
    selected_view["last_text"] = selected_view["last_executable_return_pct"].map(pct)
    selected_view["policy_text"] = selected_view["policy_return_3pct"].map(pct)
    selected_view["risk_text"] = (selected_view["risk_rate"] * 100).map(pct)
    selected_view["spread_text"] = selected_view["spread_pct"].map(lambda x: pct(x, 4))
    selected_view["outcome"] = np.where(selected_view["policy_return_3pct"] > 0, "수익", "손실")

    selected_html = html_table(selected_view, [
        ("signal_date", "신호일"), ("name", "종목"), ("lane_ko", "통과 경로"),
        ("entry_ask_text", "15:18 가상 매수가"), ("risk_text", "구조 위험"),
        ("spread_text", "스프레드"), ("max_text", "09:06 전 최고"),
        ("last_text", "09:05 수익"), ("policy_text", "정책 수익"), ("outcome", "결과"),
    ])

    sensitivity_view = sensitivity.copy()
    for col in ("mean_return_pct", "sum_return_pct", "development_mean_pct", "validation_mean_pct"):
        sensitivity_view[col] = sensitivity_view[col].map(pct)
    for col in ("loss_rate", "hit3_rate"):
        sensitivity_view[col] = sensitivity_view[col].map(rate)
    sensitivity_html = html_table(sensitivity_view, [
        ("scenario", "변경 시나리오"), ("trades", "거래"),
        ("mean_return_pct", "평균 정책수익"), ("sum_return_pct", "합계"),
        ("loss_rate", "손실률"), ("hit3_rate", "+3% 도달"),
        ("development_mean_pct", "8/24~26 평균"), ("validation_mean_pct", "8/27~31 평균"),
    ])

    condition_view = condition.copy()
    for col in ("pass_mean_pct", "mean_diff_pct_point"):
        condition_view[col] = condition_view[col].map(pct)
    for col in ("pass_loss_rate", "pass_hit3_rate"):
        condition_view[col] = condition_view[col].map(rate)
    condition_html = html_table(condition_view, [
        ("condition", "조건"), ("pass_n", "통과 건수"), ("pass_mean_pct", "통과군 평균"),
        ("pass_loss_rate", "통과군 손실률"), ("pass_hit3_rate", "+3% 도달률"),
        ("mean_diff_pct_point", "미통과군 대비"),
    ])

    friction_view = friction.copy()
    friction_view["round_trip_friction_pct"] = friction_view["round_trip_friction_pct"].map(pct)
    for col in ("mean_net_return_pct", "sum_net_return_pct", "compound_net_return_pct"):
        friction_view[col] = friction_view[col].map(pct)
    friction_view["positive_rate"] = friction_view["positive_rate"].map(rate)
    friction_html = html_table(friction_view, [
        ("round_trip_friction_pct", "왕복 마찰 가정"), ("mean_net_return_pct", "거래당 순수익"),
        ("sum_net_return_pct", "단순 합계"), ("compound_net_return_pct", "복리"),
        ("positive_rate", "순수익 거래 비율"),
    ])

    return f"""<!doctype html>
<html lang='ko'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>라르고 종가베팅 v5 재검증</title>
<style>
:root{{--bg:#edf2f7;--paper:#fff;--ink:#16243a;--muted:#68778b;--line:#d5dee9;--navy:#0b315d;--blue:#286aae;--green:#11734b;--red:#bd3446;--amber:#936100}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font-family:Arial,'Malgun Gothic',sans-serif;line-height:1.55}}
main{{max-width:1500px;margin:auto;padding:20px}}.hero{{padding:30px;border-radius:22px;background:linear-gradient(135deg,var(--navy),var(--blue));color:#fff}}
.hero p{{max-width:1050px;color:#d9ebff}}.cards{{display:grid;grid-template-columns:repeat(5,1fr);gap:10px;margin:16px 0}}
.card,.section{{background:var(--paper);border:1px solid var(--line);border-radius:17px;padding:16px;margin-bottom:16px}}
.card b{{display:block;font-size:28px}}.card span,small,.muted{{color:var(--muted)}}h1,h2,h3{{line-height:1.25}}.good{{color:var(--green);font-weight:700}}.bad{{color:var(--red);font-weight:700}}.warn{{border-left:6px solid var(--amber)}}
.rule{{display:grid;grid-template-columns:repeat(2,1fr);gap:12px}}.rule>div{{border:1px solid var(--line);border-radius:14px;padding:14px;background:#f8fbfe}}
.table{{overflow:auto}}table{{border-collapse:collapse;width:100%;font-size:13px}}th,td{{padding:9px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap;vertical-align:top}}th{{background:#f4f7fb;position:sticky;top:0}}
code{{background:#eef3f8;border-radius:5px;padding:2px 5px}}@media(max-width:1000px){{.cards{{grid-template-columns:repeat(2,1fr)}}.rule{{grid-template-columns:1fr}}}}@media(max-width:560px){{main{{padding:10px}}.cards{{grid-template-columns:1fr}}.hero{{padding:20px}}}}
</style></head><body><main>
<section class='hero'><span>읽기 전용 연구 · 자동주문 없음</span><h1>라르고 종가베팅 v5 재검증</h1><p>15시 18분 최우선 매도호가로 가상 진입하고, 다음 거래일 09시 06분 전에 최우선 매수호가가 +3%에 닿으면 익절합니다. 닿지 않으면 09시 05분 마지막 매수호가로 청산했습니다.</p></section>
<section class='cards'>
<div class='card'><b>{summary['coverage']['sessions_rebuilt']}</b><span>재구성 거래일</span></div>
<div class='card'><b>{summary['coverage']['evaluated_sessions']}</b><span>성과 평가 가능일</span></div>
<div class='card'><b>{new['trades']}</b><span>v5 최종 거래</span></div>
<div class='card'><b>{rate(new['positive_rate'])}</b><span>수익 거래 비율</span></div>
<div class='card'><b>{pct(new['mean_policy_return_pct'])}</b><span>거래당 평균 정책수익</span></div>
</section>
<section class='section'><h2>새 통과 구조</h2><div class='rule'>
<div><h3>추세·거래대금형</h3><p>당일 상승률 10~15%, 최근 60거래일 최대 거래대금 대비 당일 거래대금 0.10~1.00, 15시 18분 스프레드 0.20% 이하를 요구합니다.</p></div>
<div><h3>직접 재료형</h3><p>직접성 14점 이상, 신선도 3점 이상, 거래대금 비율 0.10~1.50, 15시 18분 스프레드 0.10% 이하를 요구합니다.</p></div>
</div><p>두 경로 모두 위험 종목과 보통주가 아닌 상품을 제외합니다. 구조 위험거리는 10% 이하여야 합니다. 하루 여러 종목이 통과하면 위험거리, 스프레드, 거래대금 순서로 한 종목만 남깁니다.</p></section>
<section class='section'><h2>기존 규칙과 비교</h2><p>기존 +3% 규칙은 {old['trades']}건을 골라 단순 합계 {pct(old['sum_policy_return_pct'])}였습니다. v5는 {new['trades']}건을 골라 {new['positive']}건이 수익이었고, 단순 합계 {pct(new['sum_policy_return_pct'])}, 복리 {pct(new['compound_return_pct'])}였습니다.</p><p>8월 24~26일 평균은 {pct(development['mean_policy_return_pct'])}, 8월 27~31일 평균은 {pct(validation['mean_policy_return_pct'])}였습니다. 후반 구간의 손실 거래는 {validation['losses']}건입니다.</p></section>
<section class='section'><h2>날짜별 최종 1종목</h2>{selected_html}</section>
<section class='section'><h2>조건 민감도</h2><p class='muted'>한 번에 조건 하나만 바꿔 결과가 급격히 무너지는지 확인했습니다. 표본이 적으므로 최고 숫자 하나보다 주변 조건의 방향을 봅니다.</p>{sensitivity_html}</section>
<section class='section'><h2>조건별 효과</h2>{condition_html}</section>
<section class='section'><h2>거래 마찰 시나리오</h2><p class='muted'>실제 세금·수수료를 특정하지 않고 왕복 비용과 미끄러짐을 합친 가정치만 차감했습니다.</p>{friction_html}</section>
<section class='section warn'><h2>판정과 한계</h2><p class='good'>기존 합산 점수와 패턴 상한은 매수 게이트에서 제외합니다. v5는 추세·거래대금 경로와 직접 재료 경로를 분리합니다.</p><p>20거래일 480건을 재구성했지만 진입호가와 다음 날 09시 06분 전 호가가 모두 남은 날은 6거래일 144건입니다. 현재 테마 구성과 종가 후 고저가가 일부 대용치로 섞여 있습니다. 새 조건도 이 6일을 보며 선택했으므로 예상 승률로 해석할 수 없습니다.</p><p>운영에서는 <code>연구 후보</code>로만 표시하고 자동 주문은 하지 않습니다. 다음 거래일부터 같은 기준을 고정해 최소 20개 실제 거래 신호를 쌓은 뒤 다시 채택 여부를 판단합니다.</p></section>
<footer class='muted'>버전 {VERSION} · 생성 결과는 투자 권유가 아닙니다.</footer>
</main></body></html>"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--detail", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    args.output.mkdir(parents=True, exist_ok=True)
    frame = load_detail(args.detail)
    metadata = json.loads(args.metadata.read_text(encoding="utf-8"))
    enriched = apply_v5(frame, risk_cap=0.10)
    evaluated = enriched[enriched["outcome_available"]].copy()
    selected = evaluated[evaluated["v5_daily_pick"]].copy().sort_values("signal_date")
    old = evaluated[evaluated["target3_eligible"].fillna(False)].copy()
    old["policy_return_3pct"] = policy_return(old) if not old.empty else pd.Series(dtype=float)
    old["hit_3pct"] = old["max_executable_return_pct"].ge(TARGET_PCT)

    summary = {
        "version": VERSION,
        "target_pct": TARGET_PCT,
        "coverage": {
            "sessions_rebuilt": int(metadata.get("date_count") or enriched["signal_date"].nunique()),
            "candidate_rows": int(len(enriched)),
            "evaluated_sessions": int(evaluated["signal_date"].nunique()),
            "evaluated_rows": int(len(evaluated)),
            "development_dates": sorted(DEVELOPMENT_DATES),
            "validation_dates": sorted(VALIDATION_DATES),
        },
        "rule": {
            "safety": "hard_reject=false, common stock, exact 15:18 ask/bid, structural risk <=10%",
            "momentum_digest": "10% <= change <= 15%, 0.10 <= digest <= 1.00, spread <=0.20%",
            "direct_event": "directness >=14, freshness >=3, 0.10 <= digest <=1.50, spread <=0.10%",
            "daily_selection": "one per signal date: lowest risk, then tightest spread, then largest turnover",
            "exit": "+3% if observed before 09:06; otherwise last observed top bid at 09:05",
        },
        "baseline_all": summarize(evaluated, "all evaluated"),
        "clean_common": summarize(evaluated[evaluated["safety_clear"]], "clean common"),
        "old_rule": summarize(old, "old target3"),
        "v5_qualified_all": summarize(evaluated[evaluated["v5_qualified"]], "v5 all qualified"),
        "v5_daily": summarize(selected, "v5 one per day"),
        "development": summarize(selected[selected["signal_date"].isin(DEVELOPMENT_DATES)], "development"),
        "validation": summarize(selected[selected["signal_date"].isin(VALIDATION_DATES)], "validation"),
        "limitations": [
            "Only six signal dates have both exact 15:18 entry quotes and next-session pre-09:06 outcome quotes.",
            "Current theme membership is used as a historical proxy.",
            "Full-day high/low can include 15:18-15:30 and is not used as a v5 core gate.",
            "The v5 thresholds were selected after reviewing the same six evaluable dates.",
        ],
        "input_sha256": {
            "detail": sha256(args.detail),
            "metadata": sha256(args.metadata),
        },
    }

    sensitivity = sensitivity_table(evaluated)
    condition = condition_effects(enriched)
    friction = friction_table(selected)

    selected_columns = [
        "signal_date", "next_date", "code", "name", "v5_lane", "v5_size_band",
        "metric_change_rate", "digest_ratio", "points_directness", "points_freshness",
        "spread_pct", "risk_rate", "entry_ask", "entry_bid", "max_bid", "last_bid",
        "max_executable_return_pct", "last_executable_return_pct", "policy_return_3pct", "hit_3pct",
        "evidence_title", "evidence_at", "theme_name",
    ]
    selected[selected_columns].to_csv(args.output / "v5_selected_trades.csv", index=False, encoding="utf-8-sig")
    enriched.to_csv(args.output / "v5_candidate_results.csv", index=False, encoding="utf-8-sig")
    sensitivity.to_csv(args.output / "v5_sensitivity.csv", index=False, encoding="utf-8-sig")
    condition.to_csv(args.output / "v5_condition_effects.csv", index=False, encoding="utf-8-sig")
    friction.to_csv(args.output / "v5_friction_scenarios.csv", index=False, encoding="utf-8-sig")
    (args.output / "v5_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.output / "v5_report.html").write_text(render_report(summary, selected, sensitivity, condition, friction), encoding="utf-8")

    final = f"""# 라르고 종가베팅 v5 재검증 요약

평가 가능한 범위는 2026년 8월 24일부터 31일까지 6거래일 144건입니다. 20거래일 480건을 재구성했지만 나머지 14일은 다음 날 09시 06분 전 호가가 남아 있지 않았습니다.

새 규칙은 합산 점수를 쓰지 않습니다. 당일 상승률과 거래대금 소화를 보는 경로, 직접 재료의 강도와 신선도를 보는 경로로 분리합니다. 두 경로 모두 위험 종목과 보통주가 아닌 상품을 제외하며 구조 위험 10%를 상한으로 둡니다. 하루 여러 종목이 통과하면 위험거리가 가장 짧은 한 종목만 고릅니다.

- 거래 {summary['v5_daily']['trades']}건
- 수익 {summary['v5_daily']['positive']}건, 손실 {summary['v5_daily']['losses']}건
- +3% 도달 {summary['v5_daily']['hit3']}건
- 거래당 평균 정책수익 {summary['v5_daily']['mean_policy_return_pct']:.4f}%
- 단순 합계 {summary['v5_daily']['sum_policy_return_pct']:.4f}%
- 복리 {summary['v5_daily']['compound_return_pct']:.4f}%

기존 규칙은 2건만 골랐고 단순 합계는 {summary['old_rule']['sum_policy_return_pct']:.4f}%였습니다. v5는 표본 내 거래 수와 합계 수익을 늘렸습니다. 다만 새 기준도 같은 6일을 보며 선택한 규칙입니다. 다음 거래일부터 조건을 고정하고 최소 20개 실제 신호를 전진검증해야 합니다.
"""
    (args.output / "FINAL_SUMMARY.md").write_text(final, encoding="utf-8")
    print(json.dumps(summary["v5_daily"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
