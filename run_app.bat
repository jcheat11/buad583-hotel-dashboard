@echo off
rem Starts the Hotel Asset Dashboard on THIS computer only (not visible to other computers).
cd /d "%~dp0"
echo Starting the Hotel Asset Dashboard at http://localhost:8501
echo Keep this window open while you use the app. Close it to stop the app.
start "" cmd /c "timeout /t 4 >nul & start http://localhost:8501"
python -m streamlit run app.py --server.address localhost --server.port 8501
pause
