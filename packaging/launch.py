"""
Starts the portable Hotel Asset Dashboard (fictional demo data) in the background, opens it in the
default browser, and puts a "Hotel Dashboard" icon in the Windows taskbar tray (near the clock) with
two commands: Open Dashboard and Quit Dashboard. No command window stays open.

How it fits together:
  - This program (run with pythonw.exe, which has no window) owns the tray icon.
  - It starts Streamlit as a hidden child process that listens on this computer only (localhost).
  - Windows ties the child to this program (a "job object"): if this program ends for ANY reason,
    Windows stops the dashboard too, so nothing is left running in the background.
  - Only one copy runs at a time. Starting it again just reopens the dashboard in the browser.
  - Closing the browser tab does NOT stop the dashboard. Use Quit Dashboard in the tray.

Diagnostic logs: %LOCALAPPDATA%\\Hotel Asset Dashboard (Demo)\\logs
They record start/stop events and errors only. Figures from the dashboard are never logged, and
any money-like numbers in Streamlit's own messages are masked before they are written.
"""
import ctypes
import ctypes.wintypes as w
import json
import logging
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent                 # ...\HotelDashboard-Demo\app
APP_NAME = "Hotel Asset Dashboard (Demo)"
DATA_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / APP_NAME
LOG_DIR = DATA_DIR / "logs"
STATE_FILE = DATA_DIR / "running.json"                 # where the running copy says which address it uses
MUTEX_NAME = "Local\\HotelAssetDashboardDemo"          # Windows' "only one copy" marker
WINDOW_CLASS = "HotelAssetDashboardDemoTray"           # hidden window that receives tray clicks
NO_BROWSER = "--no-browser" in sys.argv                # used by the automated tests only

user32 = ctypes.WinDLL("user32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
log = logging.getLogger("launcher")


# ------------------------------------------------------------------------------------------ logs
def setup_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    for name in ("launcher.log", "streamlit.log"):     # keep each log small: one previous copy
        f = LOG_DIR / name
        if f.exists() and f.stat().st_size > 1_000_000:
            f.replace(f.with_suffix(".old.log"))
    handler = logging.FileHandler(LOG_DIR / "launcher.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [pid %(process)d] %(message)s",
                                           "%Y-%m-%d %H:%M:%S"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)


# Money amounts, decimals, numbers with thousands separators and long numbers are masked.
# Short whole numbers (such as the port 8501 or a line number) are kept for diagnosis.
FIGURE = re.compile(r"\$\s?-?[\d,]+(\.\d+)?|-?\d+\.\d+|\d{1,3}(,\d{3})+|\d{5,}")


def mask_figures(text: str) -> str:
    return FIGURE.sub("[number]", text)


def message_box(text: str) -> None:
    user32.MessageBoxW(None, text, APP_NAME, 0x40)     # information icon


# ------------------------------------------------------------------------------- one copy only
def already_running() -> bool:
    """True if another copy holds the marker. The marker disappears when that copy's process ends."""
    kernel32.CreateMutexW.restype = w.HANDLE
    handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    globals()["_mutex"] = handle                       # keep it open for as long as this copy runs
    return ctypes.get_last_error() == 183              # ERROR_ALREADY_EXISTS


def reopen_existing() -> None:
    """Second launch: wait for the running copy to be ready, then open its address."""
    log.info("Already running: reopening the existing dashboard instead of starting a second copy")
    deadline = time.time() + 120
    while time.time() < deadline:
        try:
            url = json.loads(STATE_FILE.read_text())["url"]
            urllib.request.urlopen(url + "_stcore/health", timeout=2)
            open_browser(url)
            return
        except (OSError, ValueError, KeyError):
            time.sleep(0.5)
    log.error("The running copy did not answer within 2 minutes")
    message_box("The Hotel Dashboard is already starting but is not answering yet.\n\n"
                "Wait a moment and try again, or use Quit Dashboard in the tray icon.\n\n"
                f"Diagnostic logs: {LOG_DIR}")


def open_browser(url: str) -> None:
    log.info("Open Dashboard: %s%s", url, " (browser not opened: test mode)" if NO_BROWSER else "")
    if not NO_BROWSER:
        webbrowser.open(url)


# ------------------------------------------------------------------------------ the dashboard
def free_port(first: int = 8501) -> int:
    for port in range(first, first + 50):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("No free port between 8501 and 8550")


class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", w.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", w.DWORD),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", w.DWORD), ("SchedulingClass", w.DWORD)]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", ctypes.c_uint64 * 6), ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t)]


