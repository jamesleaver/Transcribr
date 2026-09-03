@echo off
REM ============================================================
REM Transcribr - Installer (Windows)
REM
REM This .bat file is a thin wrapper that runs install.ps1 with
REM PowerShell. The execution-policy bypass applies only to this
REM single invocation; it does not change any system settings.
REM ============================================================

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"

REM Keep the window open so the user can read the result.
echo.
pause
