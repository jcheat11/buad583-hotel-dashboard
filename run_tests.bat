@echo off
rem Runs the automated tests (fictional data only).
cd /d "%~dp0"
python -m unittest discover -s tests -t . -v
pause