def tie_to_this_program(process: subprocess.Popen) -> None:
    """Windows stops the child automatically when this program ends (even if it crashes)."""
    kernel32.CreateJobObjectW.restype = w.HANDLE
    kernel32.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
    kernel32.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
    job = kernel32.CreateJobObjectW(None, None)
    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = 0x2000     # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    ok = (job and kernel32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))
          and kernel32.AssignProcessToJobObject(job, int(process._handle)))
    globals()["_job"] = job                            # the job closes (and the child stops) when we exit
    log.info("Dashboard tied to the tray program: %s", "yes" if ok else f"NO (error {ctypes.get_last_error()})")


class Dashboard:
    def __init__(self) -> None:
        self.port = free_port()
        self.url = f"http://localhost:{self.port}/"
        self.process: subprocess.Popen | None = None
        self.stopping = False

    def start(self) -> None:
        python = Path(sys.executable).with_name("python.exe")
        args = [str(python), "-B", "-m", "streamlit", "run", str(HERE / "demo_app.py"),
                "--server.address", "localhost", "--server.port", str(self.port),
                "--server.headless", "true", "--browser.gatherUsageStats", "false",
                "--server.fileWatcherType", "none", "--global.developmentMode", "false",
                "--client.toolbarMode", "viewer",                      # hides Streamlit's "Deploy" button
                "--logger.messageFormat", "%(levelname)s %(name)s: %(message)s"]
        self.process = subprocess.Popen(args, cwd=HERE, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW)
        tie_to_this_program(self.process)
        log.info("Started dashboard process %s on %s (this computer only)", self.process.pid, self.url)
        threading.Thread(target=self._copy_output, daemon=True).start()

    def _copy_output(self) -> None:
        """Streamlit's messages go to streamlit.log, with any figures masked."""
        with open(LOG_DIR / "streamlit.log", "a", encoding="utf-8") as out:
            out.write(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')} dashboard started on port {self.port}\n")
            for raw in self.process.stdout:
                line = raw.decode("utf-8", "replace").rstrip()
                out.write(f"{time.strftime('%H:%M:%S')} {mask_figures(line)}\n")
                out.flush()

    def wait_until_ready(self, seconds: int = 180) -> bool:
        deadline = time.time() + seconds
        while time.time() < deadline and self.process.poll() is None:
            try:
                urllib.request.urlopen(self.url + "_stcore/health", timeout=1)
                return True
            except OSError:
                time.sleep(0.5)
        return False

    def stop(self) -> None:
        self.stopping = True
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
        STATE_FILE.unlink(missing_ok=True)
        log.info("Dashboard stopped; port %s released", self.port)


# ------------------------------------------------------------------------------ the tray icon
LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, w.HWND, w.UINT, w.WPARAM, w.LPARAM)
user32.DefWindowProcW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
user32.DefWindowProcW.restype = LRESULT
user32.CreateWindowExW.restype = w.HWND
user32.CreateWindowExW.argtypes = [w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, w.HWND, w.HMENU, w.HINSTANCE, w.LPVOID]
user32.LoadImageW.restype = w.HANDLE
user32.LoadIconW.restype = w.HANDLE
user32.CreatePopupMenu.restype = w.HMENU
user32.AppendMenuW.argtypes = [w.HMENU, w.UINT, ctypes.c_size_t, w.LPCWSTR]
user32.TrackPopupMenu.argtypes = [w.HMENU, w.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int, w.HWND, w.LPVOID]
user32.PostMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
user32.SetForegroundWindow.argtypes = [w.HWND]
user32.DestroyWindow.argtypes = [w.HWND]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", w.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", w.HINSTANCE), ("hIcon", w.HICON),
                ("hCursor", w.HANDLE), ("hbrBackground", w.HBRUSH), ("lpszMenuName", w.LPCWSTR),
                ("lpszClassName", w.LPCWSTR)]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [("cbSize", w.DWORD), ("hWnd", w.HWND), ("uID", w.UINT), ("uFlags", w.UINT),
                ("uCallbackMessage", w.UINT), ("hIcon", w.HICON), ("szTip", w.WCHAR * 128),
                ("dwState", w.DWORD), ("dwStateMask", w.DWORD), ("szInfo", w.WCHAR * 256),
                ("uVersion", w.UINT), ("szInfoTitle", w.WCHAR * 64), ("dwInfoFlags", w.DWORD),
                ("guidItem", ctypes.c_byte * 16), ("hBalloonIcon", w.HICON)]


