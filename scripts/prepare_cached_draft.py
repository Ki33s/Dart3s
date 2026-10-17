"""Prepare a draft export from the completed base-shape stage, using CLI ops.

Preserves all original sampler IDs and inputs so the running server can reuse
its existing cache. It does not submit or restart anything.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import argparse
import re
from comfy_cli.workflow_to_api import convert_ui_to_api

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--source', type=Path, default=ROOT/'outputs/20261014T133817Z_7e2bb4e8/workflow.json')
parser.add_argument('--name', default='pyramid')
args = parser.parse_args()
if not re.fullmatch('[a-z0-9-]+', args.name):
    raise ValueError('Draft name must be a simple lowercase name')
SOURCE = args.source.resolve()
DIRECTORY = ROOT / 'outputs' / (args.name+'-draft')
CATALOG = ROOT / 'logs/object_info-progress.json'
DIRECTORY.mkdir(parents=True, exist_ok=True)
target = DIRECTORY / 'workflow.json'
shutil.copy2(SOURCE, target)
ops = [
    {'op': 'connect', 'from': '18.LATENT', 'to': '92.samples'},
    {'op': 'connect', 'from': '92.mesh', 'to': '285.mesh'},
    {'op': 'set_widget', 'node': '322', 'widget': 'filename_prefix',
     'value': '3d/'+args.name+'-base-draft'},
]
ops_path = DIRECTORY / 'draft-export.recipe.json'
ops_path.write_text(json.dumps({'recipe': 'export-cached-base-shape', 'ops': ops}, indent=2), encoding='utf-8')
env = {**os.environ, 'COMFY_NO_TELEMETRY': '1', 'COMFY_WHERE': 'local',
       'COMFY_LOCAL_URL': 'http://127.0.0.1:8188', 'LOCALAPPDATA': str(ROOT / '.state'),
       'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8'}
result = subprocess.run([str(ROOT / '.venv-cli/Scripts/comfy.exe'), '--json', 'workflow',
    'apply', str(target), '--ops', str(ops_path), '--input', str(CATALOG),
    '--ack', 'summary'], cwd=ROOT, env=env, capture_output=True, text=True, encoding='utf-8',
    creationflags=subprocess.CREATE_NO_WINDOW, timeout=60)
(DIRECTORY / 'apply.stdout.json').write_text(result.stdout, encoding='utf-8')
(DIRECTORY / 'apply.stderr.log').write_text(result.stderr, encoding='utf-8')
value = json.loads(result.stdout)
if result.returncode or not value.get('ok'):
    raise RuntimeError(value.get('error') or result.stderr)
graph = json.loads(target.read_text(encoding='utf-8'))
parents = {}
for link in graph['links']:
    parents.setdefault(link[3], []).append(link[1])
needed, pending = set(), [322]
while pending:
    node = pending.pop()
    if node not in needed:
        needed.add(node)
        pending.extend(parents.get(node, []))
disable_ops = [{'op': 'set_node_field', 'node': str(n['id']), 'field': 'mode', 'value': 2}
               for n in graph['nodes'] if n['id'] not in needed and n.get('mode', 0) != 2]
disable_path = DIRECTORY / 'disable-unneeded.ops.json'
disable_path.write_text(json.dumps(disable_ops, indent=2), encoding='utf-8')
if disable_ops:
    disabled = subprocess.run([str(ROOT / '.venv-cli/Scripts/comfy.exe'), '--json', 'workflow',
        'apply', str(target), '--ops', str(disable_path), '--input', str(CATALOG),
        '--ack', 'summary'], cwd=ROOT, env=env, capture_output=True, text=True, encoding='utf-8',
        creationflags=subprocess.CREATE_NO_WINDOW, timeout=60)
    (DIRECTORY / 'disable.stdout.json').write_text(disabled.stdout, encoding='utf-8')
    checked = json.loads(disabled.stdout)
    if disabled.returncode or not checked.get('ok'):
        raise RuntimeError(checked.get('error') or disabled.stderr)
graph = json.loads(target.read_text(encoding='utf-8'))
catalog = json.loads(CATALOG.read_text(encoding='utf-8'))
compiled = convert_ui_to_api(graph, catalog)
api_path = DIRECTORY / 'workflow.compiled.json'
api_path.write_text(json.dumps(compiled, indent=2), encoding='utf-8')
original = convert_ui_to_api(json.loads(SOURCE.read_text(encoding='utf-8')), catalog)
upstream = needed - {92, 285, 322}
assert all(compiled[str(n)] == original[str(n)] for n in upstream if str(n) in compiled), 'Cached input changed'
assert '23' not in compiled and '12' not in compiled and '94' not in compiled
print(json.dumps({'ok': True, 'workflow': str(target), 'compiled': str(api_path),
                  'recipe': str(ops_path), 'cached_upstream_unchanged': True, 'nodes': len(compiled)}, indent=2))
