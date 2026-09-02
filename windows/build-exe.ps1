# Transcribr - stage the self-contained Windows application tree.
#
# Produces build\stage\, which windows\transcribr.iss packs into a
# single Setup .exe. Everything the app needs is inside: a relocatable
# CPython, the default engine, and the built web interface. Nothing is
# downloaded on the user's machine at install time.
#
# Deliberately NOT bundled: openai-whisper (PyTorch, ~900 MB) and
# mlx-whisper (Apple Silicon only, and it hard-requires PyTorch too).
# Both remain one click away in the app's Models tab, which pip-installs
# into this same tree - %LOCALAPPDATA% is user-writable, so that works
# without any elevation.
#
# Usage:  pwsh -File windows\build-exe.ps1

[CmdletBinding()]
param(
    [string] $PythonVersion = "3.12",
    [string] $StageDir      = "build\stage"
)

$ErrorActionPreference = "Stop"
$ProgressPreference     = "SilentlyContinue"   # much faster downloads

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

function Say ($m) { Write-Host "==> $m" -ForegroundColor Green }
function Die ($m) { Write-Host "Error: $m" -ForegroundColor Red; exit 1 }

# ---- version ---------------------------------------------------------------

$versionLine = Select-String -Path "transcribr.py" -Pattern '^__version__ = "(.*)"' |
    Select-Object -First 1
if (-not $versionLine) { Die "Couldn't read __version__ from transcribr.py" }
$Version = $versionLine.Matches[0].Groups[1].Value
Say "Building Transcribr $Version"

if (-not (Test-Path "webdist\index.html")) {
    Die "webdist\index.html is missing - run 'npm ci; npm run build' in web\ first."
}

# ---- clean stage -----------------------------------------------------------

if (Test-Path $StageDir) { Remove-Item -Recurse -Force $StageDir }
New-Item -ItemType Directory -Force -Path $StageDir | Out-Null

# ---- fetch a relocatable CPython -------------------------------------------

Say "Resolving a standalone CPython $PythonVersion build..."
$release = Invoke-RestMethod `
    -Uri "https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest" `
    -Headers @{ "User-Agent" = "Transcribr-build" }

# install_only_stripped: no debug symbols, which is most of the size.
$pattern = "cpython-$([regex]::Escape($PythonVersion))\.\d+\+\d+-x86_64-pc-windows-msvc-install_only_stripped\.tar\.gz$"
$asset = $release.assets | Where-Object { $_.name -match $pattern } | Select-Object -First 1
if (-not $asset) {
    # Older releases only shipped the unstripped archive.
    $pattern = "cpython-$([regex]::Escape($PythonVersion))\.\d+\+\d+-x86_64-pc-windows-msvc-install_only\.tar\.gz$"
    $asset = $release.assets | Where-Object { $_.name -match $pattern } | Select-Object -First 1
}
if (-not $asset) { Die "No x86_64-pc-windows-msvc build of CPython $PythonVersion in $($release.tag_name)" }

Say "Downloading $($asset.name) ($([math]::Round($asset.size/1MB,1)) MB)..."
$tarball = Join-Path $env:TEMP $asset.name
Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $tarball

Say "Unpacking the runtime..."
# tar ships with Windows 10+; the archive contains a single python\ root.
tar -xzf $tarball -C $StageDir
if ($LASTEXITCODE -ne 0) { Die "Failed to unpack $tarball" }

$Py = Join-Path $StageDir "python\python.exe"
if (-not (Test-Path $Py)) { Die "Runtime unpacked but $Py is missing" }
& $Py --version
if ($LASTEXITCODE -ne 0) { Die "The bundled interpreter does not run" }

# ---- application dependencies ----------------------------------------------

Say "Installing application libraries and the faster-whisper engine..."
& $Py -m ensurepip --upgrade 2>$null | Out-Null     # no-op if pip is present
& $Py -m pip install --upgrade pip --quiet
if ($LASTEXITCODE -ne 0) { Die "Could not bootstrap pip in the bundled runtime" }

& $Py -m pip install --no-warn-script-location --quiet `
    faster-whisper sherpa-onnx python-docx reportlab pywebview bottle
if ($LASTEXITCODE -ne 0) { Die "Dependency install failed" }

Say "Verifying the bundled engine imports..."
& $Py -c "import faster_whisper, bottle, webview, docx, reportlab; print('imports ok')"
if ($LASTEXITCODE -ne 0) { Die "The bundled runtime cannot import its own dependencies" }

# ---- application files ------------------------------------------------------

Say "Copying the application..."
Copy-Item "transcribr.py" -Destination $StageDir
Copy-Item "webdist" -Destination $StageDir -Recurse
Copy-Item "windows\icon.ico" -Destination $StageDir
Copy-Item "README.md", "LICENSE" -Destination $StageDir

# ---- prune ------------------------------------------------------------------
#
# Bytecode is regenerated on first run, and CPython's own test suite is
# tens of megabytes of things no user needs.

Say "Pruning..."
$before = (Get-ChildItem $StageDir -Recurse -File | Measure-Object Length -Sum).Sum

Get-ChildItem $StageDir -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
    Sort-Object { $_.FullName.Length } -Descending |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

foreach ($junk in @("python\Lib\test", "python\Lib\site-packages\PyObjCTest")) {
    $p = Join-Path $StageDir $junk
    if (Test-Path $p) { Remove-Item -Recurse -Force $p }
}

$after = (Get-ChildItem $StageDir -Recurse -File | Measure-Object Length -Sum).Sum
Say ("Staged {0:N0} MB (pruned {1:N0} MB)" -f ($after/1MB), (($before-$after)/1MB))

# ---- hand the version to Inno Setup ----------------------------------------

Set-Content -Path (Join-Path "build" "version.txt") -Value $Version -NoNewline
Say "Stage ready at $StageDir"
