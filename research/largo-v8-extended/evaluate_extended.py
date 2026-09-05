#!/usr/bin/env python3
"""Frozen v8 recipe, time-ordered retrospective DAILY-proxy evaluation.
No threshold search. No trading APIs. GS correlations are read from audited input.
"""
from pathlib import Path
import argparse,hashlib,itertools,json,math,html,datetime as dt
import numpy as np
import pandas as pd
PARAMS={'threshold':.75,'alpha':20.,'max_positions':3,'correlation_cap':.5,'correlation_penalty':.5,'shortlist':8,'target_pct':3.,'cost_pct':.3,'min_train_rows':20,'start':'2024-01-01','end':'2026-04-30','training_start':'2023-10-01','allocation':'1/k among selected k; cash only when k=0','label_cutoff':'outcome day < first signal day of test month','data_scope':'DAILY_PROXY; current universe and theme membership, incomplete old news, unverified historical alerts'}
NUM=['chg','risk','cl','wick','body','pat','br','rank','fol','dir','fresh','dig','tv','mc','open','high','low','exit']
def prep(x):
    x=x.copy()
    for k in ['hr','cs','neg','audit','known']:x[k]=x[k].astype(str).str.lower().isin(['true','1','yes'])
    for k in NUM:x[k]=pd.to_numeric(x[k],errors='coerce').replace([np.inf,-np.inf],np.nan)
    x['c']=x.c.astype(str).str.zfill(6)
    x['riskp']=100*x.risk;x['clp']=100*x.cl;x['wickp']=100*x.wick;x['bodyp']=100*x.body
    context=((x.br>=.5)&(x['rank']<=5))|(x.audit&(x['dir']>=14)&(x.fresh>=7))
    safe=(~x.hr)&x.cs&(~x.neg)
    x['pool']=safe&x.chg.between(0,15)&x.riskp.between(0,15)&x.dig.between(.2,1.5)&(x.bodyp>=45)&x.pat.between(50,90)&context
    x['base_pool']=safe&x.mk.isin(['KOSPI','KOSDAQ'])&x.chg.between(0,10)&x.riskp.between(0,15)&x.dig.between(.2,1.5)&(x.clp>=50)&(x.wickp<=50)&x.bodyp.between(45,100)&x.pat.between(50,90)&context
    valid=x[['open','high','low','exit']].notna().all(axis=1)&(x.high+1e-7>=x[['open','exit','low']].max(axis=1))&(x.low-1e-7<=x[['open','exit','high']].min(axis=1))
    x['valid']=valid
    x['ret']=np.where(x.known&valid,np.where(x.high>=3,2.7,x.exit-.3),np.nan)
    return x

def design(tr,te):
    a=pd.DataFrame(index=tr.index);b=pd.DataFrame(index=te.index);scales={}
    for k in ['chg','riskp','clp','wickp','bodyp','pat','br','rank','fol','dir','fresh','dig','tv','mc','momfit','riskfit','digfit']:
        if k=='momfit':v=-abs(tr.chg-12.5);w=-abs(te.chg-12.5)
        elif k=='riskfit':v=-abs(tr.riskp-5);w=-abs(te.riskp-5)
        elif k=='digfit':v=-abs(np.log(tr.dig.clip(lower=.05)/.7));w=-abs(np.log(te.dig.clip(lower=.05)/.7))
        elif k in ['dig','tv','mc']:v=np.log1p(tr[k].clip(lower=0));w=np.log1p(te[k].clip(lower=0))
        else:v=tr[k];w=te[k]
        med=float(v.median()) if v.notna().any() else 0.
        sd=float(v.std(ddof=0 if k.endswith('fit') else 1))
        if not np.isfinite(sd) or sd==0:sd=1.
        a[k]=(v.fillna(med)-med)/sd;b[k]=(w.fillna(med)-med)/sd
        scales[k]={'median':med,'std':sd}
    a.insert(0,'const',1.);b.insert(0,'const',1.)
    return a,b,scales

