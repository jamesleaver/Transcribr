# Transcribr - stage the self-contained Windows application tree.
#
# Produces build\stage\, which windows\transcribr.iss packs into a
# single Setup .exe. Everything the app needs is inside: a relocatable
# CPython, the default engine, and the built web interface. Nothing is
# downloaded on the user's machine at install time.
#
# Deliberately NOT bundled: openai-whisper, which pulls in PyTorch
# (~900 MB). It installs in one click from the app's Models tab, which
# pip-installs into this same tree - %LOCALAPPDATA% is user-writable, so
# that works without any elevation. (mlx-whisper is Apple Silicon only
# and does not apply on Windows at all.)
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

$versionLine = Select-String -Path "transcribr\version.py" -Pattern '^__version__ = "(.*)"' |
    Select-Object -First 1
if (-not $versionLine) { Die "Couldn't read __version__ from transcribr\version.py" }
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
# GitHub allows unauthenticated callers 60 API requests an hour per IP,
# which a shared CI runner can have spent already - so send a token when
# one is in the environment (the workflow passes its own).
$ApiHeaders = @{ "User-Agent" = "Transcribr-build" }
$Token = $env:GITHUB_TOKEN
if (-not $Token) { $Token = $env:GH_TOKEN }
if ($Token) { $ApiHeaders["Authorization"] = "Bearer $Token" }
try {
    $release = Invoke-RestMethod `
        -Uri "https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest" `
        -Headers $ApiHeaders
} catch {
    Die ("Could not ask GitHub for a CPython build: $_" + [Environment]::NewLine +
         "If this says 'rate limit', set GITHUB_TOKEN and run it again.")
}

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

# PyAV first, from the LGPL audio-only wheel windows/build-av-wheel.sh
# builds (the "Build LGPL PyAV wheel" workflow), so faster-whisper finds
# it satisfied and never pulls the stock wheel, whose FFmpeg carries the
# GPL x264/x265 encoders.
$AvVersion = if ($env:AV_VERSION) { $env:AV_VERSION } else { "19.0.0" }
if ($env:AV_WHEELS) {
    & $Py -m pip install --no-warn-script-location --quiet --no-index `
        --find-links $env:AV_WHEELS "av==$AvVersion"
    if ($LASTEXITCODE -ne 0) { Die "No LGPL PyAV $AvVersion wheel in $env:AV_WHEELS" }
    Say "PyAV: the LGPL build from $env:AV_WHEELS"
} elseif ($env:ALLOW_GPL_FFMPEG -eq "1") {
    Say "WARNING: no LGPL PyAV wheel given; using the stock (GPL) one."
} else {
    Die "Set AV_WHEELS to the folder holding the LGPL PyAV wheel, or ALLOW_GPL_FFMPEG=1 for a test build"
}

& $Py -m pip install --no-warn-script-location --quiet `
    faster-whisper sherpa-onnx python-docx reportlab pywebview bottle
if ($LASTEXITCODE -ne 0) { Die "Dependency install failed" }
if ($env:ALLOW_GPL_FFMPEG -ne "1") {
    & $Py -c "import av, sys; sys.exit(bool({'libx264', 'libx265'} & set(av.codecs_available)))"
    if ($LASTEXITCODE -ne 0) { Die "The bundled FFmpeg has GPL encoders; something replaced the LGPL wheel" }
}

Say "Verifying the bundled engine imports..."
& $Py -c "import faster_whisper, bottle, webview, docx, reportlab; print('imports ok')"
if ($LASTEXITCODE -ne 0) { Die "The bundled runtime cannot import its own dependencies" }

# Notices for exactly what was installed, read from each package's own
# metadata.
# The script writes the file itself, as UTF-8: piping it through the
# console fails on Windows, whose default encoding cannot represent the
# typographic characters in some licence texts.
& $Py -m transcribr.notices (Join-Path $StageDir "THIRD-PARTY-NOTICES.txt")
if ($LASTEXITCODE -ne 0) { Die "Could not write THIRD-PARTY-NOTICES.txt" }

# ---- WebView2 bootstrapper --------------------------------------------------
#
# The interface renders in a WebView2 window. Windows 11 and most current
# Windows 10 machines already have the Evergreen runtime, but not all -
# and without it pywebview falls back to the old MSHTML engine, which
# cannot render the interface at all. The script installer handled this
# via winget; the .exe carries Microsoft's bootstrapper instead and runs
# it only when the runtime is genuinely absent. It is ~2 MB.

Say "Fetching the WebView2 bootstrapper..."
try {
    Invoke-WebRequest -Uri "https://go.microsoft.com/fwlink/p/?LinkId=2124703" `
        -OutFile (Join-Path $StageDir "MicrosoftEdgeWebview2Setup.exe")
} catch {
    Die "Could not download the WebView2 bootstrapper: $_"
}

# ---- application files ------------------------------------------------------

Say "Copying the application..."
Copy-Item "transcribr" -Destination $StageDir -Recurse
Copy-Item "webdist" -Destination $StageDir -Recurse
Copy-Item "windows\icon.ico" -Destination $StageDir
Copy-Item "README.md", "LICENSE" -Destination $StageDir
# The installer's licence page (transcribr.iss) shows the EULA, made from
# docs\licence\EULA.md so it cannot drift from it.
& $Py tools\eula_text.py (Join-Path $StageDir "EULA.txt")
if ($LASTEXITCODE -ne 0) { Die "Could not write the EULA for the installer" }

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
