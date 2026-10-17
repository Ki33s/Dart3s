"""Bounded comparison of the CPU destination copy used by NAF.

Does not load models, initialize ComfyUI, or change the running server.
"""
import ctypes
import json
import os
import threading
import time

ctypes.windll.kernel32.SetErrorMode(0x0001 | 0x0002 | 0x8000)


def deadline():
    time.sleep(55)
    print(json.dumps({"error": "55 second deadline"}), flush=True)
    os._exit(124)


threading.Thread(target=deadline, daemon=True).start()
import torch

print(json.dumps({"torch": torch.__version__, "threads": torch.get_num_threads(),
                  "gpu": torch.cuda.get_device_name(), "aimdo": False}), flush=True)
torch.cuda.set_per_process_memory_fraction(0.01)
torch.cuda.reset_peak_memory_stats()
for tile in (16, 32):
    # CPU outputs total 64 MiB; GPU inputs and copy temporaries < 4 MiB.
    original = torch.arange(4 * tile * tile * 64, dtype=torch.int32)
    original = (original % 997).to(torch.float16).reshape(1, 4, tile, tile, 64)
    src = original.to('cuda').permute(0, 1, 4, 2, 3)
    torch.cuda.synchronize()
    direct = torch.full((1, 4, 256, 128, 128), -1, dtype=torch.float16)
    staged = torch.full_like(direct, -1)
    target = (slice(None), slice(None), slice(64, 128),
              slice(37, 37 + tile), slice(41, 41 + tile))
    print(json.dumps({"tile": tile, "phase": "direct",
                      "src_stride": src.stride(), "dst_stride": direct[target].stride()}), flush=True)
    start = time.perf_counter()
    direct[target] = src
    direct_seconds = time.perf_counter() - start
    print(json.dumps({"tile": tile, "phase": "staged"}), flush=True)
    start = time.perf_counter()
    chunk_cpu = src.contiguous().to('cpu')
    staged[target] = chunk_cpu
    staged_seconds = time.perf_counter() - start
    assert torch.equal(direct, staged)
    assert torch.equal(staged[target], original.permute(0, 1, 4, 2, 3))
    assert bool((staged[:, :, :64] == -1).all())
    print(json.dumps({"tile": tile, "direct_seconds": direct_seconds,
                      "staged_seconds": staged_seconds, "equal": True,
                      "peak_gpu_bytes": torch.cuda.max_memory_allocated()}), flush=True)
    del original, src, direct, staged, chunk_cpu
print(json.dumps({"ok": True}), flush=True)
