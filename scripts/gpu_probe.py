"""Exercise the actual GPU, not only package installation or enumeration."""
import json
import platform
import sys
import time
import faulthandler

faulthandler.enable()
if sys.platform == 'win32':
    import ctypes
    # Retain crash diagnostics in stderr, without a modal Windows error box.
    ctypes.windll.kernel32.SetErrorMode(0x0001 | 0x0002 | 0x8000)

start = time.monotonic()
report = {'python': platform.python_version()}
try:
    print('GPU probe: importing torch', file=sys.stderr, flush=True)
    import torch
    report.update(torch=torch.__version__, hip=torch.version.hip)
    print('GPU probe: enumerating devices', file=sys.stderr, flush=True)
    report['cuda_available'] = torch.cuda.is_available()
    if not report['cuda_available'] or not report['hip']:
        raise RuntimeError('ROCm GPU is unavailable')
    props = torch.cuda.get_device_properties(0)
    report.update(gpu=props.name, architecture=props.gcnArchName,
                  vram=props.total_memory)
    for dtype in (torch.float32, torch.float16, torch.bfloat16):
        print(f'GPU probe: matmul {dtype}', file=sys.stderr, flush=True)
        a = torch.randn(256, 256, device='cuda', dtype=dtype)
        b = a @ a.T
        torch.cuda.synchronize()
        if not bool(torch.isfinite(b).all().item()):
            raise RuntimeError(f'Nonfinite matmul {dtype}')
        report[str(dtype)] = 'passed'
    print('GPU probe: conv3d', file=sys.stderr, flush=True)
    conv = torch.nn.Conv3d(4, 8, 3, padding=1).to(device='cuda', dtype=torch.float16)
    volume = torch.randn(1, 4, 16, 16, 16, device='cuda', dtype=torch.float16)
    result = conv(volume)
    torch.cuda.synchronize()
    if not bool(torch.isfinite(result).all().item()):
        raise RuntimeError('Nonfinite conv3d')
    report['conv3d'] = 'passed'
    report['ok'] = True
except Exception as exc:
    report['ok'] = False
    report['error'] = f'{type(exc).__name__}: {exc}'
report['seconds'] = round(time.monotonic() - start, 2)
print(json.dumps(report, indent=2))
raise SystemExit(0 if report['ok'] else 1)
