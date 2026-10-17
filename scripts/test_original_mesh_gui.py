"""Actual Tk/Jobs wiring with simulated copy transport; no GPU or viewer window."""
import json
from pathlib import Path
import shutil
import sys
import tempfile
import time
import tkinter as tk
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from dart3s.gui import App

SOURCE=ROOT/'outputs/original-mesh-verification/20261016T185709Z_af98a279/c78c4a37_000.glb'
with tempfile.TemporaryDirectory(dir=ROOT/'logs',prefix='original-gui-') as folder:
    root=tk.Tk();root.withdraw()
    app=App(root,project=Path(folder),poll=True)
    original={'glb_path':str(SOURCE)}
    app.on_result(original,notify=False)
    assert app.parameters().mesh_mode=='original'
    app.variables['polygons'].set('10000')
    def native(script,argv,**kwargs):
        assert script=='low_poly_copy.py'
        output=Path(argv[argv.index('--out')+1])
        # Copy a preverified small artifact only inside this temporary fixture.
        output=Path(folder)/'verified-copy.glb'
        shutil.copy2(SOURCE.with_name('rocks.lowpoly-10000.glb'),output)
        return {'glb_path':str(output),'triangles':9824}
    with patch.object(app.jobs,'run_native',side_effect=native),patch('dart3s.viewer.ModelViewer') as viewer:
        app.create_low_poly()
        end=time.monotonic()+5
        while (app.jobs.busy or not viewer.called) and time.monotonic()<end:
            root.update();time.sleep(.01)
        root.update()
        assert viewer.called
        assert app.result==original
        assert str(app.low_poly_button['state'])=='normal'
        assert str(app.save_button['state'])=='normal'
    with patch.object(app.jobs,'run_native',side_effect=RuntimeError('Копия отклонена')),patch('dart3s.gui.messagebox.showinfo') as notice:
        app.create_low_poly()
        end=time.monotonic()+5
        while (app.jobs.busy or not notice.called) and time.monotonic()<end:
            root.update();time.sleep(.01)
        root.update()
        assert notice.called and app.result==original
    def cancellable(script,argv,**kwargs):
        assert kwargs['cancel_event'].wait(5)
        raise RuntimeError('cancelled')
    with patch.object(app.jobs,'run_native',side_effect=cancellable):
        app.create_low_poly()
        app.cancel()
        end=time.monotonic()+5
        while (app.jobs.busy or app.low_poly_running) and time.monotonic()<end:
            root.update();time.sleep(.01)
        root.update()
        assert not app.jobs.busy and not app.low_poly_running and app.result==original
    root.destroy()
report={'ok':True,'gui_uses_original_mesh':True,'copy_never_replaces_primary':True,
        'rejected_copy_keeps_primary':True,'cancelled_copy_keeps_primary':True,
        'fixture':'Actual Tk/Jobs, simulated copy transport; CPU copy separately verified'}
(ROOT/'logs/original-mesh-gui-verification.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps(report))
