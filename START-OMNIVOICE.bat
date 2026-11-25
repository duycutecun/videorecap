@echo off
chcp 65001 >nul
setlocal EnableExtensions

set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"

powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $r = Invoke-RestMethod -Uri 'http://127.0.0.1:8766/health' -TimeoutSec 2; if ($r) { exit 0 } } catch {}; exit 1" >nul 2>&1
if %errorlevel%==0 (
    echo [OK] Dich vu OmniVoice da dang chay ^(127.0.0.1:8766^).
    exit /b 0
)

powershell -NoProfile -ExecutionPolicy Bypass -Command "$pat = [regex]::Escape((Join-Path $env:ROOT 'omnivoice')) + '.*supervisor\.py'; if (Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -match $pat }) { exit 0 } else { exit 1 }" >nul 2>&1
if %errorlevel%==0 (
    echo [OK] Dich vu OmniVoice da duoc bat truoc do va dang nap mo hinh.
    exit /b 0
)

set "PYW="
if exist "%ROOT%\tools\omni_venv\Scripts\pythonw.exe" set "PYW=%ROOT%\tools\omni_venv\Scripts\pythonw.exe"
if not defined PYW if exist "%ROOT%\omnivoice\.venv\Scripts\pythonw.exe" set "PYW=%ROOT%\omnivoice\.venv\Scripts\pythonw.exe"
if not defined PYW if exist "%ROOT%\tools\omni_venv\Scripts\python.exe" set "PYW=%ROOT%\tools\omni_venv\Scripts\python.exe"
if not defined PYW if exist "%ROOT%\omnivoice\.venv\Scripts\python.exe" set "PYW=%ROOT%\omnivoice\.venv\Scripts\python.exe"

if not defined PYW (
    echo [!] May chua cai moi truong OmniVoice GPU ^(chay install.bat tren may co card NVIDIA de cai^).
    exit /b 1
)

if exist "%ROOT%\omnivoice\STOP" del /f /q "%ROOT%\omnivoice\STOP" >nul 2>&1
start "" "%PYW%" "%ROOT%\omnivoice\supervisor.py"
echo [OK] Da bat ngam dich vu OmniVoice ^(nap mo hinh ~20-30 giay^).
exit /b 0
