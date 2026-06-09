# Transcribr - PowerShell installer
#
# Installs Transcribr and all of its dependencies on Windows 10/11.
# Safe to re-run; skips steps that are already done.
#
# What this installs:
#   - Python 3.12 (x64) from python.org
#   - ffmpeg (via winget, Gyan.FFmpeg)
#   - A Python venv at %LOCALAPPDATA%\Transcribr\venv
#   - openai-whisper inside that venv (reference engine)
#   - faster-whisper inside that venv (CTranslate2; ~4x faster on CPU)
#   - Desktop and Start Menu shortcuts to the app
#
# (mlx-whisper is not installed on Windows; it's Apple Silicon-only.)

$ErrorActionPreference = 'Stop'

# ---------- helpers ---------------------------------------------------------

function Step($n, $total, $title) {
    Write-Host ""
    Write-Host "==> Step ${n}/${total}: $title" -ForegroundColor Cyan
}

function Ok($msg)   { Write-Host "    OK: $msg" -ForegroundColor Green }
function Info($msg) { Write-Host "    $msg" }
function Warn($msg) { Write-Host "    WARNING: $msg" -ForegroundColor Yellow }

function Fail($msg) {
    Write-Host ""
    Write-Host "ERROR: $msg" -ForegroundColor Red
    Read-Host "Press Enter to close"
    exit 1
}

function Confirm($prompt) {
    while ($true) {
        $reply = Read-Host "    $prompt [y/n]"
        switch ($reply.ToLower()) {
            'y' { return $true }
            'yes' { return $true }
            'n' { return $false }
            'no' { return $false }
            default { Write-Host "    Please answer y or n." }
        }
    }
}

# ---------- preflight -------------------------------------------------------

$installerDir = $PSScriptRoot
$sharedDir    = Split-Path $installerDir -Parent
$appDir       = Join-Path $env:LOCALAPPDATA "Transcribr"
$venv         = Join-Path $appDir "venv"
$scriptName   = "transcribr.py"
$iconName     = "icon.ico"

Write-Host ""
Write-Host "==> Transcribr installer" -ForegroundColor Cyan
Info "Source: $installerDir"
$arch = if ([Environment]::Is64BitOperatingSystem) { '64-bit' } else { '32-bit' }
Info "Windows: $([Environment]::OSVersion.Version) ($arch)"
Write-Host ""
Info "This installer will download Python 3.12 (x64) from python.org,"
Info "install ffmpeg via winget, create a Python environment containing"
Info "Whisper, and add Desktop and Start Menu shortcuts."
Write-Host ""
Info "It is safe to re-run."
Write-Host ""
if (-not (Confirm "Continue?")) {
    Info "Aborted."
    exit 0
}

# Required files
$sharedScript = Join-Path $sharedDir $scriptName
$iconFile     = Join-Path $installerDir $iconName

if (-not (Test-Path $sharedScript)) {
    Fail "Missing file: ..\$scriptName (expected one folder up from this installer)"
}
if (-not (Test-Path $iconFile)) {
    Fail "Missing file: $iconName (expected next to install.bat)"
}

# winget is needed for ffmpeg (we install Python directly from python.org).
if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
    Fail "winget is not available. Install 'App Installer' from the Microsoft Store, or update Windows."
}

