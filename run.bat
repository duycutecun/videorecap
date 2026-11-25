@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Tool chua duoc cai dat. Hay chay install.bat truoc.
  pause
  exit /b 1
)
if exist "tools\omni_venv\Scripts\pythonw.exe" (
  call "%~dp0START-OMNIVOICE.bat"
) else if exist "omnivoice\.venv\Scripts\pythonw.exe" (
  call "%~dp0START-OMNIVOICE.bat"
)
set PYTHONIOENCODING=utf-8
".venv\Scripts\python.exe" -m recap serve
pause
