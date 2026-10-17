$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot 'ComfyUI_windows_portable\python_embeded\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) { throw "Portable Python missing: $pythonPath" }
$env:PYTHONUTF8 = '1'
& $pythonPath -m pip freeze | Set-Content (Join-Path $projectRoot 'logs\portable.before-nightly.txt')
$uvPath = Join-Path $projectRoot '.venv-cli\Scripts\uv.exe'
$env:UV_CACHE_DIR = Join-Path $projectRoot '.cache\uv'
$env:UV_HTTP_TIMEOUT = '300'
$env:UV_HTTP_RETRIES = '5'
& $uvPath pip install --python $pythonPath --upgrade --prerelease allow --find-links (Join-Path $projectRoot 'downloads\amd-wheels') --index-url 'https://nightly.repo.amd.com/rocm/whl-next/' 'torch[device-gfx1032]==2.13.0+rocm10.2.0a20261013' 'torchvision[device-gfx1032]==0.28.0+rocm10.2.0a20261013' 'torchaudio==2.11.0.3+rocm10.2.0a20261013'
if ($LASTEXITCODE -ne 0) { throw 'ROCm nightly install failed' }
& $pythonPath (Join-Path $PSScriptRoot 'gpu_probe.py') | Tee-Object -FilePath (Join-Path $projectRoot 'logs\gpu-probe.json')
if ($LASTEXITCODE -ne 0) { throw 'ROCm GPU probe failed; inspect logs\gpu-probe.json' }
& $pythonPath (Join-Path $PSScriptRoot 'configure_rocm_compat.py')
if ($LASTEXITCODE -ne 0) { throw 'ComfyUI gfx1032 compatibility configuration failed' }
& $pythonPath -m pip freeze | Set-Content (Join-Path $projectRoot 'logs\portable.after-nightly.txt')
