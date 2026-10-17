"""Real fast-512 control; writes timings and exact structure attempts."""
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dart3s.comfy3d import generate_3d

reject_probe = '--reject-probe' in sys.argv
out = ROOT / 'outputs/structure-budget-verification' / ('reject-probe' if reject_probe else 'control')
started = time.monotonic()
try:
    result = generate_3d(ROOT / '.state/gui/inputs/861ee6ef6a804731adc3f0f38a6ff9f5.png',
        model='trellis2', quality='fast', polygons=10000, remesh_resolution=256,
        texture_size=256, structure_seed=56, shape_seed=42, texture_seed=42,
        max_structure_elements=1 if reject_probe else 4096, structure_retries=1 if reject_probe else 2, output_dir=out,
        progress=lambda e: print(json.dumps(e, ensure_ascii=True), flush=True) if e.get('status') != 'generating' else None)
    report = dict(ok=True, seconds=time.monotonic()-started, result=result.to_dict())
except Exception as exc:
    report = dict(ok=False, seconds=time.monotonic()-started, error=str(exc))
(ROOT / ('logs/structure-budget-verification/' + ('reject-probe' if reject_probe else 'control') + '.json')).write_text(json.dumps(report, indent=2), encoding='utf-8')
print(json.dumps(report), flush=True)
