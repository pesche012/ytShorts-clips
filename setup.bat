@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "LOG_FILE=%~dp0setup.log"
>"%LOG_FILE%" echo YouTube Shorts Maker setup log

echo.
echo ========================================
echo  YouTube Shorts Maker - First Setup
echo ========================================
echo.

echo [1/4] Checking Python...
call :find_python
if not defined PYTHON_EXE (
  echo Python 3.12 is not installed. Installing it now...
  where winget >nul 2>nul
  if errorlevel 1 goto :no_winget
  winget install --id Python.Python.3.12 -e --scope user --accept-package-agreements --accept-source-agreements >>"%LOG_FILE%" 2>&1
  if errorlevel 1 goto :error
  call :find_python
)
if not defined PYTHON_EXE goto :python_missing
"%PYTHON_EXE%" -c "import sys; assert sys.version_info >= (3, 11)" >>"%LOG_FILE%" 2>&1
if errorlevel 1 goto :python_missing

echo [2/4] Creating the app environment...
if not exist ".venv\Scripts\python.exe" (
  "%PYTHON_EXE%" -m venv .venv >>"%LOG_FILE%" 2>&1
  if errorlevel 1 goto :error
)

echo [3/4] Installing video and AI components...
echo       This may take several minutes on the first run.
".venv\Scripts\python.exe" -m pip install --upgrade pip >>"%LOG_FILE%" 2>&1
if errorlevel 1 goto :error
".venv\Scripts\python.exe" -m pip install -r requirements.txt >>"%LOG_FILE%" 2>&1
if errorlevel 1 goto :error

echo [4/4] Checking the YouTube runtime...
set "PATH=%LocalAppData%\Microsoft\WinGet\Links;%USERPROFILE%\.deno\bin;%PATH%"
call :find_deno
if not defined DENO_EXE (
  echo Deno is not installed. Installing it now...
  where winget >nul 2>nul
  if errorlevel 1 goto :no_winget
  winget install --id DenoLand.Deno -e --accept-package-agreements --accept-source-agreements >>"%LOG_FILE%" 2>&1
  if errorlevel 1 goto :error
  call :find_deno
)
if not defined DENO_EXE goto :deno_missing
"%DENO_EXE%" --version >>"%LOG_FILE%" 2>&1
if errorlevel 1 goto :error

".venv\Scripts\python.exe" -c "import imageio_ffmpeg, yt_dlp, requests, faster_whisper, keyring; print(imageio_ffmpeg.get_ffmpeg_exe())" >>"%LOG_FILE%" 2>&1
if errorlevel 1 goto :error

where git >nul 2>nul
if not errorlevel 1 if exist ".git" git config core.hooksPath .githooks >>"%LOG_FILE%" 2>&1

echo.
echo Setup completed successfully. Starting the app...
echo Setup completed successfully.>>"%LOG_FILE%"
start "" ".venv\Scripts\pythonw.exe" "app.py"
exit /b 0

:find_deno
set "DENO_EXE="
where deno >nul 2>nul
if not errorlevel 1 set "DENO_EXE=deno.exe"
if not defined DENO_EXE if exist "%USERPROFILE%\.deno\bin\deno.exe" set "DENO_EXE=%USERPROFILE%\.deno\bin\deno.exe"
if not defined DENO_EXE if exist "%LocalAppData%\Microsoft\WinGet\Packages\DenoLand.Deno_Microsoft.Winget.Source_8wekyb3d8bbwe\deno.exe" set "DENO_EXE=%LocalAppData%\Microsoft\WinGet\Packages\DenoLand.Deno_Microsoft.Winget.Source_8wekyb3d8bbwe\deno.exe"
if not defined DENO_EXE for /f "delims=" %%D in ('where /r "%LocalAppData%\Microsoft\WinGet\Packages" deno.exe 2^>nul') do set "DENO_EXE=%%D"
exit /b 0

:find_python
set "PYTHON_EXE="
if exist "%LocalAppData%\Programs\Python\Python313\python.exe" set "PYTHON_EXE=%LocalAppData%\Programs\Python\Python313\python.exe"
if not defined PYTHON_EXE if exist "%LocalAppData%\Programs\Python\Python312\python.exe" set "PYTHON_EXE=%LocalAppData%\Programs\Python\Python312\python.exe"
if not defined PYTHON_EXE if exist "%LocalAppData%\Programs\Python\Python311\python.exe" set "PYTHON_EXE=%LocalAppData%\Programs\Python\Python311\python.exe"
if not defined PYTHON_EXE if exist "%ProgramFiles%\Python313\python.exe" set "PYTHON_EXE=%ProgramFiles%\Python313\python.exe"
if not defined PYTHON_EXE if exist "%ProgramFiles%\Python312\python.exe" set "PYTHON_EXE=%ProgramFiles%\Python312\python.exe"
if not defined PYTHON_EXE (
  where py >nul 2>nul
  if not errorlevel 1 set "PYTHON_EXE=py.exe"
)
exit /b 0

:no_winget
echo ERROR: Windows Package Manager (winget) was not found.>>"%LOG_FILE%"
echo.
echo Setup could not continue because winget was not found.
echo See setup.log for details.
pause
exit /b 1

:python_missing
echo ERROR: Python 3.11 or newer was not found.>>"%LOG_FILE%"
echo.
echo Python could not be installed or found.
echo See setup.log for details.
pause
exit /b 1

:deno_missing
echo ERROR: Deno was installed but its executable was not found.>>"%LOG_FILE%"
echo.
echo Deno could not be found after installation.
echo See setup.log for details.
pause
exit /b 1

:error
echo ERROR: A setup command failed.>>"%LOG_FILE%"
echo.
echo Setup failed. The window will remain open.
echo Error details are saved here:
echo %LOG_FILE%
echo.
type "%LOG_FILE%"
pause
exit /b 1
