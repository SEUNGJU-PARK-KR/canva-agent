#!/usr/bin/env python3
"""Repair automated steps 5 and 6 against the reviewed v2 screener plan schema."""
from __future__ import annotations
import argparse, json, math, re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

ELIGIBLE={"CLOSE_READY","NEXT_DAY_HOGA_CONFIRM","WATCH","READY"}

def num(v:Any)->int|None:
    if v is None or v=="" or isinstance(v,bool): return None
    if isinstance(v,(int,float)):
        return int(round(float(v))) if math.isfinite(float(v)) else None
    m=re.search(r"-?\d+(?:\.\d+)?",str(v).replace(",","").replace("원",""))
    return int(round(float(m.group()))) if m else None

def state(row:Mapping[str,Any])->str:
    return str(row.get("final_status") or row.get("state") or row.get("status") or "UNKNOWN").upper()

def entry_of(row:Mapping[str,Any])->tuple[str,int|None]:
    values=[
        ("종가 단일가 예상체결가",row.get("estimated_price") if row.get("single_price_mode") else None),
        ("스크리너 진입 기준가",row.get("entry_reference")),
        ("현재·확정 종가",row.get("price") or row.get("close")),
    ]
    for name,value in values:
        value=num(value)
        if value and value>0:return name,value
    return "없음",None

def supports(row:Mapping[str,Any],entry:int)->list[dict[str,Any]]:
    out=[]
    def add(name:str,value:Any,priority:int,source:str):
        price=num(value)
        if price and 0<price<entry:out.append({"name":name,"price":price,"priority":priority,"source":source})
    plan=row.get("plan") if isinstance(row.get("plan"),Mapping) else {}
    for index,item in enumerate(plan.get("supports") or []):
        if not isinstance(item,Mapping):continue
        name=str(item.get("name") or f"계획 구조선 {index+1}"); lower=name.replace(" ","")
        if any(key in lower for key in ("돌파","박스","전고점")):priority=1
        elif "기준봉종가" in lower:priority=2
        elif "기준봉저점" in lower:priority=3
        elif "5일선" in lower:priority=5
        elif any(key in lower for key in ("10일선","20일선","60일선","120일선")):priority=6
        else:priority=4
        add(name,item.get("price"),priority,"plan.supports")
    add("기존 구조 손절선",row.get("structure_stop") or row.get("stop_price"),1,"candidate")
    add("오후 마지막 눌림 저점",row.get("last_afternoon_pullback_low") or row.get("afternoon_low"),1,"candidate")
    add("돌파선·박스 상단",row.get("breakout_support") or row.get("box_top"),1,"candidate")
    ref=row.get("reference_candle") if isinstance(row.get("reference_candle"),Mapping) else {}
    add("기준봉 종가",ref.get("close") or ref.get("closePrice") or ref.get("price"),2,"reference_candle")
    add("기준봉 저점",ref.get("low") or ref.get("lowPrice"),3,"reference_candle")
    metrics=row.get("metrics") if isinstance(row.get("metrics"),Mapping) else {}
    if row.get("breakout_20d") or metrics.get("breakout_20d"):
        add("돌파한 20일 전고점",row.get("prior20_high") or metrics.get("prior20_high"),2,"metrics")
    add("5일선 보조선",row.get("ma5") or metrics.get("ma5"),5,"metrics")
    add("당일 저점",row.get("low") or metrics.get("low"),9,"candidate")
    add("최종 무효화선",plan.get("invalidation"),8,"plan.invalidation")
    unique={}
    for item in out:
        old=unique.get(item["price"])
        if old is None or item["priority"]<old["priority"]:unique[item["price"]]=item
    return list(unique.values())

def risk_plan(row:Mapping[str,Any],max_rate:float=.06)->dict[str,Any]:
    entry_source,entry=entry_of(row)
    if not entry:return {"status":"FAIL","exact":True,"reason":"유효한 진입 기준가가 없습니다."}
    options=supports(row,entry)
    if not options:return {"status":"FAIL","exact":True,"entry_price":entry,"entry_source":entry_source,"reason":"진입가 아래 유효 구조선이 없습니다."}
    chosen=sorted(options,key=lambda x:(entry-x["price"],x["priority"]))[0]
    risk=entry-chosen["price"];rate=risk/entry;valid=rate<=max_rate
    return {"status":"PASS" if valid else "FAIL","exact":True,"entry_price":entry,"entry_source":entry_source,
        "stop_price":chosen["price"],"stop_source":chosen["name"],"risk_per_share":risk,"risk_rate":round(rate,6),
        "one_r_price":entry+risk,"two_r_price":entry+2*risk,"max_risk_rate":max_rate,"valid":valid,
        "support_candidates":sorted(options,key=lambda x:(entry-x["price"],x["priority"])),
        "reason":f"{chosen['name']} {chosen['price']:,}원 기준 위험거리 {rate:.2%}"+("" if valid else f" · 허용 {max_rate:.2%} 초과")}

