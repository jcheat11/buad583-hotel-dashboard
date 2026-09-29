"""
Feasibility test for the portable demo folder (run AFTER build_portable.py):

    python packaging\\test_portable.py

It unzips dist\\HotelDashboard-Demo.zip to a NEW folder whose path contains spaces and runs it
with Python removed from PATH (like a computer without Python). Diagnostic logs go to a
temporary stand-in for %LOCALAPPDATA%, so the real one is not touched. Checks:

  1. DLLs: every compiled file finds its libraries inside the folder or in Windows itself.
  2. Start: the .bat window closes by itself, no window stays open, the tray icon appears,
     the dashboard listens on this computer only, and it renders in Edge without errors.
  3. Closing the browser does not stop the dashboard; starting again reopens the same copy.
  4. No connections outside this computer.
  5. Stop three ways (tray Quit, Stop Hotel Dashboard.bat, tray program crash): nothing left
     running, port free, and an unrelated Python program is NOT stopped.
  6. Logs exist, contain no dashboard figures, and nothing was written into the app folder.

Uses only this computer's Edge; nothing is downloaded or sent anywhere.
"""
from __future__ import annotations

import asyncio
import base64
import ctypes
import ctypes.wintypes as w
import json
import os
import re
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

import websockets        # already installed on the development computer (Streamlit needs it)

PROJECT = Path(__file__).resolve().parent.parent
ZIP = PROJECT / "dist" / "HotelDashboard-Demo.zip"
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
SYSTEM32 = Path(os.environ["SystemRoot"]) / "System32"
VC_RUNTIME = ("msvcp140", "vcruntime140", "concrt140", "vcomp140", "vccorlib140")
WINDOW_CLASS, ID_OPEN, ID_QUIT, WM_COMMAND = "HotelAssetDashboardDemoTray", 1001, 1002, 0x0111
LOOPBACK = ("127.0.0.1", "::1")
RESULTS: list[tuple[str, bool, str]] = []

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32.FindWindowW.restype = w.HWND
user32.PostMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]


def record(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail else ""), flush=True)
    return ok


# ---------------------------------------------------------------------------- 1. DLL check
def pe_imports(path: Path) -> list[tuple[str, bool]]:
    """(dll name, is_delay_loaded) for a Windows .exe/.dll/.pyd, read from its import tables."""
    data = path.read_bytes()
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    n_sections, opt_size = struct.unpack_from("<H", data, pe + 6)[0], struct.unpack_from("<H", data, pe + 20)[0]
    opt = pe + 24
    dirs = opt + (112 if struct.unpack_from("<H", data, opt)[0] == 0x20B else 96)
    sections = [struct.unpack_from("<IIII", data, pe + 24 + opt_size + 40 * i + 8) for i in range(n_sections)]

    def offset(rva):
        for vsize, vaddr, rawsize, rawptr in sections:
            if vaddr <= rva < vaddr + max(vsize, rawsize):
                return rva - vaddr + rawptr
        return None

    def cstr(rva):
        o = offset(rva)
        return data[o:data.index(b"\0", o)].decode("ascii", "replace") if o is not None else "?"

    found = []
    for index, size, delay in ((1, 20, False), (13, 32, True)):
        rva = struct.unpack_from("<I", data, dirs + 8 * index)[0]
        o = offset(rva) if rva else None
        while o is not None:
            name_rva = struct.unpack_from("<I", data, o + (4 if delay else 12))[0]
            if not name_rva:
                break
            found.append((cstr(name_rva), delay))
            o += size
    return found


def dll_check(root: Path) -> None:
    binaries = [p for p in root.rglob("*") if p.suffix.lower() in (".dll", ".pyd", ".exe")]
    in_folder = {p.name.lower() for p in binaries}
    needs_runtime, missing = set(), set()
    for binary in binaries:
        for dll, delay in pe_imports(binary):
            low = dll.lower()
            if low in in_folder or low.startswith(("api-ms-win-", "ext-ms-")):
                continue
            if low.startswith(VC_RUNTIME):
                needs_runtime.add(f"{binary.relative_to(root)} -> {dll}")
            elif not (SYSTEM32 / dll).exists() and not delay:
                missing.add(f"{binary.relative_to(root)} -> {dll}")
    record("DLL check: nothing needs an extra Microsoft C++ runtime", not needs_runtime,
           "; ".join(sorted(needs_runtime)[:6]) or f"{len(binaries)} compiled files checked")
    record("DLL check: no required library missing", not missing, "; ".join(sorted(missing)[:6]))


