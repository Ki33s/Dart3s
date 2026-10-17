param([switch]$Desktop)
$ErrorActionPreference = 'Stop'
$taskProject = Split-Path -Parent $PSScriptRoot
$taskPython = Join-Path $taskProject '.venv-cli\Scripts\pythonw.exe'
$taskEntry = Join-Path $PSScriptRoot 'launch.pyw'
if (!(Test-Path -LiteralPath $taskPython) -or !(Test-Path -LiteralPath $taskEntry)) {
    throw 'Details3D application files are missing.'
}
$taskLinks = @((Join-Path $taskProject 'Details3D.lnk'))
if ($Desktop) { $taskLinks += Join-Path ([Environment]::GetFolderPath('Desktop')) 'Детали 3D.lnk' }
$taskShell = New-Object -ComObject WScript.Shell
foreach ($taskLink in $taskLinks) {
    $taskShortcut = $taskShell.CreateShortcut($taskLink)
    if ((Test-Path -LiteralPath $taskLink) -and $taskShortcut.TargetPath -ne $taskPython) {
        throw "Existing shortcut points to another application: $taskLink"
    }
    $taskShortcut.TargetPath = $taskPython
    $taskShortcut.Arguments = '"' + $taskEntry + '"'
    $taskShortcut.WorkingDirectory = $taskProject
    $taskShortcut.Description = 'Image to small 3D asset with ComfyUI'
    $taskShortcut.WindowStyle = 1
    $taskShortcut.Save()
}
@{ ok = $true; shortcuts = $taskLinks } | ConvertTo-Json
