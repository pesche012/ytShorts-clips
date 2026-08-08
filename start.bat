@echo off
setlocal EnableExtensions
cd /d "%~dp0"

if not exist ".venv\Scripts\pythonw.exe" (
  echo First-time setup is required.
  call setup.bat
  exit /b
)

set "PATH=%LocalAppData%\Microsoft\WinGet\Links;%USERPROFILE%\.deno\bin;%PATH%"
start "" ".venv\Scripts\pythonw.exe" "app.py"
