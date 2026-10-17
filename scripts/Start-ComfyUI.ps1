param([int]$Port = 8188, [switch]$Cpu)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$portableRoot = Join-Path $projectRoot 'ComfyUI_windows_portable'
$pythonPath = Join-Path $portableRoot 'python_embeded\python.exe'
$comfyRoot = Join-Path $portableRoot 'ComfyUI'
if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Install ComfyUI portable first' }
$wrapperPath = Join-Path $PSScriptRoot 'comfy_server.py'
if (-not (Test-Path -LiteralPath $wrapperPath)) { throw 'The headless ComfyUI server wrapper is missing' }
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:COMFY_NO_TELEMETRY = '1'
$env:COMFY_WHERE = 'local'
$env:LOCALAPPDATA = Join-Path $projectRoot '.state'
$null = New-Item -ItemType Directory -Path $env:LOCALAPPDATA -Force
$logsRoot = Join-Path $projectRoot 'logs'
$null = New-Item -ItemType Directory -Path $logsRoot -Force
$stdoutLog = Join-Path $logsRoot 'comfyui.stdout.log'
$stderrLog = Join-Path $logsRoot 'comfyui.stderr.log'
$serverUrl = "http://127.0.0.1:$Port"
try {
    $stats = Invoke-RestMethod -Uri "$serverUrl/system_stats" -TimeoutSec 2 -ErrorAction Stop
    if ($null -ne $stats.system -and $null -ne $stats.devices) {
        [pscustomobject]@{ status = 'already_running'; url = $serverUrl } | ConvertTo-Json
        return
    }
} catch { }
# Start-Process joins ArgumentList into a command line, so quote the script path.
$launchArgs = @('-s', ('"' + $wrapperPath + '"'), '--windows-standalone-build', '--listen', '127.0.0.1', '--port', "$Port", '--preview-method', 'none', '--disable-auto-launch')
if ($Cpu) { $launchArgs += '--cpu' }
$process = Start-Process -FilePath $pythonPath -ArgumentList $launchArgs -WorkingDirectory $portableRoot -WindowStyle Hidden -RedirectStandardOutput $stdoutLog -RedirectStandardError $stderrLog -PassThru
[pscustomobject]@{ status = 'starting'; pid = $process.Id; url = $serverUrl; stdout_log = $stdoutLog; stderr_log = $stderrLog } | ConvertTo-Json
