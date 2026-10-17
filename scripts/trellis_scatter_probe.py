"""Bounded test of Trellis2's exact padded boolean scatter on gfx1032."""
import ctypes
import json
import os
import threading
import time

ctypes.windll.kernel32.SetErrorMode(0x0001 | 0x0002 | 0x8000)


def deadline():
    time.sleep(55)
    print(json.dumps({'error': '55 second deadline'}), flush=True)
    os._exit(124)


threading.Thread(target=deadline, daemon=True).start()
import torch

torch.cuda.set_per_process_memory_fraction(0.01)
torch.cuda.reset_peak_memory_stats()
for dtype in (torch.float32, torch.float16):
    for count in (7777, 7331):
        mask = torch.arange(7777, device='cuda').unsqueeze(0) < count
        src = torch.randn((count, 32), device='cuda', dtype=dtype)
        for repetition in range(3):
            out = torch.zeros((1, 7777, 32), device='cuda', dtype=dtype)
            # Pre-synchronize to separate indexing itself from earlier model work.
            torch.cuda.synchronize()
            start = time.perf_counter()
            out[mask] = src
            torch.cuda.synchronize()
            elapsed = time.perf_counter() - start
            expected = torch.zeros((1, 7777, 32), device='cuda', dtype=dtype)
            expected[0, :count] = src
            assert torch.equal(out, expected)
            print(json.dumps({'dtype': str(dtype), 'valid_tokens': count,
                              'repeat': repetition, 'seconds': elapsed,
                              'exact': True}), flush=True)
print(json.dumps({'ok': True, 'peak_gpu_bytes': torch.cuda.max_memory_allocated()}), flush=True)
