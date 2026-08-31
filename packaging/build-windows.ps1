param(
    [string]$Python = ".venv\Scripts\python.exe"
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot ".."))
$pythonPath = Join-Path $projectRoot $Python
$packagingHelper = Join-Path $projectRoot "packaging\windows_packaging.py"

if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw "Canonical Python executable not found: $pythonPath"
}

function Invoke-IsolatedPython {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    $startInfo = [Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $pythonPath
    $startInfo.WorkingDirectory = $projectRoot.Path
    $startInfo.UseShellExecute = $false
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $startInfo.Environment["PATH"] = [string]($pathIsolation.path)
    foreach ($argument in $Arguments) {
        [void]$startInfo.ArgumentList.Add($argument)
    }

    $process = [Diagnostics.Process]::new()
    $process.StartInfo = $startInfo
    if (-not $process.Start()) {
        throw "Could not start isolated Python process."
    }
    $stdoutTask = $process.StandardOutput.ReadToEndAsync()
    $stderrTask = $process.StandardError.ReadToEndAsync()
    $process.WaitForExit()
    $stdout = $stdoutTask.GetAwaiter().GetResult()
    $stderr = $stderrTask.GetAwaiter().GetResult()
    if ($stdout) {
        Write-Output $stdout.TrimEnd()
    }
    if ($stderr) {
        Write-Output $stderr.TrimEnd()
    }
    if ($process.ExitCode -ne 0) {
        throw "Isolated Python command failed with exit code $($process.ExitCode)."
    }
}

Push-Location $projectRoot
$originalPath = $env:PATH
$pathWasIsolated = $false
try {
    $displayPythonPath = $pythonPath.Replace($projectRoot.Path, "<PROJECT_ROOT>")
    Write-Output "Packaging Python: $displayPythonPath"
    Write-Output ("Python version: {0}" -f ((& $pythonPath --version) -join " "))
    Write-Output ("PyInstaller version: {0}" -f ((& $pythonPath -m PyInstaller --version) -join " "))
    Write-Output (
        "PySide6 version: {0}" -f ((& $pythonPath -c "import PySide6; print(PySide6.__version__)") -join " ")
    )

    & $pythonPath -c "import PyInstaller, PySide6, dlsite_organizer; print('Packaging dependencies: PASS')"
    if ($LASTEXITCODE -ne 0) {
        throw "Canonical Python environment is missing packaging dependencies."
    }

    $cleanPathArguments = @(
        "clean-path",
        "--path", $originalPath,
        "--python-executable", $pythonPath
    )
    if ($env:SystemRoot) {
        $cleanPathArguments += @("--system-root", $env:SystemRoot)
    }
    $pathIsolation = (& $pythonPath $packagingHelper @cleanPathArguments | ConvertFrom-Json)
    $env:PATH = [string]($pathIsolation.path)
    $pathWasIsolated = $true
    Write-Output ("Packaging PATH entries removed: {0}" -f @($pathIsolation.removed).Count)
    $userHome = [Environment]::GetFolderPath([Environment+SpecialFolder]::UserProfile)
    if (-not $userHome) {
        $userHome = $env:USERPROFILE
    }
    foreach ($removedEntry in @($pathIsolation.removed)) {
        $displayEntry = [string]$removedEntry
        if ($userHome) {
            $displayEntry = $displayEntry -replace [regex]::Escape($userHome), "<USER_HOME>"
        }
        $displayEntry = $displayEntry.Replace($projectRoot.Path, "<PROJECT_ROOT>")
        Write-Output ("  removed: {0}" -f $displayEntry)
    }
    Write-Output ("Packaging PATH entries retained: {0}" -f @($pathIsolation.kept).Count)

    Invoke-IsolatedPython @(
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--distpath", "dist",
        "--workpath", "build",
        "packaging\dlsite-organizer.spec"
    )

    $version = (& $pythonPath -c "from dlsite_organizer import __version__; print(__version__)").Trim()
    $distribution = Join-Path $projectRoot "dist\dlsite-organizer"
    $archive = Join-Path $projectRoot ("dist\dlsite-organizer-{0}-windows-x64.zip" -f $version)
    if (-not (Test-Path -LiteralPath $distribution -PathType Container)) {
        throw "PyInstaller did not produce the expected onedir output: $distribution"
    }

    $provenance = Join-Path $projectRoot "build\dlsite-organizer-binary-provenance.json"
    Invoke-IsolatedPython @(
        $packagingHelper,
        "audit-dist",
        "--dist", $distribution,
        "--provenance", $provenance
    )
    Invoke-IsolatedPython @(
        $packagingHelper,
        "audit-native",
        "--dist", $distribution
    )
    Invoke-IsolatedPython @(
        $packagingHelper,
        "audit-icon",
        "--dist", $distribution
    )
    if (Test-Path -LiteralPath $archive) {
        Remove-Item -LiteralPath $archive -Force
    }

    # Keep the license files from the exact runtime distributions used by this
    # build alongside the application. The package metadata is the source of
    # truth; this avoids inventing or paraphrasing third-party notices.
    $pythonScripts = Split-Path -Parent $pythonPath
    $pythonRoot = Split-Path -Parent $pythonScripts
    $sitePackages = Join-Path $pythonRoot "Lib\site-packages"
    $licenseBundle = Join-Path $distribution "licenses"
    New-Item -ItemType Directory -Force -Path $licenseBundle | Out-Null
    $runtimeDistInfoPatterns = @(
        "pyside6_essentials-*.dist-info",
        "shiboken6-*.dist-info",
        "sqlalchemy-*.dist-info",
        "selectolax-*.dist-info",
        "httpx-*.dist-info",
        "httpcore-*.dist-info",
        "certifi-*.dist-info",
        "idna-*.dist-info",
        "anyio-*.dist-info",
        "h11-*.dist-info",
        "pydantic-*.dist-info",
        "pydantic_core-*.dist-info",
        "greenlet-*.dist-info",
        "annotated_types-*.dist-info",
        "typing_extensions-*.dist-info",
        "typing_inspection-*.dist-info"
    )
    foreach ($pattern in $runtimeDistInfoPatterns) {
        Get-ChildItem -LiteralPath $sitePackages -Directory -Filter $pattern | ForEach-Object {
            $sourceLicenses = Join-Path $_.FullName "licenses"
            if (Test-Path -LiteralPath $sourceLicenses -PathType Container) {
                $targetLicenses = Join-Path $licenseBundle $_.Name
                Copy-Item -LiteralPath $sourceLicenses -Destination $targetLicenses -Recurse -Force
            }
        }
    }
    Invoke-IsolatedPython @(
        $packagingHelper,
        "audit-resources",
        "--dist", $distribution
    )
    Compress-Archive -Path (Join-Path $distribution "*") -DestinationPath $archive
    Get-FileHash -Algorithm SHA256 -LiteralPath $archive
    Write-Output "Windows distribution: $archive"
}
finally {
    if ($pathWasIsolated) {
        $env:PATH = $originalPath
    }
    Pop-Location
}
