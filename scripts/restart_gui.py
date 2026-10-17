"""Ask our idle native app to close normally, then open its updated version."""
import ctypes
import argparse
from ctypes import wintypes
import json
from pathlib import Path
import subprocess
import time
from urllib.request import ProxyHandler, build_opener
from urllib.error import URLError

ROOT=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser()
parser.add_argument('--verify-boat',action='store_true')
parser.add_argument('--observe-prompt')
parser.add_argument('--observe-metadata')
parser.add_argument('--result-metadata',type=Path,help='Show an already completed model after reopening')
args=parser.parse_args()
try:
    with build_opener(ProxyHandler({})).open('http://127.0.0.1:8188/queue', timeout=5) as response:
        state=json.loads(response.read())
except URLError as error:
    # A stopped local server cannot be generating; opening the UI is still useful.
    if not isinstance(error.reason, ConnectionRefusedError):
        raise
    state={'queue_running': [], 'queue_pending': []}
owned_active = args.observe_prompt and all(job[1] == args.observe_prompt
                    for job in state['queue_running'] + state['queue_pending'])
if (state['queue_running'] or state['queue_pending']) and not owned_active:
    raise RuntimeError('Generation active; leave the window open')
pending=ROOT/'.state/gui/pending.json'
if pending.exists() and json.loads(pending.read_text(encoding='utf-8')).get('prompt_id'):
    raise RuntimeError('Pending job; leave the window open')
native=ctypes.windll.user32
native.FindWindowW.argtypes=[wintypes.LPCWSTR,wintypes.LPCWSTR]
native.FindWindowW.restype=wintypes.HWND
native.PostMessageW.argtypes=[wintypes.HWND,wintypes.UINT,wintypes.WPARAM,wintypes.LPARAM]
title='Dart3s — Генерация 3D'
handle=native.FindWindowW(None,title)
if handle:
    previous_pid=wintypes.DWORD()
    native.GetWindowThreadProcessId(handle,ctypes.byref(previous_pid))
    native.PostMessageW(handle,0x0010,0,0)
    deadline=time.monotonic()+8
    while native.FindWindowW(None,title) and time.monotonic()<deadline:
        time.sleep(.2)
    if native.FindWindowW(None,title):
        print(json.dumps({'ok':False,'needs_manual_reopen':True}))
        raise SystemExit(1)
    # The window disappears before its cleanup worker releases the app mutex.
    # Wait for that owned UI process rather than racing a new launcher.
    kernel=ctypes.windll.kernel32
    kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
    kernel.OpenProcess.restype=wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD]
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    previous=kernel.OpenProcess(0x00100000,False,previous_pid.value)
    if previous:
        try:
            if kernel.WaitForSingleObject(previous,20000)==258:
                raise RuntimeError('Previous Dart3s process is still shutting down')
        finally:
            kernel.CloseHandle(previous)
if args.result_metadata:
    record=json.loads(args.result_metadata.read_text(encoding='utf-8'))
    if record.get('status')!='completed' or not Path(record.get('glb_path','')).is_file():
        raise RuntimeError('A completed, saved GLB is required')
    settings_path=ROOT/'.state/gui/settings.json'
    settings=json.loads(settings_path.read_text(encoding='utf-8')) if settings_path.exists() else {}
    settings['result']=record
    queue_path=ROOT/'.state/gui/queue.json'
    if queue_path.exists() and record.get('image'):
        rows=json.loads(queue_path.read_text(encoding='utf-8')).get('items',[])
        image=next((row['image'] for row in rows if row.get('image',{}).get('path')==record['image']),None)
        if image:
            settings['image']=image
    settings_path.write_text(json.dumps(settings,ensure_ascii=False,indent=2),encoding='utf-8')
launch=[str(ROOT/'.venv-cli/Scripts/pythonw.exe'),str(ROOT/'scripts/launch_gui.pyw')]
if args.verify_boat:
    launch.append('--verify-boat')
if args.observe_prompt:
    launch += ['--observe-prompt',args.observe_prompt,'--observe-metadata',args.observe_metadata]
process=subprocess.Popen(launch,
                         cwd=ROOT,creationflags=subprocess.CREATE_NO_WINDOW)
deadline=time.monotonic()+8
while not native.FindWindowW(None,title) and time.monotonic()<deadline:
    time.sleep(.2)
print(json.dumps({'ok':bool(native.FindWindowW(None,title)),'pid':process.pid}))
