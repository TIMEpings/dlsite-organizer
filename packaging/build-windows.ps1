param(
    [string]$Python = ".venv\Scripts\python.exe",
    [string]$OutputRoot = "",
    [switch]$StagingOnly
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot ".."))
$pythonPath = Join-Path $projectRoot $Python
$packagingHelper = Join-Path $projectRoot "packaging\windows_packaging.py"
$officialArchive = Join-Path $projectRoot "dist\dlsite-organizer-1.1.0-windows-x64.zip"
$officialArchiveExpectedSize = 59437033
$officialArchiveExpectedSha256 = "6E060328A87F363DFBB2128EAA6D7C371C0E31FD0578DC0E172BF59EB2331684"

function Get-ArtifactFingerprint {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return $null
    }
    $item = Get-Item -LiteralPath $Path
    return [pscustomobject]@{
        Length = [int64]$item.Length
        Sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToUpperInvariant()
        LastWriteTimeUtc = $item.LastWriteTimeUtc
    }
}

function Assert-OfficialArtifactUnchanged {
    param(
        [Parameter(Mandatory = $true)]
        [AllowNull()]
        [object]$Before
    )

    if ($null -eq $Before) {
        return
    }
    $after = Get-ArtifactFingerprint -Path $officialArchive
    if ($null -eq $after -or
        $after.Length -ne $Before.Length -or
        $after.Sha256 -ne $Before.Sha256 -or
        $after.LastWriteTimeUtc -ne $Before.LastWriteTimeUtc) {
        throw "Official v1.1.0 artifact changed during the build: $officialArchive"
    }
}

$officialArchiveBefore = Get-ArtifactFingerprint -Path $officialArchive
if ($null -ne $officialArchiveBefore -and
    ($officialArchiveBefore.Length -ne $officialArchiveExpectedSize -or
     $officialArchiveBefore.Sha256 -ne $officialArchiveExpectedSha256)) {
    throw "Official v1.1.0 artifact fingerprint is unexpected; refusing to continue: $officialArchive"
}

$stagingBuild = $StagingOnly.IsPresent -or -not [string]::IsNullOrWhiteSpace($OutputRoot)
if ($stagingBuild -and [string]::IsNullOrWhiteSpace($OutputRoot)) {
    $OutputRoot = Join-Path $projectRoot "build\audit-v12-phase3-package"
}
if ($stagingBuild) {
    if ([IO.Path]::IsPathRooted($OutputRoot)) {
        $outputRootPath = [IO.Path]::GetFullPath($OutputRoot)
    }
    else {
        $outputRootPath = [IO.Path]::GetFullPath((Join-Path $projectRoot $OutputRoot))
    }
    $projectDistPath = [IO.Path]::GetFullPath((Join-Path $projectRoot "dist"))
    if ($outputRootPath.TrimEnd('\') -eq $projectDistPath.TrimEnd('\')) {
        throw "Staging OutputRoot must not be the official dist directory."
    }
    $outputRoot = $outputRootPath
}
else {
    $outputRoot = $projectRoot.Path
}

$pyInstallerDistRoot = Join-Path $outputRoot "dist"
$pyInstallerWorkRoot = if ($stagingBuild) {
    Join-Path $outputRoot "pyinstaller-work"
}
else {
    Join-Path $projectRoot "build\pyinstaller-work"
}
$nativeBuildRoot = if ($stagingBuild) {
    Join-Path $outputRoot "native-build"
}
else {
    Join-Path $projectRoot "build\native\shell_helper"
}
$distribution = Join-Path $pyInstallerDistRoot "dlsite-organizer"
$provenance = if ($stagingBuild) {
    Join-Path $outputRoot "dlsite-organizer-binary-provenance.json"
}
else {
    Join-Path $projectRoot "build\dlsite-organizer-binary-provenance.json"
}

if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw "Canonical Python executable not found: $pythonPath"
}

