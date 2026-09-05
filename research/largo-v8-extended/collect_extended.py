#!/usr/bin/env python3
"""Collect retrospective DAILY proxies, not executable 15:18/09:06 backtests.
No orders. Preserve raw responses. Candidate recipe imported from pinned repository.
"""
from pathlib import Path
import concurrent.futures as cf
import datetime as dt
import functools, gzip, hashlib, importlib.metadata, itertools, json, math, re, shutil, sys
import numpy as np
import pandas as pd
from gs_quant.timeseries.econometrics import correlation, SeriesType
sys.path.insert(0,'scripts')
sys.path.insert(0,'research/largo-target3-20day/source')
import backfill_largo_target3_20day as b
from largo_close_v6_shadow import evidence_audit, is_common_stock_name
ROOT=Path('v8-extended-data')
for s in ['raw','output','source','qc']:(ROOT/s).mkdir(parents=True,exist_ok=True)
for f in ['research/largo-target3-20day/source/backfill_largo_target3_20day.py','scripts/largo_material_0906.py','scripts/largo_close_v6_shadow.py',__file__]:
    shutil.copy2(f,ROOT/'source'/Path(f).name)
START='20231001'; END='20260430'
original_get=b.get
requests_log=[]
def archived_get(url,**kwargs):
    response=original_get(url,**kwargs)
    key=hashlib.sha256(url.encode()).hexdigest()
    dest=ROOT/'raw'/(key+'.gz')
    if not dest.exists():dest.write_bytes(gzip.compress(response.content))
    requests_log.append({'url':url,'file':dest.name,'sha256':hashlib.sha256(response.content).hexdigest(),'status':response.status_code})
    return response
b.get=archived_get
original_chart=b.chart_rows
b.chart_rows=lambda code,count=1400: original_chart(code,count=1400)
universe,market_errors=b.market_universe()
print('universe',len(universe),flush=True)
(ROOT/'output/universe.json').write_text(json.dumps(universe,ensure_ascii=False))
charts,chart_errors=b.fetch_charts(universe,16)
assert '005930' in charts and len(charts)>1000,(len(charts),chart_errors[:3])
indices=b.index_charts(charts)
calendar=[str(r['date']) for r in charts['005930']]
dates=[d for d in calendar if START<=d<=END]
assert dates and dates[0]<'20240101' and dates[-1]=='20260430',(dates[:1],dates[-1:])
next_map={d:calendar[calendar.index(d)+1] for d in dates}
(ROOT/'output/price_history.json.gz').write_bytes(gzip.compress(json.dumps(charts,separators=(',',':')).encode()))
print('dates',len(dates),dates[0],dates[-1],flush=True)
# Source features only use bars up to the signal date. Accelerate rolling computations.
features_by_date={d:[] for d in dates}
for code,bars in charts.items():
    frame=pd.DataFrame(bars); close=frame.c; volume=frame.v; previous=close.shift(1)
    avg20=volume.shift(1).rolling(20,min_periods=1).mean()
    high52=frame.h.shift(1).rolling(252,min_periods=1).max()
    typical=frame[['o','h','l','c']].mean(axis=1)
    for i,row in frame.iterrows():
        d=str(row.date)
        if d not in features_by_date or i<1:continue
        if float(row.v)<=0:continue
        features_by_date[d].append({'code':code,'name':universe[code]['name'],'market':universe[code]['market'],'date':d,
          'o':float(row.o),'h':float(row.h),'l':float(row.l),'c':float(row.c),'v':float(row.v),
          'prev_close':float(previous.iloc[i]),'change_rate':float((close.iloc[i]/previous.iloc[i]-1)*100),
          'trade_value':float(typical.iloc[i]*volume.iloc[i]),'volume_surge':float(volume.iloc[i]/avg20.iloc[i]) if avg20.iloc[i]>0 else 0.,
          'high52_ratio':float(close.iloc[i]/high52.iloc[i]),'market_cap':float(universe[code]['shares_proxy']*row.c),
          'shares_proxy':float(universe[code]['shares_proxy'])})
