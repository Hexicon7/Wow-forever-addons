"""The computer around the program: WoW folders, the game process, Windows autostart, shortcuts,
the app icon and window."""
from __future__ import annotations

import base64
import os
import re
import shutil
import string
import struct
import subprocess
import sys
import time
import webbrowser
import zlib
from pathlib import Path

from .config import entry_script
from .errors import UserError
from .util import data_dir


def version_tuple(v: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", v or "")[:4]) or (0,)
RUN_KEY, RUN_NAME = r"Software\Microsoft\Windows\CurrentVersion\Run", "WoWForeverAddons"


def host_exe() -> Path:
    """The desktop program installed next to this script (by the Setup)."""
    return entry_script().with_name("WoW Forever Addons.exe")


def autostart_enabled() -> bool:
    if os.name != "nt":
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            return bool(winreg.QueryValueEx(k, RUN_NAME)[0])
    except OSError:
        return False


def set_autostart(on: bool):
    """Start the desktop program, in the tray, when you sign in to Windows."""
    if os.name != "nt":
        raise UserError("Starting with Windows only works on Windows.")
    import winreg
    host = host_exe()
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
        if on:
            if not host.is_file():
                raise UserError("Install the desktop program with the Setup to start it with Windows.")
            winreg.SetValueEx(k, RUN_NAME, 0, winreg.REG_SZ, f'"{host}" --tray')
        else:
            try:
                winreg.DeleteValue(k, RUN_NAME)
            except OSError:
                pass


def pid_alive(pid: int) -> bool:
    if os.name == "nt":
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x00100000, False, int(pid))   # SYNCHRONIZE
        if not h:
            return False
        try:
            return k32.WaitForSingleObject(h, 0) == 0x102     # WAIT_TIMEOUT: still running
        finally:
            k32.CloseHandle(h)
    try:
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False


# --------------------------------------------------------------------------- #
# WoW folder detection
# --------------------------------------------------------------------------- #
def _wow_roots() -> list[Path]:
    roots: list[Path] = []
    if os.name == "nt":
        try:
            import winreg
            for sub in (r"SOFTWARE\WOW6432Node\Blizzard Entertainment\World of Warcraft",
                        r"SOFTWARE\Blizzard Entertainment\World of Warcraft"):
                try:
                    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, sub) as k:
                        v, _ = winreg.QueryValueEx(k, "InstallPath")
                        p = Path(os.path.normpath(v))
                        roots.append(p.parent if p.name.startswith("_") else p)
                except OSError:
                    pass
        except ImportError:
            pass
        subs = ["Program Files (x86)\\World of Warcraft", "Program Files\\World of Warcraft",
                "World of Warcraft", "Games\\World of Warcraft", "Battle.net\\World of Warcraft",
                "Blizzard\\World of Warcraft", "Program Files (x86)\\Battle.net\\World of Warcraft",
                "Games\\Battle.net\\World of Warcraft"]
        for letter in string.ascii_uppercase[2:]:
            drive = f"{letter}:\\"
            if not os.path.exists(drive):
                continue
            roots += [Path(drive) / s for s in subs]
    else:
        home = Path.home()
        roots += [Path("/Applications/World of Warcraft"),
                  home / "Games/world-of-warcraft/drive_c/Program Files (x86)/World of Warcraft",
                  home / ".wine/drive_c/Program Files (x86)/World of Warcraft"]
    extra = os.environ.get("WFAM_EXTRA_WOW_ROOT")
    if extra:
        roots.insert(0, Path(extra))
    return roots


def detect_addon_dirs() -> list[dict]:
    seen, out = set(), []
    for root in _wow_roots():
        try:
            if not root.is_dir():
                continue
            children = sorted(root.iterdir())
        except OSError:
            continue
        for child in children:
            n = child.name
            if not (child.is_dir() and n.startswith("_") and n.endswith("_")):
                continue
            addons = child / "Interface" / "AddOns"
            key = os.path.normcase(str(addons))
            if key in seen:
                continue
            seen.add(key)
            out.append({"path": os.path.normpath(str(addons)), "flavor": n, "exists": addons.is_dir(),
                        "forever": "forever" in n.lower()})
    out.sort(key=lambda d: (d["flavor"] != "_forever_", not d["forever"], d["flavor"]))
    return out


