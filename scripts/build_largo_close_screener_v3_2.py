from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import build_largo_close_screener_v3_1 as v31

impl = v31.impl
base = impl.base
ORIGINAL_RANKING = base.ranking
ORIGINAL_DETAIL_PRICE = base.detail_price
ORIGINAL_RENDER_SITE = base.render_site
SEED_PATH = Path("largo-close-screener/data/seed_candidates_v3.json")
SEED_USED = False


def extend_alias(logical: str, *names: str) -> None:
    existing = list(base.ALIASES.get(logical, ()))
    for name in names:
        if name not in existing:
            existing.append(name)
    base.ALIASES[logical] = tuple(existing)


# Actual field names returned by the current stock.naver.com read-only payloads.
extend_alias("code", "itemcode", "itemCode")
extend_alias("name", "itemname", "itemName")
extend_alias("volume", "tradeVolume", "accTradeVolume")
extend_alias("trade_value", "tradeAmount", "accTradeAmount")
extend_alias("change_rate", "prevChangeRate")
extend_alias("date", "bizdate", "bizDate")
extend_alias("time", "tradeTime")
extend_alias("industry", "upJongName", "upjongName")
extend_alias("market", "sosok", "marketGb")


def common_stock(code: str, name: str, raw: dict[str, Any] | None = None) -> bool:
    if not (len(code) == 6 and code.isdigit()):
        return False
    normalized = re.sub(r"\s+", "", name or "")
    if re.search(r"(?:우|우B|우C|1우|2우B|3우C)$", normalized):
        return False
    if raw and str(raw.get("type") or "ST") not in {"ST", ""}:
        return False
    return True


def seed_rows(order_type: str) -> list[dict[str, Any]]:
    if not SEED_PATH.exists():
        return []
    seeds = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = []
    for seed in seeds:
        if order_type not in seed.get("sources", []):
            continue
        code = str(seed.get("code") or "")
        name = str(seed.get("name") or code)
        if not common_stock(code, name):
            continue
        rows.append(
            {
                "code": code,
                "name": name,
                "price": None,
                "open": None,
                "high": None,
                "low": None,
                "volume": None,
                "trade_value": None,
                "change_rate": None,
                "industry": "",
                "market": "",
                "rank": int(seed.get("ranks", {}).get(order_type, 999)),
                "source": order_type,
                "raw": {"seed": True},
            }
        )
    return rows


def ranking_with_seed(
    client: Any,
    order_type: str,
    page_size: int = 100,
    alert_type: str | None = None,
) -> list[dict[str, Any]]:
    global SEED_USED
    rows = ORIGINAL_RANKING(client, order_type, page_size, alert_type)
    rows = [
        row
        for row in rows
        if common_stock(row.get("code", ""), row.get("name", ""), row.get("raw"))
    ]
    if rows or order_type not in {"priceTop", "up", "upperQuantTop", "high52week"}:
        return rows
    fallback = seed_rows(order_type)
    if fallback:
        SEED_USED = True
    return fallback


def detail_price_with_industry(client: Any, code: str) -> dict[str, Any]:
    result = ORIGINAL_DETAIL_PRICE(client, code)
    payload = client.get(f"/api/domestic/detail/{code}/detail", {"codeType": "KRX"})
    if not isinstance(payload, dict):
        return result
    normalized = base.normalized_record(payload, "detail-profile")
    for key, value in normalized.items():
        if key in {"raw", "source", "rank"}:
            continue
        if value not in (None, "", 0) or not result.get(key):
            result[key] = value
    industry = base.text_value(base.lookup(payload, base.ALIASES["industry"]))
    if industry:
        result["industry"] = industry
    market_code = str(payload.get("sosok") or "")
    if market_code:
        result["market"] = {"0": "KOSPI", "1": "KOSDAQ", "2": "KONEX"}.get(
            market_code, market_code
        )
    result["timestamp"] = base.text_value(
        base.lookup(payload, base.ALIASES["time"])
    ) or result.get("timestamp", "")
    result["raw_profile"] = payload
    return result


def render_site_with_mode(
    results: list[dict[str, Any]], meta: dict[str, Any], strict: dict[str, Any]
) -> str:
    if SEED_USED and meta.get("mode") == "NAVER_SNAPSHOT":
        meta["mode"] = "NAVER_SEED_SNAPSHOT"
    return ORIGINAL_RENDER_SITE(results, meta, strict)


base.ranking = ranking_with_seed
base.detail_price = detail_price_with_industry
base.render_site = render_site_with_mode


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="site-output")
    parser.add_argument("--history", default="largo-close-screener/data/history.json")
    parser.add_argument("--candidate-limit", type=int, default=20)
    args = parser.parse_args()
    meta = base.build(
        Path(args.output),
        Path(args.history),
        max(5, min(args.candidate_limit, 30)),
    )
    print(json.dumps(meta, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