WM_DESTROY, WM_CLOSE, WM_COMMAND, WM_ENDSESSION = 0x0002, 0x0010, 0x0111, 0x0016
WM_LBUTTONUP, WM_LBUTTONDBLCLK, WM_RBUTTONUP = 0x0202, 0x0203, 0x0205
WM_TRAY, WM_READY, WM_STOPPED = 0x8001, 0x8002, 0x8003          # our own messages (WM_APP + n)
ID_OPEN, ID_QUIT = 1001, 1002                                   # menu commands (the tests use these too)
NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO = 0x1, 0x2, 0x4, 0x10


class Tray:
    def __init__(self, dashboard: Dashboard) -> None:
        self.dashboard = dashboard
        self.ready = False
        self.taskbar_created = user32.RegisterWindowMessageW("TaskbarCreated")   # Explorer restarted
        self._proc = WNDPROC(self._on_message)          # kept on self so Python doesn't discard it
        kernel32.GetModuleHandleW.restype = w.HMODULE
        hinstance = kernel32.GetModuleHandleW(None)
        cls = WNDCLASSW(lpfnWndProc=self._proc, hInstance=hinstance, lpszClassName=WINDOW_CLASS)
        user32.RegisterClassW(ctypes.byref(cls))
        self.hwnd = user32.CreateWindowExW(0, WINDOW_CLASS, APP_NAME, 0, 0, 0, 0, 0, None, None, hinstance, None)
        if not self.hwnd:
            raise RuntimeError(f"Could not create the tray window (error {ctypes.get_last_error()})")
        icon_file = HERE / "dashboard.ico"
        self.icon = (user32.LoadImageW(None, str(icon_file), 1, 0, 0, 0x10 | 0x40) if icon_file.exists()
                     else None) or user32.LoadIconW(None, ctypes.c_void_p(32512))
        self._notify(NIM_ADD, "Hotel Dashboard (demo): starting…")

    def _notify(self, action: int, tip: str, title: str = "", text: str = "") -> bool:
        data = NOTIFYICONDATAW(cbSize=ctypes.sizeof(NOTIFYICONDATAW), hWnd=self.hwnd, uID=1,
                               uFlags=NIF_MESSAGE | NIF_ICON | NIF_TIP | (NIF_INFO if text else 0),
                               uCallbackMessage=WM_TRAY, hIcon=self.icon, szTip=tip,
                               szInfoTitle=title, szInfo=text, dwInfoFlags=1 if text else 0)
        ok = bool(shell32.Shell_NotifyIconW(action, ctypes.byref(data)))
        if action == NIM_ADD:
            log.info("Tray icon added: %s", "yes" if ok else "NO")
        return ok

    def show_menu(self) -> None:
        menu = user32.CreatePopupMenu()
        state = "running on this computer" if self.ready else "starting…"
        user32.AppendMenuW(menu, 0x1, 0, f"Hotel Dashboard (demo) · {state}")   # greyed title
        user32.AppendMenuW(menu, 0x800, 0, None)                                  # separator
        user32.AppendMenuW(menu, 0x0 if self.ready else 0x1, ID_OPEN, "Open Dashboard")
        user32.AppendMenuW(menu, 0x0, ID_QUIT, "Quit Dashboard")
        user32.SetMenuDefaultItem(menu, ID_OPEN, False)
        point = w.POINT()
        user32.GetCursorPos(ctypes.byref(point))
        user32.SetForegroundWindow(self.hwnd)             # lets the menu close when clicking elsewhere
        user32.TrackPopupMenu(menu, 0x0002, point.x, point.y, 0, self.hwnd, None)
        user32.PostMessageW(self.hwnd, 0, 0, 0)
        user32.DestroyMenu(menu)

    def quit(self, reason: str) -> None:
        log.info("Quit Dashboard (%s)", reason)
        self._notify(NIM_DELETE, "")
        self.dashboard.stop()
        user32.DestroyWindow(self.hwnd)

    def _on_message(self, hwnd, msg, wparam, lparam):
        try:
            return self._handle(hwnd, msg, wparam, lparam)
        except Exception:                                 # never let a tray click crash silently
            log.exception("Tray error")
            return 0

    def _handle(self, hwnd, msg, wparam, lparam):
        if msg == WM_TRAY:
            if lparam == WM_LBUTTONDBLCLK and self.ready:
                open_browser(self.dashboard.url)
            elif lparam in (WM_LBUTTONUP, WM_RBUTTONUP):
                self.show_menu()
            return 0
        if msg == WM_COMMAND:
            if wparam & 0xFFFF == ID_OPEN and self.ready:
                open_browser(self.dashboard.url)
            elif wparam & 0xFFFF == ID_QUIT:
                self.quit("tray menu")
            return 0
        if msg == WM_READY:
            self.ready = True
            self._notify(NIM_MODIFY, "Hotel Dashboard (demo): running. Click to open or quit.",
                         "Hotel Dashboard is running",
                         "Click this icon to open the dashboard or quit it. "
                         "Closing the browser tab does not stop it.")
            return 0
        if msg == WM_STOPPED:
            self.quit("dashboard stopped unexpectedly")
            message_box("The Hotel Dashboard stopped unexpectedly.\n\n"
                        f"Diagnostic logs (no financial figures): {LOG_DIR}")
            return 0
        if msg == WM_ENDSESSION and wparam:
            self.quit("Windows is signing out or shutting down")
            return 0
        if msg == WM_CLOSE:
            self.quit("window closed")
            return 0
        if msg == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        if msg == self.taskbar_created:
            self._notify(NIM_ADD, "Hotel Dashboard (demo): running. Click to open or quit.")
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def run(self) -> None:
        msg = w.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))


