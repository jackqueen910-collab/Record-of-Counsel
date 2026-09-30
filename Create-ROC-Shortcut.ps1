$ErrorActionPreference = 'Stop'
$rocRoot = $PSScriptRoot
$rocPython = Join-Path $rocRoot '.venv\Scripts\pythonw.exe'
if (-not (Test-Path -LiteralPath $rocPython)) {
    throw 'Install ROC in .venv as described in README before creating the shortcut.'
}
$rocShell = New-Object -ComObject WScript.Shell
$rocShortcut = $rocShell.CreateShortcut((Join-Path $rocRoot 'Start ROC.lnk'))
$rocShortcut.TargetPath = $rocPython
$rocShortcut.Arguments = '-m roc.desktop'
$rocShortcut.WorkingDirectory = $rocRoot
$rocShortcut.Description = 'Record of Counsel - local browser workspace'
$rocShortcut.Save()
Write-Output 'Created Start ROC in this project folder. Double-click it to launch without a console window.'
