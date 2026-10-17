"""Submit the project recipe locally and return a verified GLB artifact.

All graph mutations belong to scripts/build_workflow.py (Comfy recipe apply).
This adapter only transports inputs, submits jobs and collects their outputs.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import struct
import subprocess
import time
from typing import Any, Callable
from urllib.parse import urlencode, urlsplit
from urllib.request import ProxyHandler, build_opener
from uuid import uuid4

from .memory_cleanup import release_idle_models

PROJECT = Path(__file__).resolve().parents[1]


class ComfyError(RuntimeError):
    """Structured CLI failure; hint and details are retained for the caller."""

    def __init__(self, code: str, message: str, hint: str = "", details: Any = None):
        super().__init__(f"{code}: {message}" + (f"\n{hint}" if hint else ""))
        self.code, self.message, self.hint, self.details = code, message, hint, details
        self.metadata_path: Path | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "hint": self.hint,
                "details": self.details, "metadata_path": str(self.metadata_path) if self.metadata_path else None}


@dataclass(frozen=True)
class GenerationResult:
    glb_path: Path
    prompt_id: str
    metadata_path: Path
    output_paths: tuple[Path, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"status": "completed", "glb_path": str(self.glb_path), "prompt_id": self.prompt_id,
                "metadata_path": str(self.metadata_path), "output_paths": [str(p) for p in self.output_paths]}


@dataclass(frozen=True)
class Parameters:
    model: str = "pixal3d"
    polygons: int = 10000
    remesh_resolution: int = 256
    texture_size: int = 256
    structure_seed: int = 56
    shape_seed: int = 42
    texture_seed: int = 43
    quality: str = "standard"
    max_structure_elements: int = 0
    structure_retries: int = 2
    early_risk_seconds: int = 0
    mesh_mode: str = 'processed'

    def validate(self) -> None:
        if self.mesh_mode not in {'original', 'processed'}:
            raise ValueError('mesh_mode must be original or processed')
        if type(self.early_risk_seconds) is not int or (self.early_risk_seconds != 0 and not 60 <= self.early_risk_seconds <= 600):
            raise ValueError('early_risk_seconds must be 0 or an integer from 60 to 600')
        for key, maximum in (("max_structure_elements", 32768), ("structure_retries", 4)):
            value = getattr(self, key)
            if type(value) is not int or not 0 <= value <= maximum:
                raise ValueError(f"{key} must be an integer from 0 to {maximum}")
        if self.quality not in {"standard", "fast"}:
            raise ValueError("quality must be standard or fast")
        if self.model not in {"pixal3d", "trellis2"}:
            raise ValueError("model must be pixal3d or trellis2")
        for name in ("polygons", "remesh_resolution", "texture_size"):
            if isinstance(getattr(self, name), bool) or not isinstance(getattr(self, name), int) or getattr(self, name) <= 0:
                raise ValueError(f"{name} must be a positive integer")
        for name in ("structure_seed", "shape_seed", "texture_seed"):
            if isinstance(getattr(self, name), bool) or not isinstance(getattr(self, name), int) or not 0 <= getattr(self, name) < 2**64:
                raise ValueError(f"{name} must be an unsigned 64-bit integer")


def parse_envelope(stdout: str) -> dict[str, Any]:
    """Accept the CLI's JSON envelope and its optional NDJSON event stream."""
    try:
        value = json.loads(stdout)
        if isinstance(value, dict) and isinstance(value.get("ok"), bool):
            return value
    except json.JSONDecodeError:
        pass
    for line in reversed(stdout.splitlines()):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and isinstance(value.get("ok"), bool):
            return value
    raise ComfyError("cli_invalid_output", "Comfy CLI did not return a JSON envelope",
                     "Inspect the saved stdout/stderr logs; do not resubmit an unconfirmed job.")


