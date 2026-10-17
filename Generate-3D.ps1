[CmdletBinding()]
param(
    [string]$Image,
    [ValidateSet('pixal3d', 'trellis2')][string]$Model = 'pixal3d',
    [ValidateSet('standard', 'fast')][string]$Quality = 'standard',
    [int]$Polygons = 10000,
    [int]$RemeshResolution = 256,
    [int]$TextureSize = 256,
    [UInt64]$StructureSeed = 56,
    [UInt64]$ShapeSeed = 42,
    [UInt64]$TextureSeed = 43,
    [string]$OutputDir,
    [string]$LocalUrl = 'http://127.0.0.1:8188',
    [double]$WaitTimeout = 7200,
    [double]$StartupTimeout = 180,
    [switch]$NoStartServer,
    [switch]$PrepareOnly,
    [string]$ObjectInfo,
    [string]$Resume
)
$ErrorActionPreference = 'Stop'
$pythonPath = Join-Path $PSScriptRoot '.venv-cli\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) { throw "Comfy CLI Python is missing: $pythonPath" }
$scriptPath = Join-Path $PSScriptRoot 'scripts\generate_3d.py'
$arguments = @($scriptPath, '--local-url', $LocalUrl, '--wait-timeout', "$WaitTimeout", '--startup-timeout', "$StartupTimeout")
if ($NoStartServer) { $arguments += '--no-start-server' }
if ($Resume) {
    if ($Image -or $PrepareOnly) { throw 'Resume cannot be combined with Image or PrepareOnly.' }
    $arguments += @('--resume', $Resume)
} else {
    if (-not $Image) { throw 'Image is required, or use -Resume <prompt_id>.' }
    $arguments += @('--image', $Image, '--model', $Model, '--quality', $Quality, '--polygons', "$Polygons", '--remesh-resolution', "$RemeshResolution",
        '--texture-size', "$TextureSize", '--structure-seed', "$StructureSeed", '--shape-seed', "$ShapeSeed", '--texture-seed', "$TextureSeed")
    if ($PrepareOnly) { $arguments += '--prepare-only' }
    if ($ObjectInfo) { $arguments += @('--object-info', $ObjectInfo) }
}
if ($OutputDir) { $arguments += @('--output-dir', $OutputDir) }
& $pythonPath @arguments
exit $LASTEXITCODE
