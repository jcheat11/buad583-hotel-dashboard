@echo off
rem Backup way to stop the dashboard if the tray icon is missing or not responding.
rem Stops only the Python programs that live in THIS folder; other programs are not touched.
powershell -NoProfile -Command "$me = (Resolve-Path '%~dp0python').Path + '\'; $p = Get-Process python, pythonw -ErrorAction SilentlyContinue | Where-Object { $_.Path -and $_.Path.StartsWith($me, 'OrdinalIgnoreCase') }; if ($p) { $p | Stop-Process -Force; 'Hotel Dashboard stopped.' } else { 'The Hotel Dashboard was not running.' }"
timeout /t 3 >nul 2>&1
