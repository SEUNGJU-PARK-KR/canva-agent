#!/usr/bin/env python3
from pathlib import Path
import os,json,hashlib,shutil,zipfile,datetime as dt,html
import numpy as np
import pandas as pd
ROOT=Path(os.environ.get('LARGO_PROJECT','/home/oai/my-project/files/2026-09-07_largo_unlimited_v11'))
OUT=ROOT/'output';QC=ROOT/'qc'
s=json.loads((OUT/'summary.json').read_text());comp=pd.DataFrame(s['comparison']);monthly=pd.DataFrame(s['monthly']);stress=pd.DataFrame(s['stress'])
labels={'balanced':'수익·승률 균형안','win_priority':'승률 우선안','return_priority':'수익 우선안','same_engine_weekly_cap2':'같은 계산기·주 2일 상한','same_engine_no_cap':'같은 계산기·상한만 제거'}
def fmt(v,k):
    if pd.isna(v):return '—'
    if k in ['win_rate','coverage']:return f'{100*v:.1f}%'
    if k.endswith('_pct'):return f'{v:+.2f}%'
    if isinstance(v,(float,np.floating)) and v==int(v):return str(int(v))
    return str(v)
def table(frame,keys,head):
    return '\n'.join(['| '+' | '.join(head)+' |','|'+'|'.join(['---']*len(keys))+'|']+['| '+' | '.join(fmt(r[k],k).replace('|','/') for k in keys)+' |' for r in frame.to_dict('records')])
p=s['balanced_profile'];m=s['balanced_exit'];pick=s['selected']['balanced'];follow=comp[comp.period=='followup'].copy();follow['label']=follow.policy.map(labels)
fmon=monthly[(monthly.policy=='balanced')&(monthly.month>='2025-07')].copy()
allr=comp[(comp.period=='all')&(comp.policy=='balanced')].iloc[0]
f=follow[follow.policy=='balanced'].iloc[0]
# Independent numerical checks on daily account returns and weights.
daily=pd.read_csv(OUT/'daily_results.csv');trades=pd.read_csv(OUT/'selected_trades.csv',dtype={'c':str})
b=daily[(daily.policy=='balanced')&(daily.d>='2025-07-01')&(daily.n<'2026-05-01')]
assert abs(((1+b.return_pct/100).prod()-1)*100-f.compound_pct)<1e-7
assert (b.stocks>0).sum()==f.days
for (policy,d),g in trades.groupby(['policy','d']):
    assert abs(g.weight.sum()-1)<1e-10
    a=daily[(daily.policy==policy)&(daily.d==d)].iloc[0].return_pct
    assert abs((g.weight*g.net_return_pct).sum()-a)<1e-9