# Some older Windows configurations default to TLS 1.0/1.1, which python.org
# (and many CDNs) refuse. Force TLS 1.2 for the .NET HTTP client used by
# Invoke-WebRequest. Has no effect if TLS 1.2 is already on, which it is by
# default on Windows 10 1809+ and all of Windows 11.
[Net.ServicePointManager]::SecurityProtocol = `
    [Net.SecurityProtocolType]::Tls12 -bor [Net.ServicePointManager]::SecurityProtocol

# ---------- step 1: Python --------------------------------------------------

Step 1 5 "Python 3.12 (x64)"

# We always install x64 Python, even on ARM64 Windows. Native ARM64 Python
# works for many things, but several packages in the ML/audio stack
# (openai-whisper -> numba -> llvmlite, for one) do not publish ARM64
# Windows wheels and have no source-build path that works out of the box.
# x64 Python runs transparently under Windows-on-ARM emulation; we lose
# perhaps 20-30% performance on transcription, which is well worth the
# avoided headaches.
#
# Strategy:
#   1. If C:\Users\<you>\AppData\Local\Programs\Python\Python312\python.exe
#      exists and reports AMD64, use it.
#   2. Otherwise, download python.org's official 3.12.10 amd64 .exe
#      installer and run it silently. (3.12.10 was the last full release
#      of 3.12 with binary installers.)

function Get-PythonArch($exe) {
    # Reading platform.machine() from inside Python is unreliable under
    # Windows-on-ARM emulation: a child process inherits the parent's
    # PROCESSOR_ARCHITECTURE, so an x64 Python launched from ARM PowerShell
    # reports "ARM64" even though the binary is genuinely x64.
    #
    # Instead we read the machine-type field directly from the PE header
    # of python.exe. From the Microsoft PE/COFF spec:
    #   - bytes [0x3C..0x40)  hold a 4-byte LE pointer to the PE signature
    #   - bytes [PE+0..PE+4)  are the 'PE\0\0' magic
    #   - bytes [PE+4..PE+6)  are the COFF Machine field, little-endian
    # Values we care about: 0x8664 = AMD64 (x64), 0xAA64 = ARM64.
    if (-not (Test-Path $exe)) { return $null }
    try {
        $stream = [System.IO.File]::OpenRead($exe)
        try {
            $bytes = New-Object byte[] 6
            $stream.Position = 0x3C
            [void]$stream.Read($bytes, 0, 4)
            $peOffset = [BitConverter]::ToInt32($bytes, 0)
            $stream.Position = $peOffset + 4   # skip 'PE\0\0'
            [void]$stream.Read($bytes, 0, 2)
            $machine = [BitConverter]::ToUInt16($bytes, 0)
        } finally {
            $stream.Close()
        }
        switch ($machine) {
            0x8664  { return "AMD64" }
            0xAA64  { return "ARM64" }
            0x014C  { return "x86" }
            default { return ("0x{0:X4}" -f $machine) }
        }
    } catch {
        return $null
    }
}

$pyDirX64 = "$env:LOCALAPPDATA\Programs\Python\Python312"
$pyExeX64 = "$pyDirX64\python.exe"

$python312 = $null
$archX64 = Get-PythonArch $pyExeX64
if ($archX64 -eq "AMD64") {
    Ok "Python 3.12 (x64) already installed"
    $python312 = $pyExeX64
}

if (-not $python312) {
    # Check if an ARM64 Python is sitting alongside, just for the log message.
    $pyExeArm = "$env:LOCALAPPDATA\Programs\Python\Python312-arm64\python.exe"
    if (Test-Path $pyExeArm) {
        Info "An ARM64 Python is present at $pyExeArm but we need the x64 build."
        Info "Both can coexist; we'll add the x64 build alongside."
    }

    $pyVer    = "3.12.10"
    $pyUrl    = "https://www.python.org/ftp/python/$pyVer/python-$pyVer-amd64.exe"
    $pyTmp    = Join-Path $env:TEMP "python-$pyVer-amd64.exe"

    Info "Downloading Python $pyVer x64 from python.org..."
    try {
        Invoke-WebRequest -Uri $pyUrl -OutFile $pyTmp -UseBasicParsing
    } catch {
        Fail "Could not download Python installer:`n  $($_.Exception.Message)`n`nIf the machine is offline or behind a proxy, download $pyUrl manually and place it at $pyTmp before re-running."
    }
    if (-not (Test-Path $pyTmp)) { Fail "Python installer was not saved to $pyTmp" }

    Info "Installing Python $pyVer x64 (silent; per-user install, no PATH changes)..."
    # /quiet           : silent install (no UI)
    # InstallAllUsers=0: install for current user only (no admin needed)
    # PrependPath=0    : leave PATH alone; we use a full path to python.exe
    # Include_test=0   : skip the test suite (saves disk)
    # Include_launcher=0: don't install the 'py' launcher; not needed here
    $pyArgs = @(
        "/quiet",
        "InstallAllUsers=0",
        "PrependPath=0",
        "Include_test=0",
        "Include_launcher=0",
        "TargetDir=$pyDirX64"
    )
    Start-Process -FilePath $pyTmp -ArgumentList $pyArgs -Wait
    if (-not (Test-Path $pyExeX64)) {
        Fail "Python install completed but python.exe not found at $pyExeX64"
    }
    Remove-Item -Force $pyTmp -ErrorAction SilentlyContinue

    # Verify it really is x64.
    $archCheck = Get-PythonArch $pyExeX64
    if ($archCheck -ne "AMD64") {
        Fail "Installed Python reports machine='$archCheck', expected 'AMD64'."
    }

    Ok "Python $pyVer (x64) installed at $pyDirX64"
    $python312 = $pyExeX64
}