def next_plan(row:Mapping[str,Any],risk:Mapping[str,Any])->dict[str,Any]:
    if risk.get("status")!="PASS":return {"status":"FAIL","exact":True,"reason":"구조 손절 거리가 허용 범위를 통과하지 못해 익일 보유 계획을 만들지 않았습니다."}
    entry=int(risk["entry_price"]);stop=int(risk["stop_price"]);one_r=int(risk["one_r_price"]);prior_high=num(row.get("high") or row.get("prior_high"))
    target=f"전일 고가 {prior_high:,}원 또는 1R {one_r:,}원" if prior_high else f"1R {one_r:,}원"
    return {"status":"PASS","exact":True,
        "gap_thresholds":{"gap_up_from":round(entry*1.01),"flat_low":round(entry*.99),"flat_high":round(entry*1.01),"gap_down_below":round(entry*.99)},
        "gap_up":f"시초가가 {entry*1.01:,.0f}원 이상이면 첫 눌림이 전일 종가 {entry:,}원과 {risk['stop_source']} {stop:,}원을 지키는지 확인합니다. {target}에서 분할 청산합니다.",
        "flat":f"시초가가 {entry*.99:,.0f}~{entry*1.01:,.0f}원이면 전일 종가 {entry:,}원 재지지와 최우선 매수호가 유지가 함께 나올 때만 보유합니다. {stop:,}원 이탈 시 종료합니다.",
        "gap_down":f"시초가가 {entry*.99:,.0f}원 아래면 첫 3~5분 안에 전일 종가 {entry:,}원을 회복하는지 봅니다. 회복 실패 또는 {stop:,}원 이탈 시 정리합니다.",
        "reason":"진입가·구조 손절선·1R로 갭상승·보합·갭하락 계획을 생성했습니다."}

def upsert(row:dict[str,Any],check:dict[str,Any]):
    checks=row.setdefault("checks",[])
    if isinstance(checks,list):
        checks[:]=[x for x in checks if not(isinstance(x,Mapping) and x.get("id")==check["id"])]
        checks.append(check)
    elif isinstance(checks,dict):checks[check["id"]]=check

def main()->int:
    ap=argparse.ArgumentParser();ap.add_argument("--data",type=Path,required=True);ap.add_argument("--audit",type=Path);args=ap.parse_args()
    data=json.loads(args.data.read_text(encoding="utf-8"));rows=data.get("candidates") or data.get("results") or [];counts={}
    for row in rows:
        if not isinstance(row,dict):continue
        auto=row.setdefault("automation_356",{});risk=risk_plan(row);plan=next_plan(row,risk);bid=auto.get("best_bid") or {"status":"UNKNOWN"}
        auto["risk_plan"]=risk;auto["next_day_plan"]=plan;auto["schema_repair"]="reviewed-v2-plan-supports"
        if state(row) not in ELIGIBLE:status="NOT_ELIGIBLE"
        elif bid.get("status")=="FAIL" or risk.get("status")=="FAIL" or plan.get("status")=="FAIL":status="WEB_REJECTED"
        elif bid.get("status")=="PASS":status="WEB_PLAN_READY"
        else:status="WEB_CONFIRMATION_INCOMPLETE"
        auto["status"]=status;counts[status]=counts.get(status,0)+1
        upsert(row,{"id":"ENTRY_STOP_AUTO","name":"진입가·구조 손절 자동기록","role":"required","status":risk["status"],"value":{k:risk.get(k) for k in("entry_price","stop_price","risk_rate","one_r_price")},"rule":"계획 구조선 중 진입가 아래 가장 가까운 선, 최대 위험 6%"})
        upsert(row,{"id":"NEXT_DAY_PLAN_AUTO","name":"익일 갭별 대응계획","role":"required","status":plan["status"],"value":plan.get("gap_thresholds"),"rule":"갭상승·보합·갭하락 대응계획 생성"})
    data.setdefault("automation_356",{})["counts"]=counts;data["automation_356"]["plan_schema"]="candidate.plan.supports + candidate.plan.invalidation"
    args.data.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")
    audit={"status":"PASS","candidate_count":len(rows),"counts":counts,"risk_pass":sum((r.get("automation_356") or {}).get("risk_plan",{}).get("status")=="PASS" for r in rows),"next_day_pass":sum((r.get("automation_356") or {}).get("next_day_plan",{}).get("status")=="PASS" for r in rows)}
    if args.audit:args.audit.write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(audit,ensure_ascii=False));return 0
if __name__=="__main__":raise SystemExit(main())
