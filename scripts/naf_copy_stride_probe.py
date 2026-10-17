"""Test NAF's exact 1024 CPU output strides without filling the 2 GiB allocation."""
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

torch.cuda.set_per_process_memory_fraction(0.01)
torch.cuda.reset_peak_memory_stats()
torch.manual_seed(42)
# Only the 8 MiB tile and a few sentinel scalars are written in this allocation.
out = torch.empty((1, 4, 256, 1024, 1024), dtype=torch.float16)
raw = torch.randn((1, 4, 128, 128, 64), device='cuda', dtype=torch.float16)
src = raw.permute(0, 1, 4, 2, 3)
target = (slice(None), slice(None), slice(64, 128), slice(512, 640), slice(384, 512))
out[0, 0, 63, 512, 384] = -77
out[0, 0, 128, 512, 384] = -88
torch.cuda.synchronize()
print(json.dumps({"phase": "direct", "source_stride": src.stride(),
                  "destination_stride": out[target].stride()}), flush=True)
start = time.perf_counter()
out[target] = src
seconds = time.perf_counter() - start
print(json.dumps({"phase": "direct_done", "seconds": seconds}), flush=True)
# This copies only the selected tile, preserving the >2 GiB destination layout.
direct_tile = out[target].clone()
print(json.dumps({"phase": "staged"}), flush=True)
start = time.perf_counter()
chunk_cpu = src.contiguous().to('cpu')
out[target] = chunk_cpu
seconds = time.perf_counter() - start
print(json.dumps({"phase": "staged_done", "seconds": seconds,
                  "equal": torch.equal(out[target], direct_tile),
                  "sentinels_preserved": bool(out[0, 0, 63, 512, 384] == -77)
                      and bool(out[0, 0, 128, 512, 384] == -88),
                  "peak_gpu_bytes": torch.cuda.max_memory_allocated()}), flush=True)
assert torch.equal(out[target], direct_tile)
assert bool(out[0, 0, 63, 512, 384] == -77)
assert bool(out[0, 0, 128, 512, 384] == -88)