Info "Using: $python312"

# ---------- step 2: ffmpeg --------------------------------------------------

Step 2 5 "ffmpeg"

$ffmpegId = "Gyan.FFmpeg"
$ffmpegPresent = (winget list -e --id $ffmpegId 2>$null | Select-String $ffmpegId) -ne $null
if ($ffmpegPresent) {
    Ok "ffmpeg already installed"
} else {
    Info "Installing ffmpeg via winget..."
    winget install -e --id $ffmpegId --silent --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) { Fail "ffmpeg install failed (exit $LASTEXITCODE)" }
    Ok "ffmpeg installed"
}

# Locate ffmpeg's bin directory. winget unpacks Gyan.FFmpeg to
#   %LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg_<source>\ffmpeg-<ver>\bin
# (the version directory changes over time; we glob it).
$ffmpegBin = Get-ChildItem `
    -Path "$env:LOCALAPPDATA\Microsoft\WinGet\Packages\Gyan.FFmpeg*" `
    -Filter "bin" -Recurse -Directory -ErrorAction SilentlyContinue |
    Where-Object { Test-Path (Join-Path $_.FullName "ffmpeg.exe") } |
    Select-Object -First 1

if ($ffmpegBin) {
    $ffmpegDir = $ffmpegBin.FullName
    Info "ffmpeg location: $ffmpegDir"
} else {
    Warn "Could not auto-locate ffmpeg.exe. The launcher will rely on PATH instead."
    $ffmpegDir = $null
}

# ---------- step 3: venv ----------------------------------------------------

Step 3 5 "Python virtual environment"

if (-not (Test-Path $appDir)) {
    New-Item -ItemType Directory -Path $appDir | Out-Null
}

if (Test-Path "$venv\Scripts\activate.bat") {
    Info "Existing venv at: $venv"

    # If the existing venv's python is NOT x64, force-recreate it. This
    # happens automatically on ARM64 Windows machines that ran an earlier
    # version of this installer (which used native ARM Python).
    $venvArch = Get-PythonArch "$venv\Scripts\python.exe"
    if ($venvArch -and $venvArch -ne "AMD64") {
        Info "Existing venv was built from $venvArch Python; rebuilding with x64 Python."
        Remove-Item -Recurse -Force $venv
    }
    elseif (Confirm "Recreate from scratch (slower but cleanest)?") {
        Remove-Item -Recurse -Force $venv
    }
}

if (-not (Test-Path "$venv\Scripts\activate.bat")) {
    Info "Creating venv..."
    & $python312 -m venv $venv
    if ($LASTEXITCODE -ne 0) { Fail "venv creation failed" }
}

# Sanity check: tkinter must be importable. On standard Windows Python from
# python.org / winget, tkinter ships built-in.
& "$venv\Scripts\python.exe" -c "import tkinter; tkinter.Tk().destroy()" 2>$null
if ($LASTEXITCODE -ne 0) {
    Warn "tkinter test failed. The GUI may not start."
}
Ok "venv ready"

# ---------- step 4: Whisper -------------------------------------------------

Step 4 5 "Whisper engines (this is the slow step)"

Info "Upgrading pip..."
& "$venv\Scripts\python.exe" -m pip install --upgrade pip --quiet
if ($LASTEXITCODE -ne 0) { Fail "pip upgrade failed" }

