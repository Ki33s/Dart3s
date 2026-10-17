"""Bounded INT8 GEMM probe and exact FP32/chunked integer fallback test."""
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

torch.cuda.set_per_process_memory_fraction(0.03)
torch.cuda.reset_peak_memory_stats()
print(json.dumps({"torch": torch.__version__, "hip": torch.version.hip,
                  "prefer_hipblaslt": os.environ.get('TORCH_BLAS_PREFER_HIPBLASLT'),
                  "architecture": torch.cuda.get_device_properties(0).gcnArchName}), flush=True)


def exact_fp32_chunks(a, b):
    # Each partial integer sum is <= 1024*128*128 = 2**24, exact in FP32.
    out = torch.zeros((a.shape[0], b.shape[1]), device=a.device, dtype=torch.int32)
    for k0 in range(0, a.shape[1], 1024):
        partial = a[:, k0:k0 + 1024].float() @ b[k0:k0 + 1024].float()
        out.add_(partial.to(torch.int32))
    return out


for seed in (42, 123):
    for m, k, n in ((32, 64, 64), (32, 1024, 64), (32, 2048, 64)):
        generator = torch.Generator().manual_seed(seed)
        a_cpu = torch.randint(-128, 128, (m, k), dtype=torch.int8, generator=generator)
        b_cpu = torch.randint(-128, 128, (k, n), dtype=torch.int8, generator=generator)
        a, b = a_cpu.cuda(), b_cpu.cuda()
        expected = a_cpu.to(torch.int64) @ b_cpu.to(torch.int64)
        try:
            start = time.perf_counter()
            native = torch._int_mm(a, b)
            torch.cuda.synchronize()
            print(json.dumps({"seed": seed, "shape": [m, k, n], "native": "passed",
                              "seconds": time.perf_counter() - start,
                              "exact": torch.equal(native.cpu().to(torch.int64), expected)}), flush=True)
        except Exception as exc:
            print(json.dumps({"seed": seed, "shape": [m, k, n], "native": "failed",
                              "error": str(exc)}), flush=True)
        start = time.perf_counter()
        result = exact_fp32_chunks(a, b)
        torch.cuda.synchronize()
        assert torch.equal(result.cpu().to(torch.int64), expected)
        print(json.dumps({"seed": seed, "shape": [m, k, n], "fallback": "exact",
                          "seconds": time.perf_counter() - start}), flush=True)

# Worst case checks the FP32 exactness boundary and cross-chunk accumulation.
for k in (1024, 2049):
    a = torch.full((32, k), -128, device='cuda', dtype=torch.int8)
    b = torch.full((k, 64), -128, device='cuda', dtype=torch.int8)
    result = exact_fp32_chunks(a, b)
    assert bool((result == k * 16384).all())
    print(json.dumps({"extreme_k": k, "fallback": "exact"}), flush=True)
print(json.dumps({"ok": True, "peak_gpu_bytes": torch.cuda.max_memory_allocated()}), flush=True)
