"""Real native queue/Jobs threads with labelled fake inference; no GPU jobs."""
import json
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import time
import tkinter as tk
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dart3s.gui import App
from dart3s.image_queue import ImageQueue
from dart3s.comfy3d import ComfyError

PNG = ROOT / 'logs/dart3s-app.png'
GLB = ROOT / 'outputs/trellis2-test/20261014T091135Z_d81eb7e3/688c5b1e_000.glb'
report = {'fixture': 'Real Tk and Jobs; simulated inference with an existing GLB; no GPU submission'}


def pump(root, until, timeout=8):
    deadline = time.monotonic() + timeout
    while not until():
        if time.monotonic() > deadline:
            raise AssertionError('UI fixture timed out')
        root.update()
        time.sleep(.01)
    root.update()


with tempfile.TemporaryDirectory(dir=ROOT / 'logs', prefix='queue-gui-') as temporary:
    project = Path(temporary)
    images = []
    for name in ('axe', 'sword', 'boat', 'stone'):
        path = project / (name + '.png')
        shutil.copy2(PNG, path)
        images.append(str(path))
    root = tk.Tk()
    root.withdraw()
    calls, errors = [], []
    release = threading.Event()
    concurrent = 0
    maximum = 0

    def fake_native(script, argv, **kwargs):
        assert script == 'gui_image_bridge.py'
        source = argv[argv.index('--source') + 1]
        return {'path': source, 'preview': str(PNG), 'original': source, 'size': [256, 256]}

    def generate(image, **kwargs):
        global concurrent, maximum
        assert kwargs['visual_progress'] is False
        concurrent += 1
        maximum = max(maximum, concurrent)
        calls.append(Path(image).stem)
        kwargs['progress']({'status': 'submitted', 'prompt_id': 'TEST-' + Path(image).stem})
        if len(calls) == 1:
            assert release.wait(5)
        time.sleep(.05)
        concurrent -= 1
        return SimpleNamespace(glb_path=GLB, to_dict=lambda: {'glb_path': str(GLB), 'prompt_id': 'TEST-' + Path(image).stem})

    try:
        app = App(root, project=project, poll=True)
        app.sound.set(True)
        with patch.object(app.jobs, 'run_native', side_effect=fake_native), \
             patch.object(app.jobs, 'render', return_value=str(PNG)), \
             patch('dart3s.gui_jobs.generate_3d', side_effect=generate), \
             patch('dart3s.gui.completion_sound') as sound, \
             patch('dart3s.gui.messagebox.showerror', side_effect=lambda *a, **k: errors.append(str(a))), \
             patch('dart3s.gui.filedialog.askopenfilenames', return_value=images[:3]):
            app.choose_button.invoke()
            pump(root, lambda: len(app.image_queue.items) == 3 and not app.image_loading)
            assert [Path(x['image']['path']).stem for x in app.image_queue.items] == ['axe', 'sword', 'boat']
            app.generate_button.invoke()
            pump(root, lambda: len(calls) == 1)
            assert app.jobs.busy
            assert sound.call_count == 0
            # Add another input while the first is still running.
            app.import_image(images[3])
            pump(root, lambda: len(app.image_queue.items) == 4)
            release.set()
            pump(root, lambda: not app.queue_running and not app.jobs.busy and len(calls) == 4)
            pump(root, lambda: sound.call_count == 1)
            assert calls == ['axe', 'sword', 'boat', 'stone'], calls
            assert maximum == 1
            assert not errors, errors
            assert all(x['status'] == 'completed' for x in app.image_queue.items)
            assert len(ImageQueue(project / '.state/gui/queue.json').items) == 4
            assert str(app.viewer_button['state']) == 'normal'
            assert str(app.save_button['state']) == 'normal'
            # Viewing another result must not trigger a completion notification.
            app.queue_table.selection_set(app.image_queue.items[0]['id'])
            app.open_queue_item()
            pump(root, lambda: not app.preview_loading)
            assert sound.call_count == 1
            assert app.image['path'] == images[0]
            root.deiconify()
            root.update()
            for widget in (app.choose_button, app.queue_table, app.generate_button, app.force_button, app.save_button):
                assert widget.winfo_y() + widget.winfo_height() <= widget.master.winfo_height(), 'Clipped control'
            root.withdraw()
            report.update(ok=True, order=list(calls), max_concurrent_generations=maximum,
                          notification_calls=sound.call_count, added_while_running=True,
                          persistent_queue=True, final_model_view_enabled=True,
                          intermediate_preview=False)
            app.clear_finished_queue()
            app.image_queue.add({'path': images[0], 'preview': str(PNG), 'original': images[0], 'size': [256, 256]})
            app.image_queue.add({'path': images[1], 'preview': str(PNG), 'original': images[1], 'size': [256, 256]})
            app.refresh_queue()
            # A lost watch connection pauses the batch instead of submitting another image.
            def disconnect(image, **kwargs):
                kwargs['progress']({'status': 'submitted', 'prompt_id': 'TEST-resumable'})
                raise ComfyError('watch_timeout', 'TEST disconnected', 'TEST resume')
            with patch('dart3s.gui_jobs.generate_3d', side_effect=disconnect) as failed:
                app.generate()
                pump(root, lambda: not app.jobs.busy and not app.queue_running)
                assert failed.call_count == 1
            assert [x['status'] for x in app.image_queue.items] == ['paused', 'waiting']
            assert sound.call_count == 1
            assert app.jobs.pending_file.is_file()
            with patch('dart3s.gui_jobs.resume_3d', return_value=SimpleNamespace(
                    glb_path=GLB, to_dict=lambda: {'glb_path': str(GLB), 'prompt_id': 'TEST-resumable'})) as resumed:
                app.resume()
                pump(root, lambda: not app.jobs.busy and not app.queue_running and len(calls) == 5)
                pump(root, lambda: sound.call_count == 2)
                assert resumed.call_count == 1
                assert resumed.call_args.args[0] == 'TEST-resumable'
            assert all(x['status'] == 'completed' for x in app.image_queue.items)
            report.update(connection_failure_pauses_queue=True, resume_without_resubmission=True,
                          notification_calls_first_batch=1, notification_calls_after_recovered_batch=sound.call_count)
    finally:
        release.set()
        root.destroy()

(ROOT / 'logs/queue-gui-verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(report, ensure_ascii=False, indent=2))
