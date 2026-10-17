"""Isolated two-seed diagnostic: original graph plus read-only mesh exports.

Each invocation runs exactly one seed from a fresh server. Production settings,
workflow and GUI state are never edited. No automatic seed retries.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
from urllib.request import ProxyHandler, build_opener
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dart3s.comfy3d import _Session, server_ready
from comfy_cli.workflow_to_api import convert_ui_to_api

URL = 'http://127.0.0.1:8188'
SOURCE = ROOT / 'outputs/20261016T165926Z_b5ba9197/workflow.json'
IMAGE = ROOT / '.state/gui/inputs/b3d6d2ae357a49ab900abbaded7e9038.png'
STAGES = {'raw': '92', 'remesh': '241', 'decimate': '186'}


def api(route):
    with build_opener(ProxyHandler({})).open(URL + route, timeout=20) as response:
        return json.load(response)


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seed', type=int, choices=[42, 1024], required=True)
    args = parser.parse_args()
    if server_ready(URL):
        raise RuntimeError('Expected stopped server for controlled cold-start comparison; preserving existing server')
    report_dir = ROOT / 'logs/seed-remesh-comparison-20261016'
    report_dir.mkdir(exist_ok=True)
    checkpoint = report_dir / f'seed-{args.seed}.json'
    if checkpoint.exists():
        raise RuntimeError('Existing checkpoint; refusing duplicate generation')
    started = time.monotonic()
    report = dict(status='starting', seed=args.seed, source=str(SOURCE), image=str(IMAGE),
                  image_sha256=hashlib.sha256(IMAGE.read_bytes()).hexdigest(),
                  started_at=datetime.now(timezone.utc).isoformat(), events=[])
    dump(checkpoint, report)

    def progress(event):
        report['events'].append({'seconds': round(time.monotonic()-started, 3), **event})
        dump(checkpoint, report)
        if event.get('status') != 'generating' or (event.get('event') or {}).get('type') in ('executing', 'progress'):
            print(json.dumps(event, ensure_ascii=True), flush=True)

    session = _Session(project=ROOT, output_dir=ROOT/'outputs/seed-remesh-comparison'/f'seed-{args.seed}',
                       local_url=URL, progress=progress)
    report['run_dir'] = str(session.run_dir)
    session.env['DART3S_TIMINGS_FILE'] = str(report_dir/f'seed-{args.seed}.timings.jsonl')
    try:
        session.ensure_server(auto_start=True, startup_timeout=180)
        report['server'] = session.metadata['server']
        if api('/queue').get('queue_running') or api('/queue').get('queue_pending'):
            raise RuntimeError('Queue occupied; no test submitted')
        catalog = api('/object_info')
        catalog_path = session.run_dir/'object_info.json'
        dump(catalog_path, catalog)
        original = json.loads(SOURCE.read_text(encoding='utf-8'))
        baseline = session.run_dir/'baseline.json'
        shutil.copy2(SOURCE, baseline)
        target = session.run_dir/'workflow.json'
        shutil.copy2(SOURCE, target)
        shutil.copy2(IMAGE, session.run_dir/'source.png')
        upload = session.command('upload', ['upload', str(IMAGE), '--where', 'local', '--no-overwrite'])
        uploaded = upload['uploads'][0]
        name = '/'.join(p for p in [uploaded.get('subfolder', ''), uploaded['cloud_name']] if p)
        nodes = {str(n['id']): n for n in original['nodes']}
        seed_link = next(i['link'] for i in nodes['18']['inputs'] if i['name']=='seed')
        shared_seed = str(next(l[1] for l in original['links'] if l[0]==seed_link))
        ops = [dict(op='set_widget', node=shared_seed, widget='value', value=args.seed),
               dict(op='set_widget', node='12', widget='seed', value=args.seed),
               dict(op='set_widget', node='122', widget='image', value=name),
               dict(op='set_widget', node='322', widget='filename_prefix',
                    value=f'3d/seed-remesh-comparison/seed-{args.seed}/final')]
        allowed = {shared_seed, '12', '122', '322'}
        for nid, node in nodes.items():
            if node['type']=='Dart3sEarlyStage':
                ops.append(dict(op='set_widget', node=nid, widget='directory', value=str(session.run_dir)))
                allowed.add(nid)
            elif node['type']=='Dart3sStructureBudget':
                ops.append(dict(op='set_widget', node=nid, widget='report_path', value=str(session.run_dir/'structure-selection.json')))
                allowed.add(nid)
        for stage, producer in STAGES.items():
            file_alias, save_alias = f'{stage}_file', f'{stage}_save'
            ops.extend([
                dict(op='add_node', class_type='MeshToFile3D', **{'as':file_alias}),
                dict(op='add_node', class_type='Save3DAdvanced', **{'as':save_alias}),
                dict(op='connect', **{'from':f'{producer}.mesh', 'to':f'{file_alias}.mesh'}),
                dict(op='connect', **{'from':f'{file_alias}.model_3d', 'to':f'{save_alias}.model_3d'}),
                dict(op='set_widget', node=save_alias, widget='filename_prefix',
                     value=f'3d/seed-remesh-comparison/seed-{args.seed}/{stage}'),
                dict(op='set_widget', node=save_alias, widget='viewport_state', value=''),
                dict(op='set_widget', node=save_alias, widget='width', value=256),
                dict(op='set_widget', node=save_alias, widget='height', value=256)])
        recipe = session.run_dir/'stage-exports.recipe.json'
        dump(recipe, dict(recipe='controlled-seed-mesh-stage-comparison', ops=ops))
        applied = session.command('apply', ['workflow', 'apply', str(target), '--ops', str(recipe),
                                           '--input', str(catalog_path), '--ack', 'summary'])
        graph = json.loads(target.read_text(encoding='utf-8'))
        before, after = convert_ui_to_api(original,catalog), convert_ui_to_api(graph,catalog)
        changed = [n for n in before if before[n]!=after.get(n)]
        if set(changed)-allowed:
            raise RuntimeError(f'Unexpected original computation changes: {set(changed)-allowed}')
        report.update(changed_original_nodes=changed, shared_shape_seed_node=shared_seed,
                      save_nodes={**{s:applied['aliases'][f'{s}_save'] for s in STAGES}, 'final':322},
                      workflow=str(target), original_graph_preserved_except_seeds_and_output_paths=True,
                      status='submitting')
        dump(checkpoint, report)
        submitted = session.command('submit', ['run', '--workflow', str(target), '--where', 'local', '--no-watch', '--no-notify'])
        report['prompt_id'] = submitted['prompt_id']
        dump(checkpoint, report)
        result = session.collect(submitted['prompt_id'], 1800)
        history = json.loads((session.run_dir/'history.json').read_text(encoding='utf-8'))[submitted['prompt_id']]
        files = json.loads((session.run_dir/'download.stdout.log').read_text(encoding='utf-8'))['data']['files']
        stage_paths = {}
        for stage, save_node in report['save_nodes'].items():
            entries = history['outputs'][str(save_node)]['3d']
            if len(entries)!=1:
                raise RuntimeError('Expected one GLB per stage')
            entry = entries[0]
            matches = [f for f in files if parse_qs(urlsplit(f['url']).query).get('filename')==[entry['filename']]
                       and parse_qs(urlsplit(f['url']).query).get('subfolder')==[entry['subfolder']]]
            if len(matches)!=1:
                raise RuntimeError(f'Ambiguous download mapping for {stage}')
            path = Path(matches[0]['path'])
            if not path.is_file():
                raise RuntimeError(f'Missing downloaded stage: {path}')
            stage_paths[stage] = str(path)
        report.update(status='completed', result=result.to_dict(), stages=stage_paths)
    except Exception as exc:
        report.update(status='failed', error=str(exc))
        session.fail(exc)
        raise
    finally:
        # Stop only this project server, and only after checking for unrelated work.
        if session.metadata.get('server',{}).get('started_by_adapter') and server_ready(URL):
            queue = api('/queue')
            jobs = (queue.get('queue_running') or [])+(queue.get('queue_pending') or [])
            foreign = [j[1] for j in jobs if j[1]!=report.get('prompt_id')]
            if foreign:
                report['shutdown'] = {'status':'preserved_foreign_work', 'prompt_ids':foreign}
            else:
                stopped = subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',
                    str(ROOT/'scripts/Stop-ComfyUI.ps1')], capture_output=True, text=True, encoding='utf-8', timeout=45)
                report['shutdown'] = dict(returncode=stopped.returncode, stdout=stopped.stdout, stderr=stopped.stderr)
        report['wall_seconds'] = round(time.monotonic()-started,3)
        report['finished_at'] = datetime.now(timezone.utc).isoformat()
        dump(checkpoint, report)
    print(json.dumps({k:v for k,v in report.items() if k!='events'}, ensure_ascii=True), flush=True)


if __name__=='__main__':
    main()