# ---------------------------------------------------------------------------- helpers
class Env:
    def __init__(self, work: Path, root: Path):
        self.root = root
        self.appdata = work / "LocalAppData stand-in"
        self.data = self.appdata / "Hotel Asset Dashboard (Demo)"
        self.logs = self.data / "logs"

    def vars(self) -> dict[str, str]:
        """Like a PC without Python: no Python on PATH, no PYTHON* settings."""
        keep = ("SystemRoot", "SystemDrive", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP", "USERPROFILE",
                "USERNAME", "HOMEDRIVE", "HOMEPATH", "APPDATA", "ProgramFiles", "ProgramFiles(x86)",
                "ProgramData", "PROCESSOR_ARCHITECTURE", "NUMBER_OF_PROCESSORS")
        env = {k: os.environ[k] for k in keep if k in os.environ}
        env["PATH"] = ";".join([str(SYSTEM32), os.environ["SystemRoot"], str(SYSTEM32 / "WindowsPowerShell" / "v1.0")])
        env["LOCALAPPDATA"] = str(self.appdata)
        return env

    def launcher_log(self) -> str:
        f = self.logs / "launcher.log"
        return f.read_text(encoding="utf-8", errors="replace") if f.exists() else ""


def ps(command: str) -> str:
    return subprocess.run(["powershell", "-NoProfile", "-Command", command], capture_output=True, text=True).stdout


def app_processes(root: Path) -> list[tuple[int, str]]:
    """(pid, name) of every python/pythonw started from the portable folder."""
    out = ps(f"Get-Process python, pythonw -ErrorAction SilentlyContinue | Where-Object {{ $_.Path -like '{root}\\*' }}"
             " | ForEach-Object { \"$($_.Id) $($_.ProcessName)\" }")
    return [(int(pid), name) for pid, name in (line.split() for line in out.splitlines() if line.strip())]


def port_free(port: int) -> bool:
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def alive(pid: int) -> bool:
    return str(pid) in ps(f"Get-Process -Id {pid} -ErrorAction SilentlyContinue | ForEach-Object Id")


def run_start_bat(env: Env) -> tuple[bool, float]:
    """Double-click equivalent. Returns (window closed by itself within 10 s, seconds)."""
    t = time.time()
    shell = subprocess.Popen(f'cmd /c ""{env.root / "Start Hotel Dashboard.bat"}" --no-browser"',
                             env=env.vars(), cwd=env.root, creationflags=subprocess.CREATE_NEW_CONSOLE)
    try:
        shell.wait(timeout=10)
        return True, time.time() - t
    except subprocess.TimeoutExpired:
        subprocess.run(["taskkill", "/PID", str(shell.pid), "/T", "/F"], capture_output=True)
        return False, time.time() - t


def wait_ready(env: Env, seconds: int = 180) -> tuple[str | None, float]:
    t = time.time()
    while time.time() - t < seconds:
        try:
            url = json.loads((env.data / "running.json").read_text())["url"]
            urllib.request.urlopen(url + "_stcore/health", timeout=1)
            return url, time.time() - t
        except (OSError, ValueError, KeyError):
            time.sleep(0.5)
    return None, time.time() - t


def tray_window() -> int:
    return user32.FindWindowW(WINDOW_CLASS, None) or 0


def check_stopped(env: Env, port: int, label: str, bystander: int) -> None:
    for _ in range(60):
        if not app_processes(env.root) and port_free(port) and not tray_window():
            break
        time.sleep(0.5)
    left = app_processes(env.root)
    record(f"Stop ({label}): nothing left running, port {port} free, tray icon removed",
           not left and port_free(port) and not tray_window(), f"left running: {left}" if left else "")
    record(f"Stop ({label}): unrelated Python program still running", alive(bystander))


def visible_windows(pids: set[int]) -> list[str]:
    """Visible windows owned by the app's processes or by console hosts they started."""
    if not pids:
        return []
    ids = ",".join(map(str, pids))
    children = ps(f"Get-CimInstance Win32_Process | Where-Object {{ @({ids}) -contains $_.ParentProcessId }}"
                  " | ForEach-Object ProcessId")
    watch = pids | {int(x) for x in children.split()}
    found = []
    proc = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)

    def each(hwnd, _):
        pid = w.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in watch and user32.IsWindowVisible(hwnd):
            title = ctypes.create_unicode_buffer(200)
            user32.GetWindowTextW(hwnd, title, 200)
            found.append(f"pid {pid.value}: '{title.value}'")
        return True

    user32.EnumWindows(proc(each), 0)
    return found


