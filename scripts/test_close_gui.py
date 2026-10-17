"""Actual Tk close and ownership cleanup; no GPU, mocked server termination."""
import json
from pathlib import Path
import sys
import tempfile
import tkinter as tk
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dart3s.gui import App
from dart3s.gui_jobs import read_json, write_json

with tempfile.TemporaryDirectory() as folder:
    root = tk.Tk()
    app = App(root, project=Path(folder), poll=False)
    image = {'path': str(ROOT / 'assets/viking_wolf_rune_axe.png')}
    row = app.image_queue.add(image)
    app.image_queue.mark(row['id'], 'running')
    app.jobs.busy = True
    app.jobs.prompt_id = 'old'
    write_json(app.jobs.pending_file, {'prompt_id': 'old'})
    with patch.object(app.jobs, 'stop_server', return_value={'ok': True, 'stopped_pids': [123]}) as stop:
        app.close()
        # Wait for the non-daemon shutdown worker without a live Tk window.
        import time
        deadline = time.monotonic() + 3
        while not (Path(folder) / 'logs/gui-shutdown.json').exists() and time.monotonic() < deadline:
            time.sleep(.02)
        stop.assert_called_once()
    assert read_json(app.jobs.pending_file) == {}
    assert read_json(app.image_queue.path)['items'][0]['status'] == 'waiting'
    assert app.jobs._cancel_event.is_set()
    assert app.parameters().max_structure_elements == 4096
print(json.dumps({'ok': True, 'window_closed': True, 'server_stop_called': True,
                  'active_row_waiting': True, 'pending_cleared': True}))
