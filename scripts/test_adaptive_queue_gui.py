"""Real Tk queue: high-risk skip, next success, one completion sound. No GPU."""
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

with tempfile.TemporaryDirectory() as folder:
    root = tk.Tk()
    root.withdraw()
    app = App(root, project=Path(folder), poll=True)
    for name in ('reject', 'accept'):
        app.image_queue.add({'path': str(PNG), 'preview': str(PNG), 'original': name+'.png', 'size': [256,256]})
    calls = []
    def generate(image, **kwargs):
        calls.append(kwargs)
        kwargs['progress']({'status': 'submitted', 'prompt_id': 'test'})
        if len(calls) == 1:
            raise ComfyError('high_risk', 'Высокий риск: пропущено.')
        return SimpleNamespace(glb_path=GLB, to_dict=lambda: {'glb_path':str(GLB), 'prompt_id':'test'})
    with patch('dart3s.gui_jobs.generate_3d', side_effect=generate), \
         patch.object(app.jobs, 'render', return_value=str(PNG)), \
         patch('dart3s.gui.completion_sound') as sound, \
         patch('dart3s.gui.messagebox.showerror') as error:
        app.generate()
        end = time.monotonic() + 8
        while (app.queue_running or app.jobs.busy or sound.call_count != 1) and time.monotonic() < end:
            root.update()
            time.sleep(.01)
        assert [i['status'] for i in app.image_queue.items] == ['skipped','completed']
        assert app.image_queue.items[0]['risk']['level'] == 'high'
        assert not app.queue_running
        assert len(calls) == 2
        assert calls[0]['structure_retries'] == 1 and calls[0]['early_risk_seconds'] == 180
        assert sound.call_count == 1
        assert not error.called
        assert read_json(app.jobs.pending_file) == {}
    root.destroy()
report = {'ok':True, 'fixture':'Real Tk, simulated inference, no GPU', 'skip_then_next':True,
          'one_sound':True, 'no_error_popup':True, 'risk_high':True, 'pending_cleared':True}
(ROOT/'logs/adaptive-queue-gui-verification.json').write_text(json.dumps(report, indent=2),encoding='utf-8')
print(json.dumps(report))
