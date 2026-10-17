"""Live V3 registration/execution test using an existing GLB; no inference."""
import json
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import time
from urllib.request import ProxyHandler, build_opener

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--resume')
args = parser.parse_args()
directory = ROOT / 'outputs/gui-integration-test/live-observer'
directory.mkdir(parents=True, exist_ok=True)
input_dir = ROOT / 'ComfyUI_windows_portable/ComfyUI/input/3d'
input_dir.mkdir(parents=True, exist_ok=True)
source = ROOT / 'outputs/trellis2-test/20261014T091135Z_d81eb7e3/688c5b1e_000.glb'
shutil.copy2(source, input_dir/'TEST-progress-axe.glb')
workflow = directory/'workflow.json'
workflow.write_text(json.dumps(dict(nodes=[], links=[], last_node_id=0, last_link_id=0)), encoding='utf-8')
ops = [
    {'op':'add_node','class_type':'Load3DAdvanced','as':'load'},
    {'op':'set_widget','node':'load','widget':'model_file','value':'3d/TEST-progress-axe.glb'},
    {'op':'add_node','class_type':'Get3DComponents','as':'mesh'},
    {'op':'connect','from':'load.model_3d','to':'mesh.model_3d'},
    {'op':'add_node','class_type':'Dart3sProgressMesh','as':'view'},
    {'op':'set_widget','node':'view','widget':'stage','value':'prepared'},
    {'op':'set_widget','node':'view','widget':'preview_dir','value':str(directory/'previews')},
    {'op':'set_widget','node':'view','widget':'preview_size','value':128},
    {'op':'connect','from':'mesh.mesh','to':'view.mesh'},
    {'op':'add_node','class_type':'MeshToFile3D','as':'file'},
    {'op':'connect','from':'view.mesh','to':'file.mesh'},
    {'op':'add_node','class_type':'Save3DAdvanced','as':'save'},
    {'op':'set_widget','node':'save','widget':'filename_prefix','value':'3d/TEST-progress-observer'},
    {'op':'connect','from':'file.model_3d','to':'save.model_3d'},
]
recipe = directory/'recipe.json'
recipe.write_text(json.dumps({'recipe':'test-existing-mesh-observer','ops':ops}), encoding='utf-8')
env={**os.environ,'PYTHONUTF8':'1','PYTHONIOENCODING':'utf-8','COMFY_WHERE':'local',
     'COMFY_LOCAL_URL':'http://127.0.0.1:8188','COMFY_NO_TELEMETRY':'1','LOCALAPPDATA':str(ROOT/'.state')}
cli=ROOT/'.venv-cli/Scripts/comfy.exe'
def command(name, args, timeout=60):
    result=subprocess.run([str(cli),'--json',*args],cwd=ROOT,env=env,capture_output=True,
        encoding='utf-8',timeout=timeout,creationflags=subprocess.CREATE_NO_WINDOW)
    (directory/(name+'.stdout.json')).write_text(result.stdout,encoding='utf-8')
    (directory/(name+'.stderr.log')).write_text(result.stderr,encoding='utf-8')
    value=json.loads(result.stdout)
    assert result.returncode==0 and value.get('ok'), value.get('error') or result.stderr
    return value
if args.resume:
    prompt_id = args.resume
else:
    command('apply',['workflow','apply',str(workflow),'--ops',str(recipe),'--input',str(ROOT/'logs/object_info-progress.json')])
    submitted=command('submit',['run','--workflow',str(workflow),'--where','local','--no-watch','--no-notify'])
    prompt_id=submitted['data']['prompt_id']
command('watch',['jobs','watch',prompt_id,'--where','local','--timeout','60'],timeout=90)
deadline=time.monotonic()+20
manifest=directory/'previews/latest.json'
while not manifest.exists() and time.monotonic()<deadline:
    time.sleep(.2)
assert manifest.exists(), 'Live node did not publish a preview'
record=json.loads(manifest.read_text(encoding='utf-8'))
assert record['stage']=='prepared' and record['size']==128 and record['triangles']==9982
assert Path(record['image']).is_file()
with build_opener(ProxyHandler({})).open('http://127.0.0.1:8188/history/'+prompt_id, timeout=5) as response:
    history=json.loads(response.read())
assert history[prompt_id]['status']['completed'] is True
report=dict(ok=True, fixture='Previously generated real GLB; no sampler/VAE or new model generation',
            prompt_id=prompt_id,preview=record)
(ROOT/'logs/live-progress-verification.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps(report,indent=2))
