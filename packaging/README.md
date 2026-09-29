# Portable Windows Demo (Feasibility Test)

A test of whether the dashboard can run on a Windows computer that does not have Python.
This is not an installer. It always runs the demo (fictional data, uploads off), and the
dashboard code is not changed by anything in this folder.

## Build and test

```
python packaging\build_portable.py
python packaging\test_portable.py
```

- `build_portable.py` creates `dist\HotelDashboard-Demo\` and `dist\HotelDashboard-Demo.zip`.
  It copies this computer's Python plus only the packages in `requirements.txt` (and what they
  need). `local_data\` is never copied.
- `test_portable.py` unzips the build to a temporary folder, runs it without Python on PATH,
  checks it in Edge, and tests shutting down. It prints PASS/FAIL for each check.

The build is a snapshot. After changing the dashboard, build again to update it.

## How the launcher behaves

- `Start Hotel Dashboard.bat` starts `launch.py` with `pythonw.exe` (no window) and closes at once.
- `launch.py` shows a tray icon (Open Dashboard / Quit Dashboard), starts Streamlit as a hidden
  child process on localhost only, and opens the default browser when it is ready.
- Windows stops the dashboard automatically if the tray program ends for any reason.
- Only one copy runs; starting it again reopens the running dashboard.
- Closing the browser tab does not stop it. Quit Dashboard, or the fallback
  `Stop Hotel Dashboard.bat`, stops only the Python programs inside that folder.
- Logs: `%LOCALAPPDATA%\Hotel Asset Dashboard (Demo)\logs` (events and errors only; money-like
  numbers in Streamlit's messages are masked).

## Files

- `launch.py`: the tray launcher described above.
- `portable_files\`: the start and stop scripts and the instructions copied into the build.
  The tray icon is drawn by `build_portable.py`.
