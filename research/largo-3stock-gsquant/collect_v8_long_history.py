#!/usr/bin/env python3
"""Fixed-v8 retrospective daily proxy input builder. No trading or secrets.
Old news/risk flags are UNKNOWN. The direct-material lane receives no credit.
"""
from __future__ import annotations
import concurrent.futures as cf
import gzip, hashlib, importlib.util, json, math, os, re, sys, time
from pathlib import Path
from collections import defaultdict
from datetime import datetime, timezone
import numpy as np
import pandas as pd
ROOT=Path(os.environ.get('LARGO_OUTPUT','long-history'))
for part in ('input','output','raw','source','qc'):(ROOT/part).mkdir(parents=True,exist_ok=True)
sys.path.insert(0,str(Path('scripts').resolve()))
src=Path('research/largo-target3-20day/source/backfill_largo_target3_20day.py')
spec=importlib.util.spec_from_file_location('backfill',src); back=importlib.util.module_from_spec(spec); spec.loader.exec_module(back)
START='2022-01-01'; END='2026-04-30'; COUNT=2000

def dump(name,data):
    (ROOT/name).write_text(json.dumps(data,ensure_ascii=False,indent=2,default=str),encoding='utf-8')

def chart(code):
    target=ROOT/'raw'/f'{code}.xml'
    try:
        raw=back.get(f'{back.CHART}?symbol={code}&timeframe=day&count={COUNT}&requestType=0',timeout=25,retries=3).content
        target.write_bytes(raw)
        rows={}
        for text in re.findall(r'data="([^"]+)"',raw.decode('utf-8',errors='replace')):
            a=text.split('|')
            if len(a)<6 or not re.fullmatch(r'20\d{6}',a[0]):continue
            try:o,h,l,c,v=map(float,a[1:6])
            except ValueError:continue
            if min(o,h,l,c)<=0 or not l<=min(o,c)<=max(o,c)<=h:continue
            rows[a[0]]={'date':a[0],'o':o,'h':h,'l':l,'c':c,'v':v}
        return code,[rows[k] for k in sorted(rows)],None
    except Exception as e:return code,[],str(e)

def v8_pool(row):
    def f(k):
        a=row.get(k)
        return float(a) if a is not None else float('nan')
    return (not row['hr'] and row['cs'] and not row['neg'] and
        0<=f('chg')<=15 and 0<=f('risk')<=.15 and .2<=f('dig')<=1.5 and
        f('body')>=.45 and 50<=f('pat')<=90 and
        ((f('br')>=.5 and f('rank')<=5) or (row['audit'] and f('dir')>=14 and f('fresh')>=7)))

