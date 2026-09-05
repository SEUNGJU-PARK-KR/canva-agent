#!/usr/bin/env python3
"""Repair inherited cols[9] market-cap bug using header-based parsing.
Do not redownload price histories or alter strategy thresholds.
"""
from __future__ import annotations
import os, json, gzip, importlib.util, shutil, re, concurrent.futures as cf
from pathlib import Path
from bs4 import BeautifulSoup
os.environ['LARGO_OUTPUT']='long-history-corrected'
spec=importlib.util.spec_from_file_location('collector',Path(__file__).with_name('collect_v8_long_history.py'))
col=importlib.util.module_from_spec(spec);spec.loader.exec_module(col)
base=Path('frozen-original')
old=json.loads((base/'input/market_universe_snapshot.json').read_text())
with gzip.open(base/'input/all_daily_prices.json.gz','rt',encoding='utf-8') as f:prices=json.load(f)
fixed={};page_audit=[];diff=[]
rawdir=col.ROOT/'raw/market_pages';rawdir.mkdir(parents=True,exist_ok=True)

def parse_page(market,page):
    url=f'{col.back.NAVER_FINANCE}/sise/sise_market_sum.naver?sosok={market}&page={page}'
    raw=col.back.get(url).content
    (rawdir/f'{market}-{page:03}.html').write_bytes(raw)
    soup=BeautifulSoup(raw.decode('euc-kr',errors='replace'),'html.parser')
    table=soup.select_one('table.type_2')
    if table is None:return {},{'market':market,'page':page,'headers':[],'rows':0}
    headers=[]
    for tr in table.select('tr'):
        hs=[x.get_text(' ',strip=True) for x in tr.select('th')]
        if '시가총액' in hs and '상장주식수' in hs and '현재가' in hs:headers=hs;break
    if not headers:raise ValueError(f'Missing required headers {market}/{page}')
    ci=headers.index('시가총액');si=headers.index('상장주식수');pi=headers.index('현재가');vi=headers.index('거래량')
    found={};max_identity_error=0.
    for tr in table.select('tr'):
        anchor=tr.select_one('a.tltle')
        if not anchor:continue
        match=re.search(r'code=(\d{6})',anchor.get('href',''))
        cols=[x.get_text(' ',strip=True) for x in tr.select('td')]
        if not match or len(cols)!=len(headers):raise ValueError('Table row/header mismatch')
        code=match.group(1)
        price=col.back.fnum(cols[pi]);cap=col.back.fnum(cols[ci]);shares_k=col.back.fnum(cols[si]);volume=col.back.fnum(cols[vi])
        if any(x is None or x<=0 for x in (price,cap,shares_k)):continue
        cap_won=cap*1e8;shares=shares_k*1000;identity=abs(price*shares/cap_won-1)
        if identity>.025:raise ValueError(f'Market cap identity failed for {code}: {identity}')
        max_identity_error=max(max_identity_error,identity)
        if code in old:
            found[code]={**old[code],'current_price':price,'current_market_cap':cap_won,'shares_proxy':shares,
                'current_volume':volume,'shares_source':'listed shares in thousands x 1000',
                'market_cap_source':'header-matched 시가총액 in hundred-million KRW','cap_identity_error':identity}
    return found,{'market':market,'page':page,'headers':headers,'rows':len(found),'cap_column_zero_based':ci,
        'volume_column_zero_based':vi,'shares_column_zero_based':si,'max_cap_identity_error':max_identity_error}

jobs=[]
for market in (0,1):
    count=sum(x['market']==('KOSPI' if market==0 else 'KOSDAQ') for x in old.values())
    jobs.extend((market,page) for page in range(1,(count+49)//50+3))
with cf.ThreadPoolExecutor(max_workers=8) as ex:
    for got,audit in ex.map(lambda x:parse_page(*x),jobs):fixed.update(got);page_audit.append(audit)
missing=set(old)-set(fixed)
# Old universe is kept fixed; no added names. Missing fields never retain the erroneous volume-as-cap.
if missing:raise ValueError(f'Unable to correct {len(missing)} original universe rows: {sorted(missing)}')
for code in sorted(fixed):
    diff.append({'code':code,'name':old[code]['name'],'old_market_cap':old[code]['current_market_cap'],
        'correct_market_cap':fixed[code]['current_market_cap'],'old_shares_proxy':old[code]['shares_proxy'],
        'correct_listed_shares':fixed[code]['shares_proxy'],'ratio':old[code]['current_market_cap']/fixed[code]['current_market_cap']})
col.dump('qc/market_header_audit.json',page_audit)
col.dump('qc/market_cap_corrections.json',diff)
col.dump('input/original_market_universe_bad_columns.json',old)
col.back.market_universe=lambda:(fixed,[])
col.chart=lambda code:(code,prices.get(code,[]),None)
themes=json.loads((base/'input/theme_snapshot.json').read_text())
codemap=json.loads((base/'input/theme_code_map.json').read_text())
col.back.theme_catalog=lambda:(themes,codemap,[])
col.main()
shutil.copy2(__file__,col.ROOT/'source/repair_v8_market_cap.py')
col.dump('qc/repair_summary.json',{'repaired':True,'source_run_id':33973297417,'source_artifact_id':9971639593,
    'original_incorrect_field':'cols[9], labelled 거래량, multiplied by 100 million',
    'corrected_field':'header-matched 시가총액, generally cols[6]',
    'shares':'header-matched 상장주식수 x 1000','corrected_rows':len(fixed),'missing_rows':len(missing),
    'all_price_histories_reused':True,'all_theme_memberships_reused':True,'strategy_thresholds_changed':False,
    'historical_outstanding_shares_still_proxy':True,'supersedes_first_run':True})
