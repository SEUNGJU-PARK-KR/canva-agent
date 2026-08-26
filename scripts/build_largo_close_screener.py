#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Iterable

BASE = "https://stock.naver.com"
KST = timezone(timedelta(hours=9))
UA = "Mozilla/5.0 (compatible; LargoClosingResearch/1.0; read-only)"
POSITIVE = ("공급계약", "수주", "실적", "영업이익", "매출", "자사주", "승인", "정책", "MOU", "인수합병", "신사업", "특허")
NEGATIVE = ("유상증자", "전환사채", "횡령", "배임", "거래정지", "상장폐지", "관리종목", "감사의견", "적자전환", "불성실공시")
ALIASES = {
    "code": ("itemCode", "stockCode", "code", "symbolCode", "stock_code"),
    "name": ("stockName", "itemName", "name", "korName", "stock_name"),
    "price": ("closePrice", "currentPrice", "nowPrice", "price", "close"),
    "open": ("openPrice", "openingPrice", "open"),
    "high": ("highPrice", "high"),
    "low": ("lowPrice", "low"),
    "volume": ("accumulatedTradingVolume", "accumulatedTradeVolume", "volume", "accTradeVolume"),
    "value": ("accumulatedTradingValue", "accumulatedTradeValue", "tradingValue", "tradeValue", "accAmount", "tradePrice"),
    "rate": ("fluctuationsRatio", "changeRate", "rate", "compareToPreviousClosePriceRate", "fluctuationRate"),
    "market": ("marketType", "stockExchangeType", "market", "marketName"),
    "date": ("localDate", "tradeDate", "date", "bizdate", "businessDate"),
}

def number(value: Any) -> float | None:
    if value is None or isinstance(value, bool): return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(float(value)) else None
    text = str(value).strip().replace(",", "").replace("%", "").replace("원", "").replace("+", "")
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match: return None
    try: return float(match.group())
    except ValueError: return None

def get_any(obj: dict[str, Any], names: Iterable[str]) -> Any:
    for name in names:
        if name in obj and obj[name] not in (None, ""): return obj[name]
    lowered = {str(k).casefold(): v for k, v in obj.items()}
    for name in names:
        if name.casefold() in lowered and lowered[name.casefold()] not in (None, ""): return lowered[name.casefold()]
    return None

