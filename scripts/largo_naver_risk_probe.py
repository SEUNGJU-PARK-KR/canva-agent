from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "https://stock.naver.com"
OUT = Path(os.environ.get("OUT", "risk-probe-output"))
OUT.mkdir(parents=True, exist_ok=True)


def get(order_type: str, alert_type: str | None = None) -> object:
    params = {
        "tradeType": "KRX",
        "marketType": "ALL",
        "orderType": order_type,
        "startIdx": 0,
        "pageSize": 20,
    }
    if alert_type:
        params["alertType"] = alert_type
    path = "/api/domestic/market/stock/default?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        BASE + path,
        headers={
            "Accept": "application/json,text/plain,*/*",
            "Referer": BASE + "/",
            "User-Agent": "Mozilla/5.0 Largo risk probe/1.0",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    (OUT / f"{order_type}_{alert_type or 'none'}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return payload


def rows(payload: object) -> list[dict]:
    candidates: list[list[dict]] = []
    def visit(value: object) -> None:
        if isinstance(value, list) and value and all(isinstance(item, dict) for item in value[:5]):
            candidates.append(value)
        if isinstance(value, dict):
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value[:5]:
                visit(child)
    visit(payload)
    return max(candidates, key=len) if candidates else []


summary = {}
for order, alert in [
    ("priceTop", None),
    ("up", None),
    ("upperQuantTop", None),
    ("high52week", None),
    ("statusTag", None),
    ("tradeStopYn", None),
    ("marketAlertType", "01"),
    ("marketAlertType", "02"),
    ("marketAlertType", "03"),
]:
    try:
        payload = get(order, alert)
        data = rows(payload)
        summary[f"{order}:{alert or ''}"] = {
            "count": len(data),
            "codes": [str(item.get("itemcode") or item.get("itemCode") or "") for item in data[:20]],
            "statuses": [
                {
                    "code": item.get("itemcode") or item.get("itemCode"),
                    "manageStatusGb": item.get("manageStatusGb"),
                    "tradeStopYn": item.get("tradeStopYn"),
                    "marketAlertType": item.get("marketAlertType"),
                    "tradableStatus": item.get("tradableStatus"),
                }
                for item in data[:10]
            ],
        }
    except Exception as exc:
        summary[f"{order}:{alert or ''}"] = {"error": repr(exc)}
    time.sleep(0.4)

candidate_codes = set(summary.get("priceTop:", {}).get("codes", []))
risk_union = set()
for key in ["statusTag:", "tradeStopYn:", "marketAlertType:01", "marketAlertType:02", "marketAlertType:03"]:
    risk_union.update(summary.get(key, {}).get("codes", []))
summary["diagnosis"] = {
    "candidate_codes": sorted(candidate_codes),
    "risk_union": sorted(risk_union),
    "candidate_risk_overlap": sorted(candidate_codes & risk_union),
}
(OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(summary, ensure_ascii=False, indent=2))
