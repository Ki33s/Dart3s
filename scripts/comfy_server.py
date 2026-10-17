"""Run portable ComfyUI headlessly with native crash diagnostics in stderr.

Windows error dialogs would block an unattended server after a native failure.
Suppress those dialogs in this process; faulthandler retains the Python stack.
GPU APIs and ComfyUI startup behavior are left intact.
"""
from __future__ import annotations

import faulthandler
from pathlib import Path
import runpy
import sys


def main() -> None:
    if sys.platform == "win32":
        import ctypes
        # SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX | SEM_NOOPENFILEERRORBOX
        ctypes.windll.kernel32.SetErrorMode(0x0001 | 0x0002 | 0x8000)
    faulthandler.enable()
    core = Path(__file__).resolve().parents[1] / "ComfyUI_windows_portable/ComfyUI"
    entrypoint = core / "main.py"
    if not entrypoint.is_file():
        raise FileNotFoundError(f"ComfyUI portable entrypoint is missing: {entrypoint}")
    sys.path.insert(0, str(core))
    sys.argv = [str(entrypoint), *sys.argv[1:]]
    runpy.run_path(str(entrypoint), run_name="__main__")


if __name__ == "__main__":
    main()
