@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title dr2-save-bridge
set "SAVEDIR=%USERPROFILE%\Documents\My Games\Danganronpa2"
set "VFS=%SAVEDIR%\savedata.vfs"

where python >nul 2>&1 || (echo [!] Python 3 not found. Install it from https://www.python.org/ & pause & exit /b 1)
tasklist /FI "IMAGENAME eq DR2_us.exe" | find /I "DR2_us.exe" >nul && (echo [!] Close Danganronpa 2 first. & pause & exit /b 1)
if not exist "%VFS%" (echo [!] %VFS% not found. Make one normal save on PC first. & pause & exit /b 1)

echo === Current PC slots ===
python dr2_save_bridge.py info "%VFS%"
echo.

set "MAPS="
:ask
set "SRC=" & set "SLOT="
set /p "SRC=PSP save (decrypted .bin or DATKG.BIN, drag-and-drop ok, Enter = done): "
if not defined SRC goto chosen
set "SRC=!SRC:"=!"
if not exist "!SRC!" (echo [!] File not found. & goto ask)
set /p "SLOT=Put it into PC slot number (1-10): "
set "MAPS=!MAPS! --map "!SLOT!=!SRC!""
goto ask
:chosen
if not defined MAPS (echo Nothing to do. & pause & exit /b 0)

echo.
echo Backlog = dialogue history. PSP keeps only the last 9 lines, in the PSP language.
echo Non-English text may not render with the English PC font.
choice /C NY /N /M "Transfer the PSP backlog? [N/Y]: "
if errorlevel 2 (set "BACKLOG=keep") else (set "BACKLOG=clear")

set "EXTRA="
if exist "gamekey.bin" set "EXTRA=!EXTRA! --key gamekey.bin"
if exist "tools\psp-save.exe" set "EXTRA=!EXTRA! --psp-save tools\psp-save.exe"

for /f %%t in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set "TS=%%t"
set "BK=%~dp0backups\%TS%"
mkdir "%BK%" >nul 2>&1
copy /Y "%SAVEDIR%\savedata.*" "%BK%\" >nul || (echo [!] Backup failed, nothing changed. & pause & exit /b 1)
echo Backup: %BK%

python dr2_save_bridge.py convert --pc-vfs "%BK%\savedata.vfs" !MAPS! --backlog %BACKLOG% !EXTRA! --out "%VFS%" --force
if errorlevel 1 (
  copy /Y "%BK%\savedata.vfs" "%VFS%" >nul
  echo [!] Conversion failed, original save restored.
  pause & exit /b 1
)
echo.
echo Done. To undo: copy "%BK%\savedata.vfs" to "%SAVEDIR%"
pause