def local_environment(project: Path, local_url: str) -> dict[str, str]:
    address = urlsplit(local_url)
    if address.scheme != "http" or address.hostname not in {"127.0.0.1", "localhost", "::1"} or address.username or address.password or address.path not in {"", "/"} or address.query or address.fragment:
        raise ValueError("local_url must be an HTTP loopback address, e.g. http://127.0.0.1:8188")
    # Validate a malformed port here instead of letting a child fail much later.
    _ = address.port
    state = project / ".state"
    state.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update(COMFY_WHERE="local", COMFY_LOCAL_URL=local_url, COMFY_NO_TELEMETRY="1",
               LOCALAPPDATA=str(state), PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    return env


def glb_download_urls(history: dict[str, Any], prompt_id: str, local_url: str) -> list[str]:
    """Adapt ComfyUI's 3D SavedResult entries to CLI output URLs.

    Comfy CLI currently omits Save3DAdvanced's `3d` history key. Its download
    command accepts explicit output URLs in stdin and handles the transfer.
    """
    record = history.get(prompt_id) or {}
    urls = []
    for output in (record.get("outputs") or {}).values():
        if not isinstance(output, dict):
            continue
        for item in output.get("3d") or []:
            if not isinstance(item, dict) or not isinstance(item.get("filename"), str) or not item["filename"].lower().endswith(".glb"):
                continue
            fields = {key: item[key] for key in ("filename", "subfolder", "type") if key in item}
            urls.append(local_url.rstrip("/") + "/view?" + urlencode(fields))
    return urls


def server_ready(local_url: str) -> bool:
    """Recognize ComfyUI rather than an arbitrary service occupying its port."""
    try:
        with build_opener(ProxyHandler({})).open(local_url.rstrip("/") + "/system_stats", timeout=2) as response:
            stats = json.load(response)
        return isinstance(stats, dict) and isinstance(stats.get("system"), dict) and isinstance(stats.get("devices"), list)
    except (OSError, ValueError):
        return False


class _Session:
    def __init__(self, *, project: Path, output_dir: Path | None, local_url: str,
                 progress: Callable[[dict[str, Any]], None] | None, cancel_event=None):
        self.project = project.resolve()
        self.output_dir = (output_dir or self.project / "outputs").resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        run_name = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex[:8]
        self.run_dir = self.output_dir / run_name
        self.run_dir.mkdir()
        self.metadata_path = self.run_dir / "generation.json"
        self.cli = self.project / ".venv-cli" / "Scripts" / "comfy.exe"
        self.python = self.project / ".venv-cli" / "Scripts" / "python.exe"
        self.env = local_environment(self.project, local_url)
        self.progress = progress
        self.cancel_event = cancel_event
        self.early_guard = None
        self.metadata: dict[str, Any] = {"schema": "dart3s-generation/1", "status": "starting",
            "where": "local", "local_url": local_url, "started_at": datetime.now(timezone.utc).isoformat(), "steps": []}
        self.save()

    def save(self) -> None:
        temp = self.metadata_path.with_suffix(".tmp")
        temp.write_text(json.dumps(self.metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(self.metadata_path)

    def state(self, status: str, **fields: Any) -> None:
        if status != 'failed':
            self.check_cancel()
        self.metadata.update(status=status, **fields)
        self.save()
        if self.progress:
            self.progress({"status": status, "metadata_path": str(self.metadata_path), **fields})

    def check_cancel(self):
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise ComfyError('cancelled', 'Generation was forcibly cancelled by the user')

    def check_watch(self):
        self.check_cancel()
        if self.early_guard is not None:
            self.early_guard.check()

    def watch_event(self, event):
        if self.early_guard is not None:
            self.early_guard.event(event)
        if self.progress:
            self.progress({'status': 'generating', 'event': event, 'metadata_path': str(self.metadata_path)})

    def ensure_server(self, *, auto_start: bool, startup_timeout: float) -> None:
        self.check_cancel()
        local_url = self.env["COMFY_LOCAL_URL"]
        if server_ready(local_url):
            self.metadata["server"] = {"started_by_adapter": False, "url": local_url}
            self.save()
            return
        if not auto_start:
            raise ComfyError("server_not_running", "The local ComfyUI server is unavailable",
                             "Start ComfyUI, or remove --no-start-server / -NoStartServer.")
        portable = self.project / "ComfyUI_windows_portable"
        portable_python = portable / "python_embeded/python.exe"
        main = portable / "ComfyUI/main.py"
        wrapper = self.project / "scripts/comfy_server.py"
        if not portable_python.is_file() or not main.is_file() or not wrapper.is_file():
            raise ComfyError("portable_not_installed", f"ComfyUI portable is missing from {portable}",
                             "Complete the portable installation before generating.")
        address = urlsplit(local_url)
        bind = "::1" if address.hostname == "::1" else "127.0.0.1"
        argv = [str(portable_python), "-s", str(wrapper), "--windows-standalone-build",
                "--listen", bind, "--port", str(address.port or 8188),
                "--preview-method", "none", "--disable-auto-launch"]
        logs = self.project / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        stdout_path, stderr_path = logs / "comfyui.stdout.log", logs / "comfyui.stderr.log"
        self.state("starting_server", server={"started_by_adapter": True, "url": local_url,
                   "argv": argv, "stdout_log": str(stdout_path), "stderr_log": str(stderr_path)})
        try:
            self.check_cancel()
            with stdout_path.open("ab") as stdout, stderr_path.open("ab") as stderr:
                marker = ("\n[Dart3s startup " + datetime.now(timezone.utc).isoformat() + "]\n").encode("utf-8")
                stdout.write(marker)
                stderr.write(marker)
                stdout.flush()
                stderr.flush()
                process = subprocess.Popen(argv, cwd=portable, env=self.env, stdout=stdout, stderr=stderr,
                                           stdin=subprocess.DEVNULL, shell=False,
                                           creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        except OSError as exc:
            raise ComfyError("server_start_failed", f"Cannot launch portable ComfyUI: {exc}",
                             "Inspect the portable Python installation.") from exc
        self.metadata["server"]["pid"] = process.pid
        self.save()
        deadline = time.monotonic() + startup_timeout
        while True:
            if self.cancel_event is not None and self.cancel_event.is_set() and process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            self.check_cancel()
            if self.early_guard is not None:
                self.early_guard.check()
            if server_ready(local_url):
                self.state("server_ready", server=self.metadata["server"])
                return
            exit_code = process.poll()
            if exit_code is not None:
                raise ComfyError("server_start_failed", f"Portable ComfyUI exited with code {exit_code}",
                                 f"Inspect {stderr_path} and {stdout_path}.",
                                 {"pid": process.pid, "exit_code": exit_code, "stdout_log": str(stdout_path), "stderr_log": str(stderr_path)})
            if time.monotonic() >= deadline:
                raise ComfyError("server_start_timeout", f"ComfyUI did not become ready within {startup_timeout:g}s",
                                 f"Inspect {stderr_path}; the recorded server process may still be starting.",
                                 {"pid": process.pid, "stdout_log": str(stdout_path), "stderr_log": str(stderr_path)})
            time.sleep(1)

    def command(self, name: str, args: list[str], *, timeout: float = 120,
                builder: bool = False, input_text: str | None = None) -> dict[str, Any]:
        self.check_cancel()
        executable = self.python if builder else self.cli
        if not executable.is_file():
            raise ComfyError("cli_not_installed", f"Missing executable: {executable}", "Install Comfy CLI in .venv-cli first.")
        argv = [str(executable), *args] if builder else [str(executable), "--json", *args]
        step: dict[str, Any] = {"name": name, "argv": argv}
        self.metadata["steps"].append(step)
        self.save()
        try:
            if name == "watch" and (self.progress is not None or self.early_guard is not None):
                from .cli_stream import run_stream
                argv[1] = "--json-stream"
                step["argv"] = argv
                self.save()
                completed = run_stream(argv, cwd=self.project, env=self.env, timeout=timeout,
                                       stdout_path=self.run_dir / f"{name}.stdout.log",
                                       stderr_path=self.run_dir / f"{name}.stderr.log",
                                       on_event=self.watch_event, check_cancel=self.check_watch)
            elif self.cancel_event is not None:
                from .cli_stream import run_cancellable
                completed = run_cancellable(argv, cwd=self.project, env=self.env, timeout=timeout,
                                            input_text=input_text, check_cancel=self.check_cancel)
            else:
                completed = subprocess.run(argv, cwd=self.project, env=self.env, capture_output=True,
                                       text=True, encoding="utf-8", errors="replace", timeout=timeout, shell=False,
                                       input=input_text,
                                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        except subprocess.TimeoutExpired as exc:
            for channel in ("stdout", "stderr"):
                value = getattr(exc, channel, None) or ""
                if isinstance(value, bytes):
                    value = value.decode("utf-8", errors="replace")
                (self.run_dir / f"{name}.{channel}.log").write_text(value, encoding="utf-8")
            step["timeout"] = timeout
            self.save()
            raise ComfyError("client_timeout", f"{name} exceeded the client wait of {timeout}s",
                             "An already submitted job may still be running. Resume its prompt_id instead of submitting again.",
                             {"prompt_id": self.metadata.get("prompt_id")}) from exc
        self.check_cancel()
        for channel in ("stdout", "stderr"):
            (self.run_dir / f"{name}.{channel}.log").write_text(getattr(completed, channel), encoding="utf-8")
        step["exit_code"] = completed.returncode
        self.save()
        if builder:
            if completed.returncode:
                # The builder retains the actual `comfy workflow apply`
                # envelope alongside its output, even when apply fails.
                if "--output" in args:
                    apply_log = Path(args[args.index("--output") + 1]).with_suffix(".apply.json")
                    try:
                        apply_result = json.loads(apply_log.read_text(encoding="utf-8"))
                    except (OSError, ValueError):
                        apply_result = {}
                    error = apply_result.get("error") or {}
                    if error:
                        raise ComfyError(error.get("code", "workflow_build_failed"),
                                         error.get("message", "The Comfy recipe could not be applied"),
                                         error.get("hint", "Inspect the workflow apply log."), error.get("details"))
                raise ComfyError("workflow_build_failed", "The Comfy recipe could not be applied", "Inspect build.stderr.log and build.stdout.log.")
            try:
                result = json.loads(completed.stdout)
            except json.JSONDecodeError as exc:
                raise ComfyError("builder_invalid_output", "Workflow builder returned invalid JSON", "Inspect build.stdout.log.") from exc
            if not isinstance(result, dict):
                raise ComfyError("builder_invalid_output", "Workflow builder did not return an object")
            return result
        result = parse_envelope(completed.stdout)
        if completed.returncode != 0 or result["ok"] is not True or result.get("error"):
            error = result.get("error") or {}
            raise ComfyError(error.get("code", "cli_failed"), error.get("message", f"{name} failed ({completed.returncode})"),
                             error.get("hint", "Inspect the command logs."), error.get("details"))
        return result.get("data") or {}

    def fail(self, exc: Exception) -> None:
        if isinstance(exc, ComfyError):
            exc.metadata_path = self.metadata_path
            error = exc.to_dict()
        else:
            error = {"code": type(exc).__name__, "message": str(exc)}
        self.state("failed", error=error)

    def extra_model_urls(self, prompt_id: str) -> list[str]:
        local_url = self.env["COMFY_LOCAL_URL"]
        url = local_url.rstrip("/") + "/history/" + prompt_id
        try:
            # Read loopback metadata without an inherited network proxy.
            # Comfy CLI still handles all artifact downloading.
            with build_opener(ProxyHandler({})).open(url, timeout=30) as response:
                history = json.load(response)
        except (OSError, ValueError) as exc:
            raise ComfyError("glb_history_unavailable", "Cannot read completed local 3D job history",
                             "Check local ComfyUI and resume the existing prompt_id.", {"prompt_id": prompt_id}) from exc
        (self.run_dir / "history.json").write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
        if not isinstance(history, dict):
            raise ComfyError("glb_history_invalid", "Local history was not an object", "Inspect history.json.")
        return glb_download_urls(history, prompt_id, local_url)

    def collect(self, prompt_id: str, wait_timeout: float) -> GenerationResult:
        self.state("submitted", prompt_id=prompt_id)
        watched = self.command("watch", ["jobs", "watch", prompt_id, "--where", "local", "--timeout", "120"], timeout=wait_timeout)
        self.state("downloading", prompt_id=prompt_id)
        urls = list(watched.get("outputs") or [])
        for url in self.extra_model_urls(prompt_id):
            if url not in urls:
                urls.append(url)
        download_input = json.dumps({"ok": True, "command": "dart3s 3d history", "where": "local",
                                     "data": {"prompt_id": prompt_id, "outputs": urls}, "error": None})
        data = self.command("download", ["download", "--where", "local", "--out-dir", str(self.run_dir)], input_text=download_input)
        paths: list[Path] = []
        for item in data.get("files", []):
            path = Path(item["path"])
            if not path.is_absolute():
                path = self.project / path
            paths.append(path.resolve())
        glbs = [p for p in paths if p.suffix.lower() == ".glb" and p.is_file()]
        if not glbs:
            raise ComfyError("glb_output_missing", "The completed job produced no downloaded GLB file",
                             "Inspect download.stdout.log and ComfyUI job history; a completed job alone is not a model.", {"prompt_id": prompt_id})
        glb = glbs[0]
        with glb.open("rb") as stream:
            header = stream.read(12)
        if len(header) != 12 or header[:4] != b"glTF" or struct.unpack("<I", header[4:8])[0] != 2 or struct.unpack("<I", header[8:12])[0] != glb.stat().st_size:
            raise ComfyError("glb_invalid", f"Invalid GLB 2.0 header or file length: {glb}", "Inspect the exporter result.")
        result = GenerationResult(glb, prompt_id, self.metadata_path, tuple(paths))
        self.state("releasing_models", prompt_id=prompt_id)
        self.metadata["memory_cleanup"] = release_idle_models(self.env["COMFY_LOCAL_URL"])
        self.state("completed", **{k: v for k, v in result.to_dict().items() if k != "status"},
                   finished_at=datetime.now(timezone.utc).isoformat())
        return result


def generate_3d(image: str | Path, *, early_risk_seconds: int = 0, _retry_allowed=None, _early_limits=None, **kwargs):
    """Generate normally, or use one bounded structure retry when early checking is enabled."""
    if type(early_risk_seconds) is not int:
        raise ValueError('early_risk_seconds must be an integer')
    if early_risk_seconds:
        from .adaptive_generation import generate_adaptive
        return generate_adaptive(image, seconds=early_risk_seconds, generate_once=_generate_once,
                                 retry_allowed=_retry_allowed or (lambda: True), limits=_early_limits, **kwargs)
    return _generate_once(image, **kwargs)


def _generate_once(image: str | Path, *, model: str = "pixal3d", polygons: int = 10000,
                remesh_resolution: int = 256, texture_size: int = 256, structure_seed: int = 56,
                shape_seed: int = 42, texture_seed: int = 43, output_dir: str | Path | None = None,
                local_url: str = "http://127.0.0.1:8188", project: str | Path = PROJECT,
                wait_timeout: float = 7200, prepare_only: bool = False,
                auto_start: bool = True, startup_timeout: float = 180,
                object_info: str | Path | None = None,
                visual_progress: bool = False, preview_size: int = 256, quality: str = "standard",
                progress: Callable[[dict[str, Any]], None] | None = None, cancel_event=None,
                max_structure_elements: int = 0, structure_retries: int = 2,
                early_risk_seconds: int = 0, mesh_mode: str = 'processed', _early_guard=None) -> GenerationResult | dict[str, Any]:
    """Generate a GLB. shape_seed is shared by the shape and texture-shape samplers.

    prepare_only writes a parameterized workflow without upload or submission;
    its image basename must already exist in ComfyUI input before a later run.
    visual_progress observes existing structure, meshes and base-color texture;
    preview_size controls only their CPU-rendered snapshots, not model quality.
    """
    parameters = Parameters(model, polygons, remesh_resolution, texture_size, structure_seed, shape_seed, texture_seed, quality,
                            max_structure_elements, structure_retries, early_risk_seconds, mesh_mode)
    parameters.validate()
    if not isinstance(visual_progress, bool):
        raise ValueError("visual_progress must be a boolean")
    if isinstance(preview_size, bool) or not isinstance(preview_size, int) or preview_size not in (128, 256, 512):
        raise ValueError("preview_size must be 128, 256 or 512")
    if wait_timeout <= 0:
        raise ValueError("wait_timeout must be positive")
    if startup_timeout <= 0:
        raise ValueError("startup_timeout must be positive")
    image_path = Path(image).resolve()
    if not image_path.is_file():
        raise FileNotFoundError(image_path)
    session = _Session(project=Path(project), output_dir=Path(output_dir) if output_dir else None,
                       local_url=local_url, progress=progress, cancel_event=cancel_event)
    session.metadata.update(image=str(image_path), parameters=asdict(parameters))
    session.early_guard = _early_guard
    if _early_guard is not None:
        _early_guard.timing_dir = session.run_dir
    preview_fields = {}
    if visual_progress:
        preview_directory = session.run_dir / "previews"
        preview_directory.mkdir()
        preview_fields = {"preview_directory": str(preview_directory), "preview_size": preview_size}
        session.metadata.update(**preview_fields)
    try:
        if not (prepare_only and object_info is not None):
            session.ensure_server(auto_start=auto_start, startup_timeout=startup_timeout)
        if prepare_only:
            server_image = image_path.name
        else:
            session.state("uploading")
            upload = session.command("upload", ["upload", str(image_path), "--where", "local", "--no-overwrite"])
            uploads = upload.get("uploads", [])
            if len(uploads) != 1 or not uploads[0].get("cloud_name"):
                raise ComfyError("upload_invalid_output", "Upload did not return one server image name")
            server_image = "/".join(part for part in (uploads[0].get("subfolder", ""), uploads[0]["cloud_name"]) if part)
        workflow = session.run_dir / "workflow.json"
        args = [str(session.project / "scripts" / "build_workflow.py"), "--image", server_image,
                "--output", str(workflow)]
        for key, value in asdict(parameters).items():
            args.extend(["--" + key.replace("_", "-"), str(value)])
        if object_info is not None:
            args.extend(["--object-info", str(Path(object_info).resolve())])
        if visual_progress:
            args.extend(["--preview-directory", str(preview_directory), "--preview-size", str(preview_size)])
        session.state("building", server_image=server_image, **preview_fields)
        built = session.command("build", args, builder=True, timeout=300)
        if not workflow.is_file():
            raise ComfyError("workflow_missing", "Builder did not write the requested workflow")
        session.metadata["build"] = built
        if prepare_only:
            session.state("prepared", workflow=str(workflow))
            return {"status": "prepared", "workflow": str(workflow), "metadata_path": str(session.metadata_path),
                    "server_image": server_image, "parameters": asdict(parameters), **preview_fields}
        session.state("submitting", workflow=str(workflow))
        submitted = session.command("submit", ["run", "--workflow", str(workflow), "--where", "local", "--no-watch", "--no-notify"])
        prompt_id = submitted.get("prompt_id")
        if not isinstance(prompt_id, str) or not prompt_id:
            raise ComfyError("prompt_id_missing", "Submit returned no prompt_id", "Inspect submit.stdout.log before attempting another submission.")
        return session.collect(prompt_id, wait_timeout)
    except Exception as exc:
        session.fail(exc)
        raise


def resume_3d(prompt_id: str, *, output_dir: str | Path | None = None, local_url: str = "http://127.0.0.1:8188",
              project: str | Path = PROJECT, wait_timeout: float = 7200,
              auto_start: bool = True, startup_timeout: float = 180,
              progress: Callable[[dict[str, Any]], None] | None = None, cancel_event=None) -> GenerationResult:
    """Watch and collect an existing local job; never submit it a second time."""
    if not prompt_id or not isinstance(prompt_id, str) or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for c in prompt_id):
        raise ValueError("prompt_id is required")
    if wait_timeout <= 0:
        raise ValueError("wait_timeout must be positive")
    if startup_timeout <= 0:
        raise ValueError("startup_timeout must be positive")
    session = _Session(project=Path(project), output_dir=Path(output_dir) if output_dir else None,
                       local_url=local_url, progress=progress, cancel_event=cancel_event)
    try:
        session.ensure_server(auto_start=auto_start, startup_timeout=startup_timeout)
        return session.collect(prompt_id, wait_timeout)
    except Exception as exc:
        session.fail(exc)
        raise
