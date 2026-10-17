"""Native force-cancel UI with simulated generation/server termination, no GPU."""
import json
from pathlib import Path
import sys
import tempfile
import time
import tkinter as tk
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dart3s.gui import App
from dart3s.comfy3d import ComfyError
from dart3s.gui_jobs import read_json

PNG = ROOT / 'assets/viking_wolf_rune_axe.png'
GLB = ROOT / 'outputs/trellis2-test/20261014T091135Z_d81eb7e3/688c5b1e_000.glb'


def pump(root, condition):
    deadline = time.monotonic() + 8
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError('Force-cancel GUI timed out')
        root.update()
        time.sleep(.01)
    root.update()


with tempfile.TemporaryDirectory(dir=ROOT / 'logs', prefix='force-ui-') as directory:
    root = tk.Tk()
    root.withdraw()
    errors, calls = [], []
    try:
        app = App(root, project=Path(directory), poll=True)
        for name in ('discard', 'keep'):
            app.image_queue.add({'path': str(PNG), 'preview': str(PNG), 'original': name + '.png', 'size': [1024, 1024]})
        app.refresh_queue()
        app.update_controls()
        def generate(image, **kwargs):
            calls.append(image)
            kwargs['progress']({'status': 'submitted', 'prompt_id': 'TEST-' + str(len(calls))})
            if len(calls) == 1:
                assert kwargs['cancel_event'].wait(5)
                raise ComfyError('cancelled', 'TEST forced cancellation')
            return SimpleNamespace(glb_path=GLB, to_dict=lambda: {'glb_path': str(GLB)})
        with patch('dart3s.gui_jobs.generate_3d', side_effect=generate), \
             patch.object(app.jobs, 'stop_server', return_value={'ok': True, 'stopped_pids': ['TEST']}) as stop, \
             patch.object(app.jobs, 'render', return_value=str(PNG)), \
             patch('dart3s.gui.messagebox.askyesno', return_value=True), \
             patch('dart3s.gui.messagebox.showerror', side_effect=lambda *a, **k: errors.append(str(a))), \
             patch('dart3s.gui.completion_sound') as sound:
            app.generate_button.invoke()
            pump(root, lambda: app.jobs.prompt_id is not None)
            old_token = app.jobs.run_token
            app.jobs.cancel_requested = True
            app.handle_event({'kind': 'cancel_requested', 'job_token': old_token})
            assert str(app.cancel_button['state']) == 'disabled'
            assert str(app.force_button['state']) == 'normal'
            started = time.monotonic()
            app.force_button.invoke()
            pump(root, lambda: not app.jobs.busy and app.queue_current is None)
            elapsed = time.monotonic() - started
            assert elapsed < 3, elapsed
            assert len(app.image_queue.items) == 1
            assert app.image_queue.items[0]['image']['original'] == 'keep.png'
            assert app.image_queue.items[0]['status'] == 'waiting'
            assert read_json(app.jobs.pending_file) == {}
            assert len(calls) == 1 and sound.call_count == 0
            assert str(app.generate_button['state']) == 'normal'
            stop.assert_called_once()
            status = app.status.get()
            app.handle_event({'kind': 'progress', 'job_token': old_token, 'event': {'status': 'submitting'}})
            assert app.status.get() == status
            app.generate_button.invoke()
            pump(root, lambda: len(calls) == 2 and not app.queue_running and not app.jobs.busy)
            pump(root, lambda: sound.call_count == 1)
            assert not errors, errors
            report = dict(ok=True, fixture='Native UI; simulated inference and server stop; no GPU',
                          force_after_graceful_cancel=True, force_seconds=elapsed, discarded_row_removed=True,
                          remaining_rows_preserved=True, stale_events_ignored=True, pending_cleared=True,
                          no_automatic_next_generation=True, next_generation_can_start=True)
    finally:
        root.destroy()
(ROOT / 'logs/force-cancel-gui-verification.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
print(json.dumps(report, indent=2))
