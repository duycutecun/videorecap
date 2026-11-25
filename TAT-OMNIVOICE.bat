@echo off
chcp 65001 >nul
setlocal EnableExtensions

set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"

if not exist "%ROOT%\omnivoice" mkdir "%ROOT%\omnivoice" >nul 2>&1
echo stop> "%ROOT%\omnivoice\STOP"

powershell -NoProfile -ExecutionPolicy Bypass -Command "$pat = [regex]::Escape((Join-Path $env:ROOT 'omnivoice')) + '.*(supervisor|omnivoice_server)\.py'; $p = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -match $pat }; $p | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }; Write-Output ('Da tat ' + @($p).Count + ' tien trinh OmniVoice')"
timeout /t 2 >nul
exit /b 0
