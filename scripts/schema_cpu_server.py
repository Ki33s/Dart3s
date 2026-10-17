"""Temporary CPU-only schema server; no GPU capability queries are performed.

Used to inspect actual core node schemas while ROCm installation is in progress.
This process is never used for production generation or GPU verification.
"""
import ctypes
from pathlib import Path
import runpy
import sys

ctypes.windll.kernel32.SetErrorMode(0x0001 | 0x0002 | 0x8000)
import torch

# --cpu does not prevent comfy-kitchen's import-time GPU capability query.
# Avoid that query only in this explicitly CPU-only diagnostic process.
torch.cuda.is_available = lambda: False
torch.cuda.device_count = lambda: 0
core = Path(__file__).resolve().parents[1] / 'ComfyUI_windows_portable/ComfyUI'
sys.path.insert(0, str(core))
sys.argv = [str(core / 'main.py'), '--cpu', '--listen', '127.0.0.1', '--port', '8189',
            '--preview-method', 'none', '--disable-auto-launch']
runpy.run_path(str(core / 'main.py'), run_name='__main__')
