#!/usr/bin/env python3
"""Add Largo closing-screening steps 3, 5 and 6 to data and site output."""
from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import json
import math
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Iterable, Mapping

BASE = "https://stock.naver.com"
KST = dt.timezone(dt.timedelta(hours=9))
UA = "Mozilla/5.0 (compatible; LargoClosingAutomation/1.0; read-only)"
ELIGIBLE = {"CLOSE_READY", "NEXT_DAY_HOGA_CONFIRM", "WATCH", "READY"}
PRIORITY = {
    "structure_stop": 1,
    "last_afternoon_pullback_low": 1,
    "breakout_support": 2,
    "reference_close": 3,
    "reference_low": 4,
    "prior20_high": 5,
    "ma5": 6,
    "day_low": 9,
}


def number(value: Any) -> int | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if not math.isfinite(float(value)):
            return None
        return int(round(float(value)))
    match = re.search(r"-?\d+(?:\.\d+)?", str(value).replace(",", "").replace("원", "").strip())
    if not match:
        return None
    try:
        return int(round(float(match.group())))
    except ValueError:
        return None


def state_of(row: Mapping[str, Any]) -> str:
    return str(row.get("final_status") or row.get("state") or row.get("status") or "UNKNOWN").upper()


def score_of(row: Mapping[str, Any]) -> float:
    try:
        return float(row.get("score") or row.get("quality_score") or 0)
    except (TypeError, ValueError):
        return 0.0


def fetch_json(url: str, referer: str, timeout: float = 18.0) -> Any:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "application/json,text/plain,*/*",
            "Accept-Language": "ko-KR,ko;q=0.9",
            "Referer": referer,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code in {403, 429}:
            raise RuntimeError(f"HTTP {exc.code}: 자동 우회 없이 수집 중단") from exc
        raise


def parse_hoga(code: str, payload: Mapping[str, Any], poll: Mapping[str, Any] | None = None) -> dict[str, Any]:
    packed = str(payload.get("hoga1") or "")
    parts = packed.split(":")
    packed_bid = number(parts[1]) if len(parts) == 4 else None
    packed_bid_qty = number(parts[3]) if len(parts) == 4 else None
    poll_row = ((poll or {}).get("datas") or [{}])[0] if isinstance(poll, Mapping) else {}
    return {
        "captured_at": dt.datetime.now(KST).isoformat(),
        "source_at": poll_row.get("localTradedAt"),
        "market_status": poll_row.get("marketStatus") or payload.get("marketStatus"),
        "last_price": number(payload.get("nowPrice")) or number(poll_row.get("closePriceRaw")) or number(poll_row.get("closePrice")),
        "best_bid": number(payload.get("bestBuyHoga")) or packed_bid,
        "best_bid_quantity": packed_bid_qty,
        "best_ask": number(payload.get("bestSellHoga")) or (number(parts[0]) if len(parts) == 4 else None),
        "total_bid": number(payload.get("totalBuyVolume")),
        "total_ask": number(payload.get("totalSellVolume")),
        "polling_interval_ms": number((poll or {}).get("pollingInterval")) if isinstance(poll, Mapping) else None,
    }


def capture_one(code: str) -> dict[str, Any]:
    referer = f"{BASE}/domestic/stock/{code}/price"
    hoga = fetch_json(f"{BASE}/api/domestic/detail/{code}/hoga", referer)
    poll = fetch_json(f"{BASE}/api/polling/domestic/stock?itemCodes={code}", referer)
    return parse_hoga(code, hoga if isinstance(hoga, Mapping) else {}, poll if isinstance(poll, Mapping) else {})


