"""Real GPU verification at unchanged fast512/10000/remesh256/texture256."""
import argparse
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dart3s.comfy3d import generate_3d, ComfyError

p = argparse.ArgumentParser()
p.add_argument('--case', choices=['control','slow','count'], required=True)
p.add_argument('--shape-probe', action='store_true', help='Exercise stop/retry with a deliberately strict 10s shape threshold')
args = p.parse_args()
case_name = args.case + ('-shape-probe' if args.shape_probe else '')
sources = {'control': 'b0cb82746fb24bd7a3b22fd015f0ea96.png',
           'slow': '45d92b79151f4f01b144b7f775528f3d.png',
           'count': '54abc992d89b491bb77a58d492b0c913.png'}
target = ROOT / 'logs/adaptive-verification'
target.mkdir(exist_ok=True)
started = time.monotonic()
events = []
def progress(e):
    if e.get('status') != 'generating':
        events.append({'seconds': time.monotonic()-started, **e})
        print(json.dumps(e, ensure_ascii=True), flush=True)
try:
    result = generate_3d(ROOT / '.state/gui/inputs' / sources[args.case], model='trellis2', quality='fast',
        polygons=10000, remesh_resolution=256, texture_size=256, structure_seed=56, shape_seed=42,
        texture_seed=42, max_structure_elements=4096, structure_retries=1, early_risk_seconds=180,
        output_dir=ROOT / 'outputs/adaptive-verification' / case_name, progress=progress,
        _early_limits={'shape_two_steps_seconds':10} if args.shape_probe else None)
    report = {'case':case_name, 'status':'completed', 'result':result.to_dict()}
except ComfyError as exc:
    report = {'case':case_name, 'status':exc.code, 'error':exc.to_dict()}
report.update(wall_seconds=time.monotonic()-started, events=events)
(target / (case_name+'.json')).write_text(json.dumps(report, indent=2), encoding='utf-8')
print(json.dumps({k:v for k,v in report.items() if k!='events'}), flush=True)
