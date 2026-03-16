@echo off
setlocal
set "PYTHON_EXE="
for /f "delims=" %%I in ('python -c "import sys; print(sys.executable)" 2^>nul') do set "PYTHON_EXE=%%I"

if defined PYTHON_EXE (
    for %%I in ("%PYTHON_EXE%") do set "PYTHONW_EXE=%%~dpIpythonw.exe"
)

if defined PYTHONW_EXE if exist "%PYTHONW_EXE%" (
    start "" "%PYTHONW_EXE%" "%~dp0scripts\fork_gui.py" %*
    exit /b 0
)

python "%~dp0scripts\fork_gui.py" %*
endlocal