selected={}
for i,d in enumerate(dates):
    full=features_by_date[d]
    ranks=b.source_ranks(full)
    selected[d]=b.reranked_shortlist(full,ranks,d,{},80)
    if i%50==0:print('shortlist',d,len(selected[d]),flush=True)
assert all(len(x)<=24 for x in selected.values())
themes,code_themes,theme_errors=b.theme_catalog()
(ROOT/'output/themes.json').write_text(json.dumps(themes,ensure_ascii=False))
codes=sorted({r['code'] for xs in selected.values() for r in xs})
oldest=dt.datetime.strptime(START,'%Y%m%d').replace(tzinfo=b.KST)-dt.timedelta(days=15)
news,news_errors=b.fetch_all_news(codes,oldest,16)
(ROOT/'output/news_history.json.gz').write_bytes(gzip.compress(json.dumps(news,ensure_ascii=False,default=str).encode()))
flat=[];details=[]
def pct(x,entry):return (float(x)/entry-1)*100 if x is not None and entry>0 else None
for di,d in enumerate(dates):
    for base in selected[d]:
        code=base['code']; tm=b.historical_theme_metrics(code,d,themes,code_themes,charts,indices)
        cand,score,gate=b.candidate_row(base,d,charts,indices,tm,news.get(code,[]))
        st=score.get('structure') or {}; ev=score.get('evidence') or {}; met=cand['metrics']
        aud=evidence_audit(score)
        pos=indices[code][d]; si=charts[code][pos]
        ni=indices[code].get(next_map[d]); nb=charts[code][ni] if ni is not None else {}
        p=float(si['c']); valid=bool(nb and float(nb['v'])>0)
        def metric(key,default=None):return st.get(key) if st.get(key) is not None else met.get(key,default)
        iso=lambda x:f'{x[:4]}-{x[4:6]}-{x[6:]}'
        rec={'m':iso(d)[:7],'d':iso(d),'n':iso(next_map[d]),'c':code,'nm':base['name'],'mk':base['market'],
           'hr':bool(score.get('hard_reject')),'cs':is_common_stock_name(base['name']),
           'px':p,'tv':cand['trade_value'],'mc':cand['market_cap'],'chg':met['change_rate'],
           'dig':metric('digest_ratio'),'risk':st.get('risk_rate'),'cl':metric('close_location'),
           'wick':metric('upper_wick'),'body':metric('body_ratio'),'pat':st.get('pattern_score'),
           'th':tm.get('name'),'br':tm.get('breadth'),'rank':tm.get('leader_rank'),'fol':tm.get('follower_strong_count'),
           'fturn':tm.get('follower_turnover'),'dir':ev.get('directness_points'),'fresh':ev.get('freshness_points'),
           'estr':ev.get('event_strength'),'evt':ev.get('title'),'evidence_at':ev.get('at'),
           'audit':aud['passed'],'neg':aud['negative_context'],'ask':None,'bid':None,'spr':None,
           'mode':'DAILY_PROXY','open':pct(nb.get('o'),p) if valid else None,
           'high':pct(nb.get('h'),p) if valid else None,'low':pct(nb.get('l'),p) if valid else None,
           'exit':pct(nb.get('c'),p) if valid else None,'known':valid,
           'next_open_price':nb.get('o'),'next_high_price':nb.get('h'),'next_low_price':nb.get('l'),'next_close_price':nb.get('c'),
           'historical_alerts_verified':False,'theme_membership_asof':False,'candidate_kind':'RETROSPECTIVE_PROXY'}
        flat.append(rec)
        details.append({'date':iso(d),'code':code,'candidate':cand,'scored':score,'audit':aud})
    if di%50==0:print('scored',d,len(flat),flush=True)
