#!/usr/bin/env python3
"""Rebuild the same research recipe from cached raw data, correcting a parser bug.
No return-dependent rule tuning, no fresh prices, no silent replacement of raw data.
"""
from pathlib import Path
import gzip,json,re,datetime as dt
from collections import defaultdict
from bs4 import BeautifulSoup
CACHE=Path('cached-v8-data')

def correct_universe(b,root):
    manifest=json.loads((CACHE/'qc/request_manifest.json').read_text())
    result={};audit=[];headers_seen=[]
    for item in manifest:
        if '/sise/sise_market_sum.naver?' not in item['url']:continue
        market='KOSDAQ' if 'sosok=1' in item['url'] else 'KOSPI'
        raw=gzip.decompress((CACHE/'raw'/item['file']).read_bytes()).decode('euc-kr',errors='replace')
        soup=BeautifulSoup(raw,'html.parser');table=soup.select_one('table.type_2')
        if table is None:continue
        headers=[re.sub(r'\s+','',h.get_text()) for h in table.select('th')]
        try:
            cap_index=next(i for i,h in enumerate(headers) if h.startswith('시가총액'))
            price_index=next(i for i,h in enumerate(headers) if h.startswith('현재가'))
        except StopIteration:raise ValueError('Cannot locate market cap header: '+repr(headers))
        headers_seen.append({'url':item['url'],'headers':headers,'market_cap_index':cap_index,'legacy_index':9})
        for tr in table.select('tr'):
            a=tr.select_one('a.tltle')
            if not a:continue
            match=re.search(r'code=(\d{6})',a.get('href',''));cols=[td.get_text(' ',strip=True) for td in tr.select('td')]
            if not match or len(cols)<=max(cap_index,price_index,9):continue
            code=match.group(1);name=a.get_text(strip=True);price=b.fnum(cols[price_index]);cap=b.fnum(cols[cap_index]);legacy=b.fnum(cols[9])
            if price is None or cap is None or price<=0 or cap<=0:continue
            result[code]={'code':code,'name':name,'market':market,'current_price':price,'current_market_cap':cap*1e8,
                'shares_proxy':cap*1e8/price,'ordinary':b.ordinary(name)}
            audit.append({'code':code,'name':name,'legacy_value_as_market_cap':legacy,'correct_market_cap_eok':cap,
                          'legacy_column_header':headers[9],'correct_column_header':headers[cap_index]})
    assert result and len(result)>1000
    (root/'qc/market_cap_parser_audit.json').write_text(json.dumps({'header_examples':headers_seen[:3],'rows':audit},ensure_ascii=False,indent=2))
    return result,[]

def cached_themes():
    themes=json.loads((CACHE/'output/themes.json').read_text());mapping=defaultdict(list)
    for key,row in themes.items():
        for code in row.get('members',[]):mapping[code].append(key)
    return themes,mapping,[]

def cached_news():
    news=json.loads(gzip.decompress((CACHE/'output/news_history.json.gz').read_bytes()))
    for items in news.values():
        for item in items:
            if isinstance(item.get('at'),str):
                try:item['at']=dt.datetime.fromisoformat(item['at'])
                except ValueError:item['at']=None
    return news,[]

source=Path('research/largo-v8-extended/collect_extended.py').read_text()
replacements={
 "ROOT=Path('v8-extended-data')":"ROOT=Path('v8-extended-corrected')",
 'universe,market_errors=b.market_universe()':'universe,market_errors=correct_universe(b,ROOT)',
 'charts,chart_errors=b.fetch_charts(universe,16)':"charts=json.loads(gzip.decompress((CACHE/'output/price_history.json.gz').read_bytes())); chart_errors=[]",
 'themes,code_themes,theme_errors=b.theme_catalog()':'themes,code_themes,theme_errors=cached_themes()',
 'news,news_errors=b.fetch_all_news(codes,oldest,16)':'news,news_errors=cached_news()',
 '    for i,row in frame.iterrows():':'    for row in frame.itertuples(index=True):\n        i=row.Index',
}
for old,new in replacements.items():
    assert old in source,old
    source=source.replace(old,new)
exec(compile(source,'collect_extended_cached_corrected.py','exec'),globals())
# Preserve dependency and correction provenance.
root=Path('v8-extended-corrected')
(root/'source/collect_extended.py').write_text(Path('research/largo-v8-extended/collect_extended.py').read_text())
meta=json.loads((root/'qc/collection_metadata.json').read_text())
meta['market_cap_parser_corrected']=True
meta['fresh_market_downloads']=False
meta['missing_cached_news_codes']=sorted(set(codes)-set(news))
meta['cache_source_run']=33973088640
meta['same_archived_prices_and_themes']=True
(root/'qc/collection_metadata.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2))
print('CORRECTION_FINISHED',len(flat),len(corrs),flush=True)