def normalize_addons_dir(raw: str) -> Path:
    p = Path(os.path.normpath(raw.strip().strip('"'))).expanduser()
    if not p.exists():
        raise UserError(f"Folder not found: {p}")
    low = p.name.lower()
    if low == "addons":
        return p
    if low == "interface":
        return p / "AddOns"
    if (p / "Interface").is_dir() or (p.name.startswith("_") and p.name.endswith("_")):
        return p / "Interface" / "AddOns"
    if (p / "_forever_").is_dir():
        return p / "_forever_" / "Interface" / "AddOns"
    raise UserError("That doesn't look like a WoW folder. Pick World of Warcraft\\_forever_\\Interface\\AddOns.")


def default_downloads_dir() -> Path:
    if os.name == "nt":
        try:
            import ctypes
            import uuid
            from ctypes import wintypes

            class GUID(ctypes.Structure):
                _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                            ("Data4", ctypes.c_ubyte * 8)]

            u = uuid.UUID("374DE290-123F-4565-9164-39C4925E467B")  # FOLDERID_Downloads
            guid = GUID(u.fields[0], u.fields[1], u.fields[2], (ctypes.c_ubyte * 8)(*u.bytes[8:]))
            out = ctypes.c_wchar_p()
            if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(out)) == 0:
                path = out.value
                ctypes.windll.ole32.CoTaskMemFree(out)
                if path:
                    return Path(path)
        except Exception:
            pass
    return Path.home() / "Downloads"


# --------------------------------------------------------------------------- #
# Misc helpers
# --------------------------------------------------------------------------- #
_game_cache = [0.0, False]


def game_running() -> bool:
    """Is a World of Warcraft client running? (Windows only; cached for a few seconds)"""
    fake = os.environ.get("WFAM_FAKE_GAME_RUNNING")
    if fake is not None:
        return fake == "1"
    if os.name != "nt":
        return False
    if time.time() - _game_cache[0] < 15:
        return _game_cache[1]
    running = False
    try:
        names = _windows_process_names()
    except Exception:
        try:   # fallback: ask tasklist
            out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True, errors="replace",
                                 timeout=5, creationflags=0x08000000).stdout
            names = [line.split('","')[0].strip('"') for line in out.splitlines()]
        except Exception:
            names = []
    for name in names:
        name = name.lower()
        if re.match(r"^wow[a-z0-9_-]*\.exe$", name) and "wowup" not in name:
            running = True
            break
    _game_cache[0], _game_cache[1] = time.time(), running
    return running


def _windows_process_names() -> list[str]:
    """Running program names via the Windows API (no extra process is started)."""
    import ctypes
    from ctypes import wintypes

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]

    k32 = ctypes.windll.kernel32
    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    k32.Process32FirstW.argtypes = k32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    snap = k32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    if not snap or snap == ctypes.c_void_p(-1).value:
        raise OSError("CreateToolhelp32Snapshot failed")
    names = []
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = k32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            names.append(entry.szExeFile)
            ok = k32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        k32.CloseHandle(snap)
    return names


# --------------------------------------------------------------------------- #
# App icon (drawn in code, so it needs no image files) and its own window
# --------------------------------------------------------------------------- #
def make_icon_png(size: int = 256) -> bytes:
    """Gold rounded square with a dark “F” — the same mark as in the app's header."""
    top, bot, ink = (0xF3, 0xD4, 0x8D), (0xB9, 0x82, 0x2F), (0x1B, 0x14, 0x06)
    r = size * 0.22

    def in_f(x, y):
        u, v = (x + 0.5) / size, (y + 0.5) / size
        return (0.33 <= u <= 0.47 and 0.21 <= v <= 0.79) or (0.33 <= u <= 0.71 and 0.21 <= v <= 0.34) \
            or (0.33 <= u <= 0.64 and 0.45 <= v <= 0.57)

    rows = []
    for y in range(size):
        t = y / max(1, size - 1)
        bg = tuple(int(top[i] + (bot[i] - top[i]) * t) for i in range(3))
        row = bytearray(b"\x00")
        for x in range(size):
            cx, cy = x + 0.5, y + 0.5
            dx = max(r - cx, 0.0, cx - (size - r))
            dy = max(r - cy, 0.0, cy - (size - r))
            alpha = 255
            if dx > 0 and dy > 0:
                d = (dx * dx + dy * dy) ** 0.5 - r
                alpha = 255 if d <= -0.5 else (0 if d >= 0.5 else int((0.5 - d) * 255))
            c = ink if in_f(x, y) else bg
            row += bytes((c[0], c[1], c[2], alpha))
        rows.append(bytes(row))

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + chunk(b"IEND", b""))


def make_ico() -> bytes:
    sizes = (256, 64, 48, 32, 16)
    pngs = [make_icon_png(n) for n in sizes]
    head = struct.pack("<HHH", 0, 1, len(pngs))
    offset, entries, data = 6 + 16 * len(pngs), b"", b""
    for n, png in zip(sizes, pngs):
        entries += struct.pack("<BBBBHHII", n % 256, n % 256, 0, 0, 1, 32, len(png), offset + len(data))
        data += png
    return head + entries + data


