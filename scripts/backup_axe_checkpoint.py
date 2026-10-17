"""Preserve the successful axe workflow and the application before tuning it."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
AXE = ROOT / 'outputs/trellis2-test/20261014T091135Z_d81eb7e3'
stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
destination = ROOT / 'backups' / f'axe-working-{stamp}.zip'
destination.parent.mkdir(exist_ok=True)
files = set()
for folder in ('dart3s', 'scripts', 'tests', 'docs', 'workflows', 'blueprints', 'fragments',
               'ComfyUI_windows_portable/ComfyUI/comfy',
               'ComfyUI_windows_portable/ComfyUI/comfy_extras',
               'ComfyUI_windows_portable/ComfyUI/comfy_api',
               'ComfyUI_windows_portable/ComfyUI/custom_nodes/dart3s_progress'):
    files.update(p for p in (ROOT/folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts)
files.update(p for p in ROOT.iterdir() if p.is_file())
files.update(p for p in AXE.rglob('*') if p.is_file())
files.update(p for p in (ROOT/'ComfyUI_windows_portable/ComfyUI').glob('*.py'))
for name in ('downloads/template.original.json', 'assets/viking_wolf_rune_axe.png',
             '.state/gui/settings.json', 'logs/models.progress.json', 'logs/trellis2-glb-verification.json',
             'logs/object_info-progress.json', 'logs/amd-wheels.download.log',
             'logs/comfyui-before-progress-install.stderr.log'):
    path = ROOT/name
    if path.is_file():
        files.add(path)
entries = []
with zipfile.ZipFile(destination, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=3) as archive:
    for path in sorted(files):
        relative = path.relative_to(ROOT).as_posix()
        content = path.read_bytes()
        archive.writestr(relative, content)
        entries.append(dict(path=relative, bytes=len(content), sha256=hashlib.sha256(content).hexdigest()))
    manifest = dict(created_at=datetime.now(timezone.utc).isoformat(),
        successful_prompt='688c5b1e-6a48-44dc-a1a1-6238a5d545fc',
        axe_run=AXE.relative_to(ROOT).as_posix(), files=entries,
        model_files=[dict(path=p.relative_to(ROOT).as_posix(), bytes=p.stat().st_size)
                     for p in (ROOT/'models').rglob('*') if p.is_file()],
        note='Exact successful axe artifacts/workflow plus application code before speed tuning. '
             'Model weights and Python runtimes remain installed; they are not duplicated in this archive. '
             'The current GUI includes later progress improvements; historical axe-time GUI code was not versioned.')
    archive.writestr('CHECKPOINT.json', json.dumps(manifest, ensure_ascii=False, indent=2))
with zipfile.ZipFile(destination) as archive:
    for entry in entries:
        if hashlib.sha256(archive.read(entry['path'])).hexdigest() != entry['sha256']:
            raise RuntimeError('Backup verification failed: '+entry['path'])
report = dict(ok=True, archive=str(destination), files=len(entries), bytes=destination.stat().st_size,
              sha256=hashlib.sha256(destination.read_bytes()).hexdigest(), verified=True)
(ROOT/'logs/axe-backup-verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(report, ensure_ascii=False))