def predict(tr,te):
    a,b,scales=design(tr,te);X=a.to_numpy();y=tr.ret.clip(-10,3).to_numpy();reg=np.eye(X.shape[1])*20;reg[0,0]=0
    beta=np.linalg.solve(X.T@X+reg,X.T@y)
    return pd.Series(b.to_numpy()@beta,index=te.index),{'coefficients':dict(zip(a.columns,map(float,beta))),'scales':scales}

def make_corr(c):
    lookup={}
    for r in c.itertuples(index=False):
        assert str(r.window_end).replace('-','')<str(r.signal_date).replace('-','')
        v=max(float(r.corr_gap),float(r.corr_cc)) if np.isfinite(r.corr_gap) and np.isfinite(r.corr_cc) else .8
        lookup[(r.signal_date,*sorted([str(r.a).zfill(6),str(r.b).zfill(6)]))]=v
    return lookup

def select(g,lookup,maxn=3):
    g=g[g.pred>=.75].sort_values('pred',ascending=False,kind='stable').head(8)
    if g.empty:return g.copy()
    for k in range(min(maxn,len(g)),0,-1):
        possible=[]
        for inds in itertools.combinations(g.index,k):
            r=g.loc[list(inds)];cs=[lookup.get((r.d.iloc[0],*sorted([a,b])),.8) for a,b in itertools.combinations(r.c,2)]
            if any(c>.5 for c in cs):continue
            possible.append((float(r.pred.mean())-.5*(float(np.mean(cs)) if cs else 0),inds,cs))
        if possible:
            best=max(possible,key=lambda x:x[0]);break
    r=g.loc[list(best[1])].copy();r['weight']=1/len(r);r['avg_pair_corr']=np.mean(best[2]) if best[2] else np.nan;r['max_pair_corr']=max(best[2]) if best[2] else np.nan
    return r

def bounds(r):
    if not np.isfinite(r.ret):return np.nan,np.nan,False
    if r.open>=3:return 2.7,2.7,False
    if r.open<=-3:return r.open-.3,r.open-.3,False
    tp=r.high>=3;sl=r.low<=-3
    if tp and sl:return -3.3,2.7,True
    if sl:return -3.3,-3.3,False
    if tp:return 2.7,2.7,False
    return r.exit-.3,r.exit-.3,False

def to_daily(s,dates,policy):
    groups={d:g for d,g in s.groupby('d')} if len(s) else {};rows=[]
    for d in dates:
        r={'policy':policy,'d':d,'m':d[:7],'stocks':0,'names':'','codes':'','ret':0.,'open_ret':0.,'close_ret':0.,'stop_lower':0.,'stop_upper':0.,'fixed_slots_ret':0.,'stop_ambiguous_stocks':0,'hit3':0,'evaluated':True}
        g=groups.get(d)
        if g is not None:
            r.update(stocks=len(g),names=' + '.join(g.nm),codes='|'.join(g.c),n=g.n.iloc[0],evaluated=bool(g.ret.notna().all()),hit3=int((g.high>=3).sum()))
            if r['evaluated']:
                r['ret']=float((g.ret*g.weight).sum());r['open_ret']=float(((g.open-.3)*g.weight).sum());r['close_ret']=float(((g.exit-.3)*g.weight).sum())
                bb=[bounds(v) for v in g.itertuples()]
                r['stop_lower']=sum(v[0]*w for v,w in zip(bb,g.weight));r['stop_upper']=sum(v[1]*w for v,w in zip(bb,g.weight));r['stop_ambiguous_stocks']=sum(v[2] for v in bb);r['fixed_slots_ret']=float(g.ret.sum()/3)
            else:
                for col in ['ret','open_ret','close_ret','stop_lower','stop_upper','fixed_slots_ret']:r[col]=np.nan
        rows.append(r)
    return pd.DataFrame(rows)

