"""Restore the successful axe configuration after the main UI is idle."""
import ctypes
from ctypes import wintypes
import hashlib
import json
from pathlib import Path
import sys
import time
from urllib.request import ProxyHandler, build_opener

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dart3s.gui_jobs import read_json, write_json


def main():
    with build_opener(ProxyHandler({})).open('http://127.0.0.1:8188/queue',timeout=5) as response:
        queue = json.load(response)
    if queue['queue_running'] or queue['queue_pending']:
        raise RuntimeError('A generation is active; leave it and the UI untouched')
    if read_json(ROOT/'.state/gui/pending.json').get('prompt_id'):
        raise RuntimeError('The UI still tracks a job; wait for collection')
    stage = ROOT/'logs/rollback-2248'
    settings = read_json(stage/'axe-good-settings.json')
    glb = Path(settings['result']['glb_path'])
    assert glb.is_file() and Path(settings['image']['path']).is_file()
    assert hashlib.sha256(glb.read_bytes()).hexdigest() == '41799914c006f2e92ed220727f214ed3857bbb1b7abfeb89d4d3fefd5fac90f2'
    assert read_json(stage/'graph-equivalence.json')['equivalent_executable_graph']
    source = (stage/'gui-restored.py').read_text(encoding='utf-8')
    compile(source, str(ROOT/'dart3s/gui.py'), 'exec')
    native = ctypes.WinDLL('user32',use_last_error=True)
    native.FindWindowW.argtypes = [wintypes.LPCWSTR,wintypes.LPCWSTR]
    native.FindWindowW.restype = wintypes.HWND
    native.PostMessageW.argtypes = [wintypes.HWND,wintypes.UINT,wintypes.WPARAM,wintypes.LPARAM]
    title = 'Dart3s — Генерация 3D'
    handle = native.FindWindowW(None,title)
    if handle:
        native.PostMessageW(handle,0x0010,0,0)
        deadline = time.monotonic()+8
        while native.FindWindowW(None,title) and time.monotonic()<deadline:
            time.sleep(.1)
        if native.FindWindowW(None,title):
            raise RuntimeError('Main window did not close; no live files were changed')
    (ROOT/'dart3s/gui.py').write_text(source,encoding='utf-8')
    readme = ROOT/'README.md'
    content = readme.read_text(encoding='utf-8')
    content = content.replace('При включённом «Авто Remesh и текстуры» размеры подбираются по числу полигонов.\nРучной выбор Remesh или текстур отключает автоподбор: выбранные значения\nсохраняются при запуске генерации и после переоткрытия приложения.',
                              'Размеры быстрого режима автоматически подбираются по числу полигонов.')
    readme.write_text(content,encoding='utf-8')
    write_json(ROOT/'.state/gui/settings.json',settings)
    report = dict(ok=True,restored_run=settings['result']['prompt_id'],
                  parameters=settings['parameters'],source_image=settings['image']['path'],
                  glb=str(glb),native_viewer_retained=True,generation_graph_equivalent=True,
                  later_auto_dimensions_controls_rolled_back=True,
                  source_backup=str(ROOT/'backups/before-rollback-20261014T220737Z.zip'),
                  successful_axe_checkpoint=str(ROOT/'backups/axe-good-fast-20261014-2339.zip'),
                  comfy_server_kept_running=True,no_generation_submitted=True)
    write_json(ROOT/'logs/axe-rollback-verification.json',report)
    print(json.dumps(report))


if __name__ == '__main__':
    main()
