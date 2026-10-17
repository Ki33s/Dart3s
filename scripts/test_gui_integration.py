"""Test native UI + real CLI against clearly labelled local TEST fixtures."""
import ctypes
from contextlib import ExitStack
from ctypes import wintypes
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import tkinter as tk
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dart3s.comfy3d import resume_3d
from dart3s.gui import App
from dart3s.gui_jobs import read_json

GLB = ROOT / "outputs/trellis2-test/20261014T091135Z_d81eb7e3/688c5b1e_000.glb"
report = {"fixture": "TEST websocket progress; existing real GLB; no new GPU generation"}


def capture(root, path):
    # Capture only this application's own HWND, never the surrounding desktop.
    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND
    hwnd = user32.GetAncestor(root.winfo_id(), 2)
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    width, height = rect.right-rect.left, rect.bottom-rect.top
    user32.GetWindowDC.argtypes = [wintypes.HWND]
    user32.GetWindowDC.restype = wintypes.HDC
    hdc = user32.GetWindowDC(hwnd)
    gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    memory = gdi32.CreateCompatibleDC(hdc)
    gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
    gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
    bitmap = gdi32.CreateCompatibleBitmap(hdc, width, height)
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
    previous = gdi32.SelectObject(memory, bitmap)
    user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
    if not user32.PrintWindow(hwnd, memory, 2):
        raise RuntimeError("Cannot capture the app window")
    pixels = ctypes.create_string_buffer(width*height*4)
    gdi32.GetBitmapBits.argtypes = [wintypes.HBITMAP, ctypes.c_long, ctypes.c_void_p]
    gdi32.GetBitmapBits(bitmap, len(pixels), pixels)
    raw = path.with_suffix(".bgra")
    raw.write_bytes(pixels.raw)
    code = "from PIL import Image; import sys; from pathlib import Path; Image.frombuffer('RGB',(int(sys.argv[2]),int(sys.argv[3])),Path(sys.argv[1]).read_bytes(),'raw','BGRX',0,1).save(sys.argv[4])"
    subprocess.run([str(ROOT/"ComfyUI_windows_portable/python_embeded/python.exe"), "-c", code,
                    str(raw), str(width), str(height), str(path)], check=True,
                   creationflags=subprocess.CREATE_NO_WINDOW)
    gdi32.SelectObject(memory, previous)
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    gdi32.DeleteDC.argtypes = [wintypes.HDC]
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    gdi32.DeleteObject(bitmap)
    gdi32.DeleteDC(memory)
    user32.ReleaseDC(hwnd, hdc)