def assess_best_bid(snapshots: list[dict[str, Any]]) -> dict[str, Any]:
    usable = [row for row in snapshots if row.get("best_bid")]
    if len(usable) < 2:
        return {
            "status": "UNKNOWN",
            "exact": False,
            "snapshot_count": len(usable),
            "reason": "비교 가능한 최우선 매수호가가 2개 미만입니다.",
            "snapshots": usable,
        }
    up = down = hold = replenishment = 0
    for previous, current in zip(usable, usable[1:]):
        previous_bid, current_bid = previous["best_bid"], current["best_bid"]
        if current_bid >= previous_bid:
            hold += 1
        if current_bid > previous_bid:
            up += 1
        elif current_bid < previous_bid:
            down += 1
        if current_bid == previous_bid and previous.get("best_bid_quantity") is not None and current.get("best_bid_quantity") is not None:
            replenishment += max(current["best_bid_quantity"] - previous["best_bid_quantity"], 0)
    comparisons = len(usable) - 1
    hold_ratio = hold / comparisons if comparisons else None
    statuses = {str(row.get("market_status") or "").upper() for row in usable}
    closed_only = bool(statuses) and statuses <= {"CLOSE", "CLOSED", "END", "AFTER_HOURS"}
    if closed_only:
        status = "CLOSED"
        reason = "장 마감 뒤 고정 호가입니다. 다음 거래일 장중에 다시 확인합니다."
    elif down == 0 and hold_ratio == 1.0:
        status = "PASS"
        reason = "관측 구간에서 최우선 매수호가가 한 번도 낮아지지 않았습니다."
    elif down <= 1 and usable[-1]["best_bid"] >= usable[0]["best_bid"]:
        status = "WARN"
        reason = "한 차례 흔들림은 있었지만 마지막 매수호가가 시작값을 회복했습니다."
    else:
        status = "FAIL"
        reason = "최우선 매수호가가 하향 이동해 종가 진입 확인 조건을 충족하지 못했습니다."
    return {
        "status": status,
        "exact": True,
        "snapshot_count": len(usable),
        "first_bid": usable[0]["best_bid"],
        "last_bid": usable[-1]["best_bid"],
        "hold_ratio": round(hold_ratio, 4) if hold_ratio is not None else None,
        "up_count": up,
        "down_count": down,
        "replenishment_quantity": replenishment,
        "reason": reason,
        "snapshots": usable,
    }


def iter_supports(row: Mapping[str, Any], entry: int) -> Iterable[dict[str, Any]]:
    def emit(key: str, name: str, value: Any) -> Iterable[dict[str, Any]]:
        price = number(value)
        if price and 0 < price < entry:
            yield {"key": key, "name": name, "price": price, "priority": PRIORITY[key]}

    yield from emit("structure_stop", "기존 구조 손절선", row.get("structure_stop") or row.get("stop_price"))
    yield from emit("last_afternoon_pullback_low", "오후 마지막 눌림 저점", row.get("last_afternoon_pullback_low") or row.get("afternoon_low"))
    yield from emit("breakout_support", "돌파선·박스 상단", row.get("breakout_support") or row.get("box_top"))
    reference = row.get("reference_candle") if isinstance(row.get("reference_candle"), Mapping) else {}
    yield from emit("reference_close", "기준봉 종가", reference.get("close") or reference.get("closePrice") or reference.get("price"))
    yield from emit("reference_low", "기준봉 저점", reference.get("low") or reference.get("lowPrice"))
    if row.get("breakout_20d"):
        yield from emit("prior20_high", "돌파한 20일 전고점", row.get("prior20_high") or row.get("high20"))
    yield from emit("ma5", "5일선 보조선", row.get("ma5"))
    yield from emit("day_low", "당일 저점", row.get("low"))


