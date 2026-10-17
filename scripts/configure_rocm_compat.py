"""Use ComfyUI's existing dequantized matmul path on RX 6600/gfx1032.

RDNA2's HIP kitchen backend declines quantized GEMMs. The eager fallback uses
torch._int_mm, which fails with HIPBLAS_STATUS_INVALID_VALUE on this machine.
Mark only gfx1032 as unsupported for native INT8 compute; ComfyUI retains packed
weights and dequantizes each layer using its existing memory management.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "ComfyUI_windows_portable/ComfyUI/comfy/model_management.py"
OLD = '''def supports_int8_compute(device=None):
    # The eager comfy_kitchen backend implements int8 weight-only quantized
'''
NEW = '''def supports_int8_compute(device=None):
    # Dart3s: gfx1032 HIP has no native INT8 GEMM. Use the existing per-layer
    # dequantized matmul path rather than the failing eager torch._int_mm.
    if is_amd() and (device is None or is_device_cuda(device)):
        arch = torch.cuda.get_device_properties(device).gcnArchName.split(":")[0]
        if arch == "gfx1032":
            return False
    # The eager comfy_kitchen backend implements int8 weight-only quantized
'''


def configure(*, check: bool = False) -> dict:
    target = TARGET.resolve()
    target.relative_to(ROOT.resolve())
    original = target.read_bytes()
    source = original.decode("utf-8")
    newline = "\r\n" if "\r\n" in source else "\n"
    old, new = OLD.replace("\n", newline), NEW.replace("\n", newline)
    installed = source.count(new) == 1
    if not installed and source.count(old) != 1:
        raise RuntimeError("Upstream supports_int8_compute changed; review compatibility before applying.")
    updated = source if installed else source.replace(old, new, 1)
    compile(updated, str(target), "exec")
    result = {"ok": installed or not check, "changed": not installed and not check,
              "path": str(target), "architecture": "gfx1032",
              "policy": "ComfyUI per-layer dequantized matmul"}
    if not installed and not check:
        backup = target.with_suffix(".py.pre-dart3s")
        if not backup.exists():
            backup.write_bytes(original)
        target.write_bytes(updated.encode("utf-8"))
        result["backup"] = str(backup)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        result = configure(check=args.check)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        raise SystemExit(0 if result["ok"] else 1)
    except (OSError, ValueError, RuntimeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        raise SystemExit(1)
