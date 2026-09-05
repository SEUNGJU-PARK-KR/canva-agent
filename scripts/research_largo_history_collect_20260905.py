#!/usr/bin/env python3
"""Read-only collection for a historical audit. Never publishes signals or orders."""
from __future__ import annotations
import concurrent.futures as cf
import datetime as dt
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import sys
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(1, str(ROOT / 'research/largo-target3-20day/source'))
import backfill_largo_target3_20day as b

OUT = ROOT / 'output/largo-history-audit-20260905'
RAW = OUT / 'raw'
RAW.mkdir(parents=True, exist_ok=True)
lock = threading.Lock()
receipts = []
original_get = b.get

def get_recorded(url, *, timeout=20, retries=2):
    response = original_get(url, timeout=min(timeout, 20), retries=min(retries, 2))
    payload = response.content
    key = hashlib.sha256(url.encode()).hexdigest()
    with gzip.open(RAW / (key + '.gz'), 'wb') as stream:
        stream.write(payload)
    item = {'url': url, 'sha256': hashlib.sha256(payload).hexdigest(), 'bytes': len(payload), 'path': 'raw/' + key + '.gz', 'retrieved_at': dt.datetime.now(dt.timezone.utc).isoformat(), 'status': response.status_code}
    with lock:
        receipts.append(item)
    return response

b.get = get_recorded

def dump_gzip(name, value):
    with gzip.open(OUT / name, 'wt', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, separators=(',', ':'))

def main():
    started = dt.datetime.now(dt.timezone.utc).isoformat()
    universe, market_errors = b.market_universe()
    print('UNIVERSE', len(universe), flush=True)
    dump_gzip('universe.json.gz', universe)
    codes = sorted(code for code, row in universe.items() if row.get('ordinary'))
    charts, chart_errors = {}, []
    with cf.ThreadPoolExecutor(max_workers=16) as executor:
        futures = {executor.submit(b.chart_rows, code, 2000): code for code in codes}
        for i, future in enumerate(cf.as_completed(futures), 1):
            code = futures[future]
            try:
                rows = future.result()
                if rows:
                    charts[code] = rows
                else:
                    chart_errors.append({'code': code, 'error': 'EMPTY_CHART'})
            except Exception as exc:
                chart_errors.append({'code': code, 'error': str(exc)})
            if i % 250 == 0:
                print('CHARTS', i, len(codes), len(charts), len(chart_errors), flush=True)
    dump_gzip('charts.json.gz', charts)
    print('CHARTS_SAVED', sum(map(len, charts.values())), flush=True)
    themes, code_themes, theme_errors = b.theme_catalog()
    dump_gzip('themes.json.gz', themes)
    dump_gzip('code_themes.json.gz', code_themes)
    print('THEMES', len(themes), flush=True)
    other_errors = []
    for path in ['data/material-0906-history.json', 'data/largo-close-v6-shadow-history.json', 'data/largo-close-v6-shadow-summary.json']:
        try:
            url = 'https://raw.githubusercontent.com/SEUNGJU-PARK-KR/canva-agent/gh-pages/' + path
            response = get_recorded(url)
            value = response.json()
            dump_gzip(Path(path).name + '.gz', value)
        except Exception as exc:
            other_errors.append({'path': path, 'error': str(exc)})
    source = OUT / 'source'
    source.mkdir(exist_ok=True)
    for name in ['scripts/largo_close_v6_shadow.py', 'scripts/capture_largo_v6_shadow.py', 'scripts/largo_material_0906.py', 'scripts/validate_largo_close_v6_shadow.py', 'scripts/research_largo_history_collect_20260905.py', 'research/largo-target3-20day/source/backfill_largo_target3_20day.py', 'research/largo-close-v6-shadow/README.md']:
        target = source / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, target)
    receipt_path = OUT / 'source_receipts.json'
    receipt_path.write_text(json.dumps(receipts, ensure_ascii=False, indent=2), encoding='utf-8')
    metadata = {'started_at': started, 'finished_at': dt.datetime.now(dt.timezone.utc).isoformat(), 'base_commit': '39e3f3eb77774e263a70cf89f63e39f42895bea4', 'requested_chart_count': 2000, 'intended_signal_start': '2020-01-02', 'intended_last_market_date': '2026-09-04', 'universe_count': len(universe), 'ordinary_count': len(codes), 'chart_success': len(charts), 'chart_rows': sum(map(len, charts.values())), 'earliest_chart_date': min((rows[0]['date'] for rows in charts.values()), default=None), 'latest_chart_date': max((rows[-1]['date'] for rows in charts.values()), default=None), 'theme_count': len(themes), 'errors': {'market': market_errors, 'chart': chart_errors, 'theme': theme_errors, 'other': other_errors}, 'limitations': ['Current-listed universe; delisted securities not recovered', 'Current theme membership and current share counts are proxies', 'Daily OHLCV does not establish 15:18/09:05 executable quotes', 'No order, account, main-branch or Pages modification']}
    (OUT / 'collection_metadata.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(metadata, ensure_ascii=False), flush=True)
    assert len(universe) >= 1000, 'INCOMPLETE_UNIVERSE'
    assert len(charts) >= 0.9 * len(codes), 'INCOMPLETE_CHART_COVERAGE'
    assert len(themes) >= 100, 'INCOMPLETE_THEME_CATALOG'

if __name__ == '__main__':
    main()
