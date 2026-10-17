"""Native regressions: manual sizes reach submission and survive reopening."""
import importlib
import json
from pathlib import Path
import sys
import tempfile
import tkinter as tk
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def check(module_name, directory):
    module = importlib.import_module(module_name)
    fast = next(label for label, mode in module.QUALITY_LABELS.items() if mode == 'fast')
    saved = {'parameters': {'quality': fast, 'polygons': '2000'}, 'auto_dimensions': True}

    def make(settings):
        root = tk.Tk()
        root.withdraw()
        with patch.object(module, 'read_json', side_effect=lambda path: settings if Path(path).name == 'settings.json' else {}):
            app = module.App(root, poll=False)
        app.settings_path = directory/(module_name+'.json')
        return root, app

    root, app = make(saved)
    try:
        assert app.variables['remesh_resolution'].get() == '256'
        app.variables['remesh_resolution'].set('128')
        remesh_widget = next(widget for widget, _ in app.parameter_widgets
                             if str(widget.cget('textvariable')) == str(app.variables['remesh_resolution']))
        remesh_widget.event_generate('<<ComboboxSelected>>')
        assert not app.auto_dimensions.get(), 'Manual selection must disable auto sizes'
        app.variables['texture_size'].set('128')
        app.manual_dimensions()
        app.variables['polygons'].set('2000')
        app.change_quality()
        app.image = {'path': str(ROOT/'assets/viking_wolf_rune_axe.png')}
        app.jobs.start = Mock()
        app.generate()
        parameters = app.jobs.start.call_args.args[1]
        assert (parameters.remesh_resolution, parameters.texture_size) == (128, 128)
        saved = json.loads(app.settings_path.read_text(encoding='utf-8'))
        assert saved['auto_dimensions'] is False
    finally:
        root.destroy()
    root, app = make(saved)
    try:
        assert not app.auto_dimensions.get()
        assert app.variables['remesh_resolution'].get() == '128'
        assert app.variables['texture_size'].get() == '128'
        app.auto_dimensions.set(True)
        app.change_auto_dimensions()
        assert (app.variables['remesh_resolution'].get(), app.variables['texture_size'].get()) == ('256','256')
        app.variables['polygons'].set('500')
        assert (app.variables['remesh_resolution'].get(), app.variables['texture_size'].get()) == ('128','128')
    finally:
        root.destroy()
    return dict(manual_selection=True, submission_uses_manual_sizes=True,
                reopen_preserves_manual_sizes=True, automatic_sizes_can_be_reenabled=True)


with tempfile.TemporaryDirectory(dir=ROOT/'logs', prefix='manual-sizes-') as temp:
    results = {name: check(name, Path(temp)) for name in ('dart3s.gui','Details3D.gui')}
report = dict(ok=True, no_generation=True, applications=results)
(ROOT/'logs/manual-dimensions-verification.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps(report))