with tempfile.TemporaryDirectory(dir=ROOT/"logs") as temp:
    preview_temp = tempfile.TemporaryDirectory(dir=ROOT/'outputs/gui-integration-test', prefix='milestone-fixture-')
    preview_directory = Path(preview_temp.name) / 'previews'
    ready = Path(temp)/"ready.json"
    server = subprocess.Popen([str(ROOT/"ComfyUI_windows_portable/python_embeded/python.exe"),
        str(ROOT/"scripts/gui_test_server.py"), "--glb", str(GLB), "--ready", str(ready),
        '--preview-directory', str(preview_directory)],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
    root = tk.Tk()
    root.withdraw()
    errors = []
    patches = ExitStack()
    patches.enter_context(patch("dart3s.gui.messagebox.showerror", side_effect=lambda *a, **kw: errors.append(str(a))))
    try:
        deadline = time.monotonic()+15
        while not ready.exists():
            if server.poll() is not None or time.monotonic()>deadline:
                raise RuntimeError("TEST server did not start")
            time.sleep(.1)
        fixture = read_json(ready)
        app = App(root)
        app.sound.set(True)
        app.preview_size.set('256')
        app.settings_path = Path(temp)/"settings.json"
        app.jobs.pending_file = Path(temp)/"pending.json"
        root.update()
        app.import_image(str(ROOT/"assets/viking_wolf_rune_axe.png"))
        deadline = time.monotonic()+20
        while app.image_loading and time.monotonic()<deadline:
            root.update()
            time.sleep(.03)
        assert app.image and str(app.generate_button["state"]) == "normal", "Image was not loaded"
        app.jobs.run_token = 'removed-preview-fixture'
        app.handle_event({'kind':'intermediate_preview','token':app.jobs.run_token,
                          'snapshot':{'stage':'structure','image':str(preview_directory/'removed.png')}})
        assert not app.intermediate_shown, 'A missing preview must not break or change UI state'
        def generate_fixture(image, **kwargs):
            report["parameters_received"] = {k:v for k,v in kwargs.items() if k not in {"project", "progress"}}
            kwargs['progress']({'status': 'building', 'preview_directory': str(preview_directory)})
            return resume_3d(fixture["prompt_id"], project=ROOT, local_url=fixture["url"],
                             output_dir=ROOT/"outputs/gui-integration-test", progress=kwargs["progress"])
        observed = []
        milestones = set()
        with patch("dart3s.gui_jobs.generate_3d", side_effect=generate_fixture), patch("dart3s.gui.completion_sound") as sound:
            app.generate()
            deadline = time.monotonic()+60
            while (app.jobs.busy or app.result is None) and time.monotonic()<deadline:
                root.update()
                label = app.step_text.get()
                caption = app.result_label.cget('text')
                for stage in ('Структура объекта', 'Геометрия без текстур', 'Подготовленная сетка', 'Модель с базовой текстурой'):
                    if caption.startswith(stage):
                        milestones.add(stage)
                        assert str(app.save_button['state']) == 'disabled', 'Intermediate is not a downloadable GLB'
                        assert sound.call_count == 0, 'Intermediate preview must not announce completion'
                        original_photo = app.canvas_images[app.result_canvas][0]
                        frame_size = (original_photo.width(), original_photo.height())
                        assert min(frame_size) == 256, 'Selected size is the shorter side'
                        assert frame_size[0] != frame_size[1], 'This model has a rectangular projected view'
                        assert f'{frame_size[0]}×{frame_size[1]}' in caption
                        assert 'Этап ' in app.activity_text.get()
                if "7 / 12" in label:
                    observed.append(float(app.bar["value"]))
                time.sleep(.025)
            for _ in range(10):
                root.update()
                time.sleep(.05)
            assert app.result, f"No GLB result: {errors}"
            result = Path(app.result["glb_path"])
            assert result.read_bytes() == GLB.read_bytes(), "Downloaded GLB changed"
            assert any(abs(v-100*7/12)<.01 for v in observed), "Live percentage missing"
            assert sound.call_count == 1, "Completion notification must happen exactly once"
            assert float(app.bar["value"]) == 100
            assert "result" in app.photos, "3D preview missing"
            assert len(milestones) == 4, f'Missing intermediate stages: {milestones}'
            assert report['parameters_received']['visual_progress'] is True
            assert report['parameters_received']['preview_size'] == 256
            saved = Path(temp)/"saved.glb"
            with patch("dart3s.gui.filedialog.asksaveasfilename", return_value=str(saved)):
                app.save_result()
            assert saved.read_bytes() == GLB.read_bytes(), "Save GLB failed"
            for button in (app.generate_button, app.save_button, app.cancel_button):
                assert button.winfo_y()+button.winfo_height() <= button.master.winfo_height(), "Button clipped"
            report.update(ok=True, live_percentage=observed[0], notification_calls=sound.call_count,
                          glb=str(result), saved_copy_verified=True, native_preview=True)
            report.update(intermediate_milestones=sorted(milestones), preview_sizes=list(app.preview_selector['values']),
                          no_intermediate_beep=True, no_intermediate_save=True,
                          rectangular_milestone_size=frame_size)
            app.status.set("3D-модель готова — проверенный результат Trellis2")
            app.step_text.set("GLB: 9982 треугольника · текстуры 256×256")
            root.deiconify()
            root.update()
            capture(root, ROOT/"logs/dart3s-app.png")
    finally:
        patches.close()
        root.destroy()
        server.terminate()
        server.wait(timeout=10)
        assert Path(preview_temp.name).resolve().is_relative_to((ROOT/'outputs').resolve())
        preview_temp.cleanup()
    (ROOT/"logs/gui-integration-verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
