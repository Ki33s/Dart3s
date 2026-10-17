# Run the project's isolated Comfy CLI with its local configuration.
$ErrorActionPreference = 'Stop'
$cliPath = Join-Path $PSScriptRoot '.venv-cli\Scripts\comfy.exe'
if (-not (Test-Path -LiteralPath $cliPath -PathType Leaf)) { throw "Comfy CLI missing: $cliPath" }
$settings = @{
    PYTHONUTF8 = '1'
    PYTHONIOENCODING = 'utf-8'
    COMFY_NO_TELEMETRY = '1'
    COMFY_WHERE = 'local'
    COMFY_LOCAL_URL = 'http://127.0.0.1:8188'
    LOCALAPPDATA = (Join-Path $PSScriptRoot '.state')
}
$previous = @{}
try {
    foreach ($key in $settings.Keys) {
        $previous[$key] = [Environment]::GetEnvironmentVariable($key, 'Process')
        [Environment]::SetEnvironmentVariable($key, $settings[$key], 'Process')
    }
    & $cliPath @args
    $cliExitCode = $LASTEXITCODE
} finally {
    foreach ($key in $previous.Keys) {
        [Environment]::SetEnvironmentVariable($key, $previous[$key], 'Process')
    }
}
exit $cliExitCode
