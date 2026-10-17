"""Exercise the app button and installed WPF viewer on an existing GLB."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
import tkinter as tk

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dart3s.gui import App
from dart3s.viewer import DEFAULT_VIEWER


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--keep-open', action='store_true')
    args = parser.parse_args()
    settings = json.loads((ROOT/'.state/gui/settings.json').read_text(encoding='utf-8'))
    glb = Path(settings['result']['glb_path'])
    digest = hashlib.sha256(glb.read_bytes()).hexdigest()
    directory = ROOT/'logs/art3d-viewer-button-test'
    directory.mkdir(parents=True, exist_ok=True)
    validation = directory/'viewer-validation.json'
    validation.unlink(missing_ok=True)
    subprocess.run([str(DEFAULT_VIEWER), str(glb), '--test', str(directory)],
                   cwd=DEFAULT_VIEWER.parent, check=True, timeout=60)
    render_report = json.loads(validation.read_text(encoding='utf-8-sig'))
    assert render_report['ok'] and render_report['triangles'] > 0, render_report
    frames = [directory/name for name in ('model-color.png','model-front.png','model-back.png','model-side.png')]
    fingerprints = [hashlib.sha256(frame.read_bytes()).hexdigest() for frame in frames]
    assert len(set(fingerprints)) == len(frames), 'Native camera presets must show different views'
    root = tk.Tk()
    root.withdraw()
    app = App(root, poll=False)
    app.settings_path = directory/'isolated-settings.json'
    assert str(app.viewer_button['state']) == 'disabled'
    app.on_result(dict(glb_path=str(glb),status='completed'), notify=False)
    assert str(app.viewer_button['state']) == 'normal'
    viewer = None
    try:
        app.viewer_button.invoke()
        viewer = app.model_viewer
        assert viewer is not None
        deadline = time.monotonic()+30
        handles = []
        while time.monotonic() < deadline:
            root.update()
            assert not viewer.closed, 'Native viewer exited unexpectedly'
            _, handles = viewer._windows()
            if handles:
                break
            time.sleep(.05)
        assert handles, 'App button must open a native viewer window'
        app.viewer_button.invoke()
        assert app.model_viewer is viewer, 'Repeated click should reuse the same process'
        assert hashlib.sha256(glb.read_bytes()).hexdigest() == digest
        report = dict(ok=True, executable=str(viewer.executable), pid=viewer.process.pid,
                      source=str(glb), source_sha256=digest, source_unchanged=True,
                      app_button_verified=True, native_window=True, reused_process=True,
                      native_render=render_report, four_distinct_camera_views=True,
                      no_generation=True, kept_open=args.keep_open)
        (directory/'integration.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        print(json.dumps(report))
        viewer.focus()
    finally:
        if viewer and not args.keep_open:
            viewer.close()
            viewer.process.wait(timeout=10)
        root.destroy()


if __name__ == '__main__':
    main()