def stats(d):
    good=d[d.evaluated];a=good[good.stocks>0];e=np.r_[1.,np.cumprod(1+good.ret.to_numpy()/100)];complete=bool(d.evaluated.all())
    compound=lambda col:float((np.prod(1+good[col]/100)-1)*100) if complete else None
    return {'calendar_days':len(d),'trade_days':int((d.stocks>0).sum()),'stocks':int(d.stocks.sum()),'evaluated_trade_days':len(a),'missing_days':int((~d.evaluated).sum()),'wins':int((a.ret>0).sum()),'losses':int((a.ret<0).sum()),'hit3':int(a.hit3.sum()),'mean_trade_pct':float(a.ret.mean()) if len(a) else None,'mean_calendar_pct':float(good.ret.mean()),'sum_pct':float(good.ret.sum()) if complete else None,'compound_pct':compound('ret'),'mdd_pct':float(((e/np.maximum.accumulate(e)-1)*100).min()) if complete else None,'worst_day_pct':float(good.ret.min()),'best_day_pct':float(good.ret.max()),'open_compound_pct':compound('open_ret'),'stop_lower_compound_pct':compound('stop_lower'),'stop_upper_compound_pct':compound('stop_upper'),'fixed_slots_compound_pct':compound('fixed_slots_ret')}