def main():
    begin=time.time()
    uni,errors=back.market_universe();dump('input/market_universe_snapshot.json',uni)
    dump('qc/market_errors.json',errors)
    print('MARKET',len(uni),flush=True)
    if len(uni)<1000:raise RuntimeError('Incomplete market universe; do not report full-market test')
    charts={};chart_errors={}
    codes=sorted(k for k,v in uni.items() if v['ordinary'])
    with cf.ThreadPoolExecutor(max_workers=16) as executor:
        for i,(code,rows,err) in enumerate(executor.map(chart,codes),1):
            if rows:charts[code]=rows
            if err:chart_errors[code]=err
            if i%250==0:print('CHART',i,len(codes),len(chart_errors),flush=True)
    dump('qc/chart_errors.json',chart_errors)
    if len(charts)<len(codes)*.95:raise RuntimeError('Too many missing charts')
    indices=back.index_charts(charts)
    calendar=[r['date'] for r in charts['005930']]
    dates=[d for d in calendar if START.replace('-','')<=d<=END.replace('-','')]
    next_dates={d:calendar[calendar.index(d)+1] for d in dates if calendar.index(d)+1<len(calendar)}
    dump('input/calendar.json',{'dates':dates,'next_dates':next_dates})
    print('CALENDAR',dates[0],dates[-1],len(dates),flush=True)
    byday=defaultdict(list)
    for code,rows in charts.items():
        frame=pd.DataFrame(rows)
        prev=frame.c.shift(1); avg=frame.v.shift(1).rolling(20,min_periods=1).mean()
        hi=frame.h.shift(1).rolling(252,min_periods=1).max()
        tv=frame[['o','h','l','c']].mean(axis=1)*frame.v
        for j in frame.index[(frame.date>=START.replace('-',''))&(frame.date<=END.replace('-',''))]:
            if j<120 or not prev.iloc[j]>0:continue
            row=rows[j]
            byday[row['date']].append({**row,'code':code,'name':uni[code]['name'],'market':uni[code]['market'],
                'prev_close':float(prev.iloc[j]),'change_rate':float((row['c']/prev.iloc[j]-1)*100),
                'trade_value':float(tv.iloc[j]),'volume_surge':float(row['v']/avg.iloc[j]) if avg.iloc[j]>0 else 0.,
                'high52_ratio':float(row['c']/hi.iloc[j]) if hi.iloc[j]>0 else 0.,
                'market_cap':uni[code]['shares_proxy']*row['c'],'shares_proxy':uni[code]['shares_proxy']})
    themes,codemap,theme_errors=back.theme_catalog()
    dump('input/theme_snapshot.json',themes);dump('input/theme_code_map.json',codemap);dump('qc/theme_errors.json',theme_errors)
    print('THEMES',len(themes),flush=True)
    allrows=[];count_daily=[];failures=[]
    for di,date in enumerate(dates,1):
        market=byday[date]; md={r['code']:r for r in market}
        shortlist=back.reranked_shortlist(market,back.source_ranks(market),date,{},80)
        theme_options=defaultdict(list)
        for tid,theme in themes.items():
            members=[md[c] for c in theme.get('members',[]) if c in md]
            if len(members)<3:continue
            rising=sum(r['change_rate']>0 for r in members); breadth=rising/len(members)
            ranked=sorted(members,key=lambda r:(r['change_rate'],r['trade_value']),reverse=True)
            followers=ranked[1:3];ft=sum(x['trade_value'] for x in followers);tt=sum(x['trade_value'] for x in ranked[:3])
            common={'name':theme['name'],'code':tid,'rising':rising,'total':len(members),'breadth':breadth,
                'follower_strong_count':sum(x['change_rate']>=2 and x['trade_value']>=1e10 for x in followers),
                'follower_turnover':ft,'follower_turnover_ratio':ft/tt if tt else None,
                'observed_breadth':True,'observed_leadership':True,'observed_followers':True,'membership_proxy':'current_catalog',
                'average_change':sum(x['change_rate'] for x in members)/len(members),'total_turnover':sum(x['trade_value'] for x in members)}
            for rank,r in enumerate(ranked,1):theme_options[r['code']].append({**common,'leader_rank':rank})
        today=[]
        for base in shortlist:
            code=base['code'];options=theme_options.get(code,[])
            tm=max(options,key=lambda x:(x['breadth'],x['average_change'],x['total_turnover'])) if options else {
                'name':None,'code':None,'rising':None,'total':None,'breadth':None,'leader_rank':None,
                'follower_strong_count':None,'follower_turnover':None,'observed_breadth':False,'observed_leadership':False,'observed_followers':False}
            try:
                cand,scored,_=back.candidate_row(base,date,charts,indices,tm,[])
                met=cand['metrics'];plan=cand['plan'];price=cand['price']
                nd=next_dates.get(date);pos=indices[code].get(nd);nb=charts[code][pos] if pos is not None else None
                rtn=lambda key:(nb[key]/price-1)*100 if nb else None
                valid=bool(nb and nb['v']>0)
                row={'m':date[:4]+'-'+date[4:6],'d':date[:4]+'-'+date[4:6]+'-'+date[6:],
                    'n':nd[:4]+'-'+nd[4:6]+'-'+nd[6:] if nd else None,
                    'c':code,'nm':base['name'],'mk':base['market'],'hr':bool(scored.get('hard_reject')),
                    'cs':back.ordinary(base['name']),'px':price,'tv':cand['trade_value'],'mc':base['market_cap'],
                    'chg':met['change_rate'],'dig':met['digest_ratio'],'risk':plan.get('stop_distance'),
                    'cl':met['close_location'],'wick':met['upper_wick'],'body':met['body_ratio'],'pat':cand['pattern']['score'],
                    'th':tm.get('name'),'br':tm.get('breadth'),'rank':tm.get('leader_rank'),'fol':tm.get('follower_strong_count'),
                    'fturn':tm.get('follower_turnover'),'dir':0.,'fresh':0.,'estr':None,'evt':None,'audit':False,'neg':False,
                    'ask':None,'bid':None,'spr':None,'mode':'DAILY_PROXY_LONG','open':rtn('o') if valid else None,
                    'high':rtn('h') if valid else None,'low':rtn('l') if valid else None,'exit':rtn('c') if valid else None,'known':valid,
                    'source_rank_score':base['source_rank_score'],'history_bars':indices[code][date]+1,
                    'news_status':'UNKNOWN_NO_ARCHIVED_EVIDENCE','historical_risk_flags':'UNKNOWN',
                    'theme_membership':'2026_snapshot_proxy','market_cap_basis':'snapshot_shares_x_adjusted_historical_price',
                    'signal_price_basis':'FULL_DAY_CLOSE','exact_0906_available':False}
                row['v8_pool']=v8_pool(row);today.append(row)
            except Exception as exc:failures.append({'date':date,'code':code,'error':repr(exc)})
        allrows.extend(today)
        count_daily.append({'date':date,'market_rows':len(market),'candidates':len(today),'v8_pool':sum(r['v8_pool'] for r in today)})
        if di%30==0:print('CANDIDATES',di,len(dates),len(allrows),len(failures),flush=True)
    pd.DataFrame(allrows).to_csv(ROOT/'output/historical_candidates.csv',index=False,encoding='utf-8-sig')
    pd.DataFrame(count_daily).to_csv(ROOT/'output/data_coverage_daily.csv',index=False)
    dump('qc/candidate_errors.json',failures)
    with gzip.open(ROOT/'input/all_daily_prices.json.gz','wt',encoding='utf-8') as f:json.dump(charts,f,separators=(',',':'))
    from gs_quant.timeseries.econometrics import correlation
    from gs_quant.timeseries.helper import Window,SeriesType
    from importlib.metadata import version
    prices={}
    for code,rs in charts.items():
        fr=pd.DataFrame(rs);fr.index=pd.to_datetime(fr.date,format='%Y%m%d')
        prices[code]=pd.DataFrame({'gap':fr.o/fr.c.shift(1)-1,'cc':fr.c/fr.c.shift(1)-1})
    import itertools
    pairs=[];calls=0;max_error=0
    frame=pd.DataFrame(allrows)
    for date,g in frame[frame.v8_pool].groupby('d'):
        cutoff=pd.Timestamp(date)
        cal_window=pd.to_datetime([d for d in calendar if d<date.replace('-','')][-60:],format='%Y%m%d')
        for a,b in itertools.combinations(sorted(g.c.unique()),2):
            vals={'signal_date':date,'a':a,'b':b}
            for kind in ['gap','cc']:
                pair=pd.concat([prices[a][kind].rename('a'),prices[b][kind].rename('b')],axis=1).reindex(cal_window).dropna()
                vals['n_'+kind]=len(pair)
                if len(pair)<40 or min(pair.std())<=0:
                    vals['corr_'+kind]=None;continue
                assert pair.index.max()<cutoff
                got=correlation(pair.a,pair.b,Window(len(pair),0),type_=SeriesType.RETURNS)
                x=float(got.iloc[-1]);n=float(pair.a.corr(pair.b));max_error=max(max_error,abs(x-n));calls+=1
                vals['corr_'+kind]=x;vals['last_'+kind]=str(pair.index.max().date())
            pairs.append(vals)
    pd.DataFrame(pairs,columns=['signal_date','a','b','n_gap','n_cc','corr_gap','corr_cc','last_gap','last_cc']).to_csv(ROOT/'output/gs_correlations.csv',index=False)
    source_files=[src,Path('scripts/largo_material_0906.py'),Path(__file__)];source_hashes={}
    for p in source_files:
        (ROOT/'source'/p.name).write_bytes(p.read_bytes());source_hashes[str(p)]=hashlib.sha256(p.read_bytes()).hexdigest()
    dump('qc/collection_summary.json',{'created_at':datetime.now(timezone.utc).isoformat(),'start':START,'end':END,
       'sessions':len(dates),'universe':len(uni),'charts':len(charts),'candidate_rows':len(allrows),'pool_rows':int(frame.v8_pool.sum()),
       'gs_quant_version':version('gs-quant'),'gs_calls':calls,'pair_rows':len(pairs),'max_gs_numpy_error':max_error,
       'errors':{'market':len(errors),'charts':len(chart_errors),'themes':len(theme_errors),'candidate':len(failures)},
       'source_hashes':source_hashes,'seconds':time.time()-begin,'exact_0906_rows':0,
       'limitations':['Current survivor universe; delisted names missing','Theme membership from 2026 snapshot',
       'Historical outstanding shares unavailable','No historical news or exchange risk flags; direct material lane receives no credit',
       'Full-day prices used, not 15:18 or 09:06','Adjusted prices may incorporate later corporate actions',
       'At least 120 prior available bars required','Cost is hypothetical fixed 0.30 percentage points']})
    print('DONE',len(frame),calls,round(time.time()-begin,2),flush=True)
if __name__=='__main__':main()
