"""Read CLI NDJSON while a job is running, with a bounded client wait."""
from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import subprocess
import threading
import time


def kill_child(process):
    """Only terminate the subprocess tree created by this client."""
    if process.poll() is None:
        if os.name == 'nt':
            subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           creationflags=subprocess.CREATE_NO_WINDOW, timeout=5)
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)


def run_cancellable(argv, *, cwd, env, timeout, input_text, check_cancel):
    check_cancel()
    process = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8',
                               errors='replace', shell=False,
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    deadline = time.monotonic() + timeout
    first = True
    try:
        while True:
            check_cancel()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(argv, timeout)
            try:
                stdout, stderr = process.communicate(input=input_text if first else None, timeout=min(.1, remaining))
                check_cancel()
                return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)
            except subprocess.TimeoutExpired:
                first = False
    finally:
        kill_child(process)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream:
                stream.close()


def run_stream(argv, *, cwd, env, timeout, stdout_path: Path, stderr_path: Path, on_event, check_cancel=None):
    lines = []
    pending = queue.Queue()
    deadline = time.monotonic() + timeout
    if check_cancel:
        check_cancel()
    with stdout_path.open("w", encoding="utf-8") as log, stderr_path.open("w", encoding="utf-8") as errors:
        process = subprocess.Popen(argv, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=errors,
                                   stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
                                   shell=False, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        def reader():
            try:
                for line in process.stdout:
                    pending.put(line)
            finally:
                pending.put(None)
        thread = threading.Thread(target=reader, daemon=True)
        thread.start()
        ended = False
        try:
            while not ended or process.poll() is None:
                if check_cancel:
                    check_cancel()
                if time.monotonic() >= deadline:
                    raise subprocess.TimeoutExpired(argv, timeout, output="".join(lines))
                try:
                    line = pending.get(timeout=min(.1, max(.001, deadline-time.monotonic())))
                except queue.Empty:
                    continue
                if line is None:
                    ended = True
                    continue
                lines.append(line)
                log.write(line)
                log.flush()
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if isinstance(event, dict) and event.get("type") in {"executing", "progress", "executed", "execution_cached"}:
                    on_event(event)
            process.wait()
        finally:
            kill_child(process)
            thread.join(timeout=1)
            if process.stdout:
                process.stdout.close()
    return subprocess.CompletedProcess(argv, process.returncode, "".join(lines),
                                       stderr_path.read_text(encoding="utf-8", errors="replace"))
