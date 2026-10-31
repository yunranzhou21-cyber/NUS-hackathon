"""Benchmark the actual supplied dataset, not a simulated 1000-page corpus."""
import json
import math
import time
from datetime import datetime
from pathlib import Path
from search import SearchEngine, build_index

QUERIES = [
    ('EMB-120 序列号 120006 有哪些相关提案', {'make': 'Embraer'}),
    ('corrosion', {}),
    ('777', {'make': 'Boeing'}),
    ('2000-NM-120-AD', {}),
    ('zzzzUnlistedModel999', {}),
]


def summary(values):
    ordered = sorted(values)
    return {'samples': len(values), 'p50_ms': round(ordered[math.ceil(len(values)*.5)-1], 3),
            'p95_ms': round(ordered[math.ceil(len(values)*.95)-1], 3),
            'max_ms': round(max(values), 3)}


def main():
    begin = time.perf_counter()
    index = build_index()
    build_ms = (time.perf_counter()-begin)*1000
    engine = SearchEngine()
    values, samples = [], []
    try:
        for _ in range(20):
            for query, config in QUERIES:
                begin = time.perf_counter()
                result = engine.search(query, config)
                values.append((time.perf_counter()-begin)*1000)
                if len(samples) < len(QUERIES):
                    samples.append({'query': query, 'status': result['status'],
                                    'record_ids': [r['record_id'] for r in result['results']]})
    finally:
        engine.close()
    cold = []
    for query, config in QUERIES:
        begin = time.perf_counter()
        engine = SearchEngine()
        try:
            engine.search(query, config)
        finally:
            engine.close()
        cold.append((time.perf_counter()-begin)*1000)
    report = {'date': datetime.now().isoformat(timespec='seconds'),
              'corpus': index, 'build_ms': round(build_ms, 3),
              'warm_search': summary(values), 'open_and_search': summary(cold),
              'samples': samples, 'verified_1000_page_requirement': False,
              'limitations': 'Single machine, sequential runs, actual CSV only; '
                             'open-and-search is not OS cold-cache measurement. '
                             'No network, UI, generation, or semantic recall evaluation.'}
    Path('docs/search_benchmark.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=True, indent=2))


if __name__ == '__main__':
    main()
