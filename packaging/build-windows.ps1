param(
    [string]$Python = ".venv\Scripts\python.exe"
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot ".."))
$pythonPath = Join-Path $projectRoot $Python

if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw "Canonical Python executable not found: $pythonPath"
}

Push-Location $projectRoot
try {
    & $pythonPath -m pip install -e ".[package]"
    if ($LASTEXITCODE -ne 0) { throw "Could not install packaging dependencies." }

    & $pythonPath -m PyInstaller `
        --noconfirm `
        --clean `
        --distpath dist `
        --workpath build `
        packaging\dlsite-organizer.spec
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed." }

    $version = (& $pythonPath -c "from dlsite_organizer import __version__; print(__version__)").Trim()
    $distribution = Join-Path $projectRoot "dist\dlsite-organizer"
    $archive = Join-Path $projectRoot ("dist\dlsite-organizer-{0}-windows-x64.zip" -f $version)
    if (-not (Test-Path -LiteralPath $distribution -PathType Container)) {
        throw "PyInstaller did not produce the expected onedir output: $distribution"
    }
    if (Test-Path -LiteralPath $archive) {
        Remove-Item -LiteralPath $archive -Force
    }
    Compress-Archive -Path (Join-Path $distribution "*") -DestinationPath $archive
    Get-FileHash -Algorithm SHA256 -LiteralPath $archive
    Write-Output "Windows distribution: $archive"
}
finally {
    Pop-Location
}