# ------------------------------------------------------------------------------------ start
def watch(dashboard: Dashboard, tray: Tray) -> None:
    """Background: wait until the dashboard answers, open the browser, then watch for a crash."""
    started = time.time()
    if not dashboard.wait_until_ready():
        if not dashboard.stopping:
            log.error("Dashboard did not start (exit code %s)", dashboard.process.poll())
            user32.PostMessageW(tray.hwnd, WM_STOPPED, 0, 0)
        return
    log.info("Dashboard ready after %.0f s", time.time() - started)
    STATE_FILE.write_text(json.dumps({"url": dashboard.url, "tray_pid": os.getpid(),
                                      "dashboard_pid": dashboard.process.pid, "app_folder": str(HERE.parent)}))
    user32.PostMessageW(tray.hwnd, WM_READY, 0, 0)
    open_browser(dashboard.url)
    code = dashboard.process.wait()
    if not dashboard.stopping:
        log.error("Dashboard process ended by itself (exit code %s)", code)
        user32.PostMessageW(tray.hwnd, WM_STOPPED, 0, 0)


def main() -> None:
    setup_logging()
    log.info("Launch requested from %s (Python %s)", HERE.parent, sys.version.split()[0])
    if already_running():
        reopen_existing()
        return
    dashboard = Dashboard()
    dashboard.start()
    tray = Tray(dashboard)
    threading.Thread(target=watch, args=(dashboard, tray), daemon=True).start()
    tray.run()
    log.info("Tray program finished")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log.exception("Launcher error")
        message_box(f"The Hotel Dashboard could not start.\n\nDiagnostic logs: {LOG_DIR}")