def connections(pids: list[int], state: str) -> list[tuple[str, str]]:
    if not pids:
        return []
    out = ps(f"Get-NetTCPConnection -OwningProcess {','.join(map(str, pids))} -State {state} -ErrorAction SilentlyContinue"
             " | ForEach-Object { \"$($_.LocalAddress) $($_.RemoteAddress)\" }")
    return [tuple(line.split()) for line in out.splitlines() if line.strip()]


# ---------------------------------------------------------------------------- browser check
async def render(url: str, shot: Path, profile: Path) -> dict:
    edge = subprocess.Popen([EDGE, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
                             "--remote-debugging-port=9334", f"--user-data-dir={profile}", "about:blank"])
    try:
        for _ in range(50):
            try:
                pages = json.load(urllib.request.urlopen("http://127.0.0.1:9334/json"))
                page = next(t for t in pages if t["type"] == "page")
                break
            except Exception:
                time.sleep(0.3)
        async with websockets.connect(page["webSocketDebuggerUrl"], max_size=2**27) as ws:
            n = 0

            async def call(method, **params):
                nonlocal n
                n += 1
                await ws.send(json.dumps({"id": n, "method": method, "params": params}))
                while True:
                    msg = json.loads(await ws.recv())
                    if msg.get("id") == n:
                        return msg.get("result", {})

            async def js(expr):
                return (await call("Runtime.evaluate", expression=expr))["result"].get("value")

            await call("Emulation.setDeviceMetricsOverride", width=1440, height=900, deviceScaleFactor=1, mobile=False)
            t0 = time.time()
            await call("Page.navigate", url=url)
            charts = 0
            for _ in range(90):
                await asyncio.sleep(1)
                charts = await js("document.querySelectorAll('[data-testid=\"stVegaLiteChart\"]').length") or 0
                if charts >= 5:
                    break
            await asyncio.sleep(3)
            info = {"seconds": round(time.time() - t0, 1), "charts": charts,
                    "errors": await js("document.querySelectorAll('[data-testid=\"stException\"]').length"),
                    "text": await js("document.body.innerText") or ""}
            height = await js("document.querySelector('[data-testid=\"stMain\"]').scrollHeight")
            await call("Emulation.setDeviceMetricsOverride", width=1440, height=int(height) + 20,
                       deviceScaleFactor=1, mobile=False)
            await asyncio.sleep(3)
            shot.write_bytes(base64.b64decode((await call("Page.captureScreenshot", format="png"))["data"]))
            return info
    finally:
        edge.terminate()
        edge.wait(timeout=20)