function Resolve-NativeToolchain {
    $vswhereCandidates = @(
        (Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer\vswhere.exe"),
        (Join-Path $env:ProgramFiles "Microsoft Visual Studio\Installer\vswhere.exe")
    )
    $vswhere = $vswhereCandidates | Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Leaf) } | Select-Object -First 1
    if (-not $vswhere) {
        throw "Native packaging requires vswhere.exe and a Visual Studio C++ x64 toolchain."
    }
    $installationPath = (& $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath | Select-Object -First 1).Trim()
    if (-not $installationPath) {
        throw "Visual Studio C++ x64 workload was not found."
    }

    $cl = Get-ChildItem -Path (Join-Path $installationPath "VC\Tools\MSVC\*\bin\Hostx64\x64\cl.exe") -File -ErrorAction SilentlyContinue |
        Sort-Object FullName -Descending | Select-Object -First 1
    $cmakeCandidates = @(
        (Join-Path $installationPath "Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe"),
        (Get-Command cmake.exe -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -First 1)
    )
    $ninjaCandidates = @(
        (Join-Path $installationPath "Common7\IDE\CommonExtensions\Microsoft\CMake\Ninja\ninja.exe"),
        (Get-Command ninja.exe -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -First 1)
    )
    $cmake = $cmakeCandidates | Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Leaf) } | Select-Object -First 1
    $ninja = $ninjaCandidates | Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Leaf) } | Select-Object -First 1
    $vsDevCmd = Join-Path $installationPath "Common7\Tools\VsDevCmd.bat"
    $ctest = Join-Path (Split-Path -Parent $cmake) "ctest.exe"
    if (-not $cl -or -not $cmake -or -not $ninja -or -not (Test-Path -LiteralPath $vsDevCmd -PathType Leaf) -or -not (Test-Path -LiteralPath $ctest -PathType Leaf)) {
        throw "Native packaging requires MSVC x64, Windows SDK, CMake, Ninja, and CTest; one or more tools are missing."
    }
    return [pscustomobject]@{
        InstallationPath = $installationPath
        Cl = $cl.FullName
        CMake = $cmake
        Ninja = $ninja
        CTest = $ctest
        VsDevCmd = $vsDevCmd
        Dumpbin = (Join-Path (Split-Path -Parent $cl.FullName) "dumpbin.exe")
    }
}

function Import-VsDevEnvironment {
    param(
        [Parameter(Mandatory = $true)]
        [pscustomobject]$Toolchain
    )

    $command = '"' + $Toolchain.VsDevCmd + '" -arch=x64 -host_arch=x64 >nul && set'
    $environmentLines = & $env:ComSpec /d /s /c $command
    if ($LASTEXITCODE -ne 0) {
        throw "Could not initialize the Visual Studio x64 developer environment."
    }
    $environmentValues = @{}
    foreach ($line in $environmentLines) {
        $separator = $line.IndexOf('=')
        if ($separator -gt 0) {
            $name = $line.Substring(0, $separator)
            $value = $line.Substring($separator + 1)
            $canonicalName = $name.ToUpperInvariant()
            if (-not $environmentValues.ContainsKey($canonicalName) -or $name -ceq $canonicalName) {
                $environmentValues[$canonicalName] = $value
            }
        }
    }
    foreach ($entry in $environmentValues.GetEnumerator()) {
        [Environment]::SetEnvironmentVariable($entry.Key, $entry.Value, "Process")
    }
}

