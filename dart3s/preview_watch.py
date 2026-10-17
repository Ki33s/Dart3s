"""Read atomic milestone manifests; never request extra ComfyUI work."""
from __future__ import annotations

import json
from pathlib import Path
import threading

STAGES = ('structure', 'geometry', 'prepared', 'textured')


class PreviewMonitor:
    def __init__(self, project: Path, emit, token: str):
        self.outputs = (project / 'outputs').resolve()
        self.emit, self.token = emit, token
        self.directory = None
        self.revision = -1
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.lock = threading.Lock()

    def configure(self, directory: str | Path) -> None:
        path = Path(directory).resolve()
        if not path.is_relative_to(self.outputs):
            return
        with self.lock:
            if self.directory != path:
                self.directory, self.revision = path, -1

    def poll(self) -> None:
        with self.lock:
            directory = self.directory
            if directory is None:
                return
            try:
                record = json.loads((directory / 'latest.json').read_text(encoding='utf-8'))
                revision = record['revision']
                if not isinstance(revision, int) or revision <= self.revision or record['stage'] not in STAGES:
                    return
                image = Path(record['image']).resolve()
                if (not image.is_relative_to(directory) or image.suffix.lower() != '.png'
                        or not image.is_file() or image.stat().st_size > 4*1024*1024):
                    return
                self.revision = revision
                record['image'] = str(image)
            except (OSError, ValueError, KeyError, TypeError):
                return
        self.emit('intermediate_preview', snapshot=record, token=self.token)

    def start(self) -> None:
        self.thread.start()

    def _run(self) -> None:
        while not self.stop_event.wait(1):
            self.poll()

    def close(self) -> None:
        self.stop_event.set()
        if self.thread.is_alive():
            self.thread.join(timeout=2)
        self.poll()
