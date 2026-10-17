"""Retrospective check only: reads existing images/history, never generates."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dart3s.image_risk import analyze_image

index = json.loads((ROOT / '.state/gui/risk-history.json').read_text(encoding='utf-8'))
examples = json.loads((ROOT / 'logs/art-input-comparison.json').read_text(encoding='utf-8'))['rows']
tree = json.loads((ROOT / 'outputs/20261016T092043Z_7450e7d2/generation.json').read_text(encoding='utf-8'))
examples.append(dict(name='Дерево', input=tree['image'], parameters=tree['parameters']))
rows = []
for example in examples:
    result = analyze_image(example['input'], history_index=index, parameters=example['parameters'])
    rows.append(dict(name=example['name'], result=result,
                     without_history=analyze_image(example['input'])['level']))
tree_result = next(row for row in rows if row['name'] == 'Дерево')
assert tree_result['result']['level'] == 'high'
assert tree_result['without_history'] != 'low'
assert all(row['result']['basis'] == 'measured_history' for row in rows)
report = dict(description='Retrospective reproduction, not validation on unseen images. No generation.', rows=rows)
(ROOT / 'logs/risk-correction-known-runs.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps([dict(name=row['name'], level=row['result']['level'], without_history=row['without_history'],
                       seconds=row['result']['history']['median_seconds']) for row in rows], ensure_ascii=False, indent=2))
