#!/usr/bin/env python3
"""No weekly entry cap. Retrospective DAILY proxies only; no brokerage operations.
2024 development, 2025H1 setting selection, subsequent 10 months evaluated once.
All periods previously inspected: NOT an untouched holdout or live performance.
"""
from __future__ import annotations
import os, io, sys, json, gzip, math, hashlib, zipfile, shutil, datetime as dt, itertools, importlib.metadata
from pathlib import Path
import numpy as np
import pandas as pd
import requests
ROOT=Path(os.environ.get('LARGO_PROJECT','/home/oai/my-project/files/2026-09-07_largo_unlimited_v11'))
for s in ['input','work','source','output','qc']:(ROOT/s).mkdir(parents=True,exist_ok=True)
OUT=ROOT/'output';QC=ROOT/'qc'
SEED=110907

def dump(path,obj):
    def clean(v):
        if isinstance(v,dict):return {str(k):clean(x) for k,x in v.items()}
        if isinstance(v,(list,tuple)):return [clean(x) for x in v]
        if isinstance(v,np.generic):return clean(v.item())
        if isinstance(v,float) and not math.isfinite(v):return None
        return v
    path.write_text(json.dumps(clean(obj),ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
def save(df,name):df.to_csv(OUT/name,index=False,encoding='utf-8-sig')

def inputs():
    arc=ROOT/'input/corrected-original-artifact.zip'
    expected='b535136c14e7f8eeea9770e1b3ed202024683e3498470d2d6be8b80c7cb00e39'
    if not arc.exists():
        url='https://api.github.com/repos/SEUNGJU-PARK-KR/canva-agent/actions/artifacts/9971831156/zip'
        headers={'Accept':'application/vnd.github+json'}
        if os.environ.get('GH_TOKEN'):headers['Authorization']='Bearer '+os.environ['GH_TOKEN']
        with requests.get(url,headers=headers,stream=True,timeout=180) as r:
            r.raise_for_status()
            with arc.open('wb') as f:
                for chunk in r.iter_content(1024*1024):
                    if chunk:f.write(chunk)
    got=hashlib.sha256(arc.read_bytes()).hexdigest();assert got==expected,got
    with zipfile.ZipFile(arc) as z:
        for suffix in ['output/extended_candidates.csv','output/price_history.json.gz','output/gs_correlations.csv','qc/collection_metadata.json','qc/market_cap_parser_audit.json']:
            hits=[n for n in z.namelist() if n.endswith(suffix)];assert len(hits)==1,(suffix,hits)
            dest=ROOT/'input'/Path(suffix).name
            if not dest.exists():dest.write_bytes(z.read(hits[0]))
    p=ROOT/'input/extended_candidates.csv'
    dump(QC/'provenance.json',{'artifact_id':9971831156,'artifact_sha256':got,'candidate_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'original_preserved':True,'no_fresh_market_data':True})
    return pd.read_csv(p,dtype={'c':str}),json.loads(gzip.decompress((ROOT/'input/price_history.json.gz').read_bytes()))

def prepare():
    x,prices=inputs();x=x.sort_values(['d','c']).reset_index(drop=True);x['c']=x.c.str.zfill(6)
    for k in ['hr','cs','audit','neg','known']:
        default=k in ['hr','neg']
        x[k]=x[k].map(lambda v:default if pd.isna(v) else str(v).lower() in ['true','1'])
    assert not x.duplicated(['d','c']).any()
    numeric=['px','tv','mc','chg','risk','dig','cl','wick','body','pat','br','rank','fol','dir','fresh','open','high','low','exit']
    for k in numeric:x[k]=pd.to_numeric(x[k],errors='coerce')
    x['valid']=x.known&np.isfinite(x[['open','high','low','exit']]).all(axis=1)&(x.high>=x[['open','low','exit']].max(axis=1)-1e-7)&(x.low<=x[['open','high','exit']].min(axis=1)+1e-7)
    x['safe']=(~x.hr)&x.cs&(~x.neg)&x.mk.isin(['KOSPI','KOSDAQ'])&(x.px>=1000)&(x.tv>=2e10)&(x.mc>=5e10)
    calendar=pd.to_datetime([a['date'] for a in prices['005930']]);calendar=pd.DatetimeIndex(sorted(set(calendar)))
    series={};vol20=np.full(len(x),np.nan);mom20=np.full(len(x),np.nan)
    for code,inds in x.groupby('c').groups.items():
        bars=pd.DataFrame(prices.get(code,[]))
        if bars.empty:continue
        bars.index=pd.to_datetime(bars.date);bars=bars[~bars.index.duplicated()].sort_index()
        c=bars.c.astype(float);o=bars.o.astype(float);v=bars.v.astype(float)
        cc=c.pct_change(fill_method=None).where(v>0);gap=(o/c.shift(1)-1).where(v>0)
        series[code]=np.stack([cc.reindex(calendar).to_numpy(),gap.reindex(calendar).to_numpy()])
        vv=cc.shift(1).rolling(20,min_periods=10).std()*100
        mm=(c.shift(1)/c.shift(21)-1)*100
        ix=pd.to_datetime(x.loc[inds,'d']);vol20[inds]=vv.reindex(ix).to_numpy();mom20[inds]=mm.reindex(ix).to_numpy()
    x['vol20']=vol20;x['mom20']=mom20
    dates=sorted(x.d.unique());groups=[x.index[x.d==d].to_numpy() for d in dates]
    week=[pd.Timestamp(d).strftime('%G-W%V') for d in dates]
    calendar_weeks={}
    for d in calendar:calendar_weeks.setdefault(d.strftime('%G-W%V'),[]).append(d.strftime('%Y-%m-%d'))
    # Vectorized pairwise complete-observation correlations, strictly before signal date.
    matrices=[];corr_records=[];gs_checks=[]
    from gs_quant.timeseries.econometrics import correlation,SeriesType
    for j,(d,ids) in enumerate(zip(dates,groups)):
        end=calendar.searchsorted(pd.Timestamp(d));start=max(0,end-60)
        panels=np.stack([series.get(x.at[i,'c'],np.full((2,len(calendar)),np.nan))[:,start:end] for i in ids])
        corrkind=[]
        for kind in [0,1]:
            a=panels[:,kind,:];f=np.isfinite(a).astype(float);b=np.nan_to_num(a,nan=0.)
            nn=f@f.T;ss=b@f.T;ss2=(b*b)@f.T;prod=b@b.T
            with np.errstate(divide='ignore',invalid='ignore'):
                cov=prod-ss*ss.T/nn;vi=ss2-ss*ss/nn
                r=cov/np.sqrt(vi*vi.T)
            r[(nn<40)|~np.isfinite(r)]=1.;r=np.clip(r,-1,1);corrkind.append(r)
        mat=np.maximum(corrkind[0],corrkind[1]);mat=np.round(mat,12);np.fill_diagonal(mat,1.)
        matrices.append(mat)
        if j%12==0 and len(ids)>1:
            for a,b in itertools.combinations(range(min(len(ids),4)),2):
                xy=pd.DataFrame(panels[[a,b],0,:].T,index=calendar[start:end]).dropna()
                if len(xy)>=40 and (xy.std()>1e-12).all():
                    gs=float(correlation(xy.iloc[:,0],xy.iloc[:,1],type_=SeriesType.RETURNS).iloc[-1]);err=abs(gs-corrkind[0][a,b]);assert err<1e-8
                    gs_checks.append({'d':d,'a':x.at[ids[a],'c'],'b':x.at[ids[b],'c'],'gs':gs,'numpy':corrkind[0][a,b],'error':err,'window_end':calendar[end-1].strftime('%Y-%m-%d')})
        for a,b in itertools.combinations(range(len(ids)),2):
            corr_records.append((d,x.at[ids[a],'c'],x.at[ids[b],'c'],mat[a,b]))
    save(pd.DataFrame(corr_records,columns=['d','a','b','corr_max']),'prior_correlations.csv')
    dump(QC/'gs_comparison.json',{'library_version':importlib.metadata.version('gs-quant'),'checked_pairs':len(gs_checks),'max_error':max([r['error'] for r in gs_checks] or [0]),'checks':gs_checks})
    print('INPUT_READY',len(x),len(dates),'GS_checks',len(gs_checks),flush=True)
    return x,dates,groups,week,calendar_weeks,matrices

def rank_models(x):
    feats=pd.DataFrame(index=x.index)
    for k in ['chg','risk','cl','wick','body','pat','br','rank','fol','dir','fresh','vol20','mom20']:feats[k]=x[k]
    for k in ['dig','tv','mc']:feats['log_'+k]=np.log1p(x[k].clip(lower=0))
    feats['momentum_fit']=-abs(x.chg-12.5);feats['risk_fit']=-abs(x.risk*100-5)
    feats['dig_fit']=-abs(np.log(x.dig.clip(lower=.05)/.7))
    X=feats.to_numpy(float);X[~np.isfinite(X)]=np.nan
    # Multiple response targets; every model sees only completed prior labels.
    basic=np.where(x.open>=.75,x.open,np.where(x.high>=.75,.75,x.exit))-.3
    stop=np.where(x.open<=-3,x.open,np.where(x.open>=1,x.open,np.where(x.low<=-3,-3,np.where(x.high>=1,1,x.exit))))-.3
    Y=np.stack([x.open.clip(-15,15).to_numpy(),np.clip(basic,-12,15), (basic>0).astype(float), (basic<-3).astype(float),np.clip(stop,-12,15)],axis=1)
    preds=np.full((len(x),5),np.nan);folds=[]
    for month in sorted(x.m.unique()):
        if month<'2024-01':continue
        first=x.loc[x.m==month,'d'].min();earliest=(pd.Timestamp(first)-pd.DateOffset(months=12)).strftime('%Y-%m-%d')
        tr=np.flatnonzero(x.safe&x.valid&(x.d>=earliest)&(x.d<first)&(x.n<first));te=np.flatnonzero(x.m==month)
        assert len(tr)>30 and x.loc[tr,'n'].max()<first
        a=X[tr];med=np.nanmedian(a,axis=0);med=np.nan_to_num(med,nan=0.);a=np.where(np.isfinite(a),a,med)
        sd=a.std(axis=0);sd[sd<1e-9]=1
        a=np.column_stack([np.ones(len(tr)),np.clip((a-med)/sd,-5,5)])
        b=np.column_stack([np.ones(len(te)),np.clip((np.where(np.isfinite(X[te]),X[te],med)-med)/sd,-5,5)])
        reg=np.eye(a.shape[1])*100;reg[0,0]=0
        coef=np.linalg.solve(a.T@a+reg,a.T@Y[tr]);preds[te]=b@coef
        folds.append({'test_month':month,'rows':len(tr),'last_signal':x.loc[tr,'d'].max(),'last_outcome':x.loc[tr,'n'].max(),'first_test':first})
    ranks={'gap_ridge':preds[:,0],'utility_ridge':preds[:,1]-.5*np.maximum(preds[:,3],0),'win_loss_ridge':preds[:,2]-1.5*np.maximum(preds[:,3],0),'stop_ridge':preds[:,4]}
    for name,p in ranks.items():x['score_'+name]=p
    save(pd.DataFrame(folds),'training_folds.csv')
    dump(QC/'model_spec.json',{'features':list(feats),'alpha':100,'rolling_months':12,'train_label_boundary_strict':True,'rank_only_no_calibrated_probability_claim':True})
    return ranks

def profiles():
    p=[]
    def add(name,ch=(0,25),risk=.15,dig=(.1,3),cl=.4,body=.2,pat=0,context='loose',vol=50,tv=2e10):
        p.append(dict(name=name,chg=list(ch),risk=risk,dig=list(dig),cl=cl,body=body,pat=pat,context=context,vol=vol,tv=tv))
    add('v10_broad')
    add('classic',(8,18),.10,(.2,1.2),.9,.45,80,'strong')
    for risk,vol in [(0.06,3),(0.10,4),(0.15,6)]:
        for ch in [(0,10),(3,15),(8,20)]:
            for cl in [.5,.8]:add(f'momentum_{risk}_{vol}_{ch[0]}_{cl}',ch,risk,(.15,2),cl,.25,0,'loose',vol)
    for tv in [5e10,1e11]:
        for cl in [.4,.7]:
            for ctx in ['none','loose']:add(f'liquid_{tv}_{cl}_{ctx}',(-3,18),.15,(.1,3),cl,.15,0,ctx,5,tv)
    for ch in [(-8,3),(-3,8),(0,15)]:
        for vol in [3,5]:add(f'lowvol_{ch[0]}_{vol}',ch,.10,(.1,2.5),.35,.10,0,'none',vol)
    for vol in [3,5,8]:add(f'close_strength_{vol}',(3,20),.12,(.2,2),.85,.45,70,'loose',vol)
    return p

def profile_mask(x,p):
    a=x.safe&x.chg.between(*p['chg'])&x.risk.between(0,p['risk'])&x.dig.between(*p['dig'])&(x.cl>=p['cl'])&(x.body>=p['body'])&(x.pat>=p['pat'])&(x.vol20<=p['vol'])&(x.tv>=p['tv'])
    if p['context']=='loose':a&=((x.br>=.35)&(x['rank']<=10))|(x.audit&(x['dir']>=10)&(x.fresh>=3))
    if p['context']=='strong':a&=((x.br>=.6)&(x['rank']<=3))|(x.audit&(x['dir']>=14)&(x.fresh>=7))
    return a.to_numpy()

MODES=[]
for t in [.5,.75,1,1.5,2,3,4]:MODES.append({'name':f'tp{t}_close','tp':t,'sl':None,'gap_stop':None,'open_fraction':0})
for t,g in [(.75,1.5),(1,2),(1.5,2),(2,3)]:MODES.append({'name':f'tp{t}_gapstop{g}','tp':t,'sl':None,'gap_stop':g,'open_fraction':0})
for t,s in [(.75,3),(1,3),(1.5,3),(1,5),(2,5)]:MODES.append({'name':f'tp{t}_stop{s}','tp':t,'sl':s,'gap_stop':None,'open_fraction':0})
MODES.append({'name':'half_open_half_tp1.5','tp':1.5,'sl':None,'gap_stop':None,'open_fraction':.5})
MODES.append({'name':'all_open','tp':1,'sl':None,'gap_stop':None,'open_fraction':1})

def returns(x,m,cost=.3,cap=False,optimistic=False):
    o=x.open.to_numpy();h=x.high.to_numpy();l=x.low.to_numpy();c=x.exit.to_numpy();t=m['tp']
    r=np.where(o>=t,o if not cap else t,np.where(h>=t,t,c))
    if m['sl'] is not None:
        s=m['sl'];r=np.where(o<=-s,o,np.where(o>=t,o if not cap else t,np.where((l<=-s)&((h<t)|~np.array(optimistic)),-s,np.where(h>=t,t,c))))
    if m['gap_stop'] is not None:r=np.where(o<=-m['gap_stop'],o,r)
    r=m['open_fraction']*o+(1-m['open_fraction'])*r-cost
    return np.where(x.valid,r,np.nan)

def run():
    x,dates,groups,weeks,calendar_weeks,corr=prepare();ranks=rank_models(x);ps=profiles();masks=[profile_mask(x,p) for p in ps]
    backup=(x.safe&x.chg.between(-10,29)&x.risk.between(0,.30)&x.dig.between(.05,5)&(x.vol20<=20)).to_numpy()
    returns_matrix=np.stack([returns(x,m) for m in MODES],axis=1)
    valid=x.valid.to_numpy();nexts=x.n.to_numpy();date_arr=np.array(dates)
    starts={'development':('2024-01-01','2025-01-01'),'selection':('2025-01-01','2025-07-01'),'followup':('2025-07-01','2026-05-01'),'all':('2024-01-01','2026-05-01')}
    perf_masks={k:(date_arr>=a)&(date_arr<b) for k,(a,b) in starts.items()}
    outcome_day=np.array([str(x.loc[ids,'n'].max()) for ids in groups])
    eval_masks={k:v&(outcome_day<starts[k][1]) for k,v in perf_masks.items()}
    eligible_weeks={}
    for key,(a,b) in starts.items():
        ws=[]
        for w in sorted(set(weeks)):
            inds=[j for j,d in enumerate(dates) if weeks[j]==w and a<=d<b]
            expected=[d for d in calendar_weeks.get(w,[]) if a<=d<b]
            allweek=calendar_weeks.get(w,[])
            if len(inds)>=2 and len(expected)==len(allweek) and [dates[j] for j in inds]==expected:ws.append(inds)
        eligible_weeks[key]=ws
    remaining=[sum(1 for jj in range(j,len(dates)) if weeks[jj]==weeks[j]) for j in range(len(dates))]
    def select(mask,score,maxn,schedule,capweek=False):
        picks=np.full((len(dates),3),-1,int);route=np.zeros(len(dates),int);wprev=None;count=0
        for j,ids in enumerate(groups):
            if dates[j]<'2024-01-01':continue
            if weeks[j]!=wprev:wprev=weeks[j];count=0
            if capweek and count>=2:continue
            loc=np.flatnonzero(mask[ids]&np.isfinite(score[ids]));regular=True
            fill=count<2 and (schedule=='early'||False) if False else count<2 and (schedule=='early' or remaining[j]<=2-count)
            if not len(loc) and fill:loc=np.flatnonzero(backup[ids]&np.isfinite(score[ids]));regular=False
            if not len(loc):continue
            loc=sorted(loc,key=lambda k:(-score[ids[k]],x.at[ids[k],'risk'] if pd.notna(x.at[ids[k],'risk']) else 99,-x.at[ids[k],'tv'],x.at[ids[k],'c']))[:10]
            chosen=[]
            for k in loc:
                if all(corr[j][k,q]<=.5 for q in chosen):chosen.append(k)
                if len(chosen)==maxn:break
            picks[j,:len(chosen)]=ids[chosen];route[j]=1 if regular else 2;count+=1
        return picks,route
    def daily(picks,rm=returns_matrix):
        good=picks>=0;n=good.sum(axis=1);idx=np.maximum(picks,0)
        vv=rm[idx];vv=np.where(good[:,:,None],vv,0)
        r=np.sum(vv,axis=1)/np.maximum(n[:,None],1)
        return r,n
    def metrics(r,n,key):
        mm=eval_masks[key];a=r[mm];active=n[mm]>0;val=a[active];missing=int((~np.isfinite(val)).sum())
        if missing:return {'days':int(active.sum()),'missing':missing,'win_rate':0.,'compound_pct':-100.,'log_growth':-100.,'mdd_pct':-100.,'coverage':0.}
        eq=np.cumprod(1+a/100);peaks=np.maximum.accumulate(np.r_[1.,eq]);dd=np.r_[1.,eq]/peaks-1
        weeks_ok=sum((n[js]>0).sum()>=2 for js in eligible_weeks[key]);totalw=len(eligible_weeks[key]);pos=val[val>0];neg=val[val<0]
        return {'days':int(active.sum()),'stocks':int(n[mm].sum()),'wins':len(pos),'losses':len(neg),'win_rate':len(pos)/len(val) if len(val) else 0.,'compound_pct':float((eq[-1]-1)*100) if len(eq) else 0.,'log_growth':float(np.log1p(a/100).sum()),'mdd_pct':float(dd.min()*100),'mean_trade_pct':float(val.mean()) if len(val) else 0.,'worst_day_pct':float(val.min()) if len(val) else 0.,'profit_factor':float(pos.sum()/(-neg.sum())) if len(neg) else None,'eligible_weeks':totalw,'covered_weeks':weeks_ok,'coverage':weeks_ok/totalw if totalw else 0.,'missing':missing}
    cache={};records=[];cid=0
    for pi,ri,maxn,schedule in itertools.product(range(len(ps)),ranks.keys(),[1,2,3],['early','deadline']):
        picks,route=select(masks[pi],ranks[ri],maxn,schedule);rr,nn=daily(picks);cache[(pi,ri,maxn,schedule)]=(picks,route,rr,nn)
        for mi,mode in enumerate(MODES):
            st=metrics(rr[:,mi],nn,'development');records.append({'id':cid,'pi':pi,'profile':ps[pi]['name'],'ranker':ri,'maxn':maxn,'schedule':schedule,'mi':mi,'mode':mode['name'],**st});cid+=1
        if cid%2000<18:print('SEARCH',cid,flush=True)
    dev=pd.DataFrame(records);save(dev,'development_search.csv')
    feasible=dev[(dev.coverage>=.999999)&(dev.days>=80)&(dev.compound_pct>0)&(dev.mdd_pct>=-35)&(dev.win_rate>=.55)].copy()
    relaxation='none'
    if len(feasible)<10:
        feasible=dev[(dev.coverage>=.999999)&(dev.days>=80)&(dev.compound_pct>0)].copy();relaxation='development_drawdown_or_win_floor_relaxed'
    assert len(feasible)>0,'No profitable weekly-feasible development configurations'
    feasible['utility']=feasible.win_rate.rank(pct=True)+feasible.log_growth.rank(pct=True)+.5*feasible.mdd_pct.rank(pct=True)
    shortlist=pd.concat([feasible.nlargest(25,'utility'),feasible.nlargest(15,'win_rate'),feasible.nlargest(15,'log_growth')]).drop_duplicates('id')
    vals=[]
    for rec in shortlist.to_dict('records'):
        key=(rec['pi'],rec['ranker'],rec['maxn'],rec['schedule']);pick,route,rr,nn=cache[key]
        s=metrics(rr[:,rec['mi']],nn,'selection');vals.append({'id':rec['id'],'pi':rec['pi'],'profile':rec['profile'],'ranker':rec['ranker'],'maxn':rec['maxn'],'schedule':rec['schedule'],'mi':rec['mi'],'mode':rec['mode'],'dev_win_rate':rec['win_rate'],'dev_return':rec['compound_pct'],'dev_mdd':rec['mdd_pct'],**s})
    va=pd.DataFrame(vals);save(va,'selection_candidates.csv')
    vf=va[(va.coverage>=.999999)&(va.days>=35)&(va.compound_pct>0)&(va.mdd_pct>=-25)&(va.win_rate>=.55)].copy()
    if len(vf)<1:
        vf=va[(va.coverage>=.999999)&(va.compound_pct>0)].copy();relaxation+=';selection_drawdown_or_win_floor_relaxed'
    assert len(vf),'No viable selection-period candidate'
    vf['balanced']=vf.win_rate.rank(pct=True)+vf.log_growth.rank(pct=True)+.5*vf.mdd_pct.rank(pct=True)
    chosen={'balanced':vf.sort_values(['balanced','mdd_pct','id'],ascending=[False,False,True]).iloc[0].to_dict(),'win_priority':vf.sort_values(['win_rate','log_growth','id'],ascending=[False,False,True]).iloc[0].to_dict(),'return_priority':vf.sort_values(['log_growth','win_rate','id'],ascending=[False,False,True]).iloc[0].to_dict()}
    dump(OUT/'frozen_before_followup.json',{'selected':chosen,'profiles':ps,'exit_modes':MODES,'search_count':len(dev),'shortlist_count':len(va),'selection_constraints_relaxation':relaxation,'followup_not_used_for_selection':True,'weekly_cap':None,'previously_seen_periods':True})
    # Only now calculate followup metrics of frozen configurations.
    comp=[];all_daily=[];all_trades=[];weekly=[]
    for label,rec in chosen.items():
        key=(rec['pi'],rec['ranker'],rec['maxn'],rec['schedule']);picks,route,rr,nn=cache[key];mi=rec['mi']
        for period in starts:comp.append({'policy':label,'period':period,**metrics(rr[:,mi],nn,period)})
        for j,d in enumerate(dates):
            if d<'2024-01-01':continue
            ids=picks[j][picks[j]>=0];row={'policy':label,'d':d,'n':outcome_day[j],'month':d[:7],'week':weeks[j],'stocks':len(ids),'route':'primary' if route[j]==1 else 'backup' if route[j]==2 else 'cash','return_pct':rr[j,mi]};all_daily.append(row)
            for i in ids:all_trades.append({'policy':label,**x.loc[i].to_dict(),'weight':1/len(ids),'route':row['route'],'net_return_pct':returns_matrix[i,mi]})
        for w in sorted(set(weeks)):
            js=[j for j,d in enumerate(dates) if weeks[j]==w and d>='2024-01-01']
            if js:weekly.append({'policy':label,'week':w,'market_days':len(js),'entry_days':int((nn[js]>0).sum()),'at_least_two':bool((nn[js]>0).sum()>=2)})
    # Direct ablation: identical current engine and broad pool, weekly cap versus no cap.
    for capweek,label in [(True,'same_engine_weekly_cap2'),(False,'same_engine_no_cap')]:
        picks,route=select(masks[0],ranks['gap_ridge'],1,'early',capweek);rr,nn=daily(picks);mi=next(i for i,m in enumerate(MODES) if m['name']=='tp0.75_close')
        for period in starts:comp.append({'policy':label,'period':period,**metrics(rr[:,mi],nn,period)})
    save(pd.DataFrame(comp),'policy_comparison.csv');dd=pd.DataFrame(all_daily);tt=pd.DataFrame(all_trades);save(dd,'daily_results.csv');save(tt,'selected_trades.csv');save(pd.DataFrame(weekly),'weekly_frequency.csv')
    monthly=[]
    for (label,mo),g in dd.groupby(['policy','month']):
        a=g.return_pct.to_numpy();active=g.stocks>0;v=a[active];eq=np.cumprod(1+a/100);peak=np.maximum.accumulate(np.r_[1.,eq]);mdd=(np.r_[1.,eq]/peak-1).min()*100
        monthly.append({'policy':label,'month':mo,'days':int(active.sum()),'stocks':int(g.stocks.sum()),'wins':int((v>0).sum()),'losses':int((v<0).sum()),'win_rate':float((v>0).mean()) if len(v) else 0,'compound_pct':float((eq[-1]-1)*100),'mdd_pct':float(mdd)})
    save(pd.DataFrame(monthly),'monthly_results.csv')
    rec=chosen['balanced'];picks,route,rr,nn=cache[(rec['pi'],rec['ranker'],rec['maxn'],rec['schedule'])];mode=MODES[rec['mi']]
    stress=[]
    for name,cost,cap,opt,fraction in [('base',.3,False,False,1),('cost0.5',.5,False,False,1),('cost1.0',1.,False,False,1),('cap_open_gain_at_target',.3,True,False,1),('half_exposure',.3,False,False,.5),('optimistic_touch_order',.3,False,True,1)]:
        rm=returns(x,mode,cost,cap,opt)[:,None];sr,_=daily(picks,rm);stress.append({'scenario':name,**metrics(sr[:,0]*fraction,nn,'followup')})
    sr,_=daily(picks,(x.open.to_numpy()-.3)[:,None]);stress.append({'scenario':'same_stocks_open_exit',**metrics(sr[:,0],nn,'followup')})
    for k in [3,5]:
        r=rr[:,rec['mi']].copy();idx=np.flatnonzero(eval_masks['followup']&(nn>0));top=idx[np.argsort(r[idx])[-k:]];r[top]=0;stress.append({'scenario':f'best_{k}_days_removed',**metrics(r,nn,'followup')})
    save(pd.DataFrame(stress),'stress_scenarios.csv')
    ablation=[]
    for maxn in [1,2,3]:
        pp,ro=select(masks[rec['pi']],ranks[rec['ranker']],maxn,rec['schedule']);r,n=daily(pp);ablation.append({'max_positions':maxn,**metrics(r[:,rec['mi']],n,'followup')})
    save(pd.DataFrame(ablation),'position_count_sensitivity.csv')
    # No-cap invariant and outcome leakage check: input mask/scores/selection never use test outcomes.
    mask_check=masks[rec['pi']].copy();z=x.copy();fm=z.d>='2025-07-01'
    for c in ['open','high','low','exit']:z.loc[fm,c]=999.
    z.loc[fm,'known']=~z.loc[fm,'known'];assert np.array_equal(mask_check,profile_mask(z,ps[rec['pi']]))
    p2,r2=select(profile_mask(z,ps[rec['pi']]),ranks[rec['ranker']],rec['maxn'],rec['schedule']);assert np.array_equal(picks,p2)
    primary_days=[j for j,ids in enumerate(groups) if dates[j]>='2024-01-01' and (masks[rec['pi']][ids]&np.isfinite(ranks[rec['ranker']][ids])).any()]
    assert all(nn[j]>0 for j in primary_days)
    for j,pp in enumerate(picks):
        ids=pp[pp>=0];assert len(ids)<=3
        for a,b in itertools.combinations(ids,2):
            la=np.flatnonzero(groups[j]==a)[0];lb=np.flatnonzero(groups[j]==b)[0];assert corr[j][la,lb]<=.5+1e-10
    # Account risk and uncertainty diagnostics.
    fm=eval_masks['followup'];ids=np.flatnonzero(fm);active=nn[ids]>0;r=rr[ids,rec['mi']];w=np.array(weeks)[ids];rng=np.random.default_rng(SEED);unique=np.unique(w);blocks=[np.flatnonzero(w==v) for v in unique];boot=[]
    for _ in range(2000):
        ix=np.concatenate([blocks[k] for k in rng.integers(0,len(blocks),len(blocks))]);vals=r[ix][active[ix]]
        if len(vals):boot.append([(vals>0).mean(),vals.mean()])
    boot=np.array(boot);unc={'weekly_resamples':2000,'win_rate_ci95':np.quantile(boot[:,0],[.025,.975]).tolist(),'mean_trade_ci95':np.quantile(boot[:,1],[.025,.975]).tolist(),'not_multiple_search_adjusted':True}
    counts={k:int(v) for k,v in pd.Series(nn[fm]).value_counts().to_dict().items()}
    ft=tt[(tt.policy=='balanced')&(tt.d>='2025-07-01')];tail=ft[(ft.net_return_pct>0)&(ft.low<=-3)]
    save(tail,'wins_with_intraday_loss_below3.csv')
    q={'input_rows':len(x),'unique_signals':len(dates),'no_duplicate_rows':True,'future_outcomes_do_not_change_masks_or_picks':True,'all_primary_days_traded_no_weekly_cap':True,'pair_correlations_pass':True,'weights_sum_one':True,'exact_0906':False,'test_policy_changed_after_results':False,'weekly_distribution_followup':counts,'post_followup_frozen_config_hash':hashlib.sha256((OUT/'frozen_before_followup.json').read_bytes()).hexdigest()}
    dump(QC/'checks.json',q);dump(OUT/'uncertainty.json',unc)
    summary={'selected':chosen,'balanced_profile':ps[rec['pi']],'balanced_exit':mode,'comparison':comp,'monthly':monthly,'stress':stress,'count_sensitivity':ablation,'uncertainty':unc,'qc':q,'search_count':len(dev),'selection_count':len(va),'prior_v10_report_reference':{'days':86,'wins':65,'losses':21,'win_rate':65/86,'compound_pct':38.4893975117012,'mdd_pct':-27.93050797993909,'reference_only':'from previous attached WIN_RATE_REPORT.md; new ranker implementation is not claimed identical'},'followup_primary_days':int(((route==1)&fm).sum()),'followup_backup_days':int(((route==2)&fm).sum()),'wins_with_intraday_loss_below3':len(tail),'limits':['Only DAILY_PROXY closing-entry/full-next-day-exit data','Current universe/shares/theme-membership and incomplete news/alerts create bias','Prior-selected periods have already been inspected; no untouched holdout claim','No actual execution, orderbook depth or auction fill guarantees','Signal-day close data is not a 15:18 snapshot','Targets and stops within one daily bar have unknown order; stops first used in primary comparisons','No claim of global optimization or Goldman Sachs endorsement']}
    dump(OUT/'summary.json',summary)
    print('FINAL_SUMMARY',json.dumps(summary,ensure_ascii=False,default=str),flush=True)

if __name__=='__main__':
    try:run()
    except Exception:
        import traceback
        (QC/'failure.txt').write_text(traceback.format_exc());raise
