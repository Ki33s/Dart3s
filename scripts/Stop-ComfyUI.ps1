param([int]$Port = 8188)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$expectedPython = Join-Path $projectRoot 'ComfyUI_windows_portable\python_embeded\python.exe'
$expectedScript = Join-Path $projectRoot 'scripts\comfy_server.py'
# Enumerate even during startup, when the server may not yet have opened its port.
$owned = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object {
    $_.ExecutablePath -eq $expectedPython -and
    $_.CommandLine -like ('*' + $expectedScript + '*') -and
    $_.CommandLine -match ('--port\s+' + $Port + '(?:\s|$)')
})
$stopped = @()
foreach ($server in $owned) {
    # Re-read identity immediately before termination to guard stale process IDs.
    $current = Get-CimInstance Win32_Process -Filter "ProcessId=$($server.ProcessId)"
    if ($null -eq $current) { continue }
    if ($current.ExecutablePath -ne $server.ExecutablePath -or
        $current.CreationDate -ne $server.CreationDate -or
        $current.CommandLine -ne $server.CommandLine) { throw 'Server identity changed' }
    Stop-Process -Id $server.ProcessId -Force -ErrorAction Stop
    $stopDeadline = [DateTime]::UtcNow.AddSeconds(10)
    do {
        $stillOwned = Get-CimInstance Win32_Process -Filter "ProcessId=$($server.ProcessId)"
        if ($null -eq $stillOwned -or $stillOwned.CreationDate -ne $server.CreationDate) { break }
        Start-Sleep -Milliseconds 100
    } while ([DateTime]::UtcNow -lt $stopDeadline)
    if ($null -ne $stillOwned -and $stillOwned.CreationDate -eq $server.CreationDate) { throw 'ComfyUI process did not exit' }
    $stopped += $server.ProcessId
}
$listeners = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
if ($listeners.Count) { throw 'Port still belongs to a process that this helper will not terminate' }
[pscustomobject]@{ ok = $true; stopped_pids = $stopped; port = $Port } | ConvertTo-Json