def build_risk_plan(row: Mapping[str, Any], max_risk_rate: float = 0.06) -> dict[str, Any]:
    entry_candidates = [
        ("종가 단일가 예상체결가", row.get("estimated_price") if row.get("single_price_mode") else None),
        ("스크리너 진입 기준가", row.get("entry_reference")),
        ("현재·확정 종가", row.get("close") or row.get("price")),
    ]
    source = None
    entry = None
    for name, value in entry_candidates:
        parsed = number(value)
        if parsed and parsed > 0:
            source, entry = name, parsed
            break
    if not entry:
        return {"status": "FAIL", "exact": True, "reason": "유효한 진입 기준가가 없습니다."}
    supports = list({item["price"]: item for item in iter_supports(row, entry)}.values())
    if not supports:
        return {
            "status": "FAIL",
            "exact": True,
            "entry_price": entry,
            "entry_source": source,
            "reason": "진입가 아래의 유효 구조선이 없습니다.",
        }
    support = sorted(supports, key=lambda item: (entry - item["price"], item["priority"]))[0]
    risk = entry - support["price"]
    rate = risk / entry
    valid = rate <= max_risk_rate
    return {
        "status": "PASS" if valid else "FAIL",
        "exact": True,
        "entry_price": entry,
        "entry_source": source,
        "stop_price": support["price"],
        "stop_source": support["name"],
        "risk_per_share": risk,
        "risk_rate": round(rate, 6),
        "one_r_price": entry + risk,
        "two_r_price": entry + 2 * risk,
        "max_risk_rate": max_risk_rate,
        "valid": valid,
        "support_candidates": sorted(supports, key=lambda item: (item["priority"], -item["price"])),
        "reason": (
            f"{support['name']} {support['price']:,}원을 구조 손절선으로 사용합니다. 위험거리는 {rate:.2%}입니다."
            if valid
            else f"가장 가까운 구조선까지 {rate:.2%}로 허용 한도 {max_risk_rate:.2%}를 넘습니다."
        ),
    }


def build_next_day_plan(row: Mapping[str, Any], risk: Mapping[str, Any]) -> dict[str, Any]:
    if risk.get("status") != "PASS":
        return {"status": "FAIL", "exact": True, "reason": "유효한 진입·손절 계획이 없어 익일 계획을 만들지 않았습니다."}
    entry = int(risk["entry_price"])
    stop = int(risk["stop_price"])
    one_r = int(risk["one_r_price"])
    prior_high = number(row.get("high") or row.get("prior_high"))
    target = f"전일 고가 {prior_high:,}원 또는 1R {one_r:,}원" if prior_high else f"1R {one_r:,}원"
    return {
        "status": "PASS",
        "exact": True,
        "gap_thresholds": {
            "gap_up_from": round(entry * 1.01),
            "flat_low": round(entry * 0.99),
            "flat_high": round(entry * 1.01),
            "gap_down_below": round(entry * 0.99),
        },
        "gap_up": (
            f"시초가가 {entry * 1.01:,.0f}원 이상이면 첫 눌림에서 전일 종가 {entry:,}원과 "
            f"{risk['stop_source']} {stop:,}원을 지키는지 봅니다. {target}에서 분할 청산합니다."
        ),
        "flat": (
            f"시초가가 {entry * 0.99:,.0f}~{entry * 1.01:,.0f}원이면 전일 종가 {entry:,}원 재지지와 "
            f"최우선 매수호가 유지가 함께 나올 때만 보유합니다. {stop:,}원 이탈 시 종료합니다."
        ),
        "gap_down": (
            f"시초가가 {entry * 0.99:,.0f}원 아래면 첫 3~5분 안에 전일 종가 {entry:,}원을 회복하는지 확인합니다. "
            f"회복 실패 또는 {stop:,}원 이탈 시 손익률과 무관하게 정리합니다."
        ),
        "reason": "진입가·구조 손절선·1R을 이용해 갭상승·보합·갭하락 계획을 자동 생성했습니다.",
    }


def upsert_check(row: dict[str, Any], check: dict[str, Any]) -> None:
    checks = row.setdefault("checks", [])
    if isinstance(checks, list):
        checks[:] = [item for item in checks if not (isinstance(item, Mapping) and item.get("id") == check["id"])]
        checks.append(check)
    elif isinstance(checks, dict):
        checks[check["id"]] = check


