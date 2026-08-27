#!/usr/bin/env python3
"""Finish Largo automation steps 5/6 using the screener's nested plan structure."""
from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path
from typing import Any, Mapping

ELIGIBLE = {"CLOSE_READY", "NEXT_DAY_HOGA_CONFIRM", "WATCH", "READY"}
MAX_RISK = 0.06


def num(value: Any) -> int | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(round(float(value))) if math.isfinite(float(value)) else None
    m = re.search(r"-?\d+(?:\.\d+)?", str(value).replace(",", "").replace("원", ""))
    return int(round(float(m.group()))) if m else None


def state(row: Mapping[str, Any]) -> str:
    return str(row.get("final_status") or row.get("state") or row.get("status") or "UNKNOWN").upper()


def add_support(out: list[dict[str, Any]], name: str, value: Any, priority: int, entry: int) -> None:
    price = num(value)
    if price and 0 < price < entry and all(item["price"] != price for item in out):
        out.append({"name": name, "price": price, "priority": priority})


def supports(row: Mapping[str, Any], entry: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    plan = row.get("plan") if isinstance(row.get("plan"), Mapping) else {}
    for index, item in enumerate(plan.get("supports") or []):
        if isinstance(item, Mapping):
            add_support(out, str(item.get("name") or f"계획 지지선 {index+1}"), item.get("price"), index + 1, entry)
    add_support(out, "계획 무효화선", plan.get("invalidation"), 20, entry)
    add_support(out, "기존 구조 손절선", row.get("structure_stop") or row.get("stop_price"), 2, entry)
    if row.get("breakout_20d"):
        add_support(out, "돌파한 20일 전고점", row.get("prior20_high"), 3, entry)
    metrics = row.get("metrics") if isinstance(row.get("metrics"), Mapping) else {}
    for index, key in enumerate(("ma5", "ma10", "ma20", "ma60", "ma120"), start=30):
        add_support(out, f"{key[2:]}일선", metrics.get(key) or row.get(key), index, entry)
    add_support(out, "당일 저점", row.get("low"), 90, entry)
    return sorted(out, key=lambda item: (entry - item["price"], item["priority"]))


def risk_plan(row: Mapping[str, Any]) -> dict[str, Any]:
    estimated = row.get("estimated_price") if row.get("single_price_mode") else None
    choices = [
        ("종가 단일가 예상체결가", estimated),
        ("스크리너 진입 기준가", row.get("entry_reference")),
        ("현재·확정 종가", row.get("price") or row.get("close")),
    ]
    source = None
    entry = None
    for label, raw in choices:
        price = num(raw)
        if price and price > 0:
            source, entry = label, price
            break
    if not entry:
        return {"status": "FAIL", "exact": True, "reason": "유효한 진입 기준가가 없습니다."}
    levels = supports(row, entry)
    if not levels:
        return {"status": "FAIL", "exact": True, "entry_price": entry, "entry_source": source, "reason": "진입가 아래 유효 구조선이 없습니다."}
    chosen = levels[0]
    risk = entry - chosen["price"]
    rate = risk / entry
    valid = rate <= MAX_RISK
    return {
        "status": "PASS" if valid else "FAIL",
        "exact": True,
        "entry_price": entry,
        "entry_source": source,
        "stop_price": chosen["price"],
        "stop_source": chosen["name"],
        "risk_per_share": risk,
        "risk_rate": round(rate, 6),
        "one_r_price": entry + risk,
        "two_r_price": entry + 2 * risk,
        "max_risk_rate": MAX_RISK,
        "valid": valid,
        "support_candidates": levels,
        "reason": (
            f"{chosen['name']} {chosen['price']:,}원을 구조 손절선으로 기록했습니다. 위험거리는 {rate:.2%}입니다."
            if valid else f"가장 가까운 구조선 {chosen['price']:,}원까지 {rate:.2%}로 허용 한도 6.00%를 넘습니다."
        ),
    }


def next_day(row: Mapping[str, Any], risk: Mapping[str, Any]) -> dict[str, Any]:
    if risk.get("status") != "PASS":
        return {"status": "FAIL", "exact": True, "reason": "유효한 구조 손절선이 없어 익일 보유 계획을 만들지 않았습니다."}
    entry = int(risk["entry_price"])
    stop = int(risk["stop_price"])
    one_r = int(risk["one_r_price"])
    high = num(row.get("high") or row.get("prior_high"))
    target = f"전일 고가 {high:,}원 또는 1R {one_r:,}원" if high else f"1R {one_r:,}원"
    lo, hi = round(entry * 0.99), round(entry * 1.01)
    return {
        "status": "PASS",
        "exact": True,
        "gap_thresholds": {"gap_up_from": hi, "flat_low": lo, "flat_high": hi, "gap_down_below": lo},
        "gap_up": f"시초가가 {hi:,}원 이상이면 추격하지 않고 첫 눌림이 전일 종가 {entry:,}원과 {risk['stop_source']} {stop:,}원을 지키는지 확인합니다. {target}에서 분할 청산합니다.",
        "flat": f"시초가가 {lo:,}~{hi:,}원이면 전일 종가 {entry:,}원 재지지와 최우선 매수호가 유지가 함께 나올 때만 보유합니다. {stop:,}원 이탈 시 종료합니다.",
        "gap_down": f"시초가가 {lo:,}원 아래면 첫 3~5분 안에 전일 종가 {entry:,}원을 회복하는지 확인합니다. 회복 실패 또는 {stop:,}원 이탈 시 정리합니다.",
        "reason": "진입가·구조 손절선·1R을 기준으로 갭상승·보합·갭하락 대응계획을 생성했습니다.",
    }


def upsert(row: dict[str, Any], item: dict[str, Any]) -> None:
    checks = row.setdefault("checks", [])
    if isinstance(checks, list):
        checks[:] = [x for x in checks if not (isinstance(x, Mapping) and x.get("id") == item["id"])]
        checks.append(item)
    elif isinstance(checks, dict):
        checks[item["id"]] = item


def run(data_path: Path, site_path: Path | None, audit_path: Path | None) -> dict[str, Any]:
    data = json.loads(data_path.read_text(encoding="utf-8"))
    rows = data.get("candidates") or data.get("results") or []
    counts: dict[str, int] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        auto = row.setdefault("automation_356", {})
        bid = auto.get("best_bid") or {"status": "UNKNOWN", "reason": "반복 호가 미수집"}
        risk = risk_plan(row)
        plan = next_day(row, risk)
        if state(row) not in ELIGIBLE:
            final = "NOT_ELIGIBLE"
        elif bid.get("status") == "FAIL" or risk.get("status") == "FAIL":
            final = "WEB_REJECTED"
        elif bid.get("status") == "PASS" and plan.get("status") == "PASS":
            final = "WEB_PLAN_READY"
        else:
            final = "WEB_CONFIRMATION_INCOMPLETE"
        auto.update({"version": "automation-356-v2", "status": final, "risk_plan": risk, "next_day_plan": plan})
        upsert(row, {"id": "ENTRY_STOP_AUTO", "name": "진입가·구조 손절 자동기록", "role": "required", "status": risk.get("status"), "value": {k: risk.get(k) for k in ("entry_price", "stop_price", "risk_rate", "one_r_price")}, "rule": "기존 계획 지지선 중 진입가 아래 가장 가까운 선, 최대 위험 6%"})
        upsert(row, {"id": "NEXT_DAY_PLAN_AUTO", "name": "익일 갭별 대응계획", "role": "required", "status": plan.get("status"), "value": plan.get("gap_thresholds"), "rule": "갭상승·보합·갭하락 계획 모두 생성"})
        counts[final] = counts.get(final, 0) + 1
    meta = data.setdefault("automation_356", {})
    meta.update({"version": "automation-356-v2", "automated_steps": [3, 5, 6], "counts": counts, "risk_support_source": "candidate.plan.supports + plan.invalidation + metrics moving averages"})
    data_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    if site_path:
        text = site_path.read_text(encoding="utf-8")
        text = text.replace("filter(r=>r.automation_356);", "filter(r=>r.automation_356&&r.automation_356.status!=='NOT_ELIGIBLE');")
        text = text.replace("filter(r=>r.automation_356)", "filter(r=>r.automation_356&&r.automation_356.status!=='NOT_ELIGIBLE')")
        site_path.write_text(text, encoding="utf-8")
    audit = {"status": "PASS", "candidate_count": len(rows), "counts": counts, "eligible_render_count": sum(v for k, v in counts.items() if k != "NOT_ELIGIBLE")}
    if audit_path:
        audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    return audit


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--data", type=Path, required=True)
    p.add_argument("--site", type=Path)
    p.add_argument("--audit", type=Path)
    args = p.parse_args()
    print(json.dumps(run(args.data, args.site, args.audit), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
