"""
Builds a PORTABLE demo copy of the dashboard for Windows: one folder with its own Python, that
runs by double-clicking a launcher. No installer, no admin rights, no internet needed.

    python packaging\\build_portable.py          (from the project folder; takes a minute or two)

Output: dist\\HotelDashboard-Demo\\  (and HotelDashboard-Demo.zip next to it)

What goes in the folder:
  - python\\  a copy of this computer's Python, with ONLY the packages the dashboard needs
             (worked out from requirements.txt). Other packages on this computer are left out.
  - app\\     a snapshot of the app code, the fictional sample_data and the theme settings.
             It always starts the DEMO (demo_app.py): fictional data only, uploads switched off.
             local_data\\ (real data) is never copied.

This script only READS the project. It does not change the dashboard code.
"""
from __future__ import annotations

import compileall
import re
import shutil
import subprocess
import sys
from importlib import metadata
from pathlib import Path

from packaging.requirements import Requirement

PROJECT = Path(__file__).resolve().parent.parent
PACKAGING = Path(__file__).resolve().parent
OUT = PROJECT / "dist" / "HotelDashboard-Demo"
PY_SRC = Path(sys.base_prefix)                    # the Python installed on this computer

# Parts of the app to copy. Nothing else from the project is included.
APP_FILES = ["app.py", "demo_app.py", ".streamlit/config.toml"]
APP_FOLDERS = ["hotel_dashboard", "sample_data"]

# Parts of Python's standard library the dashboard never uses (tests, IDLE, Tk windows, pip).
STDLIB_SKIP = {"site-packages", "test", "idlelib", "tkinter", "turtledemo", "ensurepip", "__pycache__"}
DLL_SKIP = ("_tkinter", "tcl", "tk")


def needed_packages() -> list[metadata.Distribution]:
    """requirements.txt plus everything those packages need in turn (no optional extras)."""
    names = [line.split(">")[0].split("=")[0].strip()
             for line in (PROJECT / "requirements.txt").read_text().splitlines() if line.strip()]
    found: dict[str, metadata.Distribution] = {}
    while names:
        name = names.pop()
        key = re.sub(r"[-_.]+", "-", name).lower()
        if key in found:
            continue
        found[key] = metadata.distribution(name)       # fails loudly if a package is missing
        for spec in found[key].requires or []:
            req = Requirement(spec)
            if req.marker is None or req.marker.evaluate({"extra": ""}):
                names.append(req.name)
    return sorted(found.values(), key=lambda d: d.metadata["Name"].lower())


def copy_python(dest: Path, packages: list[metadata.Distribution]) -> None:
    dest.mkdir(parents=True)
    for f in PY_SRC.glob("*"):                          # python.exe, python314.dll, vcruntime DLLs ...
        if f.is_file() and f.suffix in (".exe", ".dll") and f.name != "__install__.json":
            shutil.copy2(f, dest / f.name)
    shutil.copy2(PY_SRC / "LICENSE.txt", dest / "LICENSE.txt")
    (dest / "DLLs").mkdir()
    for f in (PY_SRC / "DLLs").iterdir():
        if not f.name.lower().startswith(DLL_SKIP):
            shutil.copy2(f, dest / "DLLs" / f.name)
    (dest / "Lib").mkdir()
    for item in (PY_SRC / "Lib").iterdir():
        if item.name in STDLIB_SKIP:
            continue
        if item.is_dir():
            shutil.copytree(item, dest / "Lib" / item.name, ignore=shutil.ignore_patterns("__pycache__"))
        else:
            shutil.copy2(item, dest / "Lib" / item.name)

    site = dest / "Lib" / "site-packages"
    site.mkdir()
    for dist in packages:                               # copy exactly the files each package installed
        for rel in dist.files or []:
            src = Path(dist.locate_file(rel))
            if ".." in rel.parts or "__pycache__" in rel.parts or not src.is_file():
                continue                                # skip Scripts\*.exe and old bytecode
            target = site / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target)

    # The ._pth file makes this Python self-contained: it ignores PYTHONPATH, the registry and
    # any other Python on the computer, and looks only in these folders.
    tag = f"python{sys.version_info.major}{sys.version_info.minor}"
    (dest / f"{tag}._pth").write_text("DLLs\nLib\nLib\\site-packages\n..\\app\n")


def copy_app(dest: Path) -> None:
    for rel in APP_FILES:
        (dest / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PROJECT / rel, dest / rel)
    for rel in APP_FOLDERS:
        shutil.copytree(PROJECT / rel, dest / rel, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy2(PACKAGING / "launch.py", dest / "launch.py")
    make_icon(dest / "dashboard.ico")


def make_icon(path: Path) -> None:
    """Tray icon: a mustard "H" on a Prussian Blue tile (West77 palette), drawn so it stays sharp."""
    from PIL import Image, ImageDraw
    size = 256
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((8, 8, size - 8, size - 8), radius=44, fill="#203246")
    for box in ((64, 52, 106, 204), (150, 52, 192, 204), (64, 108, 192, 148)):   # the letter H
        draw.rectangle(box, fill="#DBB940")
    img.save(path, sizes=[(16, 16), (20, 20), (24, 24), (32, 32), (48, 48), (64, 64), (256, 256)])


def main() -> None:
    if OUT.exists():
        running = subprocess.run(["powershell", "-NoProfile", "-Command",
                                  f"Get-Process | Where-Object {{ $_.Path -like '{OUT}\\*' }} | Measure-Object | "
                                  "ForEach-Object Count"], capture_output=True, text=True).stdout.strip()
        if running not in ("", "0"):
            sys.exit(f"The demo in {OUT} is running. Quit it (tray icon > Quit Dashboard) and build again.")
        shutil.rmtree(OUT)
    packages = needed_packages()
    print(f"Python {sys.version.split()[0]} from {PY_SRC}")
    print(f"{len(packages)} packages: " + ", ".join(d.metadata["Name"] for d in packages))

    copy_python(OUT / "python", packages)
    copy_app(OUT / "app")
    for name in ("Start Hotel Dashboard.bat", "Stop Hotel Dashboard.bat", "READ ME FIRST.txt"):
        shutil.copy2(PACKAGING / "portable_files" / name, OUT / name)

    # Pre-compile once so the first start is faster (the launcher then writes nothing into the folder).
    compileall.compile_dir(OUT, quiet=2, workers=0)

    # Prove the copied Python is self-contained: it must not see this computer's other packages.
    check = subprocess.run([str(OUT / "python" / "python.exe"), "-c",
                            "import sys, streamlit, pandas; print(sys.prefix); "
                            "import importlib.util as u; print('anthropic visible:', u.find_spec('anthropic') is not None)"],
                           capture_output=True, text=True, check=True)
    print(check.stdout.strip())

    size = sum(f.stat().st_size for f in OUT.rglob("*") if f.is_file())
    longest = max(len(str(f.relative_to(OUT))) for f in OUT.rglob("*"))
    print(f"Folder: {OUT}  ({size / 1e6:.0f} MB, {sum(1 for _ in OUT.rglob('*'))} files, "
          f"longest path inside folder {longest} characters)")
    archive = shutil.make_archive(str(OUT), "zip", OUT.parent, OUT.name)
    print(f"Zip:    {archive}  ({Path(archive).stat().st_size / 1e6:.0f} MB)")


if __name__ == "__main__":
    main()