def site_block() -> str:
    return r'''
<!-- LARGO_AUTOMATION_356_START -->
<style id="largo-automation-356-style">
#largoAutomation356{margin:18px 0;background:#fff;border:1px solid #d9e2ec;border-radius:18px;padding:18px;box-shadow:0 14px 36px rgba(34,52,78,.09)}#largoAutomation356 h2{margin:0 0 6px;font-size:20px}#largoAutomation356 .auto-note{margin:0 0 14px;color:#68788c;font-size:12px;line-height:1.65}.auto356-stats{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:9px;margin-bottom:12px}.auto356-stat{border:1px solid #d9e2ec;border-radius:12px;padding:10px;background:#f8fafc}.auto356-stat b,.auto356-stat span{display:block}.auto356-stat span{font-size:11px;color:#68788c;margin-top:3px}.auto356-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:11px}.auto356-card{border:1px solid #d9e2ec;border-radius:14px;padding:13px;background:#fff}.auto356-head{display:flex;justify-content:space-between;align-items:flex-start;gap:10px}.auto356-head b{font-size:15px}.auto356-head small{display:block;color:#68788c;margin-top:3px}.auto356-badge{display:inline-flex;padding:4px 8px;border-radius:999px;font-size:11px;font-weight:800}.auto356-pass{background:#e8f7ef;color:#10804e}.auto356-warn,.auto356-closed,.auto356-unknown,.auto356-not_eligible{background:#fff5de;color:#946000}.auto356-fail,.auto356-web_rejected{background:#ffedf0;color:#b53341}.auto356-web_plan_ready{background:#e8f7ef;color:#10804e}.auto356-web_confirmation_incomplete{background:#fff5de;color:#946000}.auto356-cols{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:10px}.auto356-box{border:1px solid #e7edf3;border-radius:10px;padding:9px;background:#fbfcfe}.auto356-box strong,.auto356-box span{display:block}.auto356-box span{font-size:11px;color:#68788c;line-height:1.55;margin-top:3px}.auto356-card details{margin-top:9px;border-top:1px solid #edf1f5;padding-top:8px}.auto356-card summary{cursor:pointer;font-size:12px;font-weight:700}.auto356-plan{font-size:11px;color:#45566b;line-height:1.6;margin-top:7px}.auto356-empty{padding:22px;text-align:center;color:#68788c;border:1px dashed #cbd5e1;border-radius:12px}@media(max-width:900px){.auto356-grid{grid-template-columns:1fr}.auto356-stats{grid-template-columns:repeat(2,1fr)}}@media(max-width:560px){.auto356-cols{grid-template-columns:1fr}}
</style>
<section id="largoAutomation356" aria-live="polite"><h2>자동화된 최종 확인 3·5·6</h2><p class="auto-note">최우선 매수호가 유지·상향은 반복 웹 호가로 확인합니다. 진입·구조 손절과 다음 날 대응계획은 스크리너 구조값으로 자동 계산합니다. 주문은 실행하지 않습니다.</p><div id="largoAutomation356Body" class="auto356-empty">자동화 결과를 불러오는 중입니다.</div></section>
<script id="largo-automation-356-script">
(()=>{const esc=v=>String(v??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));const fmt=v=>Number.isFinite(Number(v))?Number(v).toLocaleString('ko-KR'):'-';const badge=s=>`<span class="auto356-badge auto356-${String(s||'unknown').toLowerCase()}">${esc(s||'UNKNOWN')}</span>`;const state=r=>String(r.final_status||r.state||r.status||'UNKNOWN');function card(r){const a=r.automation_356||{},b=a.best_bid||{},k=a.risk_plan||{},p=a.next_day_plan||{};const bt=b.snapshot_count?`${fmt(b.first_bid)} → ${fmt(b.last_bid)}원 · 상승 ${fmt(b.up_count)} / 하락 ${fmt(b.down_count)} · 재충전 ${fmt(b.replenishment_quantity)}주`:'장중 반복 호가 미수집';const rt=k.entry_price?`진입 ${fmt(k.entry_price)}원 · 손절 ${fmt(k.stop_price)}원 · 1R ${fmt(k.one_r_price)}원`:'유효 구조선 없음';return `<article class="auto356-card"><div class="auto356-head"><div><b>${esc(r.name||r.stock_name||r.code)}</b><small>${esc(r.code)} · 원래 상태 ${esc(state(r))}</small></div>${badge(a.status)}</div><div class="auto356-cols"><div class="auto356-box"><strong>3. 최우선 매수호가 ${badge(b.status)}</strong><span>${esc(bt)}</span><span>${esc(b.reason||'')}</span></div><div class="auto356-box"><strong>5. 진입·구조 손절 ${badge(k.status)}</strong><span>${esc(rt)}</span><span>${esc(k.stop_source||k.reason||'')}</span></div></div><details><summary>6. 다음 날 갭별 대응계획 ${badge(p.status)}</summary><div class="auto356-plan"><b>갭상승</b><br>${esc(p.gap_up||p.reason||'-')}<br><br><b>보합</b><br>${esc(p.flat||'-')}<br><br><b>갭하락</b><br>${esc(p.gap_down||'-')}</div></details></article>`}fetch('data/latest.json',{cache:'no-store'}).then(r=>{if(!r.ok)throw new Error(`HTTP ${r.status}`);return r.json()}).then(d=>{const rows=(d.candidates||d.results||[]).filter(r=>r.automation_356),body=document.getElementById('largoAutomation356Body');if(!body)return;if(!rows.length){body.className='auto356-empty';body.textContent='자동화 대상 후보가 없습니다.';return}const counts=rows.reduce((m,r)=>{const s=(r.automation_356||{}).status||'UNKNOWN';m[s]=(m[s]||0)+1;return m},{});body.className='';body.innerHTML=`<div class="auto356-stats"><div class="auto356-stat"><b>${rows.length}</b><span>자동 계산 종목</span></div><div class="auto356-stat"><b>${counts.WEB_PLAN_READY||0}</b><span>3·5·6 통과</span></div><div class="auto356-stat"><b>${counts.WEB_CONFIRMATION_INCOMPLETE||0}</b><span>장중 확인 미완성</span></div><div class="auto356-stat"><b>${esc(d.automation_356?.generated_at||'-')}</b><span>자동화 생성 시각</span></div></div><div class="auto356-grid">${rows.map(card).join('')}</div>`}).catch(e=>{const b=document.getElementById('largoAutomation356Body');if(b)b.textContent=`자동화 결과를 불러오지 못했습니다: ${e.message}`})})();
</script>
<!-- LARGO_AUTOMATION_356_END -->
'''