# ---------------------------------------------------------------------------- main
def main() -> None:
    if tray_window():
        sys.exit("A Hotel Dashboard demo is already running on this computer. Quit it first, then rerun this test.")
    work = Path(tempfile.mkdtemp(prefix="portable test "))
    target = work / "Unzipped Test Copy"
    print(f"Test folder: {target}")
    with zipfile.ZipFile(ZIP) as z:
        z.extractall(target)
    env = Env(work, target / "HotelDashboard-Demo")
    root = env.root

    dll_check(root)
    before = {p: p.stat().st_mtime for p in root.rglob("*")}
    streamlit_home = Path.home() / ".streamlit"
    home_before = sorted(streamlit_home.rglob("*")) if streamlit_home.exists() else []
    # An unrelated Python program: stopping the dashboard must never stop this one.
    bystander = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(900)"],
                                 creationflags=subprocess.CREATE_NO_WINDOW)
    page_text = ""
    try:
        # ---------------------------------------------------------------- run 1: full check, tray Quit
        closed, secs = run_start_bat(env)
        record("Start: the .bat window closes by itself", closed, f"after {secs:.1f} s")
        url, secs = wait_ready(env)
        if not record("Start: dashboard ready (no Python on PATH, path with spaces)", url is not None,
                      f"{url} after {secs:.0f} s" if url else env.launcher_log()[-800:]):
            return
        port = int(url.rstrip("/").rsplit(":", 1)[1])
        procs = app_processes(root)
        names = sorted(name for _, name in procs)
        record("Start: one tray program + one dashboard process", names == ["python", "pythonw"], str(procs))
        record("Start: no window stays open", not visible_windows({p for p, _ in procs}),
               "; ".join(visible_windows({p for p, _ in procs})))
        text = env.launcher_log()
        record("Tray icon added, with its menu window", tray_window() != 0 and "Tray icon added: yes" in text)
        record("Dashboard tied to the tray program (stops if the tray program ends)",
               "Dashboard tied to the tray program: yes" in text)
        listening = connections([p for p, _ in procs], "Listen")
        record("Listens on this computer only (localhost)",
               bool(listening) and all(local in LOOPBACK for local, _ in listening), str(sorted(set(listening))))

        info = asyncio.run(render(url, work / "portable_screenshot.png", work / "edge"))
        page_text = info["text"]
        record("Page renders in Edge with charts and no errors",
               info["charts"] >= 5 and not info["errors"] and "Portfolio Overview" in page_text,
               f"{info['charts']} charts, {info['errors']} error boxes, {info['seconds']} s to load")
        record("Demo mode: fictional data, no upload control",
               "fictional" in page_text.lower() and "Browse files" not in page_text)
        time.sleep(2)
        still_up = wait_ready(env, 5)[0] is not None
        record("Closing the browser does not stop the dashboard", still_up and tray_window() != 0)

        user32.PostMessageW(tray_window(), WM_COMMAND, ID_OPEN, 0)          # tray menu: Open Dashboard
        time.sleep(1)
        record("Tray 'Open Dashboard' opens the running address", f"Open Dashboard: {url}" in env.launcher_log()
               and env.launcher_log().count(f"Open Dashboard: {url}") >= 2)

        opens_before = env.launcher_log().count("Open Dashboard:")
        closed, _ = run_start_bat(env)                                        # second launch
        for _ in range(60):
            if env.launcher_log().count("Open Dashboard:") > opens_before and len(app_processes(root)) == 2:
                break
            time.sleep(0.5)
        procs2 = app_processes(root)
        record("Second launch reopens the same dashboard (no second copy)",
               "Already running" in env.launcher_log() and sorted(procs2) == sorted(procs)
               and wait_ready(env, 5)[0] == url, str(procs2))

        conns = [c for c in connections([p for p, _ in procs], "Established") if c[1] not in LOOPBACK]
        record("No connections outside this computer", not conns, str(conns))

        user32.PostMessageW(tray_window(), WM_COMMAND, ID_QUIT, 0)          # tray menu: Quit Dashboard
        check_stopped(env, port, "tray Quit", bystander.pid)
        record("Tray Quit removes the 'running' marker", not (env.data / "running.json").exists())

        # ---------------------------------------------------------------- run 2: Stop Hotel Dashboard.bat
        run_start_bat(env)
        url2, secs2 = wait_ready(env)
        record("Restart after Quit", url2 is not None, f"ready after {secs2:.0f} s")
        subprocess.run(f'cmd /c ""{root / "Stop Hotel Dashboard.bat"}""', env=env.vars(), capture_output=True)
        check_stopped(env, port, "Stop Hotel Dashboard.bat", bystander.pid)

        # ---------------------------------------------------------------- run 3: tray program crashes
        run_start_bat(env)
        url3, _ = wait_ready(env)
        tray_pid = next(p for p, name in app_processes(root) if name == "pythonw")
        subprocess.run(["taskkill", "/PID", str(tray_pid), "/F"], capture_output=True)   # tray program only
        check_stopped(env, port, "tray program killed", bystander.pid)
        run_start_bat(env)
        url4, _ = wait_ready(env)
        record("Starts normally after a crash (stale marker ignored)", url4 is not None)
        user32.PostMessageW(tray_window(), WM_COMMAND, ID_QUIT, 0)
        check_stopped(env, port, "tray Quit after restart", bystander.pid)
    finally:
        for pid, _ in app_processes(root):
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
        bystander.kill()

    # ---------------------------------------------------------------- logs and files
    logs = {f.name: f.read_text(encoding="utf-8", errors="replace") for f in env.logs.glob("*.log")}
    record("Diagnostic logs are in the user-data folder", {"launcher.log", "streamlit.log"} <= set(logs),
           str(env.logs))
    figures = set(re.findall(r"\$[\d,.]+[KM]?|\d+\.\d%", page_text))       # every figure shown on the page
    leaked = sorted({f for f in figures for text in logs.values()
                     if f in text or (len(re.sub(r"\D", "", f)) >= 4 and f.lstrip("$") in text)})
    record("Logs contain none of the dashboard's figures", not leaked and bool(figures),
           f"checked {len(figures)} figures from the page" + (f"; found {leaked[:5]}" if leaked else ""))
    after = {p: p.stat().st_mtime for p in root.rglob("*")}
    changed = [str(p.relative_to(root)) for p in after if p not in before or after[p] != before[p]]
    record("Wrote nothing into the app folder", not changed, "; ".join(changed[:5]))
    home_after = sorted(streamlit_home.rglob("*")) if streamlit_home.exists() else []
    record("Wrote nothing into the user's .streamlit settings", home_after == home_before)


def summary() -> None:
    failed = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(failed)} of {len(RESULTS)} checks passed.")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    try:
        main()
    finally:
        summary()
