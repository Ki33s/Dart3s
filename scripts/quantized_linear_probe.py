"""Exercise ComfyUI's actual packed Pixal3D QKV layer and FP32 fallback."""
import ctypes
import json
import os
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace

ctypes.windll.kernel32.SetErrorMode(0x0001 | 0x0002 | 0x8000)


def deadline():
    time.sleep(55)
    print(json.dumps({"error": "55 second deadline"}), flush=True)
    os._exit(124)


threading.Thread(target=deadline, daemon=True).start()
root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'ComfyUI_windows_portable/ComfyUI'))
sys.argv = [sys.argv[0], '--windows-standalone-build', '--preview-method', 'none']
import comfy.options
comfy.options.enable_args_parsing()
import torch
from safetensors import safe_open
import comfy.model_management as mm
import comfy.ops as ops
import comfy.utils
from comfy.quant_ops import QuantizedTensor

torch.cuda.set_per_process_memory_fraction(0.05)
torch.cuda.reset_peak_memory_stats()
device = torch.device('cuda:0')
prefix = 'model.structure_model.blocks.0.self_attn.to_qkv.'
model_file = root / 'models/diffusion_models/pixal3d_int8_convrot.safetensors'
with safe_open(model_file, framework='pt', device='cpu') as reader:
    keys = [name for name in reader.keys() if name.startswith(prefix)]
    state = {name: reader.get_tensor(name) for name in keys}
quant_config = comfy.utils.detect_layer_quantization(state, '')
layer_conf = json.loads(state[prefix + 'comfy_quant'].numpy().tobytes())
out_features, in_features = state[prefix + 'weight'].shape
disabled = ops.get_disabled_quant_formats(device)
assert not mm.supports_int8_compute(device)
assert 'int8_tensorwise' in disabled
factory = ops.pick_operations(torch.bfloat16, torch.float32, load_device=device,
                              model_config=SimpleNamespace(quant_config=quant_config))
layer = factory.Linear(in_features, out_features, bias=True, device=device)
layer.load_state_dict({name.removeprefix(prefix): value for name, value in state.items()}, strict=True)
assert isinstance(layer.weight, QuantizedTensor)
assert layer._full_precision_mm
assert layer.quant_format == 'int8_tensorwise'
layer.eval()
print(json.dumps({'phase': 'loaded', 'layer': prefix.rstrip('.'),
                  'shape': [out_features, in_features], 'metadata': layer_conf,
                  'disabled_formats': sorted(disabled),
                  'full_precision_mm': layer._full_precision_mm,
                  'packed_dtype': str(layer.weight._qdata.dtype),
                  'compute_dtype': str(layer.weight.dtype)}), flush=True)
int8_calls = 0


def forbid_int8(*args, **kwargs):
    global int8_calls
    int8_calls += 1
    raise AssertionError('Native INT8 GEMM must not execute on gfx1032')


torch._int_mm = forbid_int8
if hasattr(torch, 'int8_mm'):
    torch.int8_mm = forbid_int8
with torch.inference_mode():
    weight = layer.weight.dequantize()
    assert weight.dtype == torch.float32
    for seed in (42, 123):
        for rows in (1, 32):
            torch.manual_seed(seed)
            value = torch.randn((rows, in_features), device=device, dtype=torch.float32)
            start = time.perf_counter()
            actual = layer(value)
            torch.cuda.synchronize()
            elapsed = time.perf_counter() - start
            expected = torch.nn.functional.linear(value, weight, layer.bias)
            torch.cuda.synchronize()
            assert bool(torch.isfinite(actual).all())
            assert torch.equal(actual, expected)
            print(json.dumps({'seed': seed, 'rows': rows, 'shape': list(actual.shape),
                              'seconds': elapsed, 'finite': True, 'bitwise_equal': True}), flush=True)
assert int8_calls == 0
print(json.dumps({'ok': True, 'int8_calls': int8_calls,
                  'peak_gpu_bytes': torch.cuda.max_memory_allocated()}), flush=True)
