#!/usr/bin/env python3
"""Offline research only. No orders, no live-strategy edits, no outcome-based entries.
2024 discovery -> 2025 model selection -> 2026 Jan-Apr frozen retrospective check.
The periods have appeared in earlier research; the final segment is NOT pristine OOS.
"""
from __future__ import annotations
import argparse, datetime as dt, hashlib, itertools, json, math, shutil, zipfile
from pathlib import Path
import numpy as np
import pandas as pd

SEED=20260907
COST=0.30
EXITS=('OPEN','TP2_SL3_LOWER','TP3_SL3_LOWER','TP4_SL3_LOWER')
SOURCE_ID=9972043830

def clean(x):
    if isinstance(x,dict):return {str(k):clean(v) for k,v in x.items()}
    if isinstance(x,(list,tuple)):return [clean(v) for v in x]
    if isinstance(x,np.ndarray):return clean(x.tolist())
    if isinstance(x,(np.integer,)):return int(x)
    if isinstance(x,(np.bool_,)):return bool(x)
    if isinstance(x,(float,np.floating)):return float(x) if np.isfinite(x) else None
    if isinstance(x,Path):return str(x)
    return x

def dump(path,x):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(clean(x),ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')

def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()

def boolean(s,missing=False):
    mp={'true':True,'false':False,'1':True,'0':False,'1.0':True,'0.0':False}
    return s.map(lambda v:missing if pd.isna(v) else mp.get(str(v).lower(),missing)).astype(bool)

def locate(base,name,corrected=False):
    candidates=list(base.rglob(name))
    if corrected:candidates=[p for p in candidates if 'corrected' in p.parts]
    if not candidates:raise FileNotFoundError(f'Missing {name}; corrected={corrected}')
    candidates.sort(key=lambda p:(0 if '/largo_v8_extended/' in p.as_posix() else 1,len(p.parts)))
    return candidates[0]

def load(base,root):
    candidates=locate(base,'extended_candidates.csv',True)
    correlations=locate(base,'gs_correlations.csv',True)
    metadata=locate(base,'collection_metadata.json',True)
    for source,name in [(candidates,'corrected_candidates.csv'),(correlations,'gs_correlations.csv'),(metadata,'collection_metadata.json')]:
        shutil.copy2(source,root/'input'/name)
    x=pd.read_csv(candidates,dtype={'c':str})
    for c in ['hr','cs','neg','audit','known']:
        x[c]=boolean(x[c],missing=(c in ['hr','neg']))
    for c in ['px','tv','mc','chg','risk','dig','cl','wick','body','pat','br','rank','dir','fresh','open','high','low','exit']:
        x[c]=pd.to_numeric(x[c],errors='coerce')
    x['c']=x.c.str.zfill(6)
    x=x.sort_values(['d','c']).reset_index(drop=True)
    assert not x.duplicated(['d','c']).any()
    assert len(x)>=14000
    assert set(x['mode'].dropna())=={'DAILY_PROXY'}
    x['valid']=x.known & np.isfinite(x[['open','high','low','exit']]).all(axis=1) & (x.high>=x.low) & (x.high+1e-7>=x[['open','exit']].max(axis=1)) & (x.low-1e-7<=x[['open','exit']].min(axis=1))
    rawcorr=pd.read_csv(correlations,dtype={'a':str,'b':str})
    datecol='signal_date' if 'signal_date' in rawcorr else 'd'
    lookup={}
    for r in rawcorr.to_dict('records'):
        vals=[r.get('corr_gap'),r.get('corr_cc')]
        if all(v is not None and pd.notna(v) and np.isfinite(float(v)) for v in vals):
            a,b=sorted([str(r['a']).zfill(6),str(r['b']).zfill(6)])
            lookup[(str(r[datecol]),a,b)]=max(float(v) for v in vals)
    assert len(lookup)>0
    provenance={'source_artifact_id':SOURCE_ID,'candidates_sha256':sha(candidates),'correlations_sha256':sha(correlations),'input_rows':len(x),'unique_codes':int(x.c.nunique()),'correlation_pairs':len(lookup),'first_day':x.d.min(),'last_day':x.d.max(),'original_input':str(candidates),'all_signals_reconstructed':True,'exact_1518_and_0906':False}
    dump(root/'qc/input_provenance.json',provenance)
    return x,lookup,provenance

class Engine:
    def __init__(self,x,lookup):
        self.x=x;self.lookup=lookup
        self.a={c:x[c].to_numpy() for c in x.columns}
        # Kept even for quota research. Missing required input never becomes a pass.
        self.safe=((~x.hr)&x.cs&(~x.neg)&x.mk.isin(['KOSPI','KOSDAQ'])&(x.px>=1000)&(x.tv>=2e10)&(x.mc>=5e10)&x.risk.between(0,.15)).to_numpy()
        # Quota fallback is deliberately labelled a relaxation, not original-Largo compliance.
        self.floor=self.safe & x.chg.between(0,20).to_numpy() & x.dig.between(.1,2).to_numpy() & (x.cl>=.3).to_numpy() & (x.body>=.2).to_numpy()
        self.material=(x.audit & (x['dir']>=14)&(x.fresh>=7)).to_numpy()
        self.days=sorted(d for d in x.d.unique() if '2024-01-01'<=d<='2026-04-30')
        self.dayrows={d:g.index.to_numpy() for d,g in x.groupby('d',sort=True)}
        self.score={}
        for style in ['quality','liquidity','lowrisk']:
            score=np.full(len(x),-1e100)
            for d,ix in self.dayrows.items():
                f=ix[self.floor[ix]]
                if not len(f):continue
                z=x.loc[f]
                if style=='quality':
                    q=.30*z.tv.rank(pct=True)+.20*z.cl.clip(0,1)+.15*z.br.fillna(0).clip(0,1)+.15/(z['rank'].fillna(99).clip(lower=1))+.10*(1-z.risk/.15)+.10*(1-abs(z.dig-.7)/1.3).clip(0,1)
                elif style=='liquidity':q=z.tv.rank(pct=True)+.001*z.cl
                else:q=-z.risk+.00001*z.tv.rank(pct=True)
                score[f]=q.to_numpy()
            self.score[style]=score
        self.outcomes={};self.uppers={};self.ambiguous={}
        o,h,l,c=[self.a[k] for k in ['open','high','low','exit']]
        for policy in EXITS:
            if policy=='OPEN':raw=o.copy();upper=raw.copy();amb=np.zeros(len(x),bool)
            else:
                t=float(policy[2]);stop=-3.
                amb=(o<t)&(o>stop)&(h>=t)&(l<=stop)
                raw=np.where(o<=stop,o,np.where(o>=t,o,np.where(l<=stop,stop,np.where(h>=t,t,c))))
                upper=np.where(amb,t,raw)
            v=raw-COST;up=upper-COST;v[~x.valid.to_numpy()]=np.nan;up[~x.valid.to_numpy()]=np.nan
            self.outcomes[policy]=v;self.uppers[policy]=up;self.ambiguous[policy]=amb
        self.days_by_year={y:[d for d in self.days if d.startswith(y)] for y in ['2024','2025','2026']}

    def gate(self,p):
        a=self.a
        return self.floor & (a['chg']>=p['chg_min']) & (a['chg']<=p['chg_max']) & (a['risk']<=p['risk_max']) & (a['dig']>=p['dig_min']) & (a['dig']<=p['dig_max']) & (a['cl']>=p['close_min']) & (a['body']>=p['body_min']) & (a['pat']>=p['pattern_min']) & (((a['br']>=p['breadth_min'])&(a['rank']<=p['rank_max']))|self.material)

    def choose(self,p,days):
        core=self.gate(p);score=self.score[p['ranking']];result=[];week=None;trade_count=0
        for d in days:
            wk=str(pd.Timestamp(d).to_period('W-SUN'))
            if wk!=week:week=wk;trade_count=0
            ix=self.dayrows[d];eligible=ix[core[ix]];extra=False
            if not len(eligible) and p['quota'] and trade_count<2:
                eligible=ix[self.floor[ix]];extra=len(eligible)>0
            if not len(eligible):result.append((d,[],False,0));continue
            ordered=sorted(eligible,key=lambda i:(-score[i],-self.a['tv'][i],self.a['c'][i]))[:8]
            picks=[];missing=0
            for i in ordered:
                ok=True
                for j in picks:
                    a,b=sorted([self.a['c'][i],self.a['c'][j]])
                    cv=self.lookup.get((d,a,b))
                    if cv is None:missing+=1;ok=False;break
                    if cv>.5+1e-12:ok=False;break
                if ok:picks.append(int(i))
                if len(picks)>=p['max_positions']:break
            result.append((d,picks,extra,missing));trade_count+=bool(picks)
        return result

    def daily(self,selection,policy):
        out=[];ret=self.outcomes[policy];up=self.uppers[policy]
        for d,ix,extra,missing in selection:
            known=all(np.isfinite(ret[i]) for i in ix)
            out.append({'d':d,'month':d[:7],'week':str(pd.Timestamp(d).to_period('W-SUN')),'stocks':len(ix),'ret':float(np.mean(ret[ix])) if ix and known else (0. if not ix else np.nan),'upper_ret':float(np.mean(up[ix])) if ix and known else (0. if not ix else np.nan),'evaluated':known,'fallback':extra,'missing_pair_rejections':missing,'ambiguous_positions':int(self.ambiguous[policy][ix].sum()),'codes':'|'.join(self.a['c'][ix]),'names':'|'.join(self.a['nm'][ix])})
        return pd.DataFrame(out)

def metrics(daily):
    n=len(daily)
    if not n:return {}
    traded=daily.stocks>0;vals=daily.ret.to_numpy();complete=bool(daily.evaluated.all());nv=np.nan_to_num(vals,nan=0.)
    equity=np.r_[1.,np.cumprod(1+nv/100)];dd=equity/np.maximum.accumulate(equity)-1
    weekly=daily.groupby('week').agg(sessions=('d','size'),trade_days=('stocks',lambda s:int((s>0).sum())))
    eligible=weekly[weekly.sessions>=2]
    nt=int(traded.sum());wins=int(((daily.ret>0)&traded).sum());loss=int(((daily.ret<0)&traded).sum())
    total=int(daily.stocks.sum());qualified_days=int((traded&~daily.fallback).sum())
    return {'calendar_days':n,'trade_days':nt,'stock_trades':total,'wins':wins,'losses':loss,'win_rate_pct':100*wins/nt if nt else 0.,'compound_pct':float((equity[-1]-1)*100) if complete else None,'mdd_pct':float(dd.min()*100) if complete else None,'mean_trade_pct':float(daily.loc[traded,'ret'].mean()) if nt else None,'worst_day_pct':float(daily.ret.min()),'weeks':len(weekly),'eligible_weeks':len(eligible),'weeks_ge2':int((eligible.trade_days>=2).sum()),'weeks_lt2':int((eligible.trade_days<2).sum()),'weeks_ge2_pct':100*float((eligible.trade_days>=2).mean()) if len(eligible) else 0.,'average_trade_days_per_week':nt/len(weekly),'minimum_trade_days_per_eligible_week':int(eligible.trade_days.min()) if len(eligible) else 0,'fallback_days':int((traded&daily.fallback).sum()),'primary_days':qualified_days,'fallback_share_pct':100*int((traded&daily.fallback).sum())/nt if nt else 0.,'ambiguous_positions':int(daily.ambiguous_positions.sum()),'missing_pair_rejections':int(daily.missing_pair_rejections.sum()),'complete':complete,'missing_result_days':int((~daily.evaluated).sum())}

def utility(s,kind):
    if not s.get('complete') or s['trade_days']==0:return -1e30
    growth=math.log(max(1e-12,1+s['compound_pct']/100))
    win=s['win_rate_pct']/100;dd=s['mdd_pct']/100
    if kind=='return':return growth
    if kind=='win':return win+0.001*growth
    return growth+0.7*dd+0.5*(win-.5)

def make_params():
    options={'chg_min':[0,3,5,8],'chg_max':[15,18,20],'risk_max':[.06,.10,.15],'dig_min':[.1,.2],'dig_max':[1.2,1.5,2.],'close_min':[.5,.7,.85,.9],'body_min':[.3,.45],'pattern_min':[0,60,80],'breadth_min':[.0,.4,.6,.8],'rank_max':[3,5,10,99],'ranking':['quality','liquidity','lowrisk'],'max_positions':[1,2,3]}
    rng=np.random.default_rng(SEED);out=[];seen=set()
    broad={'chg_min':0,'chg_max':20,'risk_max':.15,'dig_min':.1,'dig_max':2.,'close_min':.3,'body_min':.2,'pattern_min':0,'breadth_min':0.,'rank_max':999,'ranking':'quality','max_positions':3}
    strict={'chg_min':8,'chg_max':18,'risk_max':.10,'dig_min':.2,'dig_max':1.2,'close_min':.9,'body_min':.45,'pattern_min':80,'breadth_min':.6,'rank_max':3,'ranking':'quality','max_positions':3}
    relaxed={'chg_min':0,'chg_max':10,'risk_max':.15,'dig_min':.2,'dig_max':1.5,'close_min':.5,'body_min':.45,'pattern_min':50,'breadth_min':.5,'rank_max':5,'ranking':'lowrisk','max_positions':1}
    candidates=[broad,strict,relaxed]
    while len(candidates)<259:
        p={k:clean(rng.choice(v)) for k,v in options.items()};key=json.dumps(p,sort_keys=True)
        if key not in seen:seen.add(key);candidates.append(p)
    for p in candidates:
        for quota in [False,True]:out.append({**p,'quota':quota})
    return out,strict,relaxed

def pct(v):return '미평가' if v is None or pd.isna(v) else f'{v:+.2f}%'
def table(headers,rows):return '\n'.join(['| '+' | '.join(headers)+' |','|'+'|'.join(['---']*len(headers))+'|']+['| '+' | '.join(map(str,r))+' |' for r in rows])

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--restored',type=Path,required=True);ap.add_argument('--root',type=Path,required=True);args=ap.parse_args();root=args.root
    for sub in ['input','work','source','output','qc']:(root/sub).mkdir(parents=True,exist_ok=True)
    shutil.copy2(__file__,root/'source/optimize_weekly.py')
    x,lookup,provenance=load(args.restored,root);eng=Engine(x,lookup)
    params,strict,relaxed=make_params()
    protocol={'created_at':dt.datetime.now(dt.timezone.utc).isoformat(),'random_seed':SEED,'development':'2024','selection_validation':'2025','frozen_check':'2026-01 through 2026-04','already_used_in_prior_research':True,'candidate_configs':len(params),'exits':EXITS,'cost_pct':COST,'correlation_cap':.5,'max_positions':3,'weekly_definition':'Two distinct signal dates per Monday-Sunday week with >=2 archived market sessions; buying three stocks once counts as one day.','quota_policy':'Until two entries have occurred in the current week, use fixed floor candidates if no primary signal. Later entries require primary signals. No future-week ranking.','fallback_floor':'Archived hard safety; ordinary shares; price>=1000; trading value>=20bn; cap>=50bn; risk 0..15%; change0..20%; digestion0.1..2; close location>=30%; body>=20%. Theme/material is NOT a fallback hard gate.','missing_correlation':'Reject additional position; never substitute zero correlation.','selection_objectives':['return','win','balanced'],'daily_proxy':True,'main_sl_rule':'Gap through stop exits at open; simultaneous TP/SL touch assumes stop first. All touch fills remain assumptions.'}
    dump(root/'source/PROTOCOL.json',protocol)
    # No 2025/2026 outcomes are inspected while generating development scores.
    records=[]
    for i,p in enumerate(params):
        selection=eng.choose(p,eng.days_by_year['2024'])
        for policy in EXITS:
            s=metrics(eng.daily(selection,policy));records.append({'id':i,'exit':policy,**s})
        if i%64==0:print('development',i,'/',len(params),flush=True)
    dev=pd.DataFrame(records);dev.to_csv(root/'output/development_search.csv',index=False,encoding='utf-8-sig')
    dump(root/'source/parameter_grid.json',params)
    viable=dev[dev.complete & (dev.trade_days>=80)]
    if viable.empty:raise RuntimeError('No adequately populated development configuration')
    bestcoverage=float(viable.weeks_ge2_pct.max())
    viable=viable[(viable.weeks_ge2_pct>=bestcoverage-1e-9)&(viable.average_trade_days_per_week>=2)]
    if viable.empty:
        viable=dev[dev.complete & (dev.weeks_ge2_pct>=bestcoverage-1e-9)]
    shortlisted=set()
    for kind in ['return','win','balanced']:
        order=sorted(viable.to_dict('records'),key=lambda s:utility(s,kind),reverse=True)
        shortlisted.update((int(s['id']),s['exit']) for s in order[:10])
    # Retain an OPEN-only comparison even if full-day candidates dominate.
    oo=viable[viable['exit']=='OPEN']
    if len(oo):shortlisted.update((int(s['id']),s['exit']) for s in sorted(oo.to_dict('records'),key=lambda s:utility(s,'balanced'),reverse=True)[:8])
    validations=[]
    for i,policy in sorted(shortlisted):
        s=metrics(eng.daily(eng.choose(params[i],eng.days_by_year['2025']),policy));validations.append({'id':i,'exit':policy,**s})
    val=pd.DataFrame(validations);val.to_csv(root/'output/selection_validation.csv',index=False,encoding='utf-8-sig')
    good=val[val.complete&(val.trade_days>=60)]
    maxcoverage=float(good.weeks_ge2_pct.max())
    good=good[good.weeks_ge2_pct>=maxcoverage-1e-9]
    frozen={}
    for kind in ['return','win','balanced']:
        r=max(good.to_dict('records'),key=lambda s:utility(s,kind));frozen[kind]={'id':int(r['id']),'exit':r['exit'],'parameters':params[int(r['id'])],'validation_2025':r}
    op=good[good['exit']=='OPEN']
    if len(op):
        r=max(op.to_dict('records'),key=lambda s:utility(s,'balanced'));frozen['open_comparator']={'id':int(r['id']),'exit':'OPEN','parameters':params[int(r['id'])],'validation_2025':r}
    # Freeze on disk before constructing any 2026 metric.
    dump(root/'output/frozen_rules.json',frozen);frozen_hash=sha(root/'output/frozen_rules.json')
    outputs=[];trades=[];periods=[];weekly=[];monthly=[]
    for label,rule in frozen.items():
        for year,days in eng.days_by_year.items():
            selections=eng.choose(rule['parameters'],days);dd=eng.daily(selections,rule['exit']);dd['variant']=label;dd['exit_policy']=rule['exit'];outputs.append(dd)
            periods.append({'variant':label,'period':year,'exit_policy':rule['exit'],**metrics(dd)})
            for d,ix,extra,miss in selections:
                for j in ix:
                    item=x.loc[j].to_dict();item.update(variant=label,exit_policy=rule['exit'],weight=1/len(ix),fallback=extra,net_return=eng.outcomes[rule['exit']][j],optimistic_return=eng.uppers[rule['exit']][j],path_ambiguous=bool(eng.ambiguous[rule['exit']][j]));trades.append(item)
            for w,g in dd.groupby('week'):
                weekly.append({'variant':label,'year':year,'week':w,'sessions':len(g),'trade_days':int((g.stocks>0).sum()),'stocks':int(g.stocks.sum()),'qualified_days':int(((g.stocks>0)&~g.fallback).sum()),'fallback_days':int(g.fallback.sum()),'eligible':len(g)>=2,'passes':len(g)<2 or int((g.stocks>0).sum())>=2})
            for m,g in dd.groupby('month'):monthly.append({'variant':label,'month':m,**metrics(g)})
    alld=pd.concat(outputs,ignore_index=True)
    for label,g in alld.groupby('variant'):periods.append({'variant':label,'period':'2024-2026-04','exit_policy':g.exit_policy.iloc[0],**metrics(g.sort_values('d'))})
    # Explicit baselines; the last chat's reported 154.75% is not treated as a verified result.
    baselines=[]
    for label,p,ex in [('previous_text_rule_reimplementation',{**strict,'quota':False},'TP4_SL3_LOWER'),('relaxed_single_OPEN',{**relaxed,'quota':False},'OPEN'),('broad_floor_OPEN',{**params[0],'quota':False},'OPEN')]:
        allbase=[]
        for y,days in eng.days_by_year.items():
            dd=eng.daily(eng.choose(p,days),ex);allbase.append(dd);baselines.append({'variant':label,'period':y,'exit_policy':ex,**metrics(dd)})
        baselines.append({'variant':label,'period':'2024-2026-04','exit_policy':ex,**metrics(pd.concat(allbase))})
    # Archived v8 baseline exactly reproduces the previously verified 17-trade study.
    basepaths=[p for p in args.restored.rglob('v8_trades.csv') if '/input/' not in p.as_posix() and '/work/' not in p.as_posix()]
    archived_qc={}
    if basepaths:
        b=pd.read_csv(sorted(basepaths,key=lambda p:len(p.parts))[0]);rr=b.groupby('d')['ret'].mean()
        archived_comp=(np.prod(1+rr.to_numpy()/100)-1)*100
        archived_qc={'trade_days':len(rr),'compound_pct':float(archived_comp),'expected_compound_pct':-8.36077380835084}
        assert len(rr)==17 and abs(archived_comp+8.36077380835084)<1e-6
    pd.DataFrame(periods).to_csv(root/'output/period_results.csv',index=False,encoding='utf-8-sig')
    pd.DataFrame(baselines).to_csv(root/'output/baselines.csv',index=False,encoding='utf-8-sig')
    pd.DataFrame(trades).to_csv(root/'output/selected_trades.csv',index=False,encoding='utf-8-sig')
    alld.to_csv(root/'output/daily_results.csv',index=False,encoding='utf-8-sig')
    pd.DataFrame(weekly).to_csv(root/'output/weekly_coverage.csv',index=False,encoding='utf-8-sig')
    pd.DataFrame(monthly).to_csv(root/'output/monthly_results.csv',index=False,encoding='utf-8-sig')
    sensitivity=[]
    for label in frozen:
        dd=alld[alld.variant==label].copy()
        for cost in [.15,.3,.5,.8]:
            z=dd.copy();z.loc[z.stocks>0,'ret']-=cost-COST;sensitivity.append({'variant':label,'cost_pct':cost,**metrics(z)})
    pd.DataFrame(sensitivity).to_csv(root/'output/cost_sensitivity.csv',index=False,encoding='utf-8-sig')
    # Distinct return/entry modules; changing all future results leaves selections unchanged.
    before=eng.choose(frozen['balanced']['parameters'],eng.days_by_year['2026'])
    xx=x.copy()
    for field in ['open','high','low','exit']:xx[field]=123.456
    xx['known']=~xx.known
    eng2=Engine(xx,lookup)
    after=eng2.choose(frozen['balanced']['parameters'],eng.days_by_year['2026'])
    assert before==after
    prefix=eng.days_by_year['2026'][:20]
    assert eng.choose(frozen['balanced']['parameters'],prefix)==before[:20]
    t=pd.DataFrame(trades)
    weights=t.groupby(['variant','d']).weight.sum()
    assert np.allclose(weights,1.)
    assert int(t.groupby(['variant','d']).size().max())<=3
    qc={'future_outcome_and_known_invariance':True,'prefix_invariance':True,'weight_sums_one':True,'max_positions':int(t.groupby(['variant','d']).size().max()),'frozen_rules_sha256_before_2026':frozen_hash,'frozen_rules_unchanged':frozen_hash==sha(root/'output/frozen_rules.json'),'source_is_corrected':True,'archived_v8_reproduction':archived_qc,'exact_0906_data':False,'live_strategy_changed':False,'test_period_previously_seen':True,'missing_correlations_are_rejected':True}
    dump(root/'qc/validation.json',qc)
    # Block bootstrap reports uncertainty, not proof against multiple-testing bias.
    balanced=alld[(alld.variant=='balanced')&alld.d.str.startswith('2026')]
    arr=balanced.ret.to_numpy();rng=np.random.default_rng(SEED);boot=[]
    for _ in range(2000):
        st=rng.integers(0,len(arr),size=math.ceil(len(arr)/5));idx=np.concatenate([(np.arange(s,s+5)%len(arr)) for s in st])[:len(arr)];boot.append(np.mean(arr[idx]))
    ci=np.percentile(boot,[2.5,97.5]).tolist()
    summary={'provenance':provenance,'protocol':protocol,'frozen_rules':frozen,'period_results':periods,'baselines':baselines,'development_max_weekly_coverage':bestcoverage,'validation_max_weekly_coverage':maxcoverage,'balanced_2026_daily_mean_ci95_pp':ci,'qc':qc}
    dump(root/'output/summary.json',summary)
    labels={'return':'수익 우선','win':'승률 우선','balanced':'균형안','open_comparator':'시가 청산 비교'}
    hdr=['안','기간','거래일','편입','주평균 거래일','주2일 충족','승률','복리','최대낙폭','보충거래일','매도']
    rows=[]
    for s in periods:
        rows.append([labels.get(s['variant'],s['variant']),s['period'],s['trade_days'],s['stock_trades'],f"{s['average_trade_days_per_week']:.2f}",f"{s['weeks_ge2']}/{s['eligible_weeks']}",pct(s['win_rate_pct']),pct(s['compound_pct']),pct(s['mdd_pct']),s['fallback_days'],s['exit_policy']])
    report=['# 라르고 주 2거래일 제약 최적화','',
        '이 결과는 저장된 교정 원자료로 실제 재계산한 후향 연구입니다. 15:18 진입 또는 익일 09:06 이전 청산 성과가 아닙니다. 투자 성과를 보장하지 않습니다.','',
        '## 기간과 목표','',
        f"후보 {len(x):,}행을 보존했습니다. 비교 범위는 2024년 1월~2026년 4월입니다. 2024년에서 {len(params)}개 진입 설정과 4개 청산 정책을 탐색했습니다. 상위 설정만 2025년에서 선택했습니다. 설정 JSON을 저장한 뒤 2026년 1~4월을 평가했습니다. 이 모든 기간은 앞선 연구에서 이미 살펴본 기간이므로 새 독립 표본이라고 부르지 않습니다.",'',
        '주 2회는 월~일 사이 서로 다른 신호일 2일입니다. 같은 날 3종목을 매수해도 1회입니다. 휴장으로 거래일이 1일 이하인 주는 충족률 분모에서 제외했습니다. 관측 달력은 보존된 시장 거래일 목록입니다.','',
        '## 결과','',table(hdr,rows),'',
        '순수익은 비용 0.30%포인트 차감 후입니다. 종목별 수익률의 단순합계가 아니라 매일 계좌 비중을 반영한 복리입니다. 1종목 100%, 2종목 각50%, 3종목 각33.3%이며 차입과 레버리지는 없습니다. 여러 종목의 추가 편입에는 저장된 GS Quant 사전 상관계수 0.50 이하가 필요합니다. 상관자료가 없으면 추가하지 않습니다.','',
        '## 주 2일 보충 규칙','',
        '해당 주에서 아직 2일 거래하지 않았고 주력 신호가 없으면 고정된 완화 후보군에서 선택합니다. 첫 두 번의 적격 거래일을 실시간 순서로 택하며 주말에 그 주의 수익 좋은 날을 골라내지 않습니다. 이 보충 편입은 원래 라르고 조건의 완전 통과가 아닙니다. 거래 빈도를 강제한 대가를 fallback으로 따로 공개합니다. 안전 후보가 없는 날에는 매수하지 않아 주2일을 미래에 보장하지 않습니다.','',
        '보충 후보도 보통주, 위험/부정재료 제외, 가격1,000원 이상, 거래대금200억원 이상, 시가총액500억원 이상, 구조위험0~15%, 당일0~20% 상승, 소화율0.1~2, 종가위치30% 이상, 몸통20% 이상을 요구합니다. 과거 거래경고 이력이 완전하다는 뜻은 아닙니다.','',
        '## 고정된 선택 설정','',
        '```json',json.dumps(clean({k:{'exit':v['exit'],'parameters':v['parameters']} for k,v in frozen.items()}),ensure_ascii=False,indent=2),'```','',
        'quality 순위는 일별 거래대금 백분위30%, 종가위치20%, 테마확산15%, 역테마순위15%, 낮은위험10%, 소화율0.7근접10%입니다. liquidity는 거래대금 우선, lowrisk는 낮은 구조위험 우선입니다. 8개 상위 후보를 순서대로 보며 상관 조건에 맞는 종목을 최대3개 편입합니다.','',
        '## 청산 가정','',
        'OPEN은 다음 거래일 시가 전량 청산입니다. TP2/3/4_SL3_LOWER는 각각 +2/+3/+4% 목표와 -3% 손절의 일봉 보수적 계산입니다. 시가가 손절 아래면 해당 시가에서 청산합니다. 목표 위로 시작하면 시가를 반영합니다. 한 일봉에서 목표와 손절을 함께 건드리면 손절이 먼저라고 계산합니다. 주문 가능 잔량·가격 지연·가격제한과 목표/손절가 체결은 보장하지 않습니다.','',
        '## 자료와 검증 한계','',
        '시가총액 열 오류를 교정한 입력만 사용했습니다. 현재 상장 목록/주식수/테마를 과거로 투영했고 뉴스 및 거래경고 이력이 불완전합니다. 종가 형태도 신호일 마감값입니다. 그러므로 결과는 수정주가 일봉 모의 성과이며 실제 매매 수익률이 아닙니다. 후보는 하루24개만 보존되어 전체 시장을 전수 평가한 결과도 아닙니다.','',
        '원래 2024~2026년 v8의 17거래 -8.3607738% 재현 여부는 qc/validation.json에 남겼습니다. 직전 대화의 +154.75% 표는 이 교정 원자료와 재현 코드로 입증된 비교 기준으로 사용하지 않았습니다.','',
        f"균형안 2026년 일평균 순수익의 5일 블록 재표집 95% 구간은 {ci[0]:+.4f}~{ci[1]:+.4f}%포인트입니다. 반복 조건 탐색과 데이터 편향을 이 구간이 제거하지는 않습니다.",'',
        '운영 main과 자동주문 기능은 변경하지 않았습니다. research/largo-weekly-two-20260907 연구 브랜치에서만 실행했습니다.'
    ]
    (root/'output/VERIFIED_RESULTS.md').write_text('\n'.join(report)+'\n',encoding='utf-8')
    (root/'README.md').write_text('# Largo weekly two-day optimization\n\nPurpose: compare cumulative returns and daily win rate under distinct-weekly-entry constraints.\nInput: immutable corrected long-history candidate and GS correlation archives.\nProcess: 2024 exploration, 2025 selection, 2026 frozen retrospective check.\nOutputs: output/VERIFIED_RESULTS.md, period_results.csv, weekly_coverage.csv, selected_trades.csv, frozen_rules.json.\nQC: source hashes, archived-v8 reproduction, future-outcome mutation invariance, prefix invariance, weights/position counts.\nReplay: python source/optimize_weekly.py --restored PATH_TO_RESTORED_ARTIFACT --root NEW_PROJECT_DIRECTORY\nAll periods were previously exposed in earlier research. No live orders or deployment.\n',encoding='utf-8')
    files=[]
    for p in sorted(root.rglob('*')):
        if p.is_file() and p.name!='manifest.json':files.append({'path':p.relative_to(root).as_posix(),'bytes':p.stat().st_size,'sha256':sha(p),'created_at':dt.datetime.fromtimestamp(p.stat().st_mtime,dt.timezone.utc).isoformat()})
    dump(root/'manifest.json',{'created_at':dt.datetime.now(dt.timezone.utc).isoformat(),'files':files})
    print(json.dumps(clean({'status':'SUCCESS','period_results':periods,'qc':qc}),ensure_ascii=False),flush=True)

if __name__=='__main__':main()
