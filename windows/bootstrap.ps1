# Transcribr - one-line installer for Windows
#
#   irm https://raw.githubusercontent.com/jamesleaver/Transcribr/main/windows/bootstrap.ps1 | iex
#
# The Setup .exe on the releases page is the easier route for most
# people. This one exists for the cases it does not cover: a machine
# where policy blocks unsigned installers, an unattended or scripted
# setup, or simply a preference for the terminal.
#
# It fetches the current release's source archive, then runs the
# PowerShell installer inside it - which downloads Python from
# python.org and builds a virtual environment, rather than using the
# self-contained runtime the .exe carries.
#
# Optionally pin a version:
#   & ([scriptblock]::Create((irm https://raw.githubusercontent.com/jamesleaver/Transcribr/main/windows/bootstrap.ps1))) -Version v0.9.14

[CmdletBinding()]
param(
    [string] $Version = ""
)

$ErrorActionPreference = "Stop"
$ProgressPreference     = "SilentlyContinue"   # much faster downloads

$Repo = "jamesleaver/Transcribr"

function Say  ($m) { Write-Host "==> $m" -ForegroundColor Green }
function Die  ($m) { Write-Host "Error: $m" -ForegroundColor Red; exit 1 }

if ($PSVersionTable.PSVersion.Major -lt 5) {
    Die "Windows PowerShell 5 or later is required."
}

# ---- find the release ------------------------------------------------------

if ($Version) {
    $api = "https://api.github.com/repos/$Repo/releases/tags/$Version"
    Say "Looking up Transcribr $Version..."
} else {
    $api = "https://api.github.com/repos/$Repo/releases/latest"
    Say "Looking up the latest Transcribr release..."
}

try {
    $release = Invoke-RestMethod -Uri $api -Headers @{ "User-Agent" = "Transcribr-bootstrap" }
} catch {
    Die "Couldn't reach GitHub. Check your internet connection and try again."
}

$tag = $release.tag_name

# The source archive carries windows\install.ps1 and everything it
# needs. GitHub generates one for every tag, so nothing has to be
# attached to the release for this to work.
$archiveUrl = $release.zipball_url
if (-not $archiveUrl) { Die "That release has no source archive. See https://github.com/$Repo/releases" }

# ---- fetch it --------------------------------------------------------------

$tmp = Join-Path $env:TEMP ("transcribr-install-" + [System.Guid]::NewGuid().ToString("N").Substring(0,8))
New-Item -ItemType Directory -Force -Path $tmp | Out-Null
try {
    $zip = Join-Path $tmp "transcribr.zip"
    Say "Downloading Transcribr $tag..."
    Invoke-WebRequest -Uri $archiveUrl -OutFile $zip `
        -Headers @{ "User-Agent" = "Transcribr-bootstrap" }

    Say "Unpacking..."
    Expand-Archive -Path $zip -DestinationPath (Join-Path $tmp "src") -Force

    $installer = Get-ChildItem -Path (Join-Path $tmp "src") -Recurse -Filter "install.ps1" |
        Select-Object -First 1
    if (-not $installer) { Die "The download didn't contain windows\install.ps1." }

    Say "Starting the installer..."
    Write-Host ""
    & $installer.FullName
    if ($LASTEXITCODE -ne 0 -and $null -ne $LASTEXITCODE) {
        Die "The installer exited with status $LASTEXITCODE."
    }

    Write-Host ""
    Say "Done. Launch Transcribr from your Desktop or Start Menu."
}
finally {
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
}
