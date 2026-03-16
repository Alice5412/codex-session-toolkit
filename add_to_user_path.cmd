@echo off
setlocal

set "TARGET_DIR=%~dp0"
if "%TARGET_DIR:~-1%"=="\" set "TARGET_DIR=%TARGET_DIR:~0,-1%"

echo Codex Session Toolkit PATH Setup
echo.
echo Target directory:
echo %TARGET_DIR%
echo.

set "WAS_PRESENT=NO"
for /f "delims=" %%I in ('powershell -NoProfile -ExecutionPolicy Bypass -Command "$target = [System.IO.Path]::GetFullPath($env:TARGET_DIR).TrimEnd('\'); $userPath = [Environment]::GetEnvironmentVariable('Path', 'User'); if ($userPath -and (($userPath -split ';') -contains $target)) { 'YES' } else { 'NO' }"') do (
    set "WAS_PRESENT=%%I"
)

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference = 'Stop';" ^
  "$target = [System.IO.Path]::GetFullPath($env:TARGET_DIR).TrimEnd('\');" ^
  "$userPath = [Environment]::GetEnvironmentVariable('Path', 'User');" ^
  "$parts = New-Object System.Collections.Generic.List[string];" ^
  "if ($userPath) { foreach ($item in ($userPath -split ';')) { $trimmed = $item.Trim(); if ($trimmed) { [void]$parts.Add($trimmed) } } };" ^
  "if (-not $parts.Contains($target)) { [void]$parts.Add($target); [Environment]::SetEnvironmentVariable('Path', ($parts -join ';'), 'User') }"

if errorlevel 1 (
    echo Failed to update the user PATH.
    echo Please run this script again from a normal user terminal.
    echo.
    pause
    exit /b 1
)

set "VERIFY_VALUE=NO"
for /f "delims=" %%I in ('powershell -NoProfile -ExecutionPolicy Bypass -Command "$target = [System.IO.Path]::GetFullPath($env:TARGET_DIR).TrimEnd('\'); $userPath = [Environment]::GetEnvironmentVariable('Path', 'User'); if ($userPath -and (($userPath -split ';') -contains $target)) { 'YES' } else { 'NO' }"') do (
    set "VERIFY_VALUE=%%I"
)

if /I "%VERIFY_VALUE%"=="YES" (
    if /I "%WAS_PRESENT%"=="YES" (
        echo Done.
        echo The project directory was already in your user PATH.
    ) else (
        echo Done.
        echo The project directory has been added to your user PATH.
    )
    echo.
    echo Open a new Command Prompt or PowerShell window, then run:
    echo codex-toolkit -ls
    echo.
    echo Legacy aliases still work:
    echo fork -ls
) else (
    echo PATH update may not have completed correctly.
    echo Please check your user PATH manually.
)

echo.
pause
endlocal
