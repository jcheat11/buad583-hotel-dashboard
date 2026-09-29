@echo off
rem Starts the demo dashboard in the background (no window stays open) and opens it in the browser.
rem Afterwards, use the Hotel Dashboard icon in the taskbar tray (near the clock) to open it again or quit.
rem Starting it again while it is running just reopens the dashboard.
start "" "%~dp0python\pythonw.exe" -B "%~dp0app\launch.py" %*