def pct(v):return '미평가' if v is None or not np.isfinite(float(v)) else f'{float(v):+.2f}%'
def mdtable(headers,rows):return '\n'.join(['| '+' | '.join(headers)+' |','|'+'|'.join(['---']*len(headers))+'|']+['| '+' | '.join(map(str,r))+' |' for r in rows])

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--input',required=True);ap.add_argument('--output',required=True);args=ap.parse_args();root=Path(args.input);out=Path(args.output);out.mkdir(parents=True,exist_ok=True)
    x=prep(pd.read_csv(root/'output/extended_candidates.csv',dtype={'c':str}));lookup=make_corr(pd.read_csv(root/'output/gs_correlations.csv',dtype={'a':str,'b':str,'window_end':str}))
    dates=sorted(x.loc[x.d.between(PARAMS['start'],PARAMS['end']),'d'].unique());months=sorted({d[:7] for d in dates});x['pred']=np.nan
    folds=[];models={};picks=[];ones=[]
    for m in months:
        first=min(d for d in dates if d.startswith(m));tr=x[x.pool&x.ret.notna()&(x.m<m)&(x.n<first)];te=x[x.pool&(x.m==m)]
        removed=x[x.pool&x.ret.notna()&(x.m<m)&(x.n>=first)]
        f={'test_month':m,'test_first_day':first,'train_rows':len(tr),'test_pool_rows':len(te),'train_first_day':tr.d.min() if len(tr) else None,'train_last_day':tr.d.max() if len(tr) else None,'train_last_outcome':tr.n.max() if len(tr) else None,'purged_boundary_labels':len(removed)};folds.append(f)
        if len(tr)<20:continue
        assert tr.n.max()<first and tr.m.max()<m
        p,model=predict(tr,te);x.loc[p.index,'pred']=p;models[m]=model
        for d,g in x.loc[p.index].groupby('d'):
            s=select(g,lookup);o=select(g,lookup,1)
            if len(s):picks.append(s)
            if len(o):ones.append(o)
    empty=pd.DataFrame(columns=list(x)+['weight','avg_pair_corr','max_pair_corr'])
    selected=pd.concat(picks) if picks else empty;one=pd.concat(ones) if ones else empty
    base=[]
    for d,g in x[x.d.isin(dates)&x.base_pool].groupby('d'):
        s=g.sort_values(['risk','tv','c'],ascending=[True,False,True]).head(1).copy();s['weight']=1.;base.append(s)
    base=pd.concat(base) if base else empty
    daily=to_daily(selected,dates,'v8_max3');bd=to_daily(base,dates,'v61_single');od=to_daily(one,dates,'v8_single');all_days=pd.concat([daily,bd,od]);monthrows=[]
    for (policy,m),g in all_days.groupby(['policy','m']):monthrows={'policy':policy,'month':m,**stats(g)};monthrows['loss_days']=monthrows['losses'];monthrows['positive_days']=monthrows['wins'];monthrows['max_drawdown_pct']=monthrows['mdd_pct'];monthrows['worst']=monthrows['worst_day_pct'];monthrows.append(monthrows)
    monthly=pd.DataFrame(monthrows for monthrows_item in [] for monthrows in []) if False else pd.DataFrame(monthrows for monthrows in []) if False else pd.DataFrame(monthrows for monthrows_item in []) if False else pd.DataFrame(monthrows)
    monthly.to_csv(out/'monthly_results.csv',index=False,encoding='utf-8-sig');selected.to_csv(out/'v8_trades.csv',index=False,encoding='utf-8-sig');base.to_csv(out/'v61_trades.csv',index=False,encoding='utf-8-sig');daily.to_csv(out/'v8_daily.csv',index=False,encoding='utf-8-sig');all_days.to_csv(out/'all_policy_daily.csv',index=False,encoding='utf-8-sig');pd.DataFrame(folds).to_csv(out/'training_folds.csv',index=False,encoding='utf-8-sig');x.to_csv(out/'all_scored_candidates.csv',index=False,encoding='utf-8-sig')
    (out/'monthly_models.json').write_text(json.dumps(models,ensure_ascii=False,indent=2))
    check_m=months[-1];first=min(d for d in dates if d.startswith(check_m));tr=x[x.pool&x.ret.notna()&(x.m<check_m)&(x.n<first)];te=x[x.pool&(x.m==check_m)].copy();p1,_=predict(tr,te)
    for k in ['open','high','low','exit','ret']:te[k]=999.
    te['known']=~te.known;p2,_=predict(tr,te)
    qc={'input_rows':len(x),'duplicate_rows':int(x.duplicated(['d','c']).sum()),'inconsistent_outcomes':int((x.known&~x.valid).sum()),'train_outcomes_before_test':all(f['train_last_outcome'] is None or f['train_last_outcome']<f['test_first_day'] for f in folds),'purged_boundary_labels':sum(f['purged_boundary_labels'] for f in folds),'prediction_invariant_to_test_outcomes':bool(np.allclose(p1,p2,equal_nan=True)),'max_positions':int(daily.stocks.max()),'pair_cap_pass':bool((selected.max_pair_corr.dropna()<=.5+1e-10).all()),'exact_0906_execution':False}
    assert qc['duplicate_rows']==0 and qc['train_outcomes_before_test'] and qc['prediction_invariant_to_test_outcomes'] and qc['pair_cap_pass']
    paired=daily.merge(bd,on='d',suffixes=('_v8','_base'));paired=paired[paired.evaluated_v8&paired.evaluated_base];z=(paired.ret_v8-paired.ret_base).to_numpy();rng=np.random.default_rng(815);vals=[]
    for _ in range(3000):
        starts=rng.integers(0,len(z),size=math.ceil(len(z)/10));idx=np.concatenate([np.arange(a,a+10)%len(z) for a in starts])[:len(z)];vals.append(z[idx].mean())
    bootstrap={'block_days':10,'repetitions':3000,'mean_difference_pp':float(z.mean()),'ci95_pp':list(map(float,np.percentile(vals,[2.5,97.5])))}
    meta=json.loads((root/'qc/collection_metadata.json').read_text());audit=json.loads((root/'qc/market_cap_parser_audit.json').read_text()) if (root/'qc/market_cap_parser_audit.json').exists() else {}
    summary={'parameters':PARAMS,'v8':stats(daily),'v61':stats(bd),'v8_single':stats(od),'bootstrap':bootstrap,'qc':qc,'collection':meta,'parser_examples':audit.get('header_examples',[])[:1],'data_sha256':hashlib.sha256((root/'output/extended_candidates.csv').read_bytes()).hexdigest(),'months':months}
    (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2,allow_nan=False));(out/'qc.json').write_text(json.dumps(qc,indent=2));(out/'config.json').write_text(json.dumps(PARAMS,ensure_ascii=False,indent=2))
    a=monthly[monthly.policy=='v8_max3'].set_index('month');b=monthly[monthly.policy=='v61_single'].set_index('month');rows=[]
    for m,r in a.iterrows():rows.append([m,int(r.calendar_days),int(r.trade_days),int(r.stocks),str(int(r.wins))+'승 '+str(int(r.losses))+'패',pct(r.compound_pct),pct(b.loc[m,'compound_pct']),pct(r.mdd_pct),pct(r.worst_day_pct)])
    headers=['월','검사일','거래일','종목 편입','계좌 승패','v8 복리','v6.1 복리','v8 최대 낙폭','v8 최악 하루']
    v=summary['v8'];bs=summary['v61']
    report=['# 라르고 v8 장기 확장 검증','',
      '검증 범위는 2024년 1월~2026년 4월입니다. 신호일 종가 진입과 다음 거래일 하루 전체 고가·종가를 사용한 일봉 대용치입니다. 15:18 매수·다음 날 09:06 이전 청산의 실전 성과가 아닙니다.','',
      '## 수집 오류 교정','',
      '기존 재구성기는 네이버 시가총액 표의 거래량 열(0 기준 9번)을 시가총액으로 읽었습니다. 시가총액 열 이름(표의 6번 열)을 찾아 읽도록 교정했습니다. 같은 날짜에 확보한 가격·테마·뉴스 캐시로 후보를 다시 계산했습니다. 기존 v8 숫자는 재현할 수 있어도 잘못된 시가총액 입력이 있으므로 실전 근거로 확정하지 않습니다.','',
      '## 규칙과 학습','',
      'v8 후보 기준과 17개 입력 변환은 유지했습니다. 예측점수 0.75, 릿지 규제 20, 최대 3종목, 60거래일 사전 상관 0.50, 공통 관측치 40일을 고정했습니다. 새 기간에서 기준값을 최적화하지 않았습니다.','',
      '2023년 10~12월로 초기 학습했습니다. 그 후 매월 이전 자료만 추가했습니다. 학습 거래의 결과일이 시험 월 첫 신호일보다 빠른 경우만 사용했습니다. 2026년에 학습한 계수를 더 과거에 가져다 쓰지 않았습니다. 숫자 규칙 자체는 나중에 고른 연구 가설이므로 당시 실시간 전진검증으로 부르지 않습니다.','',
      '선정 종목이 1개면 100%, 2개면 각 50%, 3개면 각 33.3%입니다. 후보가 없을 때만 현금입니다. 비교 v6.1도 같은 투자한도로 계산했습니다. 위험별 비중축소는 주 비교에 넣지 않았습니다.','',
      '종목별 목표 +3%에 도달하면 +3%로 제한합니다. 미도달 시 다음 날 종가에 청산합니다. 매 거래 왕복 비용 0.30%포인트를 가정했습니다. 기본 정책에는 장중 손절이 없습니다.','',
      '## 월별 모의 계좌 수익률','',mdtable(headers,rows),'',
      '월은 신호일 기준입니다. 월말 신호의 다음 달 결과도 신호 월에 포함합니다. 수익률은 종목별 합계가 아니라 날짜별 비중 적용 수익의 복리입니다. 승패 역시 계좌 기준입니다.','',
      '## 전체 비교','',mdtable(['항목','v8','v6.1'],[[k,vv,bb] for k,vv,bb in [
       ('거래일',v['trade_days'],bs['trade_days']),('편입 건수',v['stocks'],bs['stocks']),('누적 복리',pct(v['compound_pct']),pct(bs['compound_pct'])),('최대 낙폭',pct(v['mdd_pct']),pct(bs['mdd_pct'])),('거래일 평균',pct(v['mean_trade_pct']),pct(bs['mean_trade_pct'])),('다음 날 시가 청산 복리',pct(v['open_compound_pct']),pct(bs['open_compound_pct'])),('-3% 손절 가정 보수적 복리',pct(v['stop_lower_compound_pct']),pct(bs['stop_lower_compound_pct'])),('-3% 손절 가정 낙관적 복리',pct(v['stop_upper_compound_pct']),pct(bs['stop_upper_compound_pct']))]]),'',
      '손절 민감도는 같은 선정 종목을 대상으로 계산했습니다. 시가가 -3%보다 낮으면 -3%가 아니라 해당 시가로 청산합니다. 한 일봉이 목표와 손절을 모두 건드리면 순서를 알 수 없어 보수적·낙관적 경계값을 함께 제시합니다. 터치 가격의 체결은 보장되지 않습니다.','',
      '## 자료와 GS Quant','',
      f"수집 시점 목록 {meta['universe']}개 중 가격 이력은 {meta['charts']}개입니다. 후보 행은 {meta['candidate_rows']}개입니다. GS Quant {meta['gs_quant_version']}의 correlation을 {meta['gs_calls']}회 실행했습니다. 사전 종목쌍은 {meta['gs_pairs']}개입니다. NumPy 결과와 최대 차이는 {meta['gs_max_numpy_error']:.4g}입니다. 회귀·매매 정책은 자체 코드이며 골드만삭스의 성과 인증이 아닙니다.",'',
      '현재 상장 목록과 테마 구성을 과거에 대입했습니다. 당시 상장폐지 종목·과거 거래경고·테마 변경을 완전히 복원하지 못했습니다. 주식 수도 현재 시점 대용치입니다. 과거 뉴스 조회 깊이에 제한이 있어 직접 재료 누락이 있습니다. 현재 캐시에 없는 새 후보의 뉴스는 재조회하거나 만들어내지 않았습니다. 수정주가의 사후 변경도 남아 있습니다.','',
      '결과가 없는 후보를 사후에 교체하지 않았습니다. 선정 포지션에 누락이 있으면 해당 날짜는 미평가입니다. 그 기간 전체 누적 수익도 확정하지 않습니다.','',
      f"학습 경계에서 제거한 결과 미완료 행은 {qc['purged_boundary_labels']}개입니다. 시험월 결과값을 바꿔도 예측은 같습니다. 10일 블록 재표집 3,000회의 v8-v6.1 일평균 차이 95% 구간은 {bootstrap['ci95_pp'][0]:+.4f}~{bootstrap['ci95_pp'][1]:+.4f}%포인트입니다. 이 검사는 생존편향이나 뉴스 누락을 제거하지 않습니다.",'',
      '## 결과 파일','',
      'v8_trades.csv는 각 신호일의 종목·가격 대용치·비중·다음 날 수익률입니다. v8_daily.csv는 현금일 포함 계좌 결과입니다. training_folds.csv는 월별 학습 자료의 마지막 날짜와 결과일입니다. 원자료와 출처 해시, 코드, 모델 계수와 검수는 재현 패키지에 함께 보존합니다. 운영 사이트와 실제 주문 기능은 바꾸지 않았습니다.'
    ]
    (out/'VERIFIED_RESULTS.md').write_text('\n'.join(report)+'\n')
    worst=daily[daily.stocks>0].sort_values('ret').head(10);worst.to_csv(out/'worst_days.csv',index=False,encoding='utf-8-sig');selected.sort_values('ret').head(20).to_csv(out/'worst_trades.csv',index=False,encoding='utf-8-sig')
    simple='<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>v8 장기 검증</title><style>body{font-family:system-ui;margin:30px auto;max-width:1150px;padding:0 16px;line-height:1.7}pre{white-space:pre-wrap;background:#f5f6f7;padding:20px}table{border-collapse:collapse;width:100%;font-size:13px}td,th{padding:9px;border-bottom:1px solid #ddd;text-align:right}.scroll{overflow:auto}h1{font-size:28px}.warning{background:#fff2d9;padding:18px}</style></head><body><h1>라르고 v8 장기 검증</h1><p class="warning">일봉 대용 연구입니다. 09시 06분 전 실제 매도 성과가 아닙니다. 시가총액 수집 오류를 교정했습니다.</p><h2>월별 결과</h2><div class="scroll">'+pd.DataFrame(rows,columns=headers).to_html(index=False,escape=True)+'</div><h2>선정 종목</h2><div class="scroll">'+selected[['d','n','nm','c','px','pred','weight','open','high','exit','ret']].to_html(index=False,escape=True,float_format=lambda v:f'{v:,.4f}')+'</div><h2>방법·한계·검수</h2><pre>'+html.escape('\n'.join(report))+'</pre></body></html>'
    (out/'extended-v8-report.html').write_text(simple)
    print('RESULTS_START');print(monthly[monthly.policy=='v8_max3'].to_string(index=False));print(json.dumps({'v8':v,'v61':bs,'qc':qc},ensure_ascii=False,indent=2));print('RESULTS_END')
if __name__=='__main__':main()
