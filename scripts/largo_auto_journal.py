#!/usr/bin/env python3
"""Automatic paper journal. No account access, orders, manual fills or price requests.

Consumes the existing frozen 15:15 signal and its separately validated close/open
reference. This is a reporting layer: selection, timestamps and settlement stay
unchanged. Missing prices are never replaced with the observed signal price.
"""
from __future__ import annotations

import argparse
import copy
import csv
import datetime as dt
import hashlib
import io
import json
import math
from pathlib import Path
from typing import Any, Mapping

VERSION = "largo-auto-journal-v1"
ENGINE_VERSION = "largo-manual-1515-v1"
LABELS = {
    "PLANNED": "가상 매수 대기", "OPEN": "가상 보유",
    "CLOSED": "가상 매도 완료", "NO_TRADE": "가상 거래 없음",
    "DATA_BLOCK": "자료 차단", "REVIEW": "가격 확인 필요",
}


def number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        n = float(value)
    except (ValueError, TypeError):
        return None
    return n if math.isfinite(n) else None


def positive(value: Any) -> float | None:
    n = number(value)
    return n if n is not None and n > 0 else None


def iso_date(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return value if dt.date.fromisoformat(value).isoformat() == value else None
    except ValueError:
        return None


def digest(value: Any) -> str:
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def reason_text(value: Any) -> str:
    if isinstance(value, list):
        return " · ".join(str(item) for item in value)
    return str(value or "")


def make_row(day: Mapping[str, Any], cost: float, config_hash: str) -> dict[str, Any]:
    date = iso_date(day.get("date"))
    if date is None:
        raise ValueError("Invalid signal date; refusing to publish a replacement history")
    selected = day.get("selected")
    ref = day.get("reference") or {}
    if not isinstance(ref, Mapping):
        raise ValueError("Invalid reference record")
    row: dict[str, Any] = {
        "id": digest([VERSION, config_hash, date])[:24],
        "signal_date": date, "signal_status": day.get("status"),
        "signal_at": day.get("signal_at"), "published_at": day.get("published_at"),
        "code": None, "name": None, "status": "DATA_BLOCK", "status_label": LABELS["DATA_BLOCK"],
        "buy_date": None, "buy_price": None, "buy_basis": "신호일 확정 종가 · 가정",
        "sell_date": None, "sell_price": None, "sell_basis": "다음 시장 거래일 시가 · 전량 매도 가정",
        "expected_sell_date": None, "gross_pct": None, "cost_pct": cost,
        "net_pct": None, "note": "", "reference_status": ref.get("status"),
        "simulation_only": True, "real_order": False,
    }
    if day.get("status") != "SIGNAL":
        status = "NO_TRADE" if day.get("status") == "NO_TRADE" else "DATA_BLOCK"
        row.update(status=status, status_label=LABELS[status],
                   note=reason_text(day.get("reason")) or ("조건 통과 종목이 없어 가상 거래를 만들지 않았습니다." if status == "NO_TRADE" else "유효한 정시 자료가 없어 가상 거래를 차단했습니다."))
        return row
    if not isinstance(selected, Mapping) or not selected.get("code") or not selected.get("name"):
        raise ValueError("SIGNAL without a frozen selected security")
    row.update(code=str(selected["code"]), name=str(selected["name"]), status="PLANNED")
    status = str(ref.get("status") or "WAIT_CLOSE")
    close = positive(ref.get("close"))
    opening = positive(ref.get("open"))
    next_date = iso_date(ref.get("next_date"))
    if next_date is not None and next_date > date:
        row["expected_sell_date"] = next_date
    if status == "WAIT_CLOSE":
        row["note"] = "15:15 선정 완료. 확정 종가를 기다리며 가상 매수가는 아직 비워 둡니다."
    elif status in {"WAIT_OPEN", "MISSING_OPEN"} and close is not None:
        row.update(status="OPEN", buy_date=date, buy_price=close,
                   note="확정 종가에 매수한 것으로 기록했습니다. 다음 시장 거래일 시가를 기다립니다." if status == "WAIT_OPEN" else "가상 매수 후 평가일 시가가 누락됐습니다. 다른 날짜 가격으로 매도 처리하지 않습니다.")
    elif status == "COMPLETE" and close is not None and opening is not None and next_date is not None and next_date > date:
        gross = (opening / close - 1.0) * 100
        reported = number(ref.get("gross_pct"))
        if not math.isfinite(gross) or (ref.get("gross_pct") is not None and (reported is None or abs(reported - gross) > 0.00011)):
            row.update(status="REVIEW", note="원본 참고수익과 가격 재계산이 일치하지 않아 가상 매도를 확정하지 않았습니다.")
        else:
            net = gross - cost
            row.update(status="CLOSED", buy_date=date, buy_price=close, sell_date=next_date, sell_price=opening,
                       gross_pct=round(gross, 8), net_pct=round(net, 8),
                       note=f"{date} 종가에 가상 매수 → {next_date} 시가에 전량 가상 매도. 비용 {cost:g}%p를 차감했습니다.")
    else:
        row.update(status="REVIEW", note=f"가격 확인이 필요합니다({status}). 누락·정정·권리변동 의심 자료로 손익을 만들지 않습니다.")
    row["status_label"] = LABELS[row["status"]]
    return row


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    closed = [row for row in rows if row["status"] == "CLOSED"]
    returns = [row["net_pct"] for row in closed]
    equity = peak = 100.0
    worst_drawdown = 0.0
    curve = [{"date": None, "index": 100.0}]
    for row in closed:
        factor = 1 + row["net_pct"] / 100
        if factor <= 0:
            raise ValueError("Return <= -100%; cannot publish a compounding simulation")
        equity *= factor
        peak = max(peak, equity)
        worst_drawdown = min(worst_drawdown, (equity / peak - 1) * 100)
        curve.append({"date": row["sell_date"], "index": round(equity, 8)})
    return {
        "completed": len(closed), "planned": sum(r["status"] == "PLANNED" for r in rows),
        "open": sum(r["status"] == "OPEN" for r in rows),
        "review": sum(r["status"] == "REVIEW" for r in rows),
        "no_trade": sum(r["status"] == "NO_TRADE" for r in rows),
        "data_block": sum(r["status"] == "DATA_BLOCK" for r in rows),
        "win_rate_pct": round(100 * sum(r > 0 for r in returns) / len(returns), 8) if returns else None,
        "mean_pct": round(math.fsum(returns) / len(returns), 8) if returns else None,
        "compound_pct": round(equity - 100, 8) if returns else None,
        "drawdown_pct": round(worst_drawdown, 8) if returns else None,
        "worst_trade_pct": min(returns) if returns else None,
        "curve": curve if returns else [],
    }


def build_journal(state: Mapping[str, Any]) -> dict[str, Any]:
    if state.get("version") != ENGINE_VERSION or not isinstance(state.get("days"), list):
        raise ValueError("Unsupported source state")
    config = state.get("config") or {}
    if config.get("orders_enabled") is not False:
        raise ValueError("This journal accepts read-only configurations only")
    cost = number(config.get("cost_assumption_pct"))
    if cost is None or not 0 <= cost <= 5:
        raise ValueError("Missing or invalid round-trip cost assumption")
    days = state["days"]
    if not all(isinstance(d, Mapping) and iso_date(d.get("date")) for d in days):
        raise ValueError("Invalid day record")
    dates = [d["date"] for d in days]
    if len(dates) != len(set(dates)):
        raise ValueError("Duplicate source dates; refusing to silently discard a record")
    config_hash = str(state.get("config_hash") or "")
    if not config_hash:
        raise ValueError("Missing frozen configuration hash")
    rows = [make_row(d, cost, config_hash) for d in sorted(days, key=lambda d: d["date"])]
    return {
        "schema": 1, "version": VERSION, "simulation_only": True, "orders_enabled": False,
        "manual_input_required": False, "source_version": state["version"], "config_hash": config_hash,
        "source_updated_at": state.get("updated_at"), "cost_assumption_pct": cost,
        "buy_assumption": "selected security bought at signal-day final close",
        "sell_assumption": "entire position sold at next market-session open",
        "position_assumption": "return-only reference, no account balance or user holdings assumed",
        "source_data_hash": digest({"version": state["version"], "config_hash": config_hash, "days": days}),
        "rows": rows, "summary": summarize(rows),
        "notice": "입력 없이 생성된 자동 모의일지입니다. 실제 체결·계좌 수익이 아니며 주문을 보내지 않습니다.",
    }


def journal_csv(journal: Mapping[str, Any]) -> str:
    columns = [
        ("signal_date", "선정일"), ("name", "종목"), ("code", "종목코드"),
        ("status_label", "가상상태"), ("buy_date", "가상매수일"), ("buy_price", "가상매수가_종가"),
        ("expected_sell_date", "예정평가일"), ("sell_date", "가상매도일"), ("sell_price", "가상매도가_시가"),
        ("gross_pct", "비용전수익률_pct"), ("cost_pct", "왕복비용가정_pctp"), ("net_pct", "비용후수익률_pct"),
        ("note", "자동일지"), ("id", "일지ID"), ("simulation_only", "모의기록"),
    ]
    out = io.StringIO(newline="")
    writer = csv.writer(out)
    writer.writerow([title for _, title in columns])
    for row in journal["rows"]:
        values = []
        for key, _ in columns:
            value = row.get(key)
            if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
                value = "'" + value
            values.append(value)
        writer.writerow(values)
    return "\ufeff" + out.getvalue()


def publish(state_path: Path, out_dir: Path, template_path: Path) -> dict[str, Any]:
    original = json.loads(state_path.read_text(encoding="utf-8"))
    state = copy.deepcopy(original)
    journal = build_journal(state)
    state["auto_journal"] = journal
    state["journal_engine_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    template = template_path.read_text(encoding="utf-8")
    if template.count("__INITIAL_STATE__") != 1:
        raise ValueError("Expected one embedded-state placeholder")
    embedded = json.dumps(state, ensure_ascii=False, allow_nan=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    html = template.replace("__INITIAL_STATE__", embedded)
    # Materialize after all checks. The collector's input file is not modified.
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, data in {
        "state.json": json.dumps(state, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        "journal.json": json.dumps(journal, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        "journal.csv": journal_csv(journal), "index.html": html,
    }.items():
        temp = out_dir / (name + ".tmp")
        temp.write_text(data, encoding="utf-8")
        temp.replace(out_dir / name)
    assert original["days"] == state["days"]
    assert original["config_hash"] == state["config_hash"]
    return journal


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--template", type=Path, required=True)
    args = parser.parse_args()
    journal = publish(args.state, args.out, args.template)
    print(json.dumps({"journal_version": VERSION, "rows": len(journal["rows"]), "summary": {k: v for k, v in journal["summary"].items() if k != "curve"}, "orders_enabled": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
