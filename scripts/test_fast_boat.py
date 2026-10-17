"""Run one real 512 boat job through the application's generation adapter."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dart3s.comfy3d import ComfyError, generate_3d
from dart3s.preview_watch import PreviewMonitor

REPORT = ROOT / "logs/boat-fast-verification.json"
IMAGE = ROOT / ".state/gui/inputs/1350e4828fa84ef98e29287b9d395e33.png"
PYTHON = ROOT / "ComfyUI_windows_portable/python_embeded/python.exe"


def main() -> int:
    with urlopen("http://127.0.0.1:8188/queue", timeout=10) as response:
        queue = json.load(response)
    if queue.get("queue_running") or queue.get("queue_pending"):
        raise RuntimeError("A real ComfyUI job is already active; fast test did not submit")
    digest = hashlib.sha256(IMAGE.read_bytes()).hexdigest()
    start = time.monotonic()
    report = {"status": "starting", "genuine_generation": True, "started_at": datetime.now(timezone.utc).isoformat(),
              "input": str(IMAGE), "input_sha256": digest, "frames": [],
              "settings": {"quality": "fast", "model": "trellis2", "polygons": 1000,
                           "remesh_resolution": 128, "texture_size": 128,
                           "structure_seed": 56, "shape_seed": 42, "texture_seed": 43}}
    lock = threading.Lock()

    def save():
        report["elapsed_seconds"] = round(time.monotonic() - start, 3)
        temporary = REPORT.with_suffix(".tmp")
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(REPORT)

    def frame(event, **payload):
        with lock:
            report["frames"].append(payload["snapshot"])
            save()

    monitor = PreviewMonitor(ROOT, frame, "fast-boat")

    def progress(event):
        with lock:
            report["current_event"] = event
            for key in ("status", "metadata_path", "prompt_id"):
                if key in event:
                    report[key] = event[key]
            if event.get("preview_directory"):
                monitor.configure(event["preview_directory"])
            save()
        print(json.dumps(event, ensure_ascii=False), flush=True)

    with lock:
        save()
    monitor.start()
    try:
        result = generate_3d(IMAGE, quality="fast", model="trellis2", polygons=1000,
                             remesh_resolution=128, texture_size=128,
                             structure_seed=56, shape_seed=42, texture_seed=43,
                             output_dir=ROOT / "outputs/boat-fast-test", visual_progress=True, preview_size=256,
                             object_info=ROOT / "logs/object_info-progress.json", auto_start=False, progress=progress)
        monitor.close()
        validation = subprocess.run([str(PYTHON), str(ROOT / "scripts/verify_glb.py"), str(result.glb_path),
                                     "--polygons", "1000", "--texture-size", "128"], capture_output=True, encoding="utf-8")
        preview = result.glb_path.with_suffix(".preview.png")
        render = subprocess.run([str(PYTHON), str(ROOT / "scripts/gui_preview_bridge.py"), str(result.glb_path),
                                 "--out", str(preview), "--size", "256", "--yaw", "45"],
                                capture_output=True, encoding="utf-8")
        with lock:
            report.update(result=result.to_dict(), validation=json.loads(validation.stdout),
                          preview=json.loads(render.stdout), source_unchanged=hashlib.sha256(IMAGE.read_bytes()).hexdigest() == digest,
                          status="completed" if validation.returncode == 0 and render.returncode == 0 else "validation_failed")
            save()
        print(json.dumps(report, ensure_ascii=False), flush=True)
        return 0 if report["status"] == "completed" else 1
    except Exception as exc:
        monitor.close()
        with lock:
            report.update(status="failed", error=exc.to_dict() if isinstance(exc, ComfyError) else str(exc))
            save()
        raise


if __name__ == "__main__":
    raise SystemExit(main())
