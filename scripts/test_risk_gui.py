"""Real check button and CPU checker; forbid any generation or server calls."""
import json
from pathlib import Path
import sys
import tempfile
import time
import tkinter as tk
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dart3s.gui import App
from dart3s.image_queue import ImageQueue

report = {'fixture': 'Real native UI and CPU image checker; generation explicitly forbidden'}
with tempfile.TemporaryDirectory(dir=ROOT / 'logs', prefix='risk-gui-') as temporary:
    project = Path(temporary)
    root = tk.Tk()
    root.withdraw()
    try:
        app = App(root, project=project, poll=True)
        # Use production portable runtime/scripts, but isolate all GUI state.
        original_native = app.jobs.run_native
        def native(script, argv, **kwargs):
            assert script in ('check_image_risk.py', 'index_risk_history.py'), 'Only risk analysis/history is allowed'
            old = app.jobs.project
            try:
                app.jobs.project = ROOT
                return original_native(script, argv, **kwargs)
            finally:
                app.jobs.project = old
        sources = [ROOT / '.state/gui/inputs/b4f722ebdda14fc785611004804b710d.png', ROOT / 'assets/viking_wolf_rune_axe.png']
        app.variables['polygons'].set('10000')
        app.variables['texture_seed'].set('42')
        for source in sources:
            app.image_queue.add({'path': str(source), 'preview': str(source), 'original': str(source), 'size': [1024, 1024]})
        app.image_queue.mark(app.image_queue.items[0]['id'], 'completed')
        app.refresh_queue()
        app.image = app.image_queue.items[0]['image']
        app.update_controls()
        with patch.object(app.jobs, 'run_native', side_effect=native) as analysis, \
             patch('dart3s.gui_jobs.generate_3d', side_effect=AssertionError('Generation forbidden')) as generate, \
             patch('dart3s.gui_jobs.resume_3d', side_effect=AssertionError('Server forbidden')) as resume, \
             patch.object(app.jobs, 'render', side_effect=AssertionError('Rendering forbidden')) as render, \
             patch('dart3s.gui.completion_sound') as sound:
            app.check_button.invoke()
            deadline = time.monotonic() + 20
            while app.checking_images and time.monotonic() < deadline:
                root.update()
                time.sleep(.02)
            assert not app.checking_images, 'Risk check timed out'
            assert analysis.call_count == 3
            assert generate.call_count == resume.call_count == render.call_count == sound.call_count == 0
            assert all(item.get('risk', {}).get('reasons') for item in app.image_queue.items)
            assert app.image_queue.items[0]['risk']['level'] == 'high'
            assert app.image_queue.items[0]['risk']['basis'] == 'measured_history'
            assert [item['status'] for item in app.image_queue.items] == ['completed', 'waiting']
            assert len(ImageQueue(project / '.state/gui/queue.json').items) == 2
            dialogs = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)]
            assert len(dialogs) == 1
            window = dialogs[0]
            tables = [w for w in window.winfo_children() if w.winfo_class() == 'Treeview']
            table = tables[0]
            table.selection_set(app.image_queue.items[0]['id'])
            for widget in window.winfo_children():
                if widget.winfo_class() == 'TButton' and widget.cget('text') == 'Убрать выбранные из очереди':
                    widget.invoke()
            assert len(app.image_queue.items) == 1
            assert app.image_queue.items[0]['status'] == 'waiting'
            remaining_risk = app.image_queue.items[0]['risk']
            table.selection_set(app.image_queue.items[0]['id'])
            for widget in window.winfo_children():
                if widget.winfo_class() == 'TButton' and widget.cget('text') == 'Убрать выбранные из очереди':
                    widget.invoke()
            assert not app.image_queue.items
            assert str(app.generate_button['state']) == 'disabled'
            window.destroy()
            root.deiconify()
            root.update()
            assert app.check_button.winfo_y() + app.check_button.winfo_height() <= app.check_button.master.winfo_height()
            report.update(ok=True, checked=2, history_scan_calls=1, completed_items_checked=True, tree_risk='high', generation_calls=0, model_render_calls=0,
                          server_calls=0, sound_calls=0, manual_removal=True,
                          remaining_risk=remaining_risk, empty_queue_cannot_start=True)
    finally:
        root.destroy()
(ROOT / 'logs/risk-gui-verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(report, ensure_ascii=False, indent=2))