def patch_site(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    block = site_block()
    start, end = "<!-- LARGO_AUTOMATION_356_START -->", "<!-- LARGO_AUTOMATION_356_END -->"
    if start in text and end in text:
        text = text[: text.index(start)] + block + text[text.index(end) + len(end) :]
    elif "</main>" in text:
        text = text.replace("</main>", block + "\n</main>", 1)
    elif "</body>" in text:
        text = text.replace("</body>", block + "\n</body>", 1)
    else:
        text += block
    path.write_text(text, encoding="utf-8")


def run(data_path: Path, site_path: Path | None, audit_path: Path | None, max_candidates: int, rounds: int, interval: float) -> dict[str, Any]:
    payload = json.loads(data_path.read_text(encoding="utf-8"))
    rows = payload.get("candidates") or payload.get("results") or []
    if not isinstance(rows, list):
        raise RuntimeError("candidates/results 배열을 찾지 못했습니다.")
    target_rows = [row for row in rows if isinstance(row, dict) and state_of(row) in ELIGIBLE]
    target_rows.sort(key=lambda row: (state_of(row) == "CLOSE_READY", score_of(row)), reverse=True)
    targets = target_rows[: max(0, max_candidates)]
    stores = {str(row.get("code")): [] for row in targets if row.get("code")}
    errors = {code: [] for code in stores}
    for round_index in range(max(0, rounds)):
        if stores:
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(4, len(stores))) as executor:
                future_map = {executor.submit(capture_one, code): code for code in stores}
                for future in concurrent.futures.as_completed(future_map):
                    code = future_map[future]
                    try:
                        stores[code].append(future.result())
                    except Exception as exc:
                        errors[code].append(str(exc)[:180])
        if round_index + 1 < rounds:
            recommended = max([(items[-1].get("polling_interval_ms") or 0) / 1000 for items in stores.values() if items] or [0])
            time.sleep(max(interval, recommended))
    counts: dict[str, int] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        code = str(row.get("code") or "")
        bid = assess_best_bid(stores.get(code, [])) if code in stores else {
            "status": "NOT_ELIGIBLE" if state_of(row) not in ELIGIBLE else "UNKNOWN",
            "exact": state_of(row) not in ELIGIBLE,
            "snapshot_count": 0,
            "reason": "원래 스크리너 상태가 자동 호가 확인 대상이 아닙니다." if state_of(row) not in ELIGIBLE else "수집 대상 상한 밖입니다.",
            "snapshots": [],
        }
        if errors.get(code):
            bid["errors"] = errors[code]
        risk = build_risk_plan(row)
        plan = build_next_day_plan(row, risk)
        if state_of(row) not in ELIGIBLE:
            automation_status = "NOT_ELIGIBLE"
        elif risk.get("status") == "FAIL" or plan.get("status") == "FAIL" or bid.get("status") == "FAIL":
            automation_status = "WEB_REJECTED"
        elif bid.get("status") == "PASS" and risk.get("status") == "PASS" and plan.get("status") == "PASS":
            automation_status = "WEB_PLAN_READY"
        else:
            automation_status = "WEB_CONFIRMATION_INCOMPLETE"
        row["automation_356"] = {
            "version": "automation-356-v1",
            "status": automation_status,
            "best_bid": bid,
            "risk_plan": risk,
            "next_day_plan": plan,
            "limitations": [
                "최우선 매수호가는 네이버 공개 호가의 반복 스냅샷으로 계산합니다.",
                "진입가는 실제 체결가가 아니라 웹 진입 기준가입니다.",
                "다음 날 계획은 주문 실행이 아니라 사전 대응 시나리오입니다.",
            ],
        }
        upsert_check(row, {
            "id": "BEST_BID_HOLD_AUTO",
            "name": "최우선 매수호가 유지·상향",
            "role": "web_direct",
            "status": "PASS" if bid.get("status") == "PASS" else "FAIL" if bid.get("status") == "FAIL" else "WARN",
            "raw_status": bid.get("status"),
            "value": {key: bid.get(key) for key in ("first_bid", "last_bid", "hold_ratio", "up_count", "down_count", "replenishment_quantity")},
            "rule": "반복 스냅샷에서 하향 이동이 없어야 함",
        })
        upsert_check(row, {
            "id": "ENTRY_STOP_AUTO",
            "name": "진입가·구조 손절 자동기록",
            "role": "required",
            "status": risk.get("status"),
            "value": {key: risk.get(key) for key in ("entry_price", "stop_price", "risk_rate", "one_r_price")},
            "rule": "진입가 아래 가장 가까운 유효 구조선, 최대 위험 6%",
        })
        upsert_check(row, {
            "id": "NEXT_DAY_PLAN_AUTO",
            "name": "익일 갭별 대응계획",
            "role": "required",
            "status": plan.get("status"),
            "value": plan.get("gap_thresholds"),
            "rule": "갭상승·보합·갭하락 계획 모두 생성",
        })
        counts[automation_status] = counts.get(automation_status, 0) + 1
    generated_at = dt.datetime.now(KST).isoformat()
    payload["automation_356"] = {
        "version": "automation-356-v1",
        "generated_at": generated_at,
        "automated_steps": [3, 5, 6],
        "best_bid_rounds": rounds,
        "best_bid_interval_seconds": interval,
        "max_bid_candidates": max_candidates,
        "counts": counts,
        "source": "Naver Stock public read-only hoga/polling JSON + existing screener structure",
    }
    data_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if site_path:
        patch_site(site_path)
    audit = {
        "generated_at": generated_at,
        "data_path": str(data_path),
        "site_path": str(site_path) if site_path else None,
        "candidate_count": len(rows),
        "best_bid_target_count": len(targets),
        "counts": counts,
        "errors": errors,
        "status": "PASS" if all(row.get("automation_356") for row in rows if isinstance(row, dict)) else "FAIL",
    }
    if audit_path:
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    return audit


def main() -> int:
    parser = argparse.ArgumentParser(description="라르고 종가매매 3·5·6 자동화와 사이트 패치")
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--site", type=Path)
    parser.add_argument("--audit", type=Path)
    parser.add_argument("--max-candidates", type=int, default=10)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--interval", type=float, default=7.0)
    args = parser.parse_args()
    result = run(
        args.data,
        args.site,
        args.audit,
        max(0, min(args.max_candidates, 20)),
        max(0, min(args.rounds, 6)),
        max(0.0, args.interval),
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