Info "Installing openai-whisper (downloads PyTorch, ~2GB)..."
# Pin openai-whisper >= 20250625; older releases use the removed
# pkg_resources module, which is gone in setuptools 81+.
& "$venv\Scripts\python.exe" -m pip install --upgrade `
    "openai-whisper>=20250625" python-docx
if ($LASTEXITCODE -ne 0) { Fail "openai-whisper / python-docx install failed" }

# Verify whisper imports
& "$venv\Scripts\python.exe" -c "import whisper" 2>$null
if ($LASTEXITCODE -ne 0) { Fail "whisper import test failed" }
Ok "openai-whisper installed"

# faster-whisper - CTranslate2-based engine. Best CPU-only speed; also
# uses CUDA if a compatible GPU is present. Wheels exist for x64 Windows.
Info "Installing faster-whisper..."
& "$venv\Scripts\python.exe" -m pip install --upgrade faster-whisper
if ($LASTEXITCODE -ne 0) {
    Warn "faster-whisper install failed - the app will still run, but only the OpenAI engine will be available."
} else {
    & "$venv\Scripts\python.exe" -c "import faster_whisper" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Warn "faster-whisper import check failed; it will not be offered in the app."
    } else {
        Ok "faster-whisper installed"
    }
}

# ---------- step 5: Application files, launcher, shortcuts ------------------

Step 5 5 "Application files and shortcuts"

Copy-Item $sharedScript "$appDir\$scriptName" -Force
Copy-Item $iconFile     "$appDir\$iconName"   -Force

# Optional PNG copy — used by the in-app About dialog. Tk's PhotoImage
# reads PNG but not .ico/.icns, so we ship a separate file.
$sharedPng = Join-Path $sharedDir "icon.png"
if (Test-Path $sharedPng) {
    Copy-Item $sharedPng "$appDir\icon.png" -Force
}

# Copy README.md so the in-app "View README" button can find it.
$sharedReadme = Join-Path $sharedDir "README.md"
if (Test-Path $sharedReadme) {
    Copy-Item $sharedReadme "$appDir\README.md" -Force
}
Ok "Copied app files to $appDir"

# launch.bat - sets PATH, runs pythonw, captures errors to log.
$pathLine = if ($ffmpegDir) { "set `"PATH=$ffmpegDir;%PATH%`"" } else { "" }
$launchBat = @"
@echo off
REM Transcribr launcher (generated by installer)

set "VENV=$venv"
set "SCRIPT=$appDir\$scriptName"
$pathLine

set "LOG_DIR=%LOCALAPPDATA%\Transcribr"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
set "LOG=%LOG_DIR%\launch.log"

echo ==== %DATE% %TIME% ==== >> "%LOG%"
"%VENV%\Scripts\pythonw.exe" "%SCRIPT%" >> "%LOG%" 2>&1
echo Exit code: %ERRORLEVEL% >> "%LOG%"
"@
Set-Content -Path "$appDir\launch.bat" -Value $launchBat -Encoding ASCII

# launch.vbs - runs launch.bat in a hidden window so no console flashes.
$launchVbs = @"
Set WShell = CreateObject("WScript.Shell")
WShell.Run """$appDir\launch.bat""", 0, False
"@
Set-Content -Path "$appDir\launch.vbs" -Value $launchVbs -Encoding ASCII
Ok "Launcher created"

# Shortcuts
function New-Shortcut($lnkPath) {
    $shell = New-Object -ComObject WScript.Shell
    $lnk = $shell.CreateShortcut($lnkPath)
    $lnk.TargetPath       = "$appDir\launch.vbs"
    $lnk.WorkingDirectory = $appDir
    $lnk.IconLocation     = "$appDir\$iconName"
    $lnk.Description      = "Transcribr"
    $lnk.Save()
}

$desktop  = [Environment]::GetFolderPath('Desktop')
$startMnu = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs"

New-Shortcut "$desktop\Transcribr.lnk"
Ok "Desktop shortcut created"

New-Shortcut "$startMnu\Transcribr.lnk"
Ok "Start Menu shortcut created"

# ---------- done ------------------------------------------------------------

Write-Host ""
Write-Host "==> Done!" -ForegroundColor Green
Info "Transcribr has been installed."
Write-Host ""
Info "Launch it from:"
Info "  - your Desktop (double-click 'Transcribr')"
Info "  - the Start Menu (search 'whisper')"
Write-Host ""
Info "If anything misbehaves, the launch log is at:"
Info "  $appDir\launch.log"
Write-Host ""
Info "If anything misbehaves, run install.bat again - it is safe to repeat."
Write-Host ""