qc=json.loads((QC/'checks.json').read_text());qc['independent_compound_and_weight_checks']=True
(QC/'checks.json').write_text(json.dumps(qc,indent=2))
weekly=pd.read_csv(OUT/'weekly_frequency.csv');fw=weekly[(weekly.policy=='balanced')&(weekly.week>='2025-W27')]
lines=['# 주간 상한 없는 종가베팅 조건 최적화 v11','',
'## 먼저 확인할 점','',
'주 2거래일은 최소 목표입니다. 주 2거래일을 채웠다는 이유로 이후 우선 신호를 버리지 않습니다. 하루 최대 1~3종목을 비교하며 상관계수 상한은 0.50입니다.',
'이번 결과는 기존 교정 원자료 15,048행을 사용한 일봉 대용 재연구입니다. 신호일 종가 진입, 다음 날 하루 전체 가격 청산 가정이며 15:18→09:06 실전 성과가 아닙니다. 현재 상장 목록·주식수·테마 구성·오래된 뉴스 누락의 편향이 남습니다.','',
'## 후속 10개월 비교','',
'2025-07~2026-04입니다. 승률은 비용 차감 후 계좌 순수익이 양수인 거래일 비율입니다. 현금일은 승리에 포함하지 않습니다. 각 날 실제 선택된 종목에 동일비중을 배정해 투자한도 100%로 계산했습니다. 레버리지는 없습니다.','',
table(follow,['label','days','stocks','wins','losses','win_rate','compound_pct','mdd_pct','coverage'],['정책','거래일','종목 편입','승','패','승률','누적 복리','최대 낙폭','주 2일 충족률']),'',
'이전 v10 보고서의 86일/75.6%/+38.49%는 별도 참고값입니다. 이번 시계열 순위모형의 입력 변환을 재구현했으므로 기존 v10 선정과 완전 동일한 기준선이라고 주장하지 않습니다. 주간 상한의 순수 효과는 위 같은 계산기 두 행끼리 비교합니다.','',
'## 개발에서 고정한 균형안 조건','',
table(pd.DataFrame([{'condition':'당일 등락률','value':str(p['chg'])+'%'},{'condition':'구조 위험률 상한','value':str(p['risk']*100)+'%'},{'condition':'거래대금 소화율','value':str(p['dig'])},{'condition':'종가 위치 하한','value':str(p['cl']*100)+'%'},{'condition':'몸통 하한','value':str(p['body']*100)+'%'},{'condition':'패턴 점수 하한','value':p['pat']},{'condition':'테마·재료 경로','value':p['context']},{'condition':'사전 20일 변동성 상한','value':str(p['vol'])+'%'},{'condition':'거래대금 하한','value':f'{p["tv"]/1e8:g}억원'},{'condition':'하루 종목 한도','value':pick['maxn']},{'condition':'순위 모형','value':pick['ranker']},{'condition':'주간 보충','value':pick['schedule']},{'condition':'매도 규칙','value':m['name']}]),['condition','value'],['조건','설정']),'',
'loose는 테마 확산 35% 이상·10위 이내 또는 재료 감사·직접성 10 이상·신선도 3 이상입니다. strong은 테마 확산 60% 이상·3위 이내 또는 감사·직접성 14 이상·신선도 7 이상입니다. none은 추가 테마·재료 통과조건을 두지 않습니다. 기록된 부정 재료 제외는 모든 경로에 유지합니다.',
'기본 안전 범위는 기록된 강제 제외·부정 재료·비보통주 차단, 주가 1,000원·시총 500억원·거래대금 200억원 이상입니다. 실제 과거 거래경고를 모두 확인한 범위는 아닙니다.',
'주 2일 미달 시 우선 후보가 없으면 안전 범위의 상승률 -10~29%, 구조위험 0~30%, 소화율 .05~5, 사전 변동성 20% 이하 보충군을 사용합니다. early는 그날 바로 보충하며 deadline은 알려진 주간 잔여 거래일이 필요한 횟수 이하일 때 보충합니다. 그 주의 미래 종목이나 미래 수익은 보지 않습니다.',
f'후속 우선 후보 거래일은 {s["followup_primary_days"]}일, 예외 보충은 {s["followup_backup_days"]}일입니다. 비교 가능한 주는 {int(f.eligible_weeks)}주이며 {int(f.covered_weeks)}주가 최소 2신호일을 충족했습니다. 휴장 한 거래일 주와 구간 경계가 잘린 주는 분모에서 제외했습니다.',
'gap_ridge는 익일 시가 수익률 순위입니다. utility_ridge는 작은 익절정책 기대수익과 큰 손실빈도 보정입니다. win_loss_ridge는 이익빈도와 큰 손실빈도 차이입니다. stop_ridge는 보수적 손절 포함 수익 순위입니다. 모두 직전 12개월의 결과 확정 자료만 학습하고 alpha=100을 고정했습니다. 승률 점수를 보정된 실제 확률로 표시하지 않습니다.','',
'## 월별 균형안 결과','',
table(fmon,['month','days','stocks','wins','losses','win_rate','compound_pct','mdd_pct'],['신호 월','거래일','종목 편입','승','패','승률','월 복리','월 낙폭']),'',
'## 체결·비용 스트레스','',
table(stress,['scenario','days','win_rate','compound_pct','mdd_pct'],['가정','거래일','승률','누적 복리','최대 낙폭']),'',
'시가가 목표보다 높으면 기본 계산은 시가 청산을 허용합니다. 시가가 손절선 아래면 손절선이 아니라 시가로 손실을 잡습니다. stop 방식은 하루 전체 고가·저가가 목표와 손절을 모두 건드렸을 때 손절을 먼저 적용합니다. optimistic_touch_order는 반대 경계입니다. gapstop은 개장 시가에만 작동하고 장중 손절은 없는 별도 가정입니다.',
'손절선이나 목표가 접촉은 실제 체결을 보장하지 않습니다. cap_open_gain_at_target는 큰 갭 상승 이익도 목표로 제한한 진단입니다. best_N_days_removed는 상위 N거래일 이익을 0으로 바꾼 진단이며 실제 규칙이 아닙니다.',
f'이익으로 마친 종목 중 다음 날 하루 저가가 -3% 이했던 사례는 {s["wins_with_intraday_loss_below3"]}건입니다. 이 반등이 09:06 전에 발생했는지 알 수 없습니다.','',
'## 탐색 과정과 불확실성','',
f'{s["search_count"]:,}개 설정을 2024년 개발 구간에서 비교했습니다. 개발 상위 {s["selection_count"]}개를 2025년 상반기에서 비교한 뒤 균형·승률우선·수익우선안을 고정했습니다. 후속 수익으로 설정을 다시 바꾸지 않았습니다.',
'개발·선택의 원래 요구는 주 2일 100% 충족, 양의 누적수익, 각각 80/35거래일 이상입니다. 낙폭은 개발 -35%, 선택 -25%, 승률은 55% 하한을 추가했습니다. 조건을 만족하는 후보가 부족해 완화했는지는 frozen_before_followup.json에 명시했습니다.',
'균형 목적은 후보들 사이 승률 백분위+로그수익 백분위+0.5×낙폭 백분위입니다. 수익과 승률의 전역 최댓값을 찾았다는 주장이 아닙니다. 모든 기간은 이전 대화에서도 본 자료이므로 새로운 독립 미사용 검증이 아닙니다.',
f'주 단위 2,000회 재표집한 거래일 승률 95% 구간은 {s["uncertainty"]["win_rate_ci95"]}, 거래일 평균수익 구간은 {s["uncertainty"]["mean_trade_ci95"]}%입니다. 반복 탐색과 자료 편향을 모두 교정한 예측구간은 아닙니다.',
f'개발을 포함한 2024-01~2026-04 전체 균형안은 {int(allr.days)}거래일, 승률 {100*allr.win_rate:.1f}%, 복리 {allr.compound_pct:+.2f}%, 최대 낙폭 {allr.mdd_pct:.2f}%입니다. 개발 구간 포함 숫자를 독립 성과로 쓰지 않습니다.','',
'## 재현과 보관','',
'input에 원본 아티팩트를 수정 없이 보존했습니다. source에 실행기와 보고서 생성기를 보관했습니다. work는 중간 파일이며 output과 qc를 별도로 분리했습니다. README.md와 manifest.json에 과정·해시·생성 시각을 기록했습니다. 전체 프로젝트 ZIP에는 재현용 입력과 소스를 포함합니다. /home/oai는 임시 공간입니다. 저장소에는 연구 결과와 소스를 남기고 운영 전략·사이트·계좌·주문 기능은 변경하지 않았습니다.','',
'상관계수는 신호일 이전 60거래일의 종가 간 수익률과 갭 수익률 상관 중 큰 값입니다. 공통관측치 40일 미만이면 추가 편입을 차단했습니다. GS Quant 실제 함수와 NumPy 계산을 대조한 기록은 qc/gs_comparison.json에 있습니다. 이것은 골드만삭스의 전략 인증이 아닙니다.','',
'참고: https://developer.gs.com/docs/gsquant/api/functions/gs_quant.timeseries.econometrics.correlation.html',
'참고: https://www.finra.org/investors/insights/stop-orders-factors-consider-during-volatile-markets']
report='\n\n'.join(lines)+'\n';(OUT/'UNLIMITED_OPTIMIZATION_REPORT.md').write_text(report,encoding='utf-8')
# A self-contained readable report; no remote scripts or trading controls.
ht='<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>라르고 주간 상한 없는 검증</title><style>body{font-family:system-ui,sans-serif;max-width:1120px;margin:32px auto;padding:0 18px;line-height:1.7}table{border-collapse:collapse;min-width:700px;width:100%;font-size:14px}th,td{padding:9px;border-bottom:1px solid #ddd;text-align:right}th:first-child,td:first-child{text-align:left}section{overflow:auto;margin:24px 0}pre{white-space:pre-wrap;font:14px/1.7 system-ui}.note{background:#fff0d4;padding:18px}</style></head><body><h1>주간 상한 없는 조건 최적화</h1><p class="note">일봉 대용 연구 · 15:18→09:06 실전 성과 아님 · 과거 자료에서 설정 선택</p><section>'+follow[['label','days','stocks','win_rate','compound_pct','mdd_pct']].to_html(index=False,float_format=lambda v:f'{v:.4f}')+'</section><section>'+fmon.to_html(index=False,float_format=lambda v:f'{v:.4f}')+'</section><pre>'+html.escape(report)+'</pre></body></html>'
(OUT/'unlimited-optimization-report.html').write_text(ht,encoding='utf-8')
(ROOT/'README.md').write_text('# Largo unlimited v11\n\n목적: 최소 주 2거래일, 주간 상한 없음, 하루 1~3종목으로 비용 차감 수익·승률·낙폭을 비교합니다.\n\n입력: 교정 아티팩트 9971831156의 원본 ZIP, 후보 CSV와 가격 이력. 원본 해시는 qc/provenance.json에 있습니다.\n\n재현: Python 3.12 환경에서 numpy, pandas, requests, gs-quant==2.1.12 설치 후 source/run_research.py와 source/package_report.py 순서로 실행합니다. LARGO_PROJECT 환경변수로 프로젝트 폴더를 지정할 수 있습니다. 아카이브가 input에 있으면 원본 재다운로드가 필요 없습니다.\n\n결과: output/UNLIMITED_OPTIMIZATION_REPORT.md, summary.json, 일별·월별·주별 CSV, 탐색 전수 결과.\n\n검수: 원본 SHA, 미래 결과 사용 금지, 이전 완료 결과만 학습, 주간 상한 제거, GS 상관 대조, 투자비중과 복리 독립 재계산. 브라우저 시각 검수는 별도 수행하지 않았습니다.\n\n분석은 일봉 대용이며 당시 전 시장 전수검사가 아닙니다. 사이트·운영 매수 규칙은 변경하지 않았습니다. /home/oai는 임시 작업공간입니다.\n',encoding='utf-8')
now=dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).isoformat();manifest=[]
for pth in sorted(ROOT.rglob('*')):
    if pth.is_file() and pth.name!='manifest.json':manifest.append({'path':str(pth.relative_to(ROOT)),'bytes':pth.stat().st_size,'sha256':hashlib.sha256(pth.read_bytes()).hexdigest(),'modified_at':dt.datetime.fromtimestamp(pth.stat().st_mtime,dt.timezone.utc).isoformat()})
dump(ROOT/'manifest.json',{'created_at':now,'files':manifest})
delivery=Path('/mnt/data/largo_unlimited_v11');delivery.parent.mkdir(parents=True,exist_ok=True)
if delivery.exists():shutil.rmtree(delivery)
shutil.copytree(ROOT,delivery)
archive=Path('/mnt/data/largo_unlimited_v11_project.zip')
with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
    for pth in delivery.rglob('*'):
        if pth.is_file():z.write(pth,Path('largo_unlimited_v11')/pth.relative_to(delivery))
print('PACKAGED',archive.stat().st_size,flush=True)
print(follow[['label','days','stocks','wins','losses','win_rate','compound_pct','mdd_pct','coverage']].to_string(index=False),flush=True)
