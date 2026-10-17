"""Wait for scoped cancellation, then export cached base shape via Comfy CLI.

Aborts if the server tries to recompute a sampler instead of reusing cache.
"""
import json
from pathlib import Path
import sys
import time
import argparse
from urllib.request import Request, ProxyHandler, build_opener

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dart3s.cli_stream import run_stream
from dart3s.comfy3d import local_environment, parse_envelope, resume_3d

parser = argparse.ArgumentParser()
parser.add_argument('--directory', type=Path, default=ROOT/'outputs/pyramid-draft')
parser.add_argument('--source-prompt', default='1502439c-0ec8-4fcc-aa9c-5b01341c8c1c')
args = parser.parse_args()
DIRECTORY = args.directory.resolve()
if not DIRECTORY.is_relative_to((ROOT/'outputs').resolve()):
    raise ValueError('Export directory must be inside project outputs')
URL = 'http://127.0.0.1:8188'
OLD_ID = args.source_prompt
opener = build_opener(ProxyHandler({}))
report = {'source_prompt_id': OLD_ID, 'status': 'waiting_for_cancellation',
          'draft_stage': 'completed base shape 512, no textures', 'cached_nodes': []}

def save():
    (DIRECTORY / 'extraction.json').write_text(json.dumps(report, indent=2), encoding='utf-8')

def api(route, body=None):
    data = json.dumps(body).encode() if body is not None else None
    request = Request(URL+route, data=data, headers={'Content-Type': 'application/json'})
    with opener.open(request, timeout=10) as response:
        return json.loads(response.read() or '{}')

save()
deadline = time.monotonic()+1200
while True:
    state = api('/queue')
    active = [item[1] for item in state['queue_running']+state['queue_pending']]
    if OLD_ID not in active:
        if active:
            raise RuntimeError('Another job is active; preserving its work')
        break
    if time.monotonic() > deadline:
        raise TimeoutError('Cancellation is still pending; server was not terminated')
    time.sleep(20)
report['source_history'] = api('/history/'+OLD_ID)
report['status'] = 'exporting'
save()
print('Original job stopped; submitting guarded cached-base export', flush=True)

def event(value):
    node = str(value.get('node', ''))
    report['prompt_id'] = value.get('prompt_id', report.get('prompt_id'))
    if value.get('type') == 'execution_cached':
        report['cached_nodes'].append(node)
    if value.get('type') == 'executing':
        print('Executing '+node, flush=True)
        report['current_node'] = node
        if node in {'3', '18'} or (node == '92' and '18' not in report['cached_nodes']):
            if not report.get('cache_missing'):
                report['cache_missing'] = True
                api('/interrupt', {'prompt_id': report['prompt_id']})
                print('Cache missing: requested scoped cancellation, no regeneration authorized', flush=True)
    save()

command = [str(ROOT / '.venv-cli/Scripts/comfy.exe'), '--json-stream', 'run',
           '--workflow', str(DIRECTORY/'workflow.compiled.json'), '--where', 'local',
           '--wait', '--timeout', '1800', '--no-notify']
result = run_stream(command, cwd=ROOT, env=local_environment(ROOT, URL), timeout=2400,
                    stdout_path=DIRECTORY/'export.stdout.log', stderr_path=DIRECTORY/'export.stderr.log',
                    on_event=event)
envelope = parse_envelope(result.stdout)
report['envelope'] = envelope
if report.get('cache_missing') or result.returncode or not envelope.get('ok'):
    report['status'] = 'failed'
    save()
    raise RuntimeError(envelope.get('error') or 'Cached export failed')
prompt_id = report.get('prompt_id') or envelope['data']['prompt_id']
downloaded = resume_3d(prompt_id, project=ROOT, output_dir=DIRECTORY/'download', wait_timeout=60)
report.update(status='completed', result=downloaded.to_dict(), cached_base_verified=True)
save()
print(json.dumps(report['result'], indent=2), flush=True)
