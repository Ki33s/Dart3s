"""Background work for the native UI; no Tk calls from worker threads."""
from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import subprocess
import threading
from typing import Any
from urllib.request import Request, ProxyHandler, build_opener
from uuid import uuid4

from dart3s.comfy3d import ComfyError, GenerationResult, Parameters, PROJECT, generate_3d, resume_3d


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def read_json(path: Path) -> dict:
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
        return result if isinstance(result, dict) else {}
    except (OSError, ValueError):
        return {}


def recent_runs(project: Path = PROJECT, limit: int = 30) -> list[dict]:
    paths = sorted((project / "outputs/details").rglob("generation.json"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    results = []
    for path in paths[:200]:
        if "gui-integration-test" in path.relative_to(project / "outputs").parts:
            continue
        record = read_json(path)
        if not record:
            continue
        record["metadata_path"] = str(path.resolve())
        if record.get("status") == "completed" and not Path(record.get("glb_path", "")).is_file():
            record["status"] = "missing"
        results.append(record)
        if len(results) >= limit:
            break
    return results


class Jobs:
    def __init__(self, project: Path = PROJECT):
        self.project = project.resolve()
        self.events: queue.Queue[dict[str, Any]] = queue.Queue()
        self.busy = False
        self.prompt_id: str | None = None
        self.metadata_path: str | None = None
        self.cancel_requested = False
        self.preview_size = 256
        self.run_token = None
        self.pending_file = self.project / "Details3D/state/pending.json"

    def emit(self, kind: str, **fields) -> None:
        self.events.put({"kind": kind, **fields})

    def run_native(self, script: str, args: list[str], timeout: float = 120) -> dict:
        python = self.project / "ComfyUI_windows_portable/python_embeded/python.exe"
        proc = subprocess.run([str(python), str(self.project / "scripts" / script), *args],
                              cwd=self.project, env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
                              text=True, encoding="utf-8", errors="replace", capture_output=True,
                              timeout=timeout, shell=False,
                              creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        try:
            data = json.loads(proc.stdout)
        except ValueError as exc:
            raise RuntimeError(proc.stderr.strip() or "Не удалось обработать файл.") from exc
        if proc.returncode or data.get("ok") is False:
            raise RuntimeError(data.get("error") or proc.stderr.strip() or "Не удалось обработать файл.")
        return data.get("data", data)

    def import_image(self, source: str | None = None, *, clipboard: bool = False) -> None:
        def worker():
            try:
                args = ["--directory", str(self.project / "Details3D/state/inputs"), "--width", "360", "--height", "360"]
                args += ["--clipboard"] if clipboard else ["--source", str(source)]
                image = self.run_native("gui_image_bridge.py", args)
                self.emit("image", image=image)
            except Exception as exc:
                self.emit("image_error", message=str(exc))
        threading.Thread(target=worker, daemon=True).start()

    def start(self, image: str, parameters: Parameters, *, prompt_id: str | None = None,
              preview_size: int = 256) -> None:
        if self.busy:
            raise RuntimeError("Генерация уже выполняется.")
        parameters.validate()
        if preview_size not in (128, 256, 512):
            raise ValueError('Размер предпросмотра: 128, 256 или 512.')
        if not prompt_id and not Path(image).is_file():
            raise ValueError("Выберите изображение.")
        self.busy = True
        self.prompt_id = prompt_id
        self.metadata_path = None
        self.cancel_requested = False
        self.preview_size = preview_size
        self.run_token = uuid4().hex
        info = {"image": image, "parameters": parameters.__dict__, "prompt_id": prompt_id,
                "preview_size": preview_size, "visual_progress": False}

        def progress(event):
            self.metadata_path = event.get("metadata_path", self.metadata_path)
            if event.get("prompt_id"):
                self.prompt_id = event["prompt_id"]
                write_json(self.pending_file, {**info, "prompt_id": self.prompt_id,
                                               "metadata_path": self.metadata_path})
            self.emit("progress", event=event)

        def worker():
            try:
                if prompt_id:
                    result = resume_3d(prompt_id, project=self.project, progress=progress, output_dir=self.project / "outputs/details")
                else:
                    result = generate_3d(image, **parameters.__dict__, project=self.project, progress=progress,
                                         visual_progress=False, preview_size=preview_size, output_dir=self.project / "outputs/details")
                self.emit("result", result=result.to_dict())
                write_json(self.pending_file, {})
                # A preview failure cannot turn an existing GLB into a failed generation.
                self.emit("progress", event={"status": "rendering_preview"})
                try:
                    preview = self.render(result.glb_path)
                    self.emit("preview", preview=preview, glb=str(result.glb_path))
                except Exception as exc:
                    self.emit("preview_error", message=str(exc))
            except Exception as exc:
                if isinstance(exc, ComfyError):
                    code, message = exc.code, str(exc)
                    self.metadata_path = str(exc.metadata_path) if exc.metadata_path else self.metadata_path
                else:
                    code, message = type(exc).__name__, str(exc)
                terminal = not self.prompt_id or code in {"execution_error", "execution_interrupted", "cancelled", "server_died", "glb_invalid", "glb_output_missing"}
                if terminal:
                    write_json(self.pending_file, {})
                self.emit("error", code=code, message=message, metadata_path=self.metadata_path,
                          cancelled=code in {"cancelled", "execution_interrupted"}, resumable=not terminal)
            finally:
                self.busy = False
                self.emit("idle")
        threading.Thread(target=worker, daemon=True).start()

    def render(self, glb: Path, yaw: float = 45, *, elevation=15, zoom=1.0, output=None) -> str:
        output = Path(output) if output is not None else glb.with_suffix(".preview.png")
        self.run_native("gui_preview_bridge.py", [str(glb), "--out", str(output), "--size", str(self.preview_size),
                                                 "--yaw", str(yaw), '--elevation', str(elevation), '--zoom', str(zoom)], timeout=180)
        return str(output)

    def preview(self, glb: str, yaw: float = 45) -> None:
        def worker():
            try:
                self.emit("preview", preview=self.render(Path(glb), yaw), glb=glb)
            except Exception as exc:
                self.emit("preview_error", message=str(exc))
        threading.Thread(target=worker, daemon=True).start()

    def cancel(self) -> None:
        if not self.busy or not self.prompt_id:
            return
        prompt_id = self.prompt_id
        # Only this UI's submitted job: never send an unscoped global interrupt.
        def worker():
            try:
                opener = build_opener(ProxyHandler({}))
                for route, body in (("/queue", {"delete": [prompt_id]}),
                                    ("/interrupt", {"prompt_id": prompt_id})):
                    request = Request("http://127.0.0.1:8188" + route,
                                      data=json.dumps(body).encode("utf-8"),
                                      headers={"Content-Type": "application/json"}, method="POST")
                    with opener.open(request, timeout=10) as response:
                        response.read()
                self.cancel_requested = True
                self.emit("cancel_requested")
            except Exception as exc:
                self.emit("cancel_error", message=str(exc))
        threading.Thread(target=worker, daemon=True).start()
