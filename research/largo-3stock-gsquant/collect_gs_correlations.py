#!/usr/bin/env python3
"""Read-only research: restore frozen universe; collect history; run actual GS Quant.
No prices on/after a signal date enter its correlation window. No order APIs.
"""
import concurrent.futures as cf
import csv, datetime as dt, hashlib, importlib.metadata, io, itertools, json, os, re, time, zipfile
from collections import defaultdict
from pathlib import Path
import numpy as np
import pandas as pd
import requests
from gs_quant.timeseries.econometrics import correlation, SeriesType

ROOT=Path('three-stock-research')
for name in ('input','raw','output','source','qc'): (ROOT/name).mkdir(parents=True,exist_ok=True)
REPO='SEUNGJU-PARK-KR/canva-agent'
ARTIFACTS=[(9876125507,'2026-05','may-candidate-outcomes.csv'),(9873546371,'2026-06','june-candidate-outcomes.csv'),(9874347845,'2026-07','july-candidate-outcomes.csv'),(9794530194,'2026-08','target3_20day_rows.csv')]
days=defaultdict(set)
archives=[]
for aid,month,suffix in ARTIFACTS:
    r=requests.get(f'https://api.github.com/repos/{REPO}/actions/artifacts/{aid}/zip',headers={'Authorization':'Bearer '+os.environ['GH_TOKEN'],'Accept':'application/vnd.github+json'},timeout=60)
    r.raise_for_status(); data=r.content
    (ROOT/'input'/f'{aid}.zip').write_bytes(data)
    z=zipfile.ZipFile(io.BytesIO(data)); name=next(n for n in z.namelist() if n.endswith('/'+suffix))
    for row in csv.DictReader(io.StringIO(z.read(name).decode('utf-8-sig'))):
        d=row['signal_date']
        if d.startswith(month): days[d].add(str(row['code']).zfill(6))
    archives.append({'artifact_id':aid,'sha256':hashlib.sha256(data).hexdigest(),'csv':name})
assert sum(map(len,days.values()))==1944
codes=sorted(set.union(*days.values()))
(ROOT/'output'/'universe.json').write_text(json.dumps({d:sorted(v) for d,v in sorted(days.items())}))

def fetch_chart(code):
    error=''
    for attempt in range(3):
        try:
            url='https://fchart.stock.naver.com/sise.nhn'
            r=requests.get(url,params={'symbol':code,'timeframe':'day','count':460,'requestType':0},headers={'User-Agent':'Mozilla/5.0','Referer':'https://finance.naver.com/'},timeout=25)
            r.raise_for_status(); (ROOT/'raw'/f'{code}.xml').write_bytes(r.content)
            bars={}
            for packed in re.findall(r'data="([^"]+)"',r.text):
                p=packed.split('|')
                if len(p)<6 or not re.fullmatch(r'20\d{6}',p[0]): continue
                o,h,l,c,v=map(float,p[1:6])
                if min(o,h,l,c)<=0 or h<max(o,c,l) or l>min(o,c,h): continue
                date=f'{p[0][:4]}-{p[0][4:6]}-{p[0][6:]}'
                bars[date]={'o':o,'h':h,'l':l,'c':c,'v':v}
            if len(bars)<5: raise ValueError('insufficient bars')
            return code,bars,None
        except Exception as exc:
            error=f'{type(exc).__name__}: {exc}'
            time.sleep(.5*(attempt+1))
    return code,{},error

charts={};errors=[]
with cf.ThreadPoolExecutor(max_workers=12) as pool:
    for i,(code,bars,error) in enumerate(pool.map(fetch_chart,codes),1):
        charts[code]=bars
        if error: errors.append({'code':code,'error':error})
        if i%50==0: print('charts',i,len(codes),'errors',len(errors),flush=True)
(ROOT/'output'/'price_history.json').write_text(json.dumps(charts,separators=(',',':')))
calendar=sorted(charts['005930'])
series={}
for code,bars in charts.items():
    ordered=sorted(bars); close=pd.Series([bars[d]['c'] for d in ordered],index=pd.to_datetime(ordered),dtype=float)
    opens=pd.Series([bars[d]['o'] for d in ordered],index=pd.to_datetime(ordered),dtype=float)
    # No filling missing prices. Zero-volume sessions are unusable observations.
    volume=pd.Series([bars[d]['v'] for d in ordered],index=pd.to_datetime(ordered),dtype=float)
    series[code]={'cc':close.pct_change(fill_method=None).where(volume>0),'gap':(opens/close.shift(1)-1).where(volume>0)}
probe=pd.Series([.01,-.02,.03,-.01,.04],index=pd.date_range('2020-01-01',periods=5))
self_corr=float(correlation(probe,probe,type_=SeriesType.RETURNS).iloc[-1]); assert abs(self_corr-1)<1e-8,self_corr
records=[];max_error=0;calls=1
for day in sorted(days):
    window=[d for d in calendar if d<day][-60:]
    idx=pd.to_datetime(window)
    for a,b in itertools.combinations(sorted(days[day]),2):
        record={'signal_date':day,'a':a,'b':b,'window_start':window[0] if window else None,'window_end':window[-1] if window else None}
        for kind in ('gap','cc'):
            x=series[a][kind].reindex(idx);y=series[b][kind].reindex(idx)
            valid=x.notna()&y.notna();x=x[valid];y=y[valid]
            record[f'n_{kind}']=len(x)
            record[f'corr_{kind}']=None;record[f'cov_{kind}']=None
            if len(x)>=40 and x.std()>1e-12 and y.std()>1e-12:
                gs=float(correlation(x,y,type_=SeriesType.RETURNS).iloc[-1]);calls+=1
                manual=float(np.corrcoef(x.to_numpy(),y.to_numpy())[0,1])
                max_error=max(max_error,abs(gs-manual));assert abs(gs-manual)<1e-8
                record[f'corr_{kind}']=gs;record[f'cov_{kind}']=float(np.cov(x,y,ddof=1)[0,1])
        assert not record['window_end'] or record['window_end']<day
        records.append(record)
    print('correlations',day,len(records),flush=True)
with (ROOT/'output'/'gs_correlations.csv').open('w',newline='') as f:
    w=csv.DictWriter(f,fieldnames=list(records[0]));w.writeheader();w.writerows(records)
metadata={'created_at':dt.datetime.now(dt.timezone.utc).isoformat(),'gs_quant_version':importlib.metadata.version('gs-quant'),'gs_quant_function':'gs_quant.timeseries.econometrics.correlation','gs_calls':calls,'max_numpy_error':max_error,'archives':archives,'candidate_rows':sum(map(len,days.values())),'dates':len(days),'codes':len(codes),'chart_errors':errors,'pair_rows':len(records),'window_sessions':60,'minimum_joint_observations':40,'cutoff':'strictly before signal date','gap_definition':'open(t)/close(t-1)-1','close_definition':'close(t)/close(t-1)-1','limitations':['Archived candidate features are retrospective reconstructions.','Freshly retrieved daily history may contain retrospective corporate-action adjustments.','GS Quant is a calculation library, not a Goldman Sachs strategy endorsement.']}
(ROOT/'qc'/'gs_runtime.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2))
print(json.dumps(metadata,ensure_ascii=False,indent=2))