def find_app_browser() -> str | None:
    """Edge (built into Windows) or Chrome, which can show a page as a plain app window."""
    forced = os.environ.get("WFAM_APP_BROWSER")
    if forced is not None:
        return forced or None
    cands: list[str] = []
    if os.name == "nt":
        try:
            import winreg
            for exe in ("msedge.exe", "chrome.exe"):
                for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                    try:
                        with winreg.OpenKey(hive, rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe}") as k:
                            cands.append(winreg.QueryValueEx(k, "")[0])
                    except OSError:
                        pass
        except ImportError:
            pass
        for base in filter(None, (os.environ.get(v) for v in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"))):
            cands += [os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe"),
                      os.path.join(base, "Google", "Chrome", "Application", "chrome.exe")]
    elif sys.platform == "darwin":
        cands += ["/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]
    else:
        cands += [shutil.which(n) or "" for n in ("microsoft-edge", "google-chrome", "chromium", "chromium-browser")]
    return next((c for c in cands if c and os.path.isfile(c)), None)


def app_window_args(exe: str, url: str) -> list[str]:
    return [exe, f"--app={url}", f"--user-data-dir={data_dir() / 'window'}", "--window-size=1280,900",
            "--no-first-run", "--no-default-browser-check", "--disable-sync",
            "--disable-features=Translate,msEdgeSidebarV2,msHubApps", "--class=WoWForeverAddons"]


APP_USER_MODEL_ID = "WoWForeverAddons.Manager"   # also set on the installer's shortcuts


def _window_branding_values() -> dict[int, str]:
    """Taskbar identity for the app window: our own name and icon instead of Edge's/Chrome's."""
    exe = Path(sys.executable)
    pyw = exe.with_name("pythonw.exe")
    ico = entry_script().with_name("icon.ico")
    if not ico.is_file():
        ico = data_dir() / "icon.ico"
        if not ico.is_file():
            ico.write_bytes(make_ico())
    host = entry_script().with_name("WoW Forever Addons.exe")
    if host.is_file():
        return {2: f'"{host}"', 3: f"{host},0", 4: "WoW Forever Addons", 5: APP_USER_MODEL_ID}
    return {2: f'"{pyw if pyw.exists() else exe}" "{entry_script()}"',   # RelaunchCommand
            3: f"{ico},0",                                                          # RelaunchIconResource
            4: "WoW Forever Addons",                                                # RelaunchDisplayNameResource
            5: APP_USER_MODEL_ID}                                                   # AppUserModel.ID (set last)


def brand_app_windows(values: dict[int, str] | None = None) -> int:
    """Give our app window its own taskbar button (name, icon, pin target). Edge/Chrome otherwise
    group it with the browser and, a few seconds after start, swap in their own profile icon.
    Returns how many windows were (re)branded. Windows only; never raises."""
    if os.name != "nt":
        return 0
    try:
        import ctypes
        from ctypes import wintypes
        values = values or _window_branding_values()
        user32, shell32, ole32 = ctypes.windll.user32, ctypes.windll.shell32, ctypes.windll.ole32

        class GUID(ctypes.Structure):
            _fields_ = [("d1", ctypes.c_uint32), ("d2", ctypes.c_uint16), ("d3", ctypes.c_uint16),
                        ("d4", ctypes.c_ubyte * 8)]

        def guid(a, b, c, d):
            return GUID(a, b, c, (ctypes.c_ubyte * 8)(*d))

        class PROPERTYKEY(ctypes.Structure):
            _fields_ = [("fmtid", GUID), ("pid", ctypes.c_uint32)]

        class PROPVARIANT(ctypes.Structure):
            _fields_ = [("vt", ctypes.c_uint16), ("r1", ctypes.c_uint16), ("r2", ctypes.c_uint16),
                        ("r3", ctypes.c_uint16), ("p", ctypes.c_void_p), ("p2", ctypes.c_void_p)]

        iid_store = guid(0x886D8EEB, 0x8CF2, 0x4446, (0x8D, 0x02, 0xCD, 0xBA, 0x1D, 0xBD, 0xCF, 0x99))
        fmt = guid(0x9F4C2855, 0x9F79, 0x4B39, (0xA8, 0xD0, 0xE1, 0xD4, 0x2D, 0xE1, 0xD5, 0xF3))
        HRESULT = ctypes.c_long
        proto = lambda *a: ctypes.WINFUNCTYPE(HRESULT, ctypes.c_void_p, *a)
        get_value = lambda vt: proto(ctypes.POINTER(PROPERTYKEY), ctypes.POINTER(PROPVARIANT))(vt[5])
        set_value = lambda vt: proto(ctypes.POINTER(PROPERTYKEY), ctypes.POINTER(PROPVARIANT))(vt[6])
        commit = lambda vt: proto()(vt[7])
        release = lambda vt: ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vt[2])
        shell32.SHGetPropertyStoreForWindow.argtypes = [wintypes.HWND, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
        ole32.PropVariantClear.argtypes = [ctypes.POINTER(PROPVARIANT)]

        hwnds = []
        cls = ctypes.create_unicode_buffer(64)
        title = ctypes.create_unicode_buffer(256)

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def each(hwnd, _):
            user32.GetClassNameW(hwnd, cls, 64)
            if cls.value.startswith("Chrome_WidgetWin") and user32.IsWindowVisible(hwnd):
                user32.GetWindowTextW(hwnd, title, 256)
                if title.value.startswith("WoW Forever Addons"):
                    hwnds.append(hwnd)
            return True
        user32.EnumWindows(each, 0)

        done = 0
        for hwnd in hwnds:
            store = ctypes.c_void_p()
            if shell32.SHGetPropertyStoreForWindow(hwnd, ctypes.byref(iid_store), ctypes.byref(store)) < 0 or not store:
                continue
            vt = ctypes.cast(ctypes.cast(store, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p))
            try:
                key = PROPERTYKEY(fmt, 5)
                cur = PROPVARIANT()
                same = False
                if get_value(vt)(store, ctypes.byref(key), ctypes.byref(cur)) >= 0:
                    same = cur.vt == 31 and cur.p and ctypes.wstring_at(cur.p) == values[5]
                    ole32.PropVariantClear(ctypes.byref(cur))
                if same:
                    continue
                keep = []
                for pid in (2, 3, 4, 5):
                    buf = ctypes.create_unicode_buffer(values[pid])
                    keep.append(buf)
                    pv = PROPVARIANT(vt=31, p=ctypes.cast(buf, ctypes.c_void_p).value)   # VT_LPWSTR
                    set_value(vt)(store, ctypes.byref(PROPERTYKEY(fmt, pid)), ctypes.byref(pv))
                commit(vt)(store)
                done += 1
            finally:
                release(vt)(store)
        return done
    except Exception:
        return 0


def open_window(url: str, own_window: bool = True) -> str:
    exe = find_app_browser() if own_window else None
    if exe:
        try:
            flags = 0x00000008 | 0x00000200 if os.name == "nt" else 0   # DETACHED_PROCESS | NEW_PROCESS_GROUP
            subprocess.Popen(app_window_args(exe, url), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, close_fds=True, creationflags=flags)
            return "window"
        except OSError:
            pass
    webbrowser.open(url)
    return "browser"


def create_shortcuts() -> str:
    """Desktop + Start menu shortcuts with the app's icon (Windows)."""
    if os.name != "nt":
        raise UserError("Shortcuts can only be created on Windows.")
    ico = data_dir() / "icon.ico"
    ico.write_bytes(make_ico())
    host = entry_script().with_name("WoW Forever Addons.exe")
    if host.is_file():
        target, args, workdir = str(host), "", str(host.parent)
    elif getattr(sys, "frozen", False):
        target, args, workdir = sys.executable, "", str(Path(sys.executable).parent)
    else:
        exe = Path(sys.executable)
        pyw = exe.with_name("pythonw.exe")
        script = entry_script()
        target, args, workdir = str(pyw if pyw.exists() else exe), f'"{script}"', str(script.parent)
    q = lambda v: "'" + str(v).replace("'", "''") + "'"
    ps = ("$w = New-Object -ComObject WScript.Shell\n"
          "foreach ($d in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) {\n"
          "  $s = $w.CreateShortcut((Join-Path $d 'WoW Forever Addons.lnk'))\n"
          f"  $s.TargetPath = {q(target)}\n  $s.Arguments = {q(args)}\n  $s.WorkingDirectory = {q(workdir)}\n"
          f"  $s.IconLocation = {q(ico)}\n  $s.Description = 'WoW Forever Addon Manager'\n  $s.Save()\n}}\n")
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                        "-EncodedCommand", base64.b64encode(ps.encode("utf-16-le")).decode()],
                       capture_output=True, text=True, errors="replace", timeout=60, creationflags=0x08000000)
    if r.returncode != 0:
        raise UserError("Windows refused to create the shortcuts: " + (r.stderr or r.stdout).strip()[-300:])
    return ps
