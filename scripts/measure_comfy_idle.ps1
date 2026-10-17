param(
    [Parameter(Mandatory=$true)][int]$ServerPid,
    [Parameter(Mandatory=$true)][string]$OutputPath,
    [int]$Seconds = 10,
    [int]$Port = 8188
)
$ErrorActionPreference = 'Stop'
if ($Seconds -lt 1 -or $Seconds -gt 30) { throw 'Use a sample of 1 to 30 seconds' }
$projectRoot = Split-Path -Parent $PSScriptRoot
$expectedPython = Join-Path $projectRoot 'ComfyUI_windows_portable\python_embeded\python.exe'
$owner = Get-CimInstance Win32_Process -Filter "ProcessId=$ServerPid"
if ($owner.ExecutablePath -ne $expectedPython -or $owner.CommandLine -notlike '*comfy_server.py*') {
    throw 'PID is not this project''s ComfyUI server'
}
$url = "http://127.0.0.1:$Port/queue"
$beforeQueue = Invoke-RestMethod -Uri $url -TimeoutSec 3
if ($beforeQueue.queue_running.Count -or $beforeQueue.queue_pending.Count) { throw 'Queue is not idle' }
$before = Get-Process -Id $ServerPid
$beforeCpu = $before.TotalProcessorTime.TotalSeconds
$started = [DateTime]::UtcNow
$watch = [Diagnostics.Stopwatch]::StartNew()
Start-Sleep -Seconds $Seconds
$after = Get-Process -Id $ServerPid
$elapsed = $watch.Elapsed.TotalSeconds
$afterQueue = Invoke-RestMethod -Uri $url -TimeoutSec 3
if ($afterQueue.queue_running.Count -or $afterQueue.queue_pending.Count) { throw 'Queue changed during sample' }
$cpuDelta = $after.TotalProcessorTime.TotalSeconds - $beforeCpu
$result = [ordered]@{
    pid = $ServerPid
    started_utc = $started.ToString('o')
    finished_utc = [DateTime]::UtcNow.ToString('o')
    sample_seconds = $elapsed
    cpu_seconds_delta = $cpuDelta
    logical_processors = [Environment]::ProcessorCount
    cpu_percent = 100 * $cpuDelta / $elapsed / [Environment]::ProcessorCount
    working_set = $after.WorkingSet64
    private_bytes = $after.PrivateMemorySize64
    queue_running = $afterQueue.queue_running.Count
    queue_pending = $afterQueue.queue_pending.Count
}
$json = $result | ConvertTo-Json
$json | Set-Content -LiteralPath $OutputPath -Encoding UTF8
$json
