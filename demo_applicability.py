"""Run reproducible proposed-scope examples from the supplied CSV."""
import json
from pathlib import Path
from applicability import evaluate, load_records, parse_record


def main():
    records = {r['record_id']: r for r in load_records()}
    cases = [
        ('serial_inside_range', 'row-0383', {'model': 'EMB-120', 'serial_number': '120006'}, 'MATCH'),
        ('serial_gap', 'row-0383', {'model': 'EMB-120', 'serial_number': '120005'}, 'NO_MATCH'),
        ('missing_serial', 'row-0383', {'model': 'EMB-120'}, 'UNKNOWN'),
        ('line_upper_boundary', 'row-0384', {'model': '757-200', 'line_number': '95'}, 'MATCH'),
        ('complex_equipment_condition', 'row-0420', {'model': '777-200'}, 'UNKNOWN'),
        ('older_observed_record', 'row-0431', {'model': 'EMB-120'}, 'UNKNOWN'),
    ]
    outputs = []
    for name, rid, config, expected in cases:
        item = parse_record(records[rid])
        result = evaluate(item, {'make': item['make'], **config})
        if result['scope_decision'] != expected:
            raise ValueError(f'Demo failed: {name}')
        outputs.append({'case': name, 'result': result})
        print(f"{name}: {result['scope_decision']} / {result['status']}")
    target = Path('data/rules/demo_results.json')
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(outputs, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'Saved {len(outputs)} verified demo cases: {target}')


if __name__ == '__main__':
    main()
