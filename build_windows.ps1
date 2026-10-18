$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (Test-Path $venvPython) {
    $python = $venvPython
} else {
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if (-not $pythonCommand) {
        throw "Python 3.10 or later was not found."
    }
    $python = $pythonCommand.Source
}

& $python -m PyInstaller `
    --noconfirm `
    --clean `
    --windowed `
    --onedir `
    --name DouyinDataAnalyzer `
    --distpath (Join-Path $PSScriptRoot "dist") `
    --workpath (Join-Path $PSScriptRoot "build\pyinstaller") `
    --specpath (Join-Path $PSScriptRoot "build") `
    --hidden-import PySide6.QtWebEngineCore `
    --hidden-import PySide6.QtWebEngineWidgets `
    (Join-Path $PSScriptRoot "douyin_analyzer_gui.py")

if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

Write-Host "Built dist\DouyinDataAnalyzer\DouyinDataAnalyzer.exe"
