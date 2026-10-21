@echo off
setlocal
title Tev1 Demo starten
cd /d "%~dp0"
echo Tev1 Demo wird vorbereitet. Dieses Fenster bitte offen lassen.
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0Start-Demo.ps1" %*
if errorlevel 1 (
  echo.
  echo Der Start ist fehlgeschlagen. Bitte die Meldung oben beachten.
  pause
)
