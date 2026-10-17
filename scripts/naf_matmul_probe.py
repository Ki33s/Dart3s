"""Bounded test of one NAF 128-square attention-value chunk on ROCm."""
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

torch.cuda.set_per_process_memory_fraction(0.18)
torch.cuda.reset_peak_memory_stats()
torch.manual_seed(42)
print(json.dumps({"torch": torch.__version__, "phase": "allocate"}), flush=True)
attn = torch.rand((1, 4, 16384, 1, 81), device='cuda', dtype=torch.float16)
attn = attn / attn.sum(dim=-1, keepdim=True)
# F.unfold outputs [B*n, chunk*81, t], then NAF view/permute creates this stride.
raw = torch.randn((4, 64 * 81, 16384), device='cuda', dtype=torch.float16)
value = raw.view(1, 4, 64, 81, 16384).permute(0, 1, 4, 3, 2)
torch.cuda.synchronize()
print(json.dumps({"phase": "matmul", "shape": list(value.shape),
                  "stride": value.stride(), "allocated": torch.cuda.memory_allocated()}), flush=True)
start = time.perf_counter()
result = torch.matmul(attn, value).squeeze(-2)
torch.cuda.synchronize()
seconds = time.perf_counter() - start
reference = result.cpu()
del result
torch.cuda.empty_cache()
print(json.dumps({"phase": "matmul_done", "seconds": seconds,
                  "peak_gpu_bytes": torch.cuda.max_memory_allocated()}), flush=True)
print(json.dumps({"phase": "weighted_sum"}), flush=True)
start = time.perf_counter()
result = (attn.squeeze(-2).unsqueeze(-1) * value).sum(dim=-2)
torch.cuda.synchronize()
seconds = time.perf_counter() - start
result_cpu = result.cpu()
del result
torch.cuda.empty_cache()
print(json.dumps({"phase": "weighted_sum_done", "seconds": seconds,
                  "max_abs_error": float((result_cpu - reference).abs().max()),
                  "allclose_002": torch.allclose(result_cpu, reference, atol=0.002, rtol=0.002),
                  "peak_gpu_bytes": torch.cuda.max_memory_allocated()}), flush=True)
print(json.dumps({"phase": "einsum"}), flush=True)
start = time.perf_counter()
result = torch.einsum('bnti,bntic->bntc', attn.squeeze(-2), value)
torch.cuda.synchronize()
seconds = time.perf_counter() - start
result_cpu = result.cpu()
print(json.dumps({"phase": "einsum_done", "seconds": seconds,
                  "max_abs_error": float((result_cpu - reference).abs().max()),
                  "allclose_002": torch.allclose(result_cpu, reference, atol=0.002, rtol=0.002),
                  "peak_gpu_bytes": torch.cuda.max_memory_allocated()}), flush=True)
del result
torch.cuda.empty_cache()
for mode in ('matmul', 'einsum'):
    print(json.dumps({"phase": mode + "_warm"}), flush=True)
    start = time.perf_counter()
    if mode == 'matmul':
        result = torch.matmul(attn, value).squeeze(-2)
    else:
        result = torch.einsum('bnti,bntic->bntc', attn.squeeze(-2), value)
    torch.cuda.synchronize()
    seconds = time.perf_counter() - start
    result_cpu = result.cpu()
    del result
    torch.cuda.empty_cache()
    print(json.dumps({"phase": mode + "_warm_done", "seconds": seconds,
                      "max_abs_error": float((result_cpu - reference).abs().max()),
                      "peak_gpu_bytes": torch.cuda.max_memory_allocated()}), flush=True)