def fetch_json(path: str, timeout: int = 20) -> Any:
    req = urllib.request.Request(BASE + path, headers={"Accept":"application/json,text/plain,*/*","Referer":BASE+"/","User-Agent":UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code in (403, 429): raise RuntimeError(f"HTTP {exc.code}: 자동 재시도 중단") from exc
        raise

def dict_rows(value: Any) -> list[dict[str, Any]]:
    found: list[list[dict[str, Any]]] = []
    def walk(v: Any, depth: int = 0) -> None:
        if depth > 8: return
        if isinstance(v, list):
            rows = [x for x in v if isinstance(x, dict)]
            if rows: found.append(rows)
            for x in v[:20]: walk(x, depth + 1)
        elif isinstance(v, dict):
            for x in v.values(): walk(x, depth + 1)
    walk(value)
    if not found: return []
    def score(rows: list[dict[str, Any]]) -> tuple[int, int]:
        keys = {str(k) for row in rows[:8] for k in row.keys()}
        semantic = sum(any(alias in keys for alias in names) for names in ALIASES.values())
        return semantic, len(rows)
    return max(found, key=score)

def flatten_dicts(value: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    def walk(v: Any, depth: int = 0) -> None:
        if depth > 8: return
        if isinstance(v, dict):
            out.append(v)
            for x in v.values(): walk(x, depth + 1)
        elif isinstance(v, list):
            for x in v[:200]: walk(x, depth + 1)
    walk(value); return out

def normalize_code(value: Any) -> str | None:
    if value is None: return None
    text = str(value).strip().upper()
    if text.startswith("A") and text[1:].isdigit(): text = text[1:]
    match = re.search(r"\d{6}", text)
    return match.group() if match else None

def normalize_ranking(payload: Any, source: str) -> list[dict[str, Any]]:
    rows=[]
    for idx,row in enumerate(dict_rows(payload),1):
        code=normalize_code(get_any(row,ALIASES["code"])); name=get_any(row,ALIASES["name"])
        if not code or not name: continue
        rows.append({"code":code,"name":str(name),"market":str(get_any(row,ALIASES["market"]) or ""),"price":number(get_any(row,ALIASES["price"])),"trade_value":number(get_any(row,ALIASES["value"])),"volume":number(get_any(row,ALIASES["volume"])),"change_rate":number(get_any(row,ALIASES["rate"])),"rank":idx,"source":source})
    return rows

def normalize_price(payload: Any, fallback: dict[str, Any]) -> dict[str, Any]:
    candidates=flatten_dicts(payload)
    def best(field: str) -> float | None:
        for obj in candidates:
            value=number(get_any(obj,ALIASES[field]))
            if value is not None: return value
        return None
    rate=best("rate")
    return {"price":best("price") or fallback.get("price"),"open":best("open"),"high":best("high"),"low":best("low"),"volume":best("volume") or fallback.get("volume"),"trade_value":best("value") or fallback.get("trade_value"),"change_rate":rate if rate is not None else fallback.get("change_rate")}

def normalize_daily(payload: Any) -> list[dict[str, float]]:
    rows=[]
    for row in dict_rows(payload):
        o=number(get_any(row,ALIASES["open"])); h=number(get_any(row,ALIASES["high"])); l=number(get_any(row,ALIASES["low"])); c=number(get_any(row,ALIASES["price"])); v=number(get_any(row,ALIASES["volume"])); d=get_any(row,ALIASES["date"])
        if None in (o,h,l,c): continue
        rows.append({"date":str(d or ""),"o":o,"h":h,"l":l,"c":c,"v":v or 0})
    if rows and rows[0]["date"] and rows[-1]["date"] and rows[0]["date"] > rows[-1]["date"]: rows.reverse()
    return rows[-80:]

def extract_titles(payload: Any) -> list[str]:
    titles=[]
    for obj in flatten_dicts(payload):
        for key,value in obj.items():
            if isinstance(value,str) and any(token in str(key).casefold() for token in ("title","subject","name")):
                clean=re.sub(r"\s+"," ",value).strip()
                if 4 <= len(clean) <= 180 and clean not in titles: titles.append(clean)
    return titles[:20]

def grade(value: float, pass_value: float, warn_value: float, higher: bool=True) -> str:
    if higher: return "PASS" if value >= pass_value else "WARN" if value >= warn_value else "FAIL"
    return "PASS" if value <= pass_value else "WARN" if value <= warn_value else "FAIL"

def money(value: float) -> str: return f"{value/100_000_000:,.0f}억원"

def make_candidate(base: dict[str, Any], excluded: set[str]) -> dict[str, Any] | None:
    code=base["code"]
    try:
        price_payload=fetch_json(f"/api/domestic/detail/{code}/price"); time.sleep(.18)
        day_payload=fetch_json(f"/api/domestic/detail/{code}/siseDay?pageSize=60")
    except Exception: return None
    p=normalize_price(price_payload,base); daily=normalize_daily(day_payload)
    if not p["price"] or not p["high"] or not p["low"] or p["high"] <= p["low"]: return None
    current={"o":p["open"] or p["price"],"h":p["high"],"l":p["low"],"c":p["price"],"v":p["volume"] or 0,"date":datetime.now(KST).date().isoformat()}
    if not daily or daily[-1]["date"] != current["date"]: daily.append(current)
    else: daily[-1].update(current)
    hist=daily[:-1]; prev20=hist[-20:]
    positive_volumes=[x["v"] for x in prev20 if x["v"] > 0]
    avg_vol=statistics.mean(positive_volumes) if positive_volumes else 0
    volume_ratio=current["v"]/avg_vol if avg_vol else 0
    ma5=statistics.mean([x["c"] for x in daily[-5:]]) if len(daily)>=2 else current["c"]
    prev_high20=max([x["h"] for x in prev20],default=current["h"])
    high20_proximity=current["c"]/prev_high20 if prev_high20 else 0
    close_location=(current["c"]-current["l"])/(current["h"]-current["l"])
    upper_wick=(current["h"]-max(current["o"],current["c"]))/(current["h"]-current["l"])
    trade_value=p["trade_value"] or current["c"]*current["v"]
    last_lows=[x["l"] for x in daily[-4:-1]]
    support_candidates=[x for x in (ma5,prev_high20,max(last_lows,default=0)) if x and x < current["c"]]
    stop=max(support_candidates,default=current["l"]); stop_distance=max(0,(current["c"]-stop)/current["c"])
    notices=[]; news=[]
    try: notices=extract_titles(fetch_json(f"/api/domestic/detail/notice?itemCode={code}&startIdx=0&pageSize=6")); time.sleep(.12)
    except Exception: pass
    try: news=extract_titles(fetch_json(f"/api/domestic/detail/news?itemCode={code}&page=1&pageSize=6")); time.sleep(.12)
    except Exception: pass
    texts=notices+news; negative=[x for x in texts if any(k in x for k in NEGATIVE)]; positive=[x for x in texts if any(k in x for k in POSITIVE)]
    catalyst_status="FAIL" if negative else "PASS" if positive else "MISSING"; catalyst=(negative[0] if negative else positive[0] if positive else "재료 수동 확인")[:46]
    liq=grade(trade_value,50_000_000_000,30_000_000_000); loc=grade(close_location,.75,.65); wick=grade(upper_wick,.30,.45,False); vol=grade(volume_ratio,1.30,1.0); high=grade(high20_proximity,.95,.90); stop_st=grade(stop_distance,.035,.045,False); risk="FAIL" if code in excluded else "PASS"
    chart="PASS" if current["c"]>=ma5 and vol=="PASS" and high=="PASS" else "WARN" if current["c"]>=ma5 and high!="FAIL" else "FAIL"
    checks=[
        {"id":"RISK","name":"위험 종목 제외","role":"required","status":risk,"value":"정상" if risk=="PASS" else "관리·경보 목록","rule":"관리·정지·경보 제외"},
        {"id":"LIQ","name":"거래대금·유동성","role":"required","status":liq,"value":money(trade_value),"rule":"500억원 통과 / 300억원 경계"},
        {"id":"LEADER","name":"대장·재료","role":"manual","status":catalyst_status,"value":catalyst,"rule":"대장 또는 강한 직접 재료"},
        {"id":"CHART","name":"차트 자격","role":"required","status":chart,"value":f"5일선 {ma5:,.0f} · 20일고점 {high20_proximity:.2f}","rule":"기준봉·전고점·거래대금"},
        {"id":"LOCATION","name":"고가권 마감","role":"required","status":loc,"value":f"{close_location:.2f}","rule":"0.75 통과 / 0.65 미만 제외"},
        {"id":"WICK","name":"윗꼬리","role":"required","status":wick,"value":f"{upper_wick:.2f}","rule":"0.30 통과 / 0.45 초과 제외"},
        {"id":"VOLUME","name":"거래량 유지","role":"supporting","status":vol,"value":f"{volume_ratio:.2f}배","rule":"20일 평균 1.30배"},
        {"id":"STOP","name":"구조 손절","role":"required","status":stop_st,"value":f"{stop:,.0f}원 / {stop_distance*100:.1f}%","rule":"4.5% 이내"},
        {"id":"HOGA","name":"호가·체결","role":"manual","status":"MISSING","value":"실제 HTS 확인 필요","rule":"매도 물량 체결 소화"},
    ]
    required=[x["status"] for x in checks if x["role"]=="required"]
    final="EXCLUDE" if "FAIL" in required or catalyst_status=="FAIL" else "WATCH" if "WARN" in required or catalyst_status=="MISSING" else "READY"
    score=min(15,trade_value/50_000_000_000*15)+{"PASS":20,"WARN":12,"FAIL":0}[chart]+max(0,min(20,close_location*20/.75))+max(0,min(15,(.55-upper_wick)/.25*15))+min(10,volume_ratio/1.3*10)+min(10,high20_proximity/.95*10)+{"PASS":10,"MISSING":4,"FAIL":0}[catalyst_status]
    reasons=[]
    if liq=="PASS": reasons.append("거래대금 필수 기준 통과")
    if loc=="PASS": reasons.append("당일 고저 범위 상단에서 마감")
    elif loc=="FAIL": reasons.append("장중 상승폭을 크게 반납해 고가권 자격 실패")
    if wick=="FAIL": reasons.append("긴 윗꼬리로 상단 매물 출회 위험")
    if chart=="PASS": reasons.append("5일선·20일 고점·거래량 차트 자격 통과")
    if catalyst_status=="MISSING": reasons.append("대장주와 재료 강도는 수동 확인 필요")
    return {"code":code,"name":base["name"],"market":base.get("market") or "KRX","status":final,"score":round(score,1),"price":round(current["c"]),"change_rate":round(p["change_rate"] or 0,2),"trade_value":round(trade_value),"close_location":round(close_location,4),"upper_wick_ratio":round(upper_wick,4),"volume_ratio":round(volume_ratio,3),"high20_proximity":round(high20_proximity,4),"ma5":round(ma5,2),"stop_price":round(stop),"stop_distance":round(stop_distance,4),"catalyst":catalyst,"leader":"대장성 수동 확인","reasons":reasons,"checks":checks,"plan":{"entry":f"{current['c']:,.0f}원 부근 · HTS 확인 뒤 결정","stop":f"{stop:,.0f}원","next_up":"시초 강세 후 단기상승 1파 분할청산","next_flat":"전일 종가선·첫 눌림 저점 지지 확인","next_down":"전일 종가선 회복 실패 시 정리"},"daily":[{k:round(v,2) if isinstance(v,float) else v for k,v in x.items() if k in ('o','h','l','c','v')} for x in daily[-20:]],"sources":sorted(set(base.get("sources",[])))}

def collect(limit: int) -> dict[str, Any]:
    ranking_kinds={"trading-value":"priceTop","rise":"up","volume-surge":"upperQuantTop","52-week-high":"high52week"}; merged={}; source_health=[]
    for semantic,order_type in ranking_kinds.items():
        path="/api/domestic/market/stock/default?"+urllib.parse.urlencode({"tradeType":"KRX","marketType":"ALL","orderType":order_type,"startIdx":0,"pageSize":35})
        try:
            rows=normalize_ranking(fetch_json(path),semantic); source_health.append({"source":semantic,"ok":True,"rows":len(rows)})
            for row in rows:
                entry=merged.setdefault(row["code"],{**row,"sources":[]}); entry["sources"].append(semantic)
                for key in ("price","trade_value","volume","change_rate","market"):
                    if not entry.get(key) and row.get(key): entry[key]=row[key]
        except Exception as exc: source_health.append({"source":semantic,"ok":False,"error":str(exc)[:120]})
        time.sleep(.25)
    excluded=set()
    for order_type,alert in (("statusTag",None),("tradeStopYn",None),("marketAlertType","01"),("marketAlertType","02"),("marketAlertType","03")):
        params={"tradeType":"KRX","marketType":"ALL","orderType":order_type,"startIdx":0,"pageSize":100}
        if alert: params["alertType"]=alert
        try:
            for row in normalize_ranking(fetch_json("/api/domestic/market/stock/default?"+urllib.parse.urlencode(params)),order_type): excluded.add(row["code"])
        except Exception: pass
        time.sleep(.12)
    bases=sorted(merged.values(),key=lambda x:(len(set(x["sources"])),x.get("trade_value") or 0,x.get("change_rate") or 0),reverse=True)[:limit]
    candidates=[]
    for base in bases:
        candidate=make_candidate(base,excluded)
        if candidate: candidates.append(candidate)
        time.sleep(.25)
    candidates.sort(key=lambda x:({"READY":3,"WATCH":2,"EXCLUDE":1}.get(x["status"],0),x["score"]),reverse=True)
    return {"generated_at":datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST"),"source_status":"LIVE" if any(x.get("ok") for x in source_health) and candidates else "PARTIAL","method":"라르고 종가 필수 게이트 + 네이버 공개 읽기 전용 자료","source_health":source_health,"candidate_count":len(candidates),"candidates":candidates,"disclaimer":"주문 신호가 아니며 대장·재료·호가·체결은 실제 HTS 수동 확인이 필요합니다."}

def main() -> None:
    parser=argparse.ArgumentParser(); parser.add_argument("--output",required=True); parser.add_argument("--limit",type=int,default=30); args=parser.parse_args(); out=Path(args.output); out.parent.mkdir(parents=True,exist_ok=True)
    try: result=collect(max(5,min(args.limit,50)))
    except Exception as exc: result={"generated_at":datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST"),"source_status":"FAILED","error":str(exc),"candidates":[]}
    out.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8"); print(json.dumps({"source_status":result.get("source_status"),"candidate_count":len(result.get("candidates",[]))},ensure_ascii=False))

if __name__ == "__main__": main()