frame=pd.DataFrame(flat)
frame.to_csv(ROOT/'output/extended_candidates.csv',index=False,encoding='utf-8-sig')
(ROOT/'output/candidate_details.json.gz').write_bytes(gzip.compress(json.dumps(details,ensure_ascii=False,default=str,separators=(',',':')).encode()))
# Correlations only for pairs that can pass the frozen v8 pool; outcomes never enter this mask.
pool=(~frame.hr)&frame.cs&(~frame.neg)&frame.chg.between(0,15)&frame.risk.between(0,.15)&frame.dig.between(.2,1.5)&(frame.body>=.45)&frame.pat.between(50,90)&(((frame.br>=.5)&(frame['rank']<=5))|(frame.audit&(frame['dir']>=14)&(frame.fresh>=7)))
series={}
for code in frame.loc[pool,'c'].unique():
    bars=pd.DataFrame(charts[code]); ix=pd.to_datetime(bars.date); c=pd.Series(bars.c.to_numpy(),index=ix); o=pd.Series(bars.o.to_numpy(),index=ix);v=pd.Series(bars.v.to_numpy(),index=ix)
    series[code]={'cc':c.pct_change(fill_method=None).where(v>0),'gap':(o/c.shift(1)-1).where(v>0)}
corrs=[]; calls=0;maxerror=0.
for d,group in frame.loc[pool].groupby('d'):
    window=[x for x in calendar if x<d.replace('-','')][-60:]; idx=pd.to_datetime(window)
    for a,c in itertools.combinations(sorted(group.c.unique()),2):
        item={'signal_date':d,'a':a,'b':c,'window_start':window[0],'window_end':window[-1]}
        for kind in ['gap','cc']:
            xy=pd.concat([series[a][kind].reindex(idx),series[c][kind].reindex(idx)],axis=1).dropna()
            item['n_'+kind]=len(xy); item['corr_'+kind]=None
            if len(xy)>=40 and xy.iloc[:,0].std()>1e-12 and xy.iloc[:,1].std()>1e-12:
                gs=float(correlation(xy.iloc[:,0],xy.iloc[:,1],type_=SeriesType.RETURNS).iloc[-1]);calls+=1
                check=float(np.corrcoef(xy.iloc[:,0],xy.iloc[:,1])[0,1]);maxerror=max(maxerror,abs(gs-check))
                assert abs(gs-check)<1e-8
                item['corr_'+kind]=gs
        assert item['window_end']<d.replace('-','')
        corrs.append(item)
pd.DataFrame(corrs,columns=['signal_date','a','b','window_start','window_end','n_gap','corr_gap','n_cc','corr_cc']).to_csv(ROOT/'output/gs_correlations.csv',index=False)
metadata={'created_at':dt.datetime.now(b.KST).isoformat(),'requested_start':START,'requested_end':END,
 'dates':len(dates),'candidate_rows':len(flat),'codes':len(codes),'universe':len(universe),'charts':len(charts),
 'pool_rows':int(pool.sum()),'evidence_rows':int(frame.audit.sum()),'gs_quant_version':importlib.metadata.version('gs-quant'),
 'gs_calls':calls,'gs_pairs':len(corrs),'gs_max_numpy_error':maxerror,
 'errors':{'market':market_errors,'chart':chart_errors,'theme':theme_errors,'news':news_errors},
 'limits':['DAILY proxy entry and exit, no historical intraday quotes','Current listing universe creates survivorship bias',
 'Current shares-proxy and current theme membership are not point-in-time','News retrieval history has finite depth; missing old evidence is unverified',
 'Historical trading-alert status is unverified','Adjusted prices may reflect later corporate actions','Signal-day final data cannot reconstruct a 15:18 decision']}
(ROOT/'qc/collection_metadata.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2))
(ROOT/'qc/request_manifest.json').write_text(json.dumps(requests_log,ensure_ascii=False))
print(json.dumps({k:v for k,v in metadata.items() if k!='errors'},ensure_ascii=False,indent=2),flush=True)
