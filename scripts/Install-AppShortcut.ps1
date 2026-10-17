param([switch]$Desktop)
$ErrorActionPreference = 'Stop'
$projectPath = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectPath '.venv-cli\Scripts\pythonw.exe'
$entryPath = Join-Path $projectPath 'scripts\launch_gui.pyw'
if (!(Test-Path -LiteralPath $pythonPath) -or !(Test-Path -LiteralPath $entryPath)) {
    throw 'Dart3s application files are missing.'
}
$shortcutPaths = @((Join-Path $projectPath 'Dart3s.lnk'))
if ($Desktop) {
    $shortcutPaths += Join-Path ([Environment]::GetFolderPath('Desktop')) 'Dart3s.lnk'
}
$shell = New-Object -ComObject WScript.Shell
foreach ($shortcutPath in $shortcutPaths) {
    $shortcut = $shell.CreateShortcut($shortcutPath)
    if ((Test-Path -LiteralPath $shortcutPath) -and $shortcut.TargetPath -ne $pythonPath) {
        throw "Existing shortcut points to another application: $shortcutPath"
    }
    $shortcut.TargetPath = $pythonPath
    $shortcut.Arguments = '"' + $entryPath + '"'
    $shortcut.WorkingDirectory = $projectPath
    $shortcut.Description = 'Dart3s: Image to 3D with ComfyUI'
    $shortcut.WindowStyle = 1
    $shortcut.Save()
}
@{ ok = $true; shortcuts = $shortcutPaths; target = $pythonPath } | ConvertTo-Json