function Invoke-NativeTool {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Executable,
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments,
        [Parameter(Mandatory = $true)]
        [string]$Description
    )

    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Description failed with exit code $LASTEXITCODE."
    }
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
$processOriginalPath = $env:PATH
$originalPath = $processOriginalPath
$pathWasIsolated = $false
$previousPackagingOutputRoot = $env:DLSITE_PACKAGING_OUTPUT_ROOT
$packagingOutputRootWasSet = $false
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

    $version = (& $pythonPath -c "from dlsite_organizer import __version__; print(__version__)").Trim()
    if (-not $stagingBuild -and $version -eq "1.1.0") {
        throw "Refusing to emit a v1.1.0 release artifact during a release build. Use -OutputRoot <staging-path> -StagingOnly."
    }

    $nativeToolchain = Resolve-NativeToolchain
    Import-VsDevEnvironment $nativeToolchain
    $originalPath = $env:PATH
    Write-Output "MSVC toolchain: $($nativeToolchain.Cl)"
    Write-Output "CMake: $($nativeToolchain.CMake)"
    Write-Output "Ninja: $($nativeToolchain.Ninja)"
    Write-Output "Windows SDK: $env:WindowsSdkDir $env:WindowsSDKVersion"

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

    New-Item -ItemType Directory -Force -Path $outputRoot | Out-Null
    $env:DLSITE_PACKAGING_OUTPUT_ROOT = $outputRoot
    $packagingOutputRootWasSet = $true

    Invoke-NativeTool -Executable $nativeToolchain.CMake -Description "Native CMake configure" -Arguments @(
        "-G", "Ninja",
        "-DCMAKE_BUILD_TYPE=Release",
        "-DCMAKE_MAKE_PROGRAM=$($nativeToolchain.Ninja)",
        "-DCMAKE_CXX_COMPILER=$($nativeToolchain.Cl)",
        "-S", (Join-Path $projectRoot "native\shell_helper"),
        "-B", $nativeBuildRoot
    )
    Invoke-NativeTool -Executable $nativeToolchain.CMake -Description "Native helper Release x64 build" -Arguments @(
        "--build", $nativeBuildRoot, "--config", "Release", "--parallel"
    )
    Invoke-NativeTool -Executable $nativeToolchain.CTest -Description "Native CTest" -Arguments @(
        "--test-dir", $nativeBuildRoot, "--output-on-failure", "-C", "Release"
    )

    $nativeHelper = Join-Path $nativeBuildRoot "bin\dlsite-shell-helper.exe"
    if (-not (Test-Path -LiteralPath $nativeHelper -PathType Leaf)) {
        throw "Native build completed without the production helper: $nativeHelper"
    }
    if (-not (Test-Path -LiteralPath $nativeToolchain.Dumpbin -PathType Leaf)) {
        throw "Native dependency audit requires dumpbin.exe: $($nativeToolchain.Dumpbin)"
    }
    Write-Output "Production helper dependencies:"
    $dependencyOutput = & $nativeToolchain.Dumpbin /dependents $nativeHelper 2>&1
    $dependencyOutput | ForEach-Object { Write-Output $_ }
    $forbiddenDependencyPattern = "(?i)(python|qt\d|qt6|winhttp|wininet|ws2_32|powershell|cmd\.exe)"
    if (($dependencyOutput -join "`n") -match $forbiddenDependencyPattern) {
        throw "Production helper imports a forbidden runtime or shell dependency."
    }
    if (($dependencyOutput -join "`n") -match "(?i)(vcruntime|msvcp)\d*\.dll") {
        throw "Production helper still imports the dynamic MSVC runtime; keep the portable helper on /MT."
    }

    Invoke-IsolatedPython @(
        $packagingHelper,
        "audit-helper",
        "--helper", $nativeHelper
    )

    Invoke-IsolatedPython @(
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--distpath", $pyInstallerDistRoot,
        "--workpath", $pyInstallerWorkRoot,
        "packaging\dlsite-organizer.spec"
    )

    if (-not (Test-Path -LiteralPath $distribution -PathType Container)) {
        throw "PyInstaller did not produce the expected onedir output: $distribution"
    }

    Copy-Item -LiteralPath $nativeHelper -Destination (Join-Path $distribution "dlsite-shell-helper.exe") -Force
    $packagedHelpers = @(Get-ChildItem -LiteralPath $distribution -Recurse -File -Filter "dlsite-shell-helper.exe")
    if ($packagedHelpers.Count -ne 1 -or $packagedHelpers[0].DirectoryName -ne $distribution) {
        throw "Package helper audit failed: expected exactly one root-level dlsite-shell-helper.exe."
    }

    Invoke-IsolatedPython @(
        $packagingHelper,
        "audit-dist",
        "--dist", $distribution,
        "--provenance", $provenance
    )
    $certifiBundle = (& $pythonPath -c "import certifi; print(certifi.where())").Trim()
    if (-not $certifiBundle) {
        throw "Canonical certifi bundle path is empty."
    }
    Invoke-IsolatedPython @(
        $packagingHelper,
        "audit-certifi",
        "--dist", $distribution,
        "--source", $certifiBundle
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
    Invoke-IsolatedPython @(
        $packagingHelper,
        "audit-helper",
        "--dist", $distribution
    )
    Invoke-IsolatedPython @(
        $packagingHelper,
        "audit-hygiene",
        "--dist", $distribution
    )

    # Run the TLS checks in a separate frozen runtime using the same custom
    # certifi hook.  This is a packaging gate only and is not shipped to users.
    $tlsSmokeRoot = if ($stagingBuild) {
        Join-Path $outputRoot "tls-runtime-smoke"
    }
    else {
        Join-Path $projectRoot "build\tls-runtime-smoke"
    }
    $tlsSmokeDist = Join-Path $tlsSmokeRoot "dist"
    $tlsSmokeWork = Join-Path $tlsSmokeRoot "work"
    $tlsSmokeSpec = Join-Path $tlsSmokeRoot "spec"
    New-Item -ItemType Directory -Force -Path $tlsSmokeSpec | Out-Null
    Invoke-IsolatedPython @(
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--console",
        "--name", "dlsite-tls-runtime-smoke",
        "--distpath", $tlsSmokeDist,
        "--workpath", $tlsSmokeWork,
        "--specpath", $tlsSmokeSpec,
        "--additional-hooks-dir", (Join-Path $projectRoot "packaging\hooks"),
        (Join-Path $projectRoot "packaging\tls_runtime_smoke.py")
    )
    $tlsSmokeExecutable = Join-Path $tlsSmokeDist "dlsite-tls-runtime-smoke\dlsite-tls-runtime-smoke.exe"
    if (-not (Test-Path -LiteralPath $tlsSmokeExecutable -PathType Leaf)) {
        throw "Frozen TLS smoke executable not found: $tlsSmokeExecutable"
    }
    $tlsSmokeOutput = & $tlsSmokeExecutable 2>&1
    $tlsSmokeExitCode = $LASTEXITCODE
    if ($tlsSmokeOutput) {
        $tlsSmokeOutput | ForEach-Object { Write-Output $_ }
    }
    if ($tlsSmokeExitCode -ne 0) {
        throw "Frozen TLS smoke failed with exit code $tlsSmokeExitCode."
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
    if ($stagingBuild) {
        Write-Output "Staging-only Windows distribution: $distribution"
        Write-Output "Official v1.1.0 ZIP was not written."
    }
    else {
        $archive = Join-Path $projectRoot ("dist\dlsite-organizer-{0}-windows-x64.zip" -f $version)
        if (Test-Path -LiteralPath $archive) {
            Remove-Item -LiteralPath $archive -Force
        }
        Compress-Archive -Path (Join-Path $distribution "*") -DestinationPath $archive
        Get-FileHash -Algorithm SHA256 -LiteralPath $archive
        Write-Output "Windows distribution: $archive"
    }
    Assert-OfficialArtifactUnchanged -Before $officialArchiveBefore
}
finally {
    if ($packagingOutputRootWasSet) {
        if ($null -eq $previousPackagingOutputRoot) {
            Remove-Item Env:DLSITE_PACKAGING_OUTPUT_ROOT -ErrorAction SilentlyContinue
        }
        else {
            $env:DLSITE_PACKAGING_OUTPUT_ROOT = $previousPackagingOutputRoot
        }
    }
    if ($pathWasIsolated) {
        $env:PATH = $processOriginalPath
    }
    Pop-Location
}
