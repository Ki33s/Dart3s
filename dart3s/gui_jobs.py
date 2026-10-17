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

from .comfy3d import ComfyError, GenerationResult, Parameters, PROJECT, generate_3d, resume_3d


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
    paths = sorted((project / "outputs").rglob("generation.json"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    results = []
    for path in paths[:200]:
        if any(part in {'gui-integration-test', 'adaptive-verification', 'structure-budget-verification'}
               for part in path.relative_to(project / "outputs").parts):
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
        self.force_stopping = False
        self._lock = threading.RLock()
        self._cancel_event = threading.Event()
        self._worker_done = threading.Event()
        self._worker_done.set()
        self.pending_file = self.project / ".state/gui/pending.json"

    def emit(self, kind: str, **fields) -> None:
        self.events.put({"kind": kind, **fields})

    def run_native(self, script: str, args: list[str], timeout: float = 120, cancel_event=None) -> dict:
        python = self.project / "ComfyUI_windows_portable/python_embeded/python.exe"
        argv = [str(python), str(self.project / 'scripts' / script), *args]
        env = {**os.environ, 'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8'}
        if cancel_event is not None:
            from .cli_stream import run_cancellable
            def check():
                if cancel_event.is_set():
                    raise ComfyError('cancelled', 'Generation cancelled')
            proc = run_cancellable(argv, cwd=self.project, env=env, timeout=timeout, input_text=None, check_cancel=check)
        else:
            proc = subprocess.run(argv,
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
                args = ["--directory", str(self.project / ".state/gui/inputs"), "--width", "360", "--height", "360"]
                args += ["--clipboard"] if clipboard else ["--source", str(source)]
                image = self.run_native("gui_image_bridge.py", args)
                self.emit("image", image=image)
            except Exception as exc:
                self.emit("image_error", message=str(exc))
        threading.Thread(target=worker, daemon=True).start()

    def check_images(self, items, parameters=None) -> None:
        def worker():
            try:
                index = self.project / '.state/gui/risk-history.json'
                history_error = None
                try:
                    self.run_native('index_risk_history.py', ['--output', str(index)], timeout=60)
                except Exception as exc:
                    history_error = str(exc)
                for item in items:
                    try:
                        argv = ['--source', item['image']['path']]
                        if not history_error:
                            argv += ['--history-index', str(index), '--parameters', json.dumps(parameters)]
                        risk = self.run_native('check_image_risk.py', argv, timeout=30)
                        if history_error:
                            risk['reasons'].append('История запусков недоступна: ' + history_error)
                    except Exception as exc:
                        risk = {'level': 'unknown', 'reasons': ['Не удалось проверить: ' + str(exc)]}
                    self.emit('image_risk', item_id=item['id'], risk=risk)
            finally:
                self.emit('risk_check_done')
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
        token = self.run_token
        cancel_event = self._cancel_event = threading.Event()
        done = self._worker_done = threading.Event()
        def current():
            return self.run_token == token and not cancel_event.is_set()
        def emit_run(kind, **fields):
            with self._lock:
                if current():
                    self.emit(kind, job_token=token, **fields)
        info = {"image": image, "parameters": parameters.__dict__, "prompt_id": prompt_id,
                "preview_size": preview_size, "visual_progress": False}

        def progress(event):
            with self._lock:
                if not current():
                    return
                self.metadata_path = event.get("metadata_path", self.metadata_path)
                if event.get('status') == 'retrying_structure':
                    self.prompt_id = None
                    write_json(self.pending_file, {})
                if event.get("prompt_id"):
                    self.prompt_id = event["prompt_id"]
                    write_json(self.pending_file, {**info, "prompt_id": self.prompt_id,
                                                   "metadata_path": self.metadata_path})
                emit_run('progress', event=event)

        def worker():
            try:
                if prompt_id:
                    result = resume_3d(prompt_id, project=self.project, progress=progress, cancel_event=cancel_event)
                else:
                    result = generate_3d(image, **parameters.__dict__, project=self.project, progress=progress,
                                         visual_progress=False, preview_size=preview_size, cancel_event=cancel_event,
                                         _retry_allowed=lambda: not self.cancel_requested)
                with self._lock:
                    if not current():
                        return
                    emit_run('result', result=result.to_dict())
                    write_json(self.pending_file, {})
                # A preview failure cannot turn an existing GLB into a failed generation.
                emit_run("progress", event={"status": "rendering_preview"})
                try:
                    preview = self.render(result.glb_path, cancel_event=cancel_event)
                    emit_run("preview", preview=preview, glb=str(result.glb_path))
                except Exception as exc:
                    emit_run("preview_error", message=str(exc))
            except Exception as exc:
                if not current():
                    return
                if isinstance(exc, ComfyError):
                    code, message = exc.code, str(exc)
                    metadata_path = str(exc.metadata_path) if exc.metadata_path else self.metadata_path
                else:
                    code, message = type(exc).__name__, str(exc)
                    metadata_path = self.metadata_path
                terminal = not self.prompt_id or code in {"high_risk", "execution_error", "execution_interrupted", "cancelled", "server_died", "glb_invalid", "glb_output_missing"}
                with self._lock:
                    if current():
                        self.metadata_path = metadata_path
                        if terminal:
                            write_json(self.pending_file, {})
                        if code == 'high_risk':
                            self.prompt_id = None
                            emit_run('risk_skipped', message=message, metadata_path=self.metadata_path)
                        else:
                            emit_run("error", code=code, message=message, metadata_path=self.metadata_path,
                                     cancelled=code in {"cancelled", "execution_interrupted"}, resumable=not terminal)
            finally:
                with self._lock:
                    if current():
                        self.busy = False
                        emit_run("idle")
                done.set()
        threading.Thread(target=worker, daemon=True).start()

    def render(self, glb: Path, yaw: float = 45, *, elevation=15, zoom=1.0, output=None, cancel_event=None) -> str:
        output = Path(output) if output is not None else glb.with_suffix(".preview.png")
        report = self.run_native("gui_preview_bridge.py", [str(glb), "--out", str(output), "--size", str(self.preview_size),
                                                 "--yaw", str(yaw), '--elevation', str(elevation), '--zoom', str(zoom)],
                        timeout=180, **({'cancel_event': cancel_event} if cancel_event is not None else {}))
        write_json(output.with_suffix('.json'), report)
        return str(output)

    def preview(self, glb: str, yaw: float = 45) -> None:
        def worker():
            try:
                self.emit("preview", preview=self.render(Path(glb), yaw), glb=glb)
            except Exception as exc:
                self.emit("preview_error", message=str(exc))
        threading.Thread(target=worker, daemon=True).start()

    def simplify(self, glb: str, polygons: int) -> None:
        if self.busy:
            raise RuntimeError('Дождитесь окончания текущего задания')
        self.busy, self.prompt_id, self.cancel_requested = True, None, False
        self.run_token = token = uuid4().hex
        self._cancel_event = cancellation = threading.Event()
        self._worker_done = done = threading.Event()
        source = Path(glb).resolve()
        output = source.with_name(f'{source.stem}.lowpoly-{polygons}-{token[:8]}.glb')
        def worker():
            try:
                result = self.run_native('low_poly_copy.py', [str(source),'--out',str(output),'--polygons',str(polygons)],
                                         timeout=180, cancel_event=cancellation)
                if self.run_token == token and not cancellation.is_set():
                    self.emit('low_poly_result', result=result, job_token=token)
            except Exception as exc:
                if self.run_token == token and not cancellation.is_set():
                    self.emit('low_poly_error', message=str(exc), job_token=token)
            finally:
                with self._lock:
                    if self.run_token == token:
                        self.busy = False
                        if cancellation.is_set():
                            self.emit('low_poly_cancelled',job_token=token)
                        self.emit('idle',job_token=token)
                done.set()
        threading.Thread(target=worker,daemon=True).start()

    def cancel_copy(self):
        self.cancel_requested = True
        self._cancel_event.set()

    def cancel(self) -> None:
        if not self.busy or not self.prompt_id:
            return
        self.cancel_requested = True
        prompt_id = self.prompt_id
        token = self.run_token
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
                if self.run_token == token:
                    self.cancel_requested = True
                    self.emit("cancel_requested", job_token=token)
            except Exception as exc:
                if self.run_token == token:
                    self.emit("cancel_error", message=str(exc), job_token=token)
        threading.Thread(target=worker, daemon=True).start()

    def stop_server(self):
        completed = subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
            str(self.project / 'scripts/Stop-ComfyUI.ps1')], cwd=self.project, capture_output=True,
            text=True, encoding='utf-8', errors='replace', timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW)
        if completed.returncode:
            raise RuntimeError(completed.stderr.strip() or 'Не удалось завершить ComfyUI')
        return json.loads(completed.stdout)

    def shutdown(self):
        """Finish cleanup after the window closes; a non-daemon thread owns it."""
        with self._lock:
            self._cancel_event.set()
            self.run_token = uuid4().hex
            done = self._worker_done
        def cleanup():
            report = {'ok': False}
            try:
                done.wait(3)
                report = self.stop_server()
                write_json(self.pending_file, {})
            except Exception as exc:
                report['error'] = str(exc)
            finally:
                write_json(self.project / 'logs/gui-shutdown.json', report)
        thread = threading.Thread(target=cleanup, daemon=False, name='Dart3s shutdown')
        thread.start()
        return thread

    def force_cancel(self):
        if self.force_stopping:
            return
        with self._lock:
            self.force_stopping = True
            self.busy = True
            self._cancel_event.set()
            self.run_token = uuid4().hex  # reject already queued events of the abandoned job
            force_token = self.run_token
            done = self._worker_done
        self.emit('force_stopping', job_token=force_token)
        def worker():
            try:
                done.wait(3)
                report = self.stop_server()
                with self._lock:
                    write_json(self.pending_file, {})
                    self.prompt_id = None
                    self.metadata_path = None
                    self.busy = False
                    self.cancel_requested = False
                    self.force_stopping = False
                self.emit('force_cancelled', report=report, job_token=force_token)
            except Exception as exc:
                self.force_stopping = False
                self.emit('force_cancel_error', message=str(exc), job_token=force_token)
        threading.Thread(target=worker, daemon=True).start()
