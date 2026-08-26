from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

BASE = "https://stock.naver.com"
OUT = Path(os.environ.get("OUT", "probe-output"))
OUT.mkdir(parents=True, exist_ok=True)
HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://stock.naver.com/",
    "User-Agent": "Mozilla/5.0 LargoTV-Close-Screener-Probe/1.0",
}


def get(path: str) -> tuple[Any, dict[str, Any]]:
    req = urllib.request.Request(BASE + path, headers=HEADERS)
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=45) as response:
            raw = response.read()
            meta = {
                "path": path,
                "status": response.status,
                "elapsed_ms": round((time.time() - started) * 1000, 1),
                "content_type": response.headers.get("content-type"),
                "cors": response.headers.get("access-control-allow-origin"),
                "cache_control": response.headers.get("cache-control"),
                "bytes": len(raw),
            }
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        meta = {
            "path": path,
            "status": exc.code,
            "elapsed_ms": round((time.time() - started) * 1000, 1),
            "content_type": exc.headers.get("content-type"),
            "cors": exc.headers.get("access-control-allow-origin"),
            "bytes": len(raw),
            "error": raw[:1000].decode("utf-8", errors="replace"),
        }
        raise RuntimeError(json.dumps(meta, ensure_ascii=False)) from exc
    payload = json.loads(raw.decode("utf-8"))
    return payload, meta


def find_codes(value: Any) -> list[str]:
    found: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, item in node.items():
                lowered = str(key).lower()
                if lowered in {"itemcode", "item_code", "code", "stockcode", "stock_code"}:
                    text = str(item).strip()
                    if len(text) == 6 and text.isdigit() and text not in found:
                        found.append(text)
                walk(item)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(value)
    return found


endpoints = {
    "rank_trading_value": "/api/domestic/market/stock/default?tradeType=KRX&marketType=ALL&orderType=priceTop&startIdx=0&pageSize=8",
    "rank_rise": "/api/domestic/market/stock/default?tradeType=KRX&marketType=ALL&orderType=up&startIdx=0&pageSize=8",
    "rank_volume_surge": "/api/domestic/market/stock/default?tradeType=KRX&marketType=ALL&orderType=upperQuantTop&startIdx=0&pageSize=8",
    "rank_high52": "/api/domestic/market/stock/default?tradeType=KRX&marketType=ALL&orderType=high52week&startIdx=0&pageSize=8",
    "theme_ranking": "/api/stockSecurity/rankings/v2/domestic/themes?sortType=changeRate&period=daily&size=8",
}

summary: dict[str, Any] = {"base": BASE, "requests": {}, "errors": []}
all_codes: list[str] = []
for name, path in endpoints.items():
    try:
        payload, meta = get(path)
        (OUT / f"{name}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        summary["requests"][name] = meta
        for code in find_codes(payload):
            if code not in all_codes:
                all_codes.append(code)
    except Exception as exc:
        summary["errors"].append({"name": name, "error": str(exc)})

if not all_codes:
    all_codes = ["005930"]
code = all_codes[0]
summary["sample_code"] = code
summary["candidate_codes"] = all_codes[:20]

detail_endpoints = {
    "detail": f"/api/domestic/detail/{code}/detail?codeType=KRX",
    "price": f"/api/domestic/detail/{code}/price",
    "polling": f"/api/polling/domestic/stock?itemCodes={code}",
    "hoga": f"/api/domestic/detail/{code}/hoga",
    "sise_day": f"/api/domestic/detail/{code}/siseDay?pageSize=30",
    "sise_tick": f"/api/domestic/detail/{code}/siseTick?startIdx=0&pageSize=20",
    "news": f"/api/domestic/detail/news?itemCode={code}&page=1&pageSize=5",
    "notice": f"/api/domestic/detail/notice?itemCode={code}&startIdx=0&pageSize=5",
}
for name, path in detail_endpoints.items():
    try:
        payload, meta = get(path)
        (OUT / f"{name}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        summary["requests"][name] = meta
    except Exception as exc:
        summary["errors"].append({"name": name, "error": str(exc)})

(OUT / "probe_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(summary, ensure_ascii=False, indent=2))
