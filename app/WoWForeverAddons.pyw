#!/usr/bin/env python3
"""
WoW Forever Addon Manager
=========================

A small, dependency-free addon manager for World of Warcraft: Forever.

* Paste CurseForge links -> it downloads the WoW Forever file and extracts it
  into  World of Warcraft\\_forever_\\Interface\\AddOns
* "Check for updates" / "Update all" keep everything current without the
  CurseForge app.
* A local page in your browser lists every installed addon, its version,
  whether an update is waiting, and which folders it owns.

Double-click this file to start it (needs Python 3.9+ from python.org).
It needs a free CurseForge API key - see README.txt.

Command line (optional):
    python WoWForeverAddons.pyw                 open the manager in your browser
    python WoWForeverAddons.pyw --list          list installed addons
    python WoWForeverAddons.pyw --check         check for updates
    python WoWForeverAddons.pyw --update-all    check and install all updates
    python WoWForeverAddons.pyw --add URL ...   install addons from links
"""
from __future__ import annotations

import argparse
import base64
import collections
import concurrent.futures
import gzip
import http.client
import ssl
import hashlib
import io
import json
import os
import platform
import re
import html as htmlmod
import shutil
import struct
import subprocess
import stat
import string
import sys
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
import zipfile
import zlib
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

APP_NAME = "WoW Forever Addon Manager"
APP_VERSION = "3.8.3"
API_BASE = os.environ.get("WFAM_API_BASE", "https://api.curseforge.com").rstrip("/")
GITHUB_API = os.environ.get("WFAM_GITHUB_API", "https://api.github.com").rstrip("/")
GITHUB_RAW = os.environ.get("WFAM_GITHUB_RAW", "https://raw.githubusercontent.com").rstrip("/")
WAGO_BASE = os.environ.get("WFAM_WAGO_BASE", "https://addons.wago.io").rstrip("/")
FOREVER_FLAVORS = {"forever", "camelot"}
WOW_GAME_ID = 1
DEFAULT_PORT = 47811
DEFAULT_INTERFACE = "16001"
USER_AGENT = f"WoWForeverAddonManager/{APP_VERSION}"
RELEASE_TYPES = {1: "Release", 2: "Beta", 3: "Alpha"}
CHANNELS = {"release": {1}, "beta": {1, 2}, "alpha": {1, 2, 3}}
REQUIRED_DEPENDENCY = 3
MAX_DOWNLOAD_BYTES = 300 * 1024 * 1024
MAX_UNPACKED_BYTES = 1024 * 1024 * 1024
IDLE_EXIT_SECONDS = 180  # GUI mode: quit when no browser tab has polled for 3 min
# The API key is only ever sent to CurseForge's own hosts.
KEY_HOSTS = ("curseforge.com", "forgecdn.net")

DEFAULT_CONFIG = {
    "api_key": "",
    "addons_dir": "",
    "version_type_id": None,
    "version_type_name": "",
    "channel": "release",
    "interface": DEFAULT_INTERFACE,
    "interface_auto": True,        # follow the number the game itself reports (WTF/Config.wtf)
    "auto_dependencies": True,
    "auto_manual": True,          # open CurseForge's download page and install the zip when it lands
    "downloads_dir": "",          # empty = the Windows Downloads folder
    "delete_after_install": True,  # remove the zip from Downloads once it is installed
    "use_github": True,           # get CurseForge-site-only addons from their GitHub releases when possible
    "backup_wtf": True,           # zip _forever_\\WTF (addon settings) before changing addons
    "backup_keep": 10,
    "auto_check": True,           # check for updates when the manager opens (if the last check is >30 min old)
    "wago_key": "",               # personal Wago Addons key (Wago Patreon) — enables Wago as a source
    "use_wago": True,
    "wago_game_version": "forever",
    "app_window": True,           # open in its own window (Edge/Chrome app mode) instead of a browser tab
    # Desktop program ("WoW Forever Addons.exe") only:
    "tray": True,                 # closing the window keeps the manager running in the system tray
    "bg_check_hours": 3,          # check for updates in the background every N hours (0 = never)
    "bg_auto_update": True,       # install the updates it finds (otherwise just show a notification)
    "self_update_dismissed": "",  # program version the user chose "Later" for (not asked again)
}
BG_CHECK_CHOICES = (0, 1, 3, 6, 12, 24)
SELF_UPDATE_EVERY = float(os.environ.get("WFAM_SELF_UPDATE_EVERY") or 6 * 3600)
UPDATE_REPO = "Hexicon7/Wow-forever-addons"   # owner/repo on GitHub where new versions of this program are published


def version_tuple(v: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", v or "")[:4]) or (0,)
RUN_KEY, RUN_NAME = r"Software\Microsoft\Windows\CurrentVersion\Run", "WoWForeverAddons"


def host_exe() -> Path:
    """The desktop program installed next to this script (by the Setup)."""
    return Path(__file__).resolve().with_name("WoW Forever Addons.exe")


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
KEEP_PREVIOUS_VERSIONS = 2
WAGO_KEY_PREFIX = "wago-"      # addons that are only on Wago are stored under "wago-<Wago id>"


def is_wago_key(key: str) -> bool:
    return str(key).startswith(WAGO_KEY_PREFIX)


def wago_key(wago_id: str) -> str:
    wid = str(wago_id or "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", wid):
        raise UserError("That doesn't look like a Wago addon.")
    return WAGO_KEY_PREFIX + wid
# Fields that hold data from the CurseForge API: kept in memory only, never written to disk.
CF_FIELDS = ("name", "slug", "url", "summary", "authors", "logo", "library", "distribution_disabled", "latest_file_id",
             "latest_version", "latest_date", "latest_sha1", "latest_release_type", "latest_checked_at", "awaiting")
CF_INSTALLED_FIELDS = ("installed_version", "installed_file_name", "installed_file_date", "installed_release_type")
AWAIT_SECONDS = 30 * 60


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
class UserError(Exception):
    """An expected problem with a clear message for the user."""


class CurseForgeError(UserError):
    pass


class NotFound(CurseForgeError):
    pass


class DistributionDisabled(CurseForgeError):
    pass


class InstallError(UserError):
    pass


class BusyError(Exception):
    pass


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def data_dir() -> Path:
    override = os.environ.get("WFAM_DATA_DIR")
    if override:
        p = Path(override)
    elif os.name == "nt":
        p = Path(os.environ.get("APPDATA") or Path.home()) / "WoWForeverAddonManager"
    else:
        p = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")) / "wowforever-addon-manager"
    p.mkdir(parents=True, exist_ok=True)
    return p


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text("utf-8"))
    except FileNotFoundError:
        return default
    except Exception:
        # Keep a corrupt file for inspection instead of silently losing it.
        try:
            path.rename(path.with_name(f"{path.stem}.corrupt-{int(time.time())}{path.suffix}"))
        except OSError:
            pass
        return default


def save_json(path: Path, data) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), "utf-8")
    os.replace(tmp, path)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def rmtree(path: Path) -> None:
    def _fix(func, p, _exc):
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except Exception:
            pass

    if not path.exists():
        return
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=_fix)
    else:
        shutil.rmtree(path, onerror=_fix)


def quote_url(url: str) -> str:
    """CurseForge file names can contain spaces; make the URL safe for urllib."""
    parts = urllib.parse.urlsplit(url)
    path = urllib.parse.quote(parts.path, safe="/%:@+()!,;=-._~")
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, path, parts.query, parts.fragment))


def clean_key(key: str) -> str:
    """Remove whitespace/line breaks and wrapping quotes that sneak in when copying a key."""
    return re.sub(r"\s+", "", key or "").strip("\"'")


def key_allowed_for(url: str) -> bool:
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    api_host = (urllib.parse.urlsplit(API_BASE).hostname or "").lower()
    return host == api_host or any(host == h or host.endswith("." + h) for h in KEY_HOSTS)


INVALID_FOLDER_CHARS = set('<>:"/\\|?*')
RESERVED_NAMES = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


def safe_folder_name(name: str) -> bool:
    if not name or name in (".", "..") or name.endswith((" ", ".")):
        return False
    if any(c in INVALID_FOLDER_CHARS or ord(c) < 32 for c in name):
        return False
    return name.split(".")[0].lower() not in RESERVED_NAMES


# --------------------------------------------------------------------------- #
# .toc reading
# --------------------------------------------------------------------------- #
_COLOR_RE = re.compile(r"\|c[0-9a-fA-F]{8}|\|r|\|T[^|]*\|t|\|A[^|]*\|a")
_TOC_LINE_RE = re.compile(r"^##\s*([^:]+?)\s*:\s*(.*)$")


# The WoW Forever client picks an addon's .toc the same way as other clients:
# first a game-type file (Name_Forever.toc / Name_Camelot.toc), then the family
# file (Name_Mainline.toc), then plain Name.toc. "_" and "-" both work.
TOC_SUFFIX_RANK = {"forever": 0, "camelot": 0, "mainline": 1}


def pick_toc(folder_name: str, tocs: list[Path]):
    base = folder_name.lower()
    ranked = []
    for p in tocs:
        stem = p.stem.lower()
        if stem == base:
            ranked.append((2, p.name, p))
        elif stem.startswith(base) and len(stem) > len(base) and stem[len(base)] in "_-":
            r = TOC_SUFFIX_RANK.get(stem[len(base) + 1:])
            if r is not None:
                ranked.append((r, p.name, p))
    if ranked:
        return min(ranked)[2], True
    # Only .toc files for other clients (e.g. Name_TBC.toc): Forever won't load this folder.
    return sorted(tocs, key=lambda p: p.name)[0], False


def parse_toc_text(text: str) -> dict[str, str]:
    meta: dict[str, str] = {}
    for line in text.lstrip("\ufeff").splitlines()[:120]:
        m = _TOC_LINE_RE.match(line.strip())
        if m:
            meta.setdefault(m.group(1).strip().lower(), m.group(2).strip())
    return meta


def zip_supports_interface(zip_path: Path, wanted: str) -> bool:
    """Would the WoW Forever client load this zip's addon? (checks the .toc it would pick)"""
    from pathlib import PurePosixPath
    analysis = analyze_zip(zip_path)
    strip = analysis["strip"]
    with zipfile.ZipFile(zip_path) as zf:
        names = {i.filename.replace("\\", "/"): i.filename for i in zf.infolist()}
        for folder in analysis["toc_folders"]:
            tocs = []
            for norm in names:
                parts = [x for x in norm.split("/") if x]
                if len(parts) == strip + 2 and parts[strip] == folder and parts[-1].lower().endswith(".toc"):
                    tocs.append(PurePosixPath(norm))
            if not tocs:
                continue
            toc, loads = pick_toc(folder, tocs)
            if not loads:
                continue
            meta = parse_toc_text(zf.read(names[str(toc)]).decode("utf-8", "replace"))
            if wanted in [x.strip() for x in meta.get("interface", "").split(",")]:
                return True
    return False


_toc_cache: dict[str, tuple] = {}


def toc_required_deps(meta: dict[str, str]) -> list[str]:
    """Addons this one can't load without (## Dependencies / ## RequiredDeps / ## Dep...)."""
    out = []
    for k, v in meta.items():
        if k in ("dependencies", "requireddeps") or (k.startswith("dep") and not k.startswith("deprecat")):
            for name in v.split(","):
                name = name.strip()
                if name and not name.lower().startswith("blizzard_") and name not in out:
                    out.append(name)
    return out


def read_toc(folder: Path) -> dict:
    """read_toc with a cache: the folder's modification time changes whenever an update replaces it."""
    try:
        mt = folder.stat().st_mtime_ns
    except OSError:
        return {}
    hit = _toc_cache.get(str(folder))
    if hit and hit[0] == mt:
        return hit[1]
    res = _read_toc(folder)
    _toc_cache[str(folder)] = (mt, res)
    return res


def _read_toc(folder: Path) -> dict:
    try:
        tocs = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".toc"]
    except OSError:
        return {}
    if not tocs:
        return {}
    toc, loads = pick_toc(folder.name, tocs)
    try:
        meta = parse_toc_text(toc.read_text("utf-8", errors="replace"))
    except OSError:
        return {}
    title = _COLOR_RE.sub("", meta.get("title", "")).strip() or folder.name
    interfaces = [s.strip() for s in meta.get("interface", "").split(",") if s.strip()]
    pid = meta.get("x-curse-project-id", "").strip()
    return {
        "title": title,
        "version": _COLOR_RE.sub("", meta.get("version", "")).strip(),
        "interfaces": interfaces,
        "project_id": int(pid) if pid.isdigit() else None,
        "wago_id": (meta.get("x-wago-id") or "").strip() or None,
        "deps": toc_required_deps(meta),
        "default_state": "disabled" if (meta.get("defaultstate") or "").strip().lower() == "disabled" else "enabled",
        "toc": toc.name,
        "loads": loads,
    }


_iface_cache: dict[str, tuple] = {}


def game_interface(addons_dir: str | Path | None) -> str | None:
    """The interface number the WoW Forever client itself uses: it writes it to WTF/Config.wtf
    (SET lastAddonVersion "...") when it starts. None until the game has been started once."""
    if not addons_dir:
        return None
    cfg = Path(addons_dir).parent.parent / "WTF" / "Config.wtf"
    try:
        mt = cfg.stat().st_mtime_ns
    except OSError:
        return None
    hit = _iface_cache.get(str(cfg))
    if hit and hit[0] == mt:
        return hit[1]
    try:
        m = re.search(r'^\s*SET\s+lastAddonVersion\s+"(\d{4,7})"', cfg.read_text("utf-8", errors="replace")[:200000],
                      re.M | re.I)
    except OSError:
        return None
    val = m.group(1) if m else None
    _iface_cache[str(cfg)] = (mt, val)
    return val


def compat_of(interfaces: list[str], wanted: str) -> str:
    if not interfaces or not wanted:
        return "unknown"
    return "ok" if wanted in interfaces else "mismatch"


# --------------------------------------------------------------------------- #
# Zip handling
# --------------------------------------------------------------------------- #
def _safe_entries(zf: zipfile.ZipFile):
    for info in zf.infolist():
        name = info.filename.replace("\\", "/")
        if name.startswith("/") or re.match(r"^[A-Za-z]:", name):
            raise InstallError(f"The zip contains an unsafe absolute path: {info.filename}")
        parts = [p for p in name.split("/") if p not in ("", ".")]
        if any(p == ".." for p in parts):
            raise InstallError(f"The zip contains an unsafe path: {info.filename}")
        if not parts or parts[0] == "__MACOSX":
            continue
        yield info, parts


def analyze_zip(path: Path) -> dict:
    try:
        with zipfile.ZipFile(path) as zf:
            entries = list(_safe_entries(zf))
    except zipfile.BadZipFile:
        raise InstallError("The downloaded file is not a valid zip archive.")
    total = sum(info.file_size for info, _ in entries)
    if total > MAX_UNPACKED_BYTES:
        raise InstallError("The zip unpacks to more than 1 GB - refusing to extract it.")

    def toc_dirs(strip):
        return {p[strip] for _, p in entries if len(p) == strip + 2 and p[-1].lower().endswith(".toc")}

    strip = None
    for candidate in (0, 1):
        if toc_dirs(candidate):
            if candidate == 1 and len({p[0] for _, p in entries}) != 1:
                continue
            strip = candidate
            break
    if strip is None:
        raise InstallError("This zip doesn't look like a WoW addon (no folder with a .toc file at the top).")
    folders = sorted({p[strip] for _, p in entries if len(p) > strip + 1})
    bad = [f for f in folders if not safe_folder_name(f)]
    if bad:
        raise InstallError(f"The zip contains folder names Windows can't use: {', '.join(bad)}")
    return {"strip": strip, "folders": folders, "toc_folders": sorted(toc_dirs(strip))}


def extract_zip(path: Path, analysis: dict, staging: Path) -> None:
    strip = analysis["strip"]
    root = staging.resolve()
    with zipfile.ZipFile(path) as zf:
        for info, parts in _safe_entries(zf):
            if info.is_dir() or len(parts) <= strip + 1:
                continue
            target = staging.joinpath(*parts[strip:])
            if root not in target.resolve().parents:
                raise InstallError(f"Unsafe path in zip: {info.filename}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst, 1024 * 256)


def install_zip_into(addons_dir: Path, zip_path: Path, old_folders: list[str], protected: set[str]) -> dict:
    """Extract an addon zip into AddOns, replacing the addon's old folders.

    Old folders are moved to a backup first and restored if anything fails,
    so a broken download never leaves you with a half-installed addon.
    Folders that another managed addon also uses (`protected`) are only
    replaced when the new zip ships them too, never deleted.
    """
    analysis = analyze_zip(zip_path)
    new = analysis["folders"]
    interface_dir = addons_dir.parent
    staging = Path(tempfile.mkdtemp(prefix=".wfam-staging-", dir=interface_dir))
    backup = Path(tempfile.mkdtemp(prefix=".wfam-backup-", dir=interface_dir))
    moved: list[tuple[Path, Path]] = []
    placed: list[Path] = []
    try:
        extract_zip(zip_path, analysis, staging)
        to_clear = set(new) | {f for f in old_folders if f not in protected}
        for name in sorted(to_clear):
            if not safe_folder_name(name):
                continue
            p = addons_dir / name
            if p.exists():
                b = backup / name
                shutil.move(str(p), str(b))
                moved.append((p, b))
        for name in new:
            dst = addons_dir / name
            shutil.move(str(staging / name), str(dst))
            placed.append(dst)
    except Exception as exc:
        for p in placed:
            rmtree(p)
        for p, b in reversed(moved):
            if not p.exists() and b.exists():
                shutil.move(str(b), str(p))
        if isinstance(exc, PermissionError):
            raise InstallError("Windows wouldn't let me replace the addon's files. Close WoW (or anything else "
                               "using them) and try again — nothing was changed.") from exc
        raise
    finally:
        rmtree(staging)
    rmtree(backup)
    return analysis


def pick_primary(folders: list[str], toc_folders: list[str], slug: str = "", name: str = "") -> str | None:
    candidates = toc_folders or folders
    if not candidates:
        return None
    norm = lambda s: re.sub(r"[^a-z0-9]", "", s.lower())
    for want in (norm(slug), norm(name)):
        for f in candidates:
            if want and norm(f) == want:
                return f
    return sorted(candidates, key=lambda f: (len(f), f))[0]


# --------------------------------------------------------------------------- #
# HTTP: reused connections (keep-alive) + gzip, so many small requests stay fast
# --------------------------------------------------------------------------- #
class NetError(Exception):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


_tls = threading.local()
_ssl_ctx = None


def _proxied(url: str) -> bool:
    parts = urllib.parse.urlsplit(url)
    proxies = urllib.request.getproxies()
    return bool(proxies.get(parts.scheme)) and not urllib.request.proxy_bypass(parts.hostname or "")


def _conn_for(scheme: str, netloc: str, timeout: float):
    global _ssl_ctx
    pool = getattr(_tls, "pool", None)
    if pool is None:
        pool = _tls.pool = {}
    conn = pool.get((scheme, netloc))
    if conn is None:
        if scheme == "https":
            if _ssl_ctx is None:
                _ssl_ctx = ssl.create_default_context()
            conn = http.client.HTTPSConnection(netloc, timeout=timeout, context=_ssl_ctx)
        else:
            conn = http.client.HTTPConnection(netloc, timeout=timeout)
        pool[(scheme, netloc)] = conn
    conn.timeout = timeout
    return conn


def _drop_conn(scheme: str, netloc: str):
    pool = getattr(_tls, "pool", None) or {}
    conn = pool.pop((scheme, netloc), None)
    if conn:
        conn.close()


def wago_allowed_for(url: str) -> bool:
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    base = (urllib.parse.urlsplit(WAGO_BASE).hostname or "").lower()
    return host == base or host == "wago.io" or host.endswith(".wago.io")


def http_call(method: str, url: str, headers: dict, body: bytes | None = None, key: str | None = None,
              dest: Path | None = None, hashers=(), max_bytes: int = MAX_DOWNLOAD_BYTES, timeout: float = 30,
              wago_key: str | None = None):
    """One HTTP request, following redirects. Returns (status, headers, body_bytes).
    With `dest`, a 200 response is streamed to that file (body_bytes is b"").
    `key` (CurseForge API key) is only sent to CurseForge's own hosts, on every hop."""
    for _hop in range(6):
        h = dict(headers)
        if key and key_allowed_for(url):
            h["x-api-key"] = key
        if wago_key and wago_allowed_for(url):
            h["Authorization"] = f"Bearer {wago_key}"
        url = quote_url(url)
        if _proxied(url):
            return _urllib_call(method, url, h, body, dest, hashers, max_bytes, timeout)
        parts = urllib.parse.urlsplit(url)
        path = (parts.path or "/") + ("?" + parts.query if parts.query else "")
        for attempt in (0, 1):
            conn = _conn_for(parts.scheme, parts.netloc, timeout)
            try:
                conn.request(method, path, body=body, headers=h)
                r = conn.getresponse()
                break
            except (http.client.HTTPException, OSError) as e:
                _drop_conn(parts.scheme, parts.netloc)   # stale keep-alive connection: retry once on a new one
                if attempt:
                    raise NetError(getattr(e, "strerror", None) or str(e) or type(e).__name__)
        if r.status in (301, 302, 303, 307, 308) and r.getheader("Location"):
            r.read()
            if r.will_close:
                _drop_conn(parts.scheme, parts.netloc)
            url = urllib.parse.urljoin(url, r.getheader("Location"))
            if r.status == 303:
                method, body = "GET", None
            continue
        try:
            if dest is not None and r.status == 200:
                total = 0
                with open(dest, "wb") as out:
                    while True:
                        chunk = r.read(1024 * 256)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > max_bytes:
                            raise UserError("The download is larger than 300 MB - stopped.")
                        for hs in hashers:
                            hs.update(chunk)
                        out.write(chunk)
                data = b""
            else:
                data = r.read()
                if r.getheader("Content-Encoding") == "gzip":
                    data = gzip.decompress(data)
        except UserError:
            _drop_conn(parts.scheme, parts.netloc)
            raise
        except (http.client.HTTPException, OSError) as e:
            _drop_conn(parts.scheme, parts.netloc)
            raise NetError(str(e) or type(e).__name__)
        if r.will_close:
            _drop_conn(parts.scheme, parts.netloc)
        return r.status, r.headers, data
    raise NetError("too many redirects")


def _urllib_call(method, url, headers, body, dest, hashers, max_bytes, timeout):
    """Fallback for PCs that go through a proxy (urllib knows how to use it)."""
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if dest is None:
                data = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    data = gzip.decompress(data)
                return r.status, r.headers, data
            total = 0
            with open(dest, "wb") as out:
                while True:
                    chunk = r.read(1024 * 256)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        raise UserError("The download is larger than 300 MB - stopped.")
                    for hs in hashers:
                        hs.update(chunk)
                    out.write(chunk)
            return r.status, r.headers, b""
    except urllib.error.HTTPError as e:
        try:
            data = e.read()
        except Exception:
            data = b""
        return e.code, e.headers, data
    except urllib.error.URLError as e:
        raise NetError(str(e.reason))


def run_parallel(fn, items, workers: int = 6):
    """fn(item) for each item on a few threads; returns {item: result or exception}."""
    out = {}
    items = list(items)
    if not items:
        return out
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(workers, len(items))) as ex:
        futs = {ex.submit(fn, it): it for it in items}
        for fut in concurrent.futures.as_completed(futs):
            try:
                out[futs[fut]] = fut.result()
            except Exception as e:  # noqa: BLE001 - reported per item by the caller
                out[futs[fut]] = e
    return out


# --------------------------------------------------------------------------- #
# CurseForge API
# --------------------------------------------------------------------------- #
class CurseForge:
    def __init__(self, api_key: str):
        self.key = clean_key(api_key)

    def _request(self, method: str, path: str, params=None, body=None):
        url = API_BASE + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"Accept": "application/json", "Accept-Encoding": "gzip", "User-Agent": USER_AGENT}
        if data is not None:
            headers["Content-Type"] = "application/json"
        last_err = None
        for attempt in range(3):
            try:
                code, hdrs, raw = http_call(method, url, headers, data, key=self.key)
            except NetError as e:
                last_err = e
                if attempt < 2:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise CurseForgeError(f"Couldn't reach CurseForge: {e.reason}")
            if code == 200:
                return json.loads(raw.decode("utf-8"))
            last_err = f"HTTP {code}"
            if code in (429, 500, 502, 503, 504) and attempt < 2:
                time.sleep(1.5 * (attempt + 1))
                continue
            if code == 404:
                raise NotFound(f"Not found on CurseForge ({path}).")
            detail = raw[:600].decode("utf-8", "replace")
            server = (hdrs.get("Server") or "").lower() if hdrs else ""
            snippet = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", detail)).strip()[:160]
            if code in (401, 403) and ("<html" in detail.lower() or "cloudflare" in detail.lower()):
                raise CurseForgeError(
                    f"CurseForge's firewall blocked the request (HTTP {code}, {server or 'web firewall'}) — "
                    f"this is not about your key. Try again later or from another network. [{snippet}]")
            if code in (401, 403):
                raise CurseForgeError(
                    f"CurseForge rejected the API key (HTTP {code}). Use “Test key” in Settings."
                    + (f" Server said: {snippet}" if snippet else " (no reason given)"))
            raise CurseForgeError(f"CurseForge returned HTTP {code} for {path}." + (f" {snippet}" if snippet else ""))
        raise CurseForgeError(f"CurseForge request failed: {last_err}")

    def version_types(self) -> list[dict]:
        return self._request("GET", f"/v1/games/{WOW_GAME_ID}/version-types").get("data") or []

    def search_slug(self, slug: str) -> list[dict]:
        return self._request("GET", "/v1/mods/search",
                             {"gameId": WOW_GAME_ID, "slug": slug, "pageSize": 20}).get("data") or []

    def search(self, query: str, type_id: int | None, sort: int, index: int, page_size: int = 30,
               category_id: int | None = None):
        params = {"gameId": WOW_GAME_ID, "sortField": sort, "sortOrder": "asc" if sort == 4 else "desc",
                  "index": index, "pageSize": page_size}
        if type_id:
            params["gameVersionTypeId"] = int(type_id)
        if query:
            params["searchFilter"] = query
        if category_id:
            params["categoryId"] = int(category_id)
        r = self._request("GET", "/v1/mods/search", params)
        return r.get("data") or [], r.get("pagination") or {}

    def fingerprint_matches(self, prints: list[int]) -> dict:
        try:
            return self._request("POST", f"/v1/fingerprints/{WOW_GAME_ID}", body={"fingerprints": prints}).get("data") or {}
        except NotFound:
            return self._request("POST", "/v1/fingerprints", body={"fingerprints": prints}).get("data") or {}

    def changelog(self, mod_id: int, file_id: int) -> str:
        return self._request("GET", f"/v1/mods/{int(mod_id)}/files/{int(file_id)}/changelog").get("data") or ""

    def categories(self) -> list[dict]:
        return self._request("GET", "/v1/categories", {"gameId": WOW_GAME_ID}).get("data") or []

    def description(self, mod_id: int) -> str:
        return self._request("GET", f"/v1/mods/{int(mod_id)}/description").get("data") or ""

    def mod(self, mod_id: int) -> dict:
        return self._request("GET", f"/v1/mods/{int(mod_id)}")["data"]

    def mods(self, mod_ids: list[int]) -> list[dict]:
        return self._request("POST", "/v1/mods", body={"modIds": [int(i) for i in mod_ids]}).get("data") or []

    def file(self, mod_id: int, file_id: int) -> dict:
        return self._request("GET", f"/v1/mods/{int(mod_id)}/files/{int(file_id)}")["data"]

    def files_by_ids(self, file_ids: list[int]) -> list[dict]:
        return self._request("POST", "/v1/mods/files", body={"fileIds": [int(i) for i in file_ids]}).get("data") or []

    def files(self, mod_id: int, type_id: int | None) -> list[dict]:
        params = {"pageSize": 50, "index": 0}
        if type_id:
            params["gameVersionTypeId"] = int(type_id)
        return self._request("GET", f"/v1/mods/{int(mod_id)}/files", params).get("data") or []

    def download_url(self, mod_id: int, file_id: int) -> str:
        try:
            url = self._request("GET", f"/v1/mods/{int(mod_id)}/files/{int(file_id)}/download-url").get("data")
        except (NotFound, CurseForgeError) as e:
            if isinstance(e, NotFound) or "HTTP 403" in str(e):
                raise DistributionDisabled("The author has turned off downloads through third-party apps.")
            raise
        if not url:
            raise DistributionDisabled("The author has turned off downloads through third-party apps.")
        return url

    def download(self, url: str, dest: Path, sha1: str | None = None) -> str:
        h = hashlib.sha1()
        try:
            code, _hdrs, _ = http_call("GET", url, {"User-Agent": USER_AGENT}, key=self.key, dest=dest,
                                       hashers=(h,), timeout=90)
        except NetError as e:
            raise CurseForgeError(f"Download failed: {e.reason}")
        if code in (401, 403):
            raise CurseForgeError(f"The CurseForge download server refused the request (HTTP {code}).")
        if code != 200:
            raise CurseForgeError(f"Download failed (HTTP {code}).")
        digest = h.hexdigest()
        if sha1 and digest.lower() != sha1.lower():
            raise CurseForgeError("The download was corrupted (checksum mismatch). Try again.")
        return digest


_GH_RE = re.compile(r"github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?(?:[/#?]|$)", re.I)


def github_repo_of(mod: dict) -> str | None:
    links = mod.get("links") or {}
    for key in ("sourceUrl", "issuesUrl", "wikiUrl"):
        m = _GH_RE.search(links.get(key) or "")
        if m and m.group(1).lower() not in ("orgs", "sponsors", "users"):
            return f"{m.group(1)}/{m.group(2)}"
    return None


class GitHub:
    """Public GitHub releases (no account needed; GitHub allows ~60 requests/hour)."""

    def _get(self, url: str, accept="application/vnd.github+json"):
        try:
            code, hdrs, raw = http_call("GET", url, {"Accept": accept, "Accept-Encoding": "gzip", "User-Agent": USER_AGENT,
                                                     "X-GitHub-Api-Version": "2022-11-28"})
        except NetError as e:
            raise UserError(f"couldn't reach GitHub: {e.reason}")
        if code == 200:
            try:
                return json.loads(raw.decode("utf-8"))
            except ValueError:
                raise UserError("GitHub sent something that isn't JSON")
        if code in (403, 429) and (hdrs.get("X-RateLimit-Remaining") == "0" or code == 429):
            raise UserError("GitHub's hourly limit for apps without an account was reached — try again later")
        if code == 404:
            raise UserError("GitHub repository not found")
        raise UserError(f"GitHub returned HTTP {code}")

    def find_forever(self, repo: str, channel: str, wanted: str):
        """Newest release with a WoW Forever zip -> (release, asset), or (None, None)."""
        releases = self._get(f"{GITHUB_API}/repos/{repo}/releases?per_page=10")
        for rel in releases if isinstance(releases, list) else []:
            if rel.get("draft"):
                continue
            if rel.get("prerelease") and channel == "release":
                continue
            asset = self.pick_asset(rel, wanted)
            if asset:
                return rel, asset
        return None, None

    def release(self, repo: str, release_id: int) -> dict:
        return self._get(f"{GITHUB_API}/repos/{repo}/releases/{int(release_id)}")

    def rate_limit(self) -> dict:
        return self._get(f"{GITHUB_API}/rate_limit")

    def pick_asset(self, rel: dict, wanted: str):
        assets = rel.get("assets") or []
        zips = [a for a in assets if (a.get("name") or "").lower().endswith(".zip")]
        if not zips:
            return None
        by_name = {a["name"]: a for a in zips}
        meta = next((a for a in assets if a.get("name") == "release.json"), None)
        if meta:  # BigWigs packager metadata: which zip is for which game version
            try:
                info = self._get(meta["browser_download_url"], accept="application/octet-stream")
                hits = []
                for r in info.get("releases") or []:
                    for md in r.get("metadata") or []:
                        if (str(md.get("interface")) == wanted or (md.get("flavor") or "").lower() in FOREVER_FLAVORS) \
                                and r.get("filename") in by_name:
                            hits.append((bool(r.get("nolib")), by_name[r["filename"]]))
                if hits:
                    return sorted(hits, key=lambda h: h[0])[0][1]
                return None  # release.json exists but lists no Forever build
            except (UserError, ValueError, KeyError):
                pass
        named = [a for a in zips if re.search(r"forever|camelot", a["name"], re.I)]
        if named:
            return sorted(named, key=lambda a: "nolib" in a["name"].lower())[0]
        generic = [a for a in zips if not re.search(
            r"nolib|classic|bcc|tbc|wrath|wotlk|cata|mists|mop|vanilla|era|retail|mainline", a["name"], re.I)]
        if len(zips) == 1:
            return zips[0]          # one package for all versions; checked after download
        if len(generic) == 1:
            return generic[0]
        return None

    def download(self, asset: dict, dest: Path) -> str:
        h1, h256 = hashlib.sha1(), hashlib.sha256()
        try:
            code, _h, _ = http_call("GET", asset["browser_download_url"], {"User-Agent": USER_AGENT}, dest=dest,
                                    hashers=(h1, h256), timeout=90)
        except NetError as e:
            raise UserError(f"GitHub download failed: {e.reason}")
        if code != 200:
            raise UserError(f"GitHub download failed (HTTP {code})")
        digest = (asset.get("digest") or "")
        if digest.startswith("sha256:") and digest[7:].lower() != h256.hexdigest():
            raise UserError("the GitHub download was corrupted (checksum mismatch)")
        return h1.hexdigest()


class Wago:
    """Wago Addons, read with a personal key from Wago's Patreon (addons.wago.io/patreon)."""
    _slugs: dict = {}

    def __init__(self, key: str, game_version: str = "forever"):
        self.key = clean_key(key)
        self.gv = game_version or "forever"

    def _call(self, method: str, path: str, params: dict | None = None, body=None):
        url = WAGO_BASE + path + ("?" + urllib.parse.urlencode(params) if params else "")
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"Accept": "application/json", "Accept-Encoding": "gzip", "User-Agent": USER_AGENT}
        if data is not None:
            headers["Content-Type"] = "application/json"
        for attempt in range(2):
            try:
                code, _h, raw = http_call(method, url, headers, data, wago_key=self.key)
            except NetError as e:
                if attempt == 0:
                    time.sleep(1)
                    continue
                raise UserError(f"couldn't reach Wago: {e.reason}")
            if code == 200:
                try:
                    return json.loads(raw.decode("utf-8"))
                except ValueError:
                    raise UserError("Wago sent something that isn't JSON")
            if code in (429, 500, 502, 503, 504) and attempt == 0:
                time.sleep(1.5)
                continue
            raise UserError({401: "Wago rejected the key (missing or wrong) — check it in Settings",
                             403: "your Wago key has expired — get a new one at addons.wago.io/patreon",
                             404: "not found on Wago",
                             423: "your Wago key is blocked — get a new one at addons.wago.io/patreon",
                             429: "Wago is busy — try again in a moment"}.get(code, f"Wago returned HTTP {code}"))
        raise UserError("Wago didn't answer")

    def addon(self, ident: str) -> dict:
        return self._call("GET", f"/api/external/addons/{urllib.parse.quote(str(ident), safe='')}", {"game_version": self.gv})

    def recents(self, ids: list[str]) -> dict:
        out = {}
        for c in chunks(list(ids), 100):
            out.update(self._call("POST", "/api/external/addons/_recents", {"game_version": self.gv},
                                  {"addons": c}).get("addons") or {})
        return out

    def search(self, query: str) -> list[dict]:
        return self._call("GET", "/api/external/addons/_search", {"query": query, "game_version": self.gv}).get("data") or []

    _popular: dict = {}

    def popular(self) -> list[dict]:
        """Wago's most popular addons for this game version (what its own app shows before you search)."""
        hit = Wago._popular.get(self.gv)
        if hit and time.time() - hit[0] < 600:
            return hit[1]
        r = self._call("GET", "/api/external/addons/popular", {"game_version": self.gv})
        data = r if isinstance(r, list) else (r.get("data") or r.get("addons") or [] if isinstance(r, dict) else [])
        data = [a for a in data if isinstance(a, dict)]
        Wago._popular[self.gv] = (time.time(), data)
        return data

    def slugs(self) -> dict:
        hit = Wago._slugs.get(self.gv)
        if hit and time.time() - hit[0] < 3600:
            return hit[1]
        data = self._call("GET", "/api/data/slugs", {"game_version": self.gv}).get("addons") or {}
        Wago._slugs[self.gv] = (time.time(), data)
        return data

    @staticmethod
    def pick_release(payload: dict, channel: str) -> dict | None:
        rr = payload.get("recent_releases") or payload.get("recent_release") or {}
        allowed = {"release": ("stable",), "beta": ("stable", "beta"), "alpha": ("stable", "beta", "alpha")}.get(
            channel, ("stable",))
        best = None
        for ch in allowed:
            r = rr.get(ch)
            if not isinstance(r, dict) or not r.get("created_at"):
                continue
            link = r.get("download_link") or r.get("link")
            if not link:
                continue
            if not best or r["created_at"] > best["created_at"]:
                best = {"label": r.get("label") or "", "created_at": r["created_at"], "download": link, "channel": ch}
        return best

    def download(self, rel: dict, dest: Path) -> str:
        h = hashlib.sha1()
        try:
            code, _h, _ = http_call("GET", rel["download"], {"User-Agent": USER_AGENT}, dest=dest, hashers=(h,),
                                    timeout=90, wago_key=self.key)
        except NetError as e:
            raise UserError(f"Wago download failed: {e.reason}")
        if code != 200:
            raise UserError(f"Wago download failed (HTTP {code})")
        return h.hexdigest()


def _ts(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def wago_categories(a: dict) -> list[str]:
    """Category names from a Wago addon entry, whichever shape Wago sends them in."""
    out = []
    raw = a.get("categories") or a.get("category") or a.get("tags") or []
    for c in raw if isinstance(raw, list) else [raw]:
        name = c if isinstance(c, str) else (c.get("display_name") or c.get("name") or c.get("title") or "") \
            if isinstance(c, dict) else ""
        name = str(name).strip()
        if name and name not in out:
            out.append(name[:40])
    return out[:6]


def norm_version(v: str | None) -> str:
    return re.sub(r"[^0-9a-z]", "", (v or "").lower()).lstrip("v")


_DOTTED_RE = re.compile(r"(?<![\d.])\d+(?:\.\d+)+(?![\d.]*\d)")


def version_cmp(a: str | None, b: str | None) -> int | None:
    """Compare two version labels by their dotted numbers ("Questie-v12.6.0" vs "v12.10.1").
    1: a is newer, -1: b is newer, 0: same numbers, None: no version numbers to compare."""
    ta = [tuple(int(x) for x in m.split(".")) for m in _DOTTED_RE.findall(a or "")]
    tb = [tuple(int(x) for x in m.split(".")) for m in _DOTTED_RE.findall(b or "")]
    if not ta or not tb:
        return None
    return (ta > tb) - (ta < tb)


def same_name(a: str | None, b: str | None) -> bool:
    na, nb = re.sub(r"[^a-z0-9]", "", (a or "").lower()), re.sub(r"[^a-z0-9]", "", (b or "").lower())
    return bool(na and nb) and (na == nb or (len(na) > 4 and len(nb) > 4 and (na in nb or nb in na)))


def file_sha1(f: dict) -> str | None:
    for h in f.get("hashes") or []:
        if h.get("algo") == 1 and h.get("value"):
            return h["value"]
    return None


def has_forever_file(mod: dict, type_id: int | None) -> bool:
    """Does CurseForge list a file of this addon for our game version (WoW Forever)?"""
    if not type_id:
        return True
    if any(x.get("gameVersionTypeId") == type_id for x in mod.get("latestFilesIndexes") or []):
        return True
    return any(type_id in file_type_ids(f) for f in mod.get("latestFiles") or [])


def file_type_ids(f: dict) -> set[int]:
    return {sv.get("gameVersionTypeId") for sv in (f.get("sortableGameVersions") or []) if sv.get("gameVersionTypeId")}


def pick_latest(files: list[dict], type_id: int | None, channel: str) -> dict | None:
    allowed = CHANNELS.get(channel, {1})
    ok = []
    for f in files:
        if f.get("isAvailable") is False:
            continue
        if f.get("releaseType", 1) not in allowed:
            continue
        types = file_type_ids(f)
        if type_id and types and type_id not in types:
            continue
        ok.append(f)
    return max(ok, key=lambda f: (f.get("fileDate") or "", f.get("id", 0)), default=None)


# --------------------------------------------------------------------------- #
# Link parsing
# --------------------------------------------------------------------------- #
_CF_URL_RE = re.compile(
    r"curseforge\.com/(?P<game>[a-z0-9\-]+)/(?P<section>[a-z0-9\-]+)/(?P<slug>[A-Za-z0-9\-_.]+)"
    r"(?:/(?:files|download)/(?P<file>\d+))?", re.I)
_LEGACY_RE = re.compile(
    r"(?:wow\.curseforge\.com|wowace\.com)/projects/(?P<slug>[A-Za-z0-9\-_.]+)(?:/files/(?P<file>\d+))?", re.I)


def parse_ref(text: str) -> dict:
    t = text.strip().strip("<>\"'")
    if t.isdigit():
        return {"kind": "id", "id": int(t), "file": None, "raw": t}
    m = _LEGACY_RE.search(t)
    if m:
        return {"kind": "slug", "slug": m["slug"], "file": int(m["file"]) if m["file"] else None, "raw": t}
    m = _CF_URL_RE.search(t)
    if m:
        if m["game"].lower() != "wow":
            raise UserError(f"{t} is not a World of Warcraft addon link.")
        return {"kind": "slug", "slug": m["slug"], "file": int(m["file"]) if m["file"] else None, "raw": t}
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9\-_.]*", t) and "." not in t:
        return {"kind": "slug", "slug": t, "file": None, "raw": t}
    raise UserError(f"Couldn't read '{t}'. Paste a link like https://www.curseforge.com/wow/addons/questie")


def split_refs(text: str) -> list[str]:
    return [p for p in re.split(r"[\s,]+", text or "") if p.strip()]


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


def _dir_matches(folder: Path, want: dict[str, str]) -> bool:
    """True if folder holds exactly the files in `want` (relative path -> sha1)."""
    found = 0
    for root, _dirs, files in os.walk(folder):
        for fn in files:
            rel = Path(root, fn).relative_to(folder).as_posix()
            if rel not in want or sha1_of_file(Path(root, fn)) != want[rel]:
                return False
            found += 1
    return found == len(want)


def sha1_of_file(path: Path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 256), b""):
            h.update(chunk)
    return h.hexdigest()


# --------------------------------------------------------------------------- #
# CurseForge fingerprints (to recognise addons installed by hand)
# --------------------------------------------------------------------------- #
def cf_murmur2(data: bytes, normalize: bool) -> int:
    """CurseForge's fingerprint: MurmurHash2, seed 1, optionally ignoring whitespace bytes."""
    if normalize:
        data = data.translate(None, b"\t\n\r ")
    m, length = 0x5BD1E995, len(data)
    h = (1 ^ length) & 0xFFFFFFFF
    n4 = length - (length % 4)
    for (k,) in struct.iter_unpack("<I", data[:n4]):
        k = (k * m) & 0xFFFFFFFF
        k ^= k >> 24
        k = (k * m) & 0xFFFFFFFF
        h = ((h * m) & 0xFFFFFFFF) ^ k
    rest = data[n4:]
    if len(rest) == 3:
        h ^= rest[2] << 16
    if len(rest) >= 2:
        h ^= rest[1] << 8
    if len(rest) >= 1:
        h ^= rest[0]
        h = (h * m) & 0xFFFFFFFF
    h ^= h >> 13
    h = (h * m) & 0xFFFFFFFF
    h ^= h >> 15
    return h


_XML_INCLUDE_RE = re.compile(r"<\s*(?:Include|Script)\s+file\s*=\s*[\"']([^\"']+)[\"']", re.I)
_TOC_VARIANTS_OLD = "mainline|bcc|tbc|classic|vanilla|wrath|wotlk|cata|mists"


def folder_signature(folder: Path) -> list[int]:
    """Cheap change detector for a folder: file count, total size, newest modification time."""
    n = size = newest = 0
    for root, _dirs, files in os.walk(folder):
        for fn in files:
            try:
                st = os.stat(os.path.join(root, fn))
            except OSError:
                continue
            n += 1
            size += st.st_size
            newest = max(newest, st.st_mtime_ns)
    return [n, size, newest]


def is_library(mod: dict) -> bool:
    """CurseForge files Ace3, LibStub & co. under the "Libraries" category."""
    cats = mod.get("categories") or []
    name = mod.get("name") or ""
    return (any("librar" in (c.get("name") or "").lower() for c in cats)
            or bool(re.match(r"^(Lib[A-Z0-9-]|Ace3\b)", name)))


def install_token(rec: dict) -> str:
    """Changes whenever the manager itself installs, updates or reverts the addon."""
    return "|".join(str(rec.get(k) or "") for k in ("installed_at", "installed_file_id", "installed_sha1", "source"))


def local_signature(base: Path, rec: dict) -> list:
    """What an addon's folders look like on disk right now (to notice changes made by other tools)."""
    return [[f] + folder_signature(base / f) for f in sorted(rec.get("folders") or [])]


def folder_fingerprints(folder: Path) -> set[int]:
    """Fingerprint an addon folder the way CurseForge's app does: the .toc files, every file
    they list (and files pulled in by .xml includes), bindings.xml; hash each file with
    whitespace removed, sort, concatenate the numbers, hash that string. Two variants are
    returned (with/without the newer Forever .toc names) since we can't know which CurseForge uses."""
    files: dict[str, Path] = {}
    base = folder.name
    for root, _dirs, fns in os.walk(folder):
        for fn in fns:
            fp = Path(root, fn)
            files[(base + "/" + fp.relative_to(folder).as_posix()).lower()] = fp
    out = set()
    for variants in (_TOC_VARIANTS_OLD + "|forever|camelot", _TOC_VARIANTS_OLD):
        start_re = re.compile(r"^([^/]+)/\1([-_](%s))?\.toc$|^[^/]+/bindings\.xml$" % variants, re.I)
        matched: list[str] = []
        seen: set[str] = set()

        def include(base_dir: str, name: str):
            parts = []
            for part in (base_dir + "/" + name.replace("\\", "/").strip()).split("/"):
                if part in ("", "."):
                    continue
                if part == "..":
                    if parts:
                        parts.pop()
                    continue
                parts.append(part)
            process("/".join(parts).lower())

        def process(rel: str):
            if rel in seen or rel not in files:
                return
            seen.add(rel)
            matched.append(rel)
            try:
                text = files[rel].read_text("utf-8", errors="replace")
            except OSError:
                return
            base_dir = rel.rsplit("/", 1)[0]
            if rel.endswith(".toc"):
                for line in text.splitlines():
                    line = line.strip()
                    if line and not line.startswith("#"):
                        include(base_dir, line)
            elif rel.endswith(".xml"):
                for mm in _XML_INCLUDE_RE.finditer(text):
                    include(base_dir, mm.group(1))

        for rel in sorted(r for r in files if start_re.match(r)):
            process(rel)
        if not matched:
            continue
        hashes = sorted(cf_murmur2(files[r].read_bytes(), True) for r in matched)
        out.add(cf_murmur2("".join(str(x) for x in hashes).encode("ascii"), False))
    return out


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


def html_to_text(raw: str) -> str:
    t = re.sub(r"(?is)<(script|style).*?</\1>", "", raw or "")
    t = re.sub(r"(?i)<br\s*/?>|</(p|div|h[1-6]|tr|ul|ol)>", "\n", t)
    t = re.sub(r"(?i)<li[^>]*>", "\n• ", t)
    t = re.sub(r"<[^>]+>", "", t)
    t = htmlmod.unescape(t)
    t = re.sub(r"[ \t]+\n", "\n", t)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def fmt_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return str(n)


# --------------------------------------------------------------------------- #
# App icon (drawn in code, so the program stays a single file) and its own window
# --------------------------------------------------------------------------- #
def make_icon_png(size: int = 256) -> bytes:
    """Gold rounded square with a dark “F” — the same mark as in the app's header."""
    import zlib
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
    ico = Path(__file__).resolve().with_name("icon.ico")
    if not ico.is_file():
        ico = data_dir() / "icon.ico"
        if not ico.is_file():
            ico.write_bytes(make_ico())
    host = Path(__file__).resolve().with_name("WoW Forever Addons.exe")
    if host.is_file():
        return {2: f'"{host}"', 3: f"{host},0", 4: "WoW Forever Addons", 5: APP_USER_MODEL_ID}
    return {2: f'"{pyw if pyw.exists() else exe}" "{Path(__file__).resolve()}"',   # RelaunchCommand
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
    host = Path(__file__).resolve().with_name("WoW Forever Addons.exe")
    if host.is_file():
        target, args, workdir = str(host), "", str(host.parent)
    elif getattr(sys, "frozen", False):
        target, args, workdir = sys.executable, "", str(Path(sys.executable).parent)
    else:
        exe = Path(sys.executable)
        pyw = exe.with_name("pythonw.exe")
        script = Path(__file__).resolve()
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


# --------------------------------------------------------------------------- #
# Characters (which addons each character has switched on)
# --------------------------------------------------------------------------- #
_ADDONS_TXT_RE = re.compile(r"^\s*(.+?)\s*:\s*(enabled|disabled)\s*$", re.I)


def parse_addons_txt(path: Path) -> dict[str, str]:
    out = {}
    try:
        for line in path.read_text("utf-8", errors="replace").splitlines():
            m = _ADDONS_TXT_RE.match(line)
            if m:
                out[m.group(1).lower()] = m.group(2).lower()
    except OSError:
        pass
    return out


# --------------------------------------------------------------------------- #
# Persistent state
# --------------------------------------------------------------------------- #
class Store:
    def __init__(self):
        self.dir = data_dir()
        self.lock = threading.RLock()
        self.config_path = self.dir / "config.json"
        self.config = dict(DEFAULT_CONFIG)
        self.config.update(load_json(self.config_path, {}))
        self._load_state()
        if self.state.get("addons"):
            self.save_state()   # older versions stored CurseForge data: rewrite without it

    def _state_path(self) -> Path:
        d = self.config.get("addons_dir") or ""
        key = os.path.normcase(os.path.abspath(d)) if d else "none"
        return self.dir / f"addons-{hashlib.sha1(key.encode()).hexdigest()[:12]}.json"

    def _load_state(self):
        self.state = load_json(self._state_path(), {})
        self.state.setdefault("addons_dir", self.config.get("addons_dir"))
        self.state.setdefault("addons", {})
        if not self.state.get("local_tracking"):
            # Addons installed by older versions: verify once what is really on disk.
            for r in self.state["addons"].values():
                r.setdefault("local", {"pending": True})
            self.state["local_tracking"] = 1

    def _stamp_local(self):
        """Remember how each addon's folders looked right after the manager installed it, so a
        later change by another tool (Wago app, CurseForge app, by hand) can be noticed."""
        d = self.config.get("addons_dir")
        if not d:
            return
        base = Path(d)
        for r in self.state["addons"].values():
            loc = r.get("local")
            if not r.get("folders") or not r.get("installed_at"):
                continue
            if loc is None or (not loc.get("pending") and loc.get("at") != install_token(r)):
                toc = read_toc(base / r["primary"]) if r.get("primary") else {}
                r["local"] = {"at": install_token(r), "sig": local_signature(base, r), "toc": toc.get("version")}
                r.pop("external", None)
                r.pop("local_date", None)

    @property
    def addons(self) -> dict:
        return self.state["addons"]

    def save_config(self):
        with self.lock:
            save_json(self.config_path, {k: v for k, v in self.config.items() if k != "version_type_name"})

    def save_state(self):
        with self.lock:
            try:
                self._stamp_local()
            except Exception:
                pass
            save_json(self._state_path(), self._persistable())

    def _persistable(self) -> dict:
        """What goes on disk. CurseForge's API terms forbid saving or caching data from the API,
        so only identifiers are kept (which project, which file, which folders, pins, source);
        names, versions and update info are fetched again each time the manager starts."""
        out = {k: v for k, v in self.state.items() if k not in ("addons", "recognized", "fp_query")}
        addons = {}
        for key, r in self.state["addons"].items():
            if is_wago_key(key):      # nothing from CurseForge in these
                d = {k: v for k, v in r.items() if k not in ("latest_checked_at", "awaiting")}
                addons[key] = d
                continue
            d = {k: v for k, v in r.items() if k not in CF_FIELDS}
            if (r.get("source") or "curseforge") == "curseforge":
                for k in CF_INSTALLED_FIELDS:
                    d.pop(k, None)
            if r.get("previous"):
                d["previous"] = [{k: v for k, v in pv.items()
                                  if not ((pv.get("source") or "curseforge") == "curseforge" and k in CF_INSTALLED_FIELDS)}
                                 for pv in r["previous"]]
            addons[key] = d
        out["addons"] = addons
        return out

    def update_config(self, **changes):
        with self.lock:
            old_dir = self.config.get("addons_dir")
            old_filter = (self.config.get("channel"), self.config.get("version_type_id"))
            self.config.update(changes)
            self.save_config()
            if self.config.get("addons_dir") != old_dir:
                self._load_state()
            elif (self.config.get("channel"), self.config.get("version_type_id")) != old_filter:
                # "Latest" results were computed for another channel/version: forget them.
                for rec in self.addons.values():
                    for k in ("latest_file_id", "latest_version", "latest_date", "latest_sha1",
                              "latest_release_type", "latest_checked_at"):
                        rec.pop(k, None)
                self.save_state()


# --------------------------------------------------------------------------- #
# Manager: all addon operations
# --------------------------------------------------------------------------- #
class Manager:
    def __init__(self, store: Store, echo: bool = False):
        self.store = store
        self.echo = echo
        self._job_lock = threading.Lock()
        self.job = {"running": False, "title": "", "started": None, "finished": None, "ok": None}
        self.logs: collections.deque = collections.deque(maxlen=400)
        self._seq = 0
        self.last_contact = time.time()
        self._errors = 0
        self.interactive = False   # True while the browser UI is running (we may open download pages)
        self.hosted = False             # running inside the desktop program
        self.self_update = None         # newer version of this program, if found
        self.notices: list[dict] = []   # for the desktop program's tray notifications
        self._notice_id = 0
        self._last_available: tuple = ()
        self.last_check_at = 0.0
        self.diag: dict | None = None
        self.watch_wake = threading.Event()
        self._fill_local_names()

    def _fill_local_names(self):
        """Until CurseForge has been asked, show each addon under the title from its own .toc."""
        d = self.store.config.get("addons_dir")
        for k, r in self.store.addons.items():
            if not r.get("name"):
                toc = read_toc(Path(d) / r["primary"]) if d and r.get("primary") else {}
                r["name"] = toc.get("title") or r.get("primary") or f"Project {k}"
                if not r.get("installed_version") and toc.get("version"):
                    r["installed_version"] = toc["version"]
        self._opened = 0

    # -- logging / jobs ---------------------------------------------------- #
    def log(self, msg: str, level: str = "info"):
        self._seq += 1
        entry = {"seq": self._seq, "t": time.strftime("%H:%M:%S"), "level": level, "msg": msg}
        self.logs.append(entry)
        if level == "error":
            self._errors += 1
        if self.echo and level != "debug":
            prefix = {"error": "ERROR: ", "warn": "Note: ", "ok": ""}.get(level, "")
            print(f"{prefix}{msg}", flush=True)

    def run_job(self, title: str, fn, *args, background: bool = True):
        if not self._job_lock.acquire(blocking=False):
            raise BusyError("Another task is still running.")
        self.job = {"running": True, "title": title, "started": time.time(), "finished": None, "ok": None}
        self._errors = 0
        self.log(f"— {title} —", "head")

        def runner():
            ok = True
            try:
                fn(*args)
            except UserError as e:
                self.log(str(e), "error")
            except Exception as e:  # unexpected: keep the app alive, show the details
                self.log(f"Unexpected error: {e}", "error")
                self.log(traceback.format_exc(), "debug")
            finally:
                ok = self._errors == 0
                self.job.update(running=False, finished=time.time(), ok=ok)
                self._job_lock.release()
            return ok

        if background:
            threading.Thread(target=runner, daemon=True).start()
            return True
        return runner()

    # -- prerequisites ------------------------------------------------------ #
    def client(self) -> CurseForge:
        key = (self.store.config.get("api_key") or "").strip()
        if not key:
            raise UserError("Add your CurseForge API key in Settings first.")
        return CurseForge(key)

    def addons_dir(self) -> Path:
        d = self.store.config.get("addons_dir")
        if not d:
            raise UserError("Choose your WoW Forever AddOns folder in Settings first.")
        p = Path(d)
        if not p.is_dir():
            if p.parent.is_dir():
                p.mkdir(exist_ok=True)
            else:
                raise UserError(f"The AddOns folder doesn't exist: {p}")
        return p

    def ensure_type(self, cf: CurseForge) -> int:
        tid = self.store.config.get("version_type_id")
        if tid:
            return int(tid)
        types = cf.version_types()
        forever = [t for t in types if "forever" in f"{t.get('name', '')} {t.get('slug', '')}".lower()]
        live = [t for t in forever if not re.search(r"beta|ptr|test", f"{t.get('name', '')} {t.get('slug', '')}", re.I)]
        pick = (live or forever or [None])[0]
        if not pick:
            raise UserError("CurseForge doesn't list a 'Forever' game version yet. Pick one manually in Settings.")
        self.store.update_config(version_type_id=pick["id"], version_type_name=pick.get("name", ""))
        self.log(f"Using CurseForge game version “{pick.get('name')}” (id {pick['id']}).")
        return int(pick["id"])

    # -- metadata ---------------------------------------------------------- #
    @staticmethod
    def _mod_url(mod: dict) -> str:
        url = (mod.get("links") or {}).get("websiteUrl")
        return url or f"https://www.curseforge.com/wow/addons/{mod.get('slug', mod.get('id'))}"

    def _apply_meta(self, rec: dict, mod: dict):
        rec.update({
            "id": mod["id"],
            "name": mod.get("name") or rec.get("name") or str(mod["id"]),
            "slug": mod.get("slug") or rec.get("slug"),
            "url": self._mod_url(mod),
            "summary": mod.get("summary") or "",
            "authors": [a.get("name") for a in (mod.get("authors") or []) if a.get("name")],
            "logo": (mod.get("logo") or {}).get("thumbnailUrl") or "",
            "library": is_library(mod),
            "distribution_disabled": mod.get("allowModDistribution") is False,
        })

    @staticmethod
    def _set_latest(rec: dict, f: dict | None):
        rec["latest_checked_at"] = now_iso()
        if not f:
            rec.update(latest_file_id=None, latest_version=None, latest_date=None, latest_sha1=None,
                       latest_release_type=None)
            return
        rec.update(latest_file_id=f["id"], latest_version=f.get("displayName") or f.get("fileName"),
                   latest_date=f.get("fileDate"), latest_sha1=file_sha1(f),
                   latest_release_type=f.get("releaseType", 1))

    def _owners(self, exclude_key: str | None = None) -> dict[str, list[str]]:
        owners: dict[str, list[str]] = {}
        for k, r in self.store.addons.items():
            if k == exclude_key:
                continue
            for f in r.get("folders") or []:
                owners.setdefault(f, []).append(r.get("name", k))
        return owners

    # -- resolving ---------------------------------------------------------- #
    def _resolve_mod(self, cf: CurseForge, ref: dict) -> dict:
        if ref["kind"] == "id":
            try:
                mod = cf.mod(ref["id"])
            except NotFound:
                raise UserError(f"No CurseForge project with id {ref['id']}.")
        else:
            results = cf.search_slug(ref["slug"])
            mod = next((m for m in results if (m.get("slug") or "").lower() == ref["slug"].lower()), None)
            if not mod:
                raise UserError(f"No WoW addon called “{ref['slug']}” was found on CurseForge.")
        if mod.get("gameId") not in (None, WOW_GAME_ID):
            raise UserError(f"“{mod.get('name')}” is not a World of Warcraft addon.")
        return mod

    def _choose_file(self, cf: CurseForge, mod: dict, file_id: int | None, type_id: int):
        if file_id:
            f = cf.file(mod["id"], file_id)
            types = file_type_ids(f)
            if types and type_id not in types:
                self.log(f"{mod['name']}: the file you linked isn't tagged for WoW Forever — installing it anyway "
                         f"because you picked it.", "warn")
            return f, True
        channel = self.store.config.get("channel", "release")
        files = cf.files(mod["id"], type_id)
        f = pick_latest(files, type_id, channel)
        if f:
            return f, False
        other = pick_latest(files, type_id, "alpha")
        if other:
            kind = RELEASE_TYPES.get(other.get("releaseType"), "test")
            raise UserError(f"{mod['name']} only has {kind.lower()} builds for WoW Forever. "
                            f"Set Settings → Release channel to “{kind}” to allow them.")
        raise UserError(f"{mod['name']} has no WoW Forever file yet (the author may only have Retail/Classic "
                        f"versions). Check its Files tab on CurseForge.")

    # -- install ------------------------------------------------------------ #
    def _install(self, cf: CurseForge, mod: dict, f: dict, pinned: bool = False,
                 prefetched: tuple | None = None) -> list[int]:
        addons_dir = self.addons_dir()
        key = str(mod["id"])
        with self.store.lock:
            rec = self.store.addons.setdefault(key, {})
            self._apply_meta(rec, mod)
            rec["pinned"] = pinned
        version = f.get("displayName") or f.get("fileName")
        try:
            url = None if rec["distribution_disabled"] else (
                "prefetched" if prefetched else (f.get("downloadUrl") or cf.download_url(mod["id"], f["id"])))
            if not url:
                raise DistributionDisabled("downloads disabled")
        except DistributionDisabled:
            deps = [d["modId"] for d in (f.get("dependencies") or [])
                    if d.get("relationType") == REQUIRED_DEPENDENCY and d.get("modId")]
            with self.store.lock:
                rec["distribution_disabled"] = True
            if self._try_wago(mod, rec, pinned) or self._try_github(mod, rec, pinned):
                return deps
            page = f"{rec['url']}/download/{f['id']}"
            auto = self.store.config.get("auto_manual", True) and self.interactive
            with self.store.lock:
                rec["distribution_disabled"] = True
                if not rec.get("latest_file_id") or f["id"] >= rec["latest_file_id"]:
                    self._set_latest(rec, f)
                if auto:
                    rec["awaiting"] = {"file_id": f["id"], "sha1": file_sha1(f), "version": version,
                                       "file_name": f.get("fileName"), "length": f.get("fileLength"),
                                       "date": f.get("fileDate"), "page": page, "since": time.time()}
                self.store.save_state()
            self.watch_wake.set()
            if auto and self.interactive and self._opened < 8:
                self._opened += 1
                webbrowser.open(page)
                self.log(f"{mod['name']}: the author only allows downloads on CurseForge's site, so its download "
                         f"page was opened. It installs by itself as soon as the download finishes.", "warn")
            elif auto and self.interactive:
                self.log(f"{mod['name']}: waiting for you to download it — use “Download page” on its row.", "warn")
            else:
                self.log(f"{mod['name']}: the author only allows downloads on CurseForge's site: {page} "
                         f"— then use “Install zip…” on its row.", "warn")
            return deps

        if not prefetched:
            self.log(f"Downloading {mod['name']} {version}…")
        tmp_dir = Path(tempfile.mkdtemp(prefix="wfam-dl-", dir=self.store.dir))
        try:
            if prefetched:
                zip_path, digest = prefetched
            else:
                zip_path = tmp_dir / "addon.zip"
                digest = cf.download(url, zip_path, file_sha1(f))
            owners = self._owners(exclude_key=key)
            prev = self._archive_current(key, rec, digest)
            analysis = install_zip_into(addons_dir, zip_path, rec.get("folders") or [], set(owners))
            self._push_previous(key, rec, prev)
        finally:
            rmtree(tmp_dir)

        shared = [fo for fo in analysis["folders"] if fo in owners]
        for fo in shared:
            self.log(f"{mod['name']}: folder {fo} is also used by {', '.join(owners[fo])} (kept for both).", "warn")
        with self.store.lock:
            rec.update({
                "folders": analysis["folders"],
                "primary": pick_primary(analysis["folders"], analysis["toc_folders"], rec.get("slug", ""), rec["name"]),
                "installed_file_id": f["id"],
                "installed_version": version,
                "installed_file_name": f.get("fileName"),
                "installed_file_date": f.get("fileDate"),
                "installed_release_type": f.get("releaseType", 1),
                "installed_sha1": digest,
                "installed_at": now_iso(),
                "manual": False,
                "source": "curseforge",
            })
            for k in ("gh_installed", "gh_latest", "wago_installed"):
                rec.pop(k, None)
            if not rec.get("latest_file_id") or f["id"] >= rec["latest_file_id"]:
                self._set_latest(rec, f)
            self.store.save_state()
        n = len(analysis["folders"])
        self.log(f"Installed {mod['name']} {version} ({n} folder{'s' if n != 1 else ''}).", "ok")
        return [d["modId"] for d in (f.get("dependencies") or [])
                if d.get("relationType") == REQUIRED_DEPENDENCY and d.get("modId")]

    # -- WTF (addon settings) backups ------------------------------------------ #
    def wtf_dir(self) -> Path | None:
        d = self.store.config.get("addons_dir")
        return Path(d).parent.parent / "WTF" if d else None

    # -- leftovers from a crash or power cut ------------------------------------ #
    def cleanup_leftovers(self, min_age: float = 120.0) -> list[str]:
        """Remove working files a crashed run left behind, restoring anything they still hold.

        Runs at startup once this copy owns the port, so no other copy is mid-install.
        Items younger than `min_age` seconds are left alone as an extra safety margin.
        """
        done: list[str] = []
        cutoff = time.time() - min_age

        def old(p: Path) -> bool:
            try:
                return p.stat().st_mtime < cutoff
            except OSError:
                return False

        def drop(p: Path):
            if p.is_dir():
                rmtree(p)
            else:
                try:
                    p.unlink()
                except OSError:
                    pass
            if not p.exists():
                done.append(str(p))

        # 1) Temporary download/extract folders and half-written backups in the data folder.
        for pat in ("wfam-*", "backups/*.part"):
            for p in self.store.dir.glob(pat):
                if old(p):
                    drop(p)
        # 2) Previous-version archives of addons that are no longer managed (any AddOns folder).
        keys: set[str] = set()
        for f in self.store.dir.glob("addons-*.json"):
            keys.update((load_json(f, {}).get("addons") or {}).keys())
        keys.update(self.store.addons.keys())
        vroot = self.store.dir / "versions"
        if vroot.is_dir():
            for p in vroot.iterdir():
                if p.is_dir() and p.name not in keys:
                    drop(p)
        # 3) Working folders inside the WoW folder.
        d = self.store.config.get("addons_dir")
        if d and Path(d).is_dir():
            addons = Path(d)
            interface = addons.parent
            for b in interface.glob(".wfam-backup-*"):
                if not b.is_dir() or not old(b):
                    continue
                for sub in list(b.iterdir()):   # an install stopped half-way: put the old folders back
                    if sub.is_dir() and safe_folder_name(sub.name) and not (addons / sub.name).exists():
                        try:
                            shutil.move(str(sub), str(addons / sub.name))
                            self.log(f"Restored {sub.name} from an install that was interrupted.", "warn")
                        except OSError:
                            pass
                drop(b)
            for s in interface.glob(".wfam-staging-*"):
                if old(s):
                    drop(s)
            t = addons / ".wfam-write-test"
            if t.exists():
                drop(t)
        w = self.wtf_dir()
        if w:
            for s in w.parent.glob(".wfam-wtf-*"):
                if old(s):
                    drop(s)
            o = w.with_name("WTF.wfam-old")
            if o.is_dir() and old(o):
                if not w.exists():   # a restore stopped between the two renames
                    try:
                        os.replace(o, w)
                        self.log("Put your WTF folder back after an interrupted restore.", "warn")
                    except OSError:
                        pass
                else:
                    drop(o)
        return done

    def backups_dir(self) -> Path:
        custom = (self.store.config.get("backup_dir") or "").strip()
        if custom:
            try:
                p = Path(custom)
                p.mkdir(parents=True, exist_ok=True)
                return p
            except OSError:
                pass   # e.g. a USB drive that isn't plugged in: fall back to the default
        p = self.store.dir / "backups"
        p.mkdir(exist_ok=True)
        return p

    def set_backup_dir(self, raw: str):
        """Keep WTF backups somewhere else (e.g. OneDrive). Existing backups move along."""
        old = self.backups_dir()
        raw = (raw or "").strip().strip('"')
        if raw:
            new = Path(os.path.expandvars(raw)).expanduser()
            if not new.is_absolute():
                raise UserError("Use a full folder path for backups, e.g. C:\\Users\\you\\OneDrive\\WoW backups.")
            try:
                new.mkdir(parents=True, exist_ok=True)
                (new / ".wfam-write-test").write_text("x")
                (new / ".wfam-write-test").unlink()
            except OSError as e:
                raise UserError(f"Can't save backups in {new}: {e}")
        else:
            new = self.store.dir / "backups"
            new.mkdir(exist_ok=True)
        if new.resolve() != old.resolve():
            moved = 0
            for z in old.glob("WTF-*.zip"):
                try:
                    shutil.move(str(z), str(new / z.name))
                    moved += 1
                except OSError:
                    pass
            if moved:
                self.log(f"Moved {moved} backup(s) to {new}.", "ok")
        self.store.update_config(backup_dir=str(new) if raw else "")

    def list_backups(self) -> list[dict]:
        out = []
        for p in sorted(self.backups_dir().glob("WTF-*.zip"), reverse=True):
            st = p.stat()
            out.append({"name": p.name, "size": fmt_size(st.st_size), "bytes": st.st_size,
                        "date": datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat()})
        return out

    def backup_wtf(self, force: bool = False):
        """Zip WTF (where every addon keeps its settings) before addons change.
        At most one automatic backup per 10 minutes; the newest `backup_keep` are kept."""
        if not force and not self.store.config.get("backup_wtf", True):
            return
        w = self.wtf_dir()
        if not w or not w.is_dir():
            return
        backups = self.list_backups()
        if not force and backups and time.time() - datetime.fromisoformat(backups[0]["date"]).timestamp() < 600:
            return
        bdir = self.backups_dir()
        final = bdir / f"WTF-{time.strftime('%Y%m%d-%H%M%S')}.zip"
        tmp = final.with_suffix(".part")
        skipped = 0
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as z:
            for root, _dirs, files in os.walk(w):
                for fn in files:
                    fp = Path(root, fn)
                    if fn.lower().endswith(".bak") and "savedvariables" in root.lower():
                        continue  # the game's own .bak copies
                    try:
                        z.write(fp, fp.relative_to(w.parent).as_posix())
                    except OSError:
                        skipped += 1
        os.replace(tmp, final)
        keep = max(1, int(self.store.config.get("backup_keep") or 10))
        for old in sorted(bdir.glob("WTF-*.zip"), reverse=True)[keep:]:
            try:
                old.unlink()
            except OSError:
                pass
        self.log(f"Backed up your addon settings (WTF, {fmt_size(final.stat().st_size)})"
                 + (f" — {skipped} locked file(s) skipped" if skipped else "") + ".")

    def op_backup_now(self):
        if not self.wtf_dir() or not self.wtf_dir().is_dir():
            raise UserError("No WTF folder found next to your AddOns folder yet (it appears after you log in once).")
        self.backup_wtf(force=True)

    def op_restore_wtf(self, name: str):
        if game_running():
            raise UserError("Close WoW first — the game rewrites these settings when you log out.")
        src = self.backups_dir() / Path(name).name
        w = self.wtf_dir()
        if not src.is_file() or not w:
            raise UserError("That backup doesn't exist.")
        with zipfile.ZipFile(src) as z:
            entries = list(_safe_entries(z))
            if not entries or any(parts[0] != "WTF" for _, parts in entries):
                raise UserError("That file isn't a WTF backup.")
            if w.is_dir():
                self.backup_wtf(force=True)   # so the restore itself can be undone
            staging = Path(tempfile.mkdtemp(prefix=".wfam-wtf-", dir=w.parent))
            try:
                for info, parts in entries:
                    if info.is_dir():
                        continue
                    target = staging.joinpath(*parts)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with z.open(info) as a, open(target, "wb") as b:
                        shutil.copyfileobj(a, b)
                old = w.with_name("WTF.wfam-old")
                rmtree(old)
                if w.exists():
                    os.replace(w, old)
                os.replace(staging / "WTF", w)
                rmtree(old)
            finally:
                rmtree(staging)
        self.log(f"Restored your addon settings from {src.name}.", "ok")

    # -- previous versions (Revert) ------------------------------------------- #
    def _versions_dir(self, key: str) -> Path:
        p = self.store.dir / "versions" / key
        p.mkdir(parents=True, exist_ok=True)
        return p

    def _archive_current(self, key: str, rec: dict, new_sha1: str | None) -> dict | None:
        """Zip the installed folders before they are replaced, so Revert can bring them back."""
        if not rec.get("folders") or not self._folders_present(rec):
            return None
        if new_sha1 and (rec.get("installed_sha1") or "").lower() == new_sha1.lower():
            return None
        addons_dir = self.addons_dir()
        zp = self._versions_dir(key) / f"{int(time.time() * 1000)}.zip"
        with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as z:
            for folder in rec["folders"]:
                base = addons_dir / folder
                for root, _dirs, files in os.walk(base):
                    for fn in files:
                        fp = Path(root, fn)
                        z.write(fp, fp.relative_to(addons_dir).as_posix())
        keys = ("installed_file_id", "installed_version", "installed_file_name", "installed_file_date",
                "installed_release_type", "installed_sha1", "source", "gh_installed", "manual", "folders", "primary")
        return {"zip": zp.name, "saved_at": now_iso(), **{k: rec.get(k) for k in keys}}

    def _push_previous(self, key: str, rec: dict, meta: dict | None):
        if meta:
            rec["previous"] = [meta] + list(rec.get("previous") or [])[:KEEP_PREVIOUS_VERSIONS - 1]
        wanted = {m["zip"] for m in rec.get("previous") or []}
        for z in self._versions_dir(key).glob("*.zip"):
            if z.name not in wanted:
                try:
                    z.unlink()
                except OSError:
                    pass

    def op_revert(self, key: str):
        rec = self.store.addons.get(key)
        if not rec or not rec.get("previous"):
            raise UserError("No earlier version saved for that addon.")
        target = rec["previous"][0]
        zp = self._versions_dir(key) / target["zip"]
        if not zp.is_file():
            raise UserError("The saved earlier version is missing.")
        self.backup_wtf()
        addons_dir = self.addons_dir()
        current = self._archive_current(key, rec, None)
        owners = self._owners(exclude_key=key)
        install_zip_into(addons_dir, zp, rec.get("folders") or [], set(owners))
        with self.store.lock:
            for k, v in target.items():
                if k not in ("zip", "saved_at"):
                    rec[k] = v
            rec["installed_at"] = now_iso()
            rec["pinned"] = True
            rec["previous"] = ([current] if current else []) + rec["previous"][1:KEEP_PREVIOUS_VERSIONS]
            self._push_previous(key, rec, None)
            self.store.save_state()
        self.log(f"Reverted {rec['name']} to {rec.get('installed_version')}. It's pinned so “Update all” "
                 f"skips it — unpin when a fixed version is out.", "ok")

    # -- changelog -------------------------------------------------------------- #
    def _batch_view(self, recs: dict) -> dict | None:
        b = self.store.state.get("last_batch") or {}
        items = []
        for k in b.get("keys") or []:
            r = recs.get(k)
            if r and r.get("previous"):
                items.append({"key": k, "name": r.get("name") or k, "to": r.get("installed_version"),
                              "from": (r["previous"][0] or {}).get("installed_version")})
        return {"at": b.get("at"), "items": items} if items else None

    def wago_search(self, q: str, show_all: bool = False) -> tuple[list[dict], int]:
        wg = self.wago()
        if not wg:
            raise UserError("Add your Wago key in Settings to browse Wago.")
        q = (q or "").strip()
        if len(q) == 1:
            return [], 0
        managed = {(r.get("slug") or "").lower() for r in self.store.addons.values() if self._folders_present(r)}
        managed_ids = {str((r.get("wago") or {}).get("id")) for r in self.store.addons.values()
                       if r.get("wago") and self._folders_present(r)}
        try:
            found = wg.search(q) if q else wg.popular()
        except UserError as e:
            if q:
                raise
            raise UserError(f"Wago didn't send its list of popular addons ({e}). Type a name to search instead.")
        hits = [a for a in found[:100] if a.get("id")]
        has_build, newest = None, {}
        try:   # same lookup the update check uses: which of these have a WoW Forever release on Wago
            rec = wg.recents([str(a["id"]) for a in hits]) if hits else {}
            has_build = set()
            for i, v in rec.items():
                dates = [r.get("created_at") for r in ((v or {}).get("recent_releases") or {}).values()
                         if isinstance(r, dict) and r.get("created_at")]
                if dates:
                    has_build.add(str(i))
                    newest[str(i)] = max(dates)
        except UserError:
            pass
        out, hidden = [], 0
        for a in hits:
            fv = has_build is None or str(a["id"]) in has_build
            if not fv and not show_all:
                hidden += 1
                continue
            slug = a.get("slug") or ""
            out.append({"id": a.get("id"), "slug": slug, "name": a.get("display_name") or a.get("name") or slug,
                        "summary": a.get("summary") or a.get("description") or "",
                        "logo": a.get("thumbnail_image") or a.get("thumbnail") or a.get("logo") or "",
                        "authors": [x if isinstance(x, str) else (x.get("name") or "") for x in a.get("authors") or []],
                        "downloads": int(a.get("download_count") or a.get("downloads") or 0),
                        "url": a.get("website_url") or f"https://addons.wago.io/addons/{slug}",
                        "categories": wago_categories(a),
                        "updated": newest.get(str(a["id"])) or a.get("updated_at") or a.get("modified_at") or "",
                        "installed": slug.lower() in managed or str(a.get("id")) in managed_ids, "forever": fv})
        return out, hidden

    def details(self, mod_id: int) -> dict:
        """Everything the detail panel shows. Fetched live each time (CurseForge data isn't stored)."""
        cf = self.client()
        type_id = self.ensure_type(cf)
        got = run_parallel(lambda what: {"mod": lambda: cf.mod(mod_id), "desc": lambda: cf.description(mod_id),
                                         "files": lambda: cf.files(mod_id, type_id)}[what](), ["mod", "desc", "files"])
        if isinstance(got["mod"], Exception):
            if isinstance(got["mod"], NotFound):
                raise UserError("That addon no longer exists on CurseForge.")
            raise got["mod"]
        mod = got["mod"]
        rec = self.store.addons.get(str(mod_id)) or {}
        files = [] if isinstance(got["files"], Exception) else got["files"]
        files.sort(key=lambda f: f.get("fileDate") or "", reverse=True)
        links = mod.get("links") or {}
        rt = {1: "Release", 2: "Beta", 3: "Alpha"}
        return {
            "id": mod["id"], "key": str(mod["id"]), "name": mod.get("name"), "summary": mod.get("summary") or "",
            "authors": [a.get("name") for a in mod.get("authors") or [] if a.get("name")],
            "logo": (mod.get("logo") or {}).get("thumbnailUrl") or "",
            "url": self._mod_url(mod), "source": links.get("sourceUrl") or "", "issues": links.get("issuesUrl") or "",
            "wiki": links.get("wikiUrl") or "",
            "downloads": int(mod.get("downloadCount") or 0), "updated": mod.get("dateModified"),
            "released": mod.get("dateReleased"), "library": is_library(mod),
            "categories": [c.get("name") for c in mod.get("categories") or [] if c.get("name")],
            "screenshots": [{"thumb": x.get("thumbnailUrl"), "url": x.get("url"), "title": x.get("title") or ""}
                            for x in mod.get("screenshots") or [] if x.get("url")][:12],
            "description": "" if isinstance(got["desc"], Exception) else got["desc"],
            "manual_only": mod.get("allowModDistribution") is False,
            "managed": bool(rec), "installed_file_id": rec.get("installed_file_id"),
            "files": [{"id": f["id"], "name": f.get("displayName") or f.get("fileName"), "date": f.get("fileDate"),
                       "type": rt.get(f.get("releaseType"), "Release"), "size": int(f.get("fileLength") or 0),
                       "installed": rec.get("installed_file_id") == f["id"]} for f in files[:40]],
        }

    def file_changelog(self, mod_id: int, file_id: int) -> str:
        return html_to_text(self.client().changelog(int(mod_id), int(file_id))) or \
            "(the author didn't write a changelog for this version)"

    def op_install_file(self, mod_id: int, file_id: int):
        """Install one exact version (it gets pinned so Update all leaves it alone)."""
        self._add_refs([{"kind": "id", "id": int(mod_id), "file": int(file_id), "raw": f"{mod_id}/{file_id}"}])

    def set_ignored(self, folders: list[str], ignore: bool):
        cur = set(self.store.config.get("ignored_folders") or [])
        names = {str(f) for f in folders if f and safe_folder_name(str(f))}
        cur = (cur | names) if ignore else (cur - names)
        self.store.update_config(ignored_folders=sorted(cur, key=str.lower))

    def _char_dir(self, account: str, realm: str, name: str) -> Path:
        w = self.wtf_dir()
        for part in (account, realm, name):
            if not part or not safe_folder_name(part):
                raise UserError("Unknown character.")
        p = w / "Account" / account / realm / name if w else None
        if not p or not p.is_dir():
            raise UserError(f"Couldn't find {name} ({realm}) in the WTF folder.")
        return p

    def op_save_characters(self, changes: list[dict]):
        """Write which addons each character has switched on (the game's own AddOns.txt)."""
        d = self.store.config.get("addons_dir")
        present = {p.name.lower(): p.name for p in Path(d).iterdir() if p.is_dir()} if d and Path(d).is_dir() else {}
        if not changes:
            return
        self.backup_wtf()
        for ch in changes:
            folder = self._char_dir(str(ch.get("account")), str(ch.get("realm")), str(ch.get("name")))
            f = folder / "AddOns.txt"
            want = {str(k).lower(): bool(v) for k, v in (ch.get("states") or {}).items() if str(k).lower() in present}
            lines, seen = [], set()
            try:
                old = f.read_text("utf-8", errors="replace").splitlines() if f.is_file() else []
            except OSError:
                old = []
            for line in old:
                m = _ADDONS_TXT_RE.match(line)
                if m and m.group(1).lower() in want:
                    k = m.group(1).lower()
                    lines.append(f"{m.group(1)}: {'enabled' if want[k] else 'disabled'}")
                    seen.add(k)
                elif line.strip():
                    lines.append(line)
            for k, on in want.items():
                if k not in seen:
                    lines.append(f"{present[k]}: {'enabled' if on else 'disabled'}")
            tmp = f.with_name("AddOns.txt.wfam-tmp")
            tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
            os.replace(tmp, f)
            n_on = sum(want.values())
            self.log(f"{ch.get('name')} ({ch.get('realm')}): saved {len(want)} addon setting(s) — {n_on} on, "
                     f"{len(want) - n_on} off.", "ok")
        if game_running():
            self.log("WoW is running: the game saves AddOns.txt again when you log out, so change characters "
                     "at the character select screen or with the game closed.", "warn")

    def save_profile(self, name: str, states: dict | None):
        name = (name or "").strip()[:40]
        if not name:
            raise UserError("Give the profile a name.")
        profiles = dict(self.store.config.get("addon_profiles") or {})
        if states is None:
            profiles.pop(name, None)
        else:
            profiles[name] = {str(k).lower(): bool(v) for k, v in states.items()}
        self.store.update_config(addon_profiles=profiles)

    def changelog(self, key: str) -> dict:
        rec = self.store.addons.get(key)
        if not rec:
            raise UserError("That addon isn't managed.")
        if rec.get("source") == "github" and rec.get("gh_latest"):
            gl = rec["gh_latest"]
            body = GitHub().release(rec["github"], gl["release_id"]).get("body") or ""
            return {"title": f"{rec['name']} {gl.get('name')}", "text": body.strip() or "(no release notes)",
                    "url": gl.get("url")}
        if is_wago_key(key):
            wl = rec.get("wago_latest") or {}
            return {"title": f"{rec['name']} {wl.get('label') or rec.get('installed_version') or ''}",
                    "text": "Wago doesn't share release notes with other apps — they're on the addon's Wago page.",
                    "url": (rec.get("wago") or {}).get("url") or rec.get("url")}
        fid = rec.get("latest_file_id") or rec.get("installed_file_id")
        if not fid:
            raise UserError("Check for updates first.")
        text = html_to_text(self.client().changelog(int(key), fid))
        return {"title": f"{rec['name']} {rec.get('latest_version') or rec.get('installed_version') or ''}",
                "text": text or "(the author didn't write a changelog for this version)",
                "url": f"{rec.get('url')}/files/{fid}"}

    # -- recognise hand-installed addons ---------------------------------------- #
    def unmanaged_folders(self) -> list[Path]:
        d = self.store.config.get("addons_dir")
        if not d or not Path(d).is_dir():
            return []
        owned = {f for r in self.store.addons.values() for f in (r.get("folders") or [])}
        return sorted(p for p in Path(d).iterdir() if p.is_dir() and p.name not in owned
                      and not p.name.startswith(".") and not p.name.startswith("Blizzard_"))

    def _warm_fingerprints(self):
        """Fingerprint new hand-installed folders in the background (no job lock), so the next
        check can look them up instantly. Reading big addons once can take a few seconds."""
        if getattr(self, "_warming", False):
            return
        self._warming = True

        def work():
            try:
                for f in self.unmanaged_folders():
                    cache = self.store.state.setdefault("fp_cache", {})
                    sig = folder_signature(f)
                    if (cache.get(f.name) or {}).get("sig") != sig:
                        prints = sorted(folder_fingerprints(f))
                        with self.store.lock:
                            cache[f.name] = {"sig": sig, "prints": prints}
                with self.store.lock:
                    self.store.save_state()
            except Exception:
                self.log(traceback.format_exc(), "debug")
            finally:
                self._warming = False
        threading.Thread(target=work, daemon=True).start()

    def op_scan(self, quiet: bool = False, cached_only: bool = False) -> tuple[int, int]:
        cf = self.client()
        type_id = self.ensure_type(cf)
        folders = self.unmanaged_folders()
        if not folders:
            if not quiet:
                self.log("No unmanaged folders to look up.", "ok")
            return 0, 0
        cache = self.store.state.setdefault("fp_cache", {})
        by_print: dict[int, str] = {}
        for f in folders:
            sig = folder_signature(f)
            c = cache.get(f.name)
            if not c or c.get("sig") != sig:   # only re-read folders whose files changed
                if cached_only:
                    self._warm_fingerprints()
                    return len(self.store.state.get("recognized") or {}), len(folders)
                c = cache[f.name] = {"sig": sig, "prints": sorted(folder_fingerprints(f))}
            for fp in c["prints"]:
                by_print[fp] = f.name
        for name in [n for n in cache if n not in {f.name for f in folders}]:
            del cache[name]
        qhash = hashlib.sha1(f"{type_id}:{sorted(by_print)}".encode()).hexdigest()
        if qhash == self.store.state.get("fp_query") and "recognized" in self.store.state:
            found = self.store.state["recognized"]       # same folders as last time: nothing to ask
            if not quiet:
                self.log(f"Recognised {len(found)} addon(s) among {len(folders)} unmanaged folders (unchanged).", "ok")
            return len(found), len(folders)
        data = cf.fingerprint_matches(list(by_print)) if by_print else {}
        found: dict[str, dict] = {}
        for match in data.get("exactMatches") or []:
            fobj = match.get("file") or {}
            mod_id = match.get("id") or fobj.get("modId")
            if not mod_id:
                continue
            names = {x.name for x in folders}
            present = [m["name"] for m in fobj.get("modules") or [] if m.get("name") in names]
            if not present:
                continue
            found[str(mod_id)] = {"mod_id": mod_id, "file_id": fobj.get("id"), "version": fobj.get("displayName"),
                                  "folders": present, "forever": type_id in file_type_ids(fobj) if file_type_ids(fobj) else None}
        if found:
            for m in cf.mods([int(k) for k in found]):
                found[str(m["id"])]["name"] = m.get("name")
        with self.store.lock:
            self.store.state["recognized"] = found
            self.store.state["fp_query"] = qhash
            self.store.save_state()
        n_folders = sum(len(v["folders"]) for v in found.values())
        if not quiet:
            self.log(f"Recognised {len(found)} addon(s) ({n_folders} of {len(folders)} unmanaged folders) on CurseForge.",
                     "ok")
        return len(found), len(folders)

    def op_adopt(self, mod_id: int):
        """Start managing an addon that's already installed, without downloading it again —
        unless the installed copy isn't a WoW Forever build, then the Forever one is installed."""
        info = (self.store.state.get("recognized") or {}).get(str(mod_id))
        cf = self.client()
        if not info or info.get("forever") is False:
            return self.op_add(str(int(mod_id)))
        mod = cf.mod(int(mod_id))
        f = cf.file(int(mod_id), info["file_id"])
        key = str(mod["id"])
        with self.store.lock:
            rec = self.store.addons.setdefault(key, {})
            self._apply_meta(rec, mod)
            rec.update({
                "folders": info["folders"],
                "primary": pick_primary(info["folders"], info["folders"], mod.get("slug", ""), mod.get("name", "")),
                "installed_file_id": f["id"], "installed_version": f.get("displayName") or f.get("fileName"),
                "installed_file_name": f.get("fileName"), "installed_file_date": f.get("fileDate"),
                "installed_release_type": f.get("releaseType", 1), "installed_sha1": file_sha1(f),
                "installed_at": now_iso(), "manual": False, "pinned": False, "source": "curseforge",
            })
            self._set_latest(rec, f)
            rec["latest_checked_at"] = None
            self.store.state.get("recognized", {}).pop(key, None)
            self.store.save_state()
        self.log(f"Now managing {mod['name']} {rec['installed_version']} (already installed — nothing downloaded). "
                 f"“Check for updates” will look for newer versions.", "ok")

    # -- characters ------------------------------------------------------------------ #
    def characters(self) -> dict:
        w = self.wtf_dir()
        d = self.store.config.get("addons_dir")
        out = {"characters": [], "addons": [], "wtf_found": bool(w and w.is_dir())}
        if not w or not (w / "Account").is_dir() or not d or not Path(d).is_dir():
            return out
        chars = []
        for acc in sorted(p for p in (w / "Account").iterdir() if p.is_dir()):
            for realm in sorted(p for p in acc.iterdir() if p.is_dir() and p.name != "SavedVariables"):
                for ch in sorted(p for p in realm.iterdir() if p.is_dir() and p.name != "SavedVariables"):
                    f = ch / "AddOns.txt"
                    chars.append({"account": acc.name, "realm": realm.name, "name": ch.name, "has_file": f.is_file(),
                                  "states": parse_addons_txt(f) if f.is_file() else {}})
        owner = {}
        for k, r in self.store.addons.items():
            for f in r.get("folders") or []:
                owner[f] = r.get("name") or k
        rows = []
        for p in sorted(Path(d).iterdir(), key=lambda p: p.name.lower()):
            if not p.is_dir() or p.name.startswith("."):
                continue
            toc = read_toc(p)
            if not toc:
                continue
            default = toc.get("default_state", "enabled")
            states = [c["states"].get(p.name.lower()) for c in chars]
            rows.append({"folder": p.name, "title": toc.get("title") or p.name, "addon": owner.get(p.name),
                         "default": default, "states": states,
                         "on": [(s or default) == "enabled" for s in states]})
        out["characters"] = [{k: c[k] for k in ("account", "realm", "name", "has_file")} for c in chars]
        out["addons"] = rows
        return out

    # -- diagnostics ---------------------------------------------------------------- #
    def op_diagnose(self):
        res: list[dict] = []

        def add(status, title, detail=""):
            res.append({"status": status, "title": title, "detail": detail})

        self.diag = {"at": now_iso(), "results": res, "running": True}
        cfg = self.store.config
        add("info", "App", f"{APP_NAME} {APP_VERSION} · Python {platform.python_version()} · {platform.platform()}")
        d = cfg.get("addons_dir") or ""
        if d and Path(d).is_dir():
            try:
                t = Path(d) / ".wfam-write-test"
                t.write_text("x")
                t.unlink()
                add("ok", "AddOns folder", f"{d} (writable)" + ("" if "_forever_" in d.lower() else
                                                                " — NOTE: not inside _forever_"))
            except OSError as e:
                add("fail", "AddOns folder", f"{d} is not writable: {e}")
        else:
            add("fail", "AddOns folder", "not set or missing")
        w = self.wtf_dir()
        add("ok" if w and w.is_dir() else "warn", "WTF folder (addon settings)",
            str(w) if w and w.is_dir() else "not found yet (appears after the first login)")
        dl = self.downloads_dir()
        add("ok" if dl.is_dir() else "warn", "Downloads folder", str(dl))
        add("info", "WoW running right now", "yes" if game_running() else "no")
        try:
            cf = self.client()
            types = cf.version_types()
            forever = [f"{t.get('name')} ({t.get('id')})" for t in types if "forever" in (t.get("name") or "").lower()]
            add("ok", "CurseForge API key", f"works — {len(types)} WoW game versions; Forever: {', '.join(forever) or 'none!'}")
            type_id = self.ensure_type(cf)
        except UserError as e:
            add("fail", "CurseForge API key", str(e))
            self._finish_diag()
            return
        site_only, allowed = [], []
        try:
            mods, pag = cf.search("", type_id, 2, 0, 50)
            tc = int(pag.get("totalCount") or 0)
            add("ok", "Catalog search", f"{'10,000+' if tc >= 10000 else tc} addons with a Forever version; most popular: "
                + ", ".join(m.get("name", "?") for m in mods[:5]))
            allowed = [m for m in mods if m.get("allowModDistribution") is not False]
            site_only = [m for m in mods if m.get("allowModDistribution") is False]
            gh = [m for m in site_only if github_repo_of(m)]
            add("info", "Download permissions (top 50)", f"{len(allowed)} direct · {len(site_only)} CurseForge-site only "
                f"({len(gh)} of those link a GitHub page)")
        except UserError as e:
            add("fail", "Catalog search", str(e))
        try:
            best = None
            for m in allowed[:12]:
                f = pick_latest(cf.files(m["id"], type_id), type_id, "release")
                if f and f.get("fileLength") and (not best or f["fileLength"] < best[1]["fileLength"]):
                    best = (m, f)
            if best:
                m, f = best
                url = f.get("downloadUrl") or cf.download_url(m["id"], f["id"])
                tmp_dir = Path(tempfile.mkdtemp(prefix="wfam-diag-", dir=self.store.dir))
                try:
                    zp = tmp_dir / "t.zip"
                    cf.download(url, zp, file_sha1(f))
                    ok_toc = zip_supports_interface(zp, str(cfg.get("interface") or DEFAULT_INTERFACE))
                    add("ok" if ok_toc else "warn", "Test download from CurseForge",
                        f"{m['name']} {f.get('displayName')} ({fmt_size(zp.stat().st_size)}): checksum OK, "
                        + ("Forever .toc found" if ok_toc else "but no .toc that Forever would load was found"))
                finally:
                    rmtree(tmp_dir)
            else:
                add("warn", "Test download from CurseForge", "no small addon with direct downloads found to test")
        except UserError as e:
            add("fail", "Test download from CurseForge", str(e))
        try:
            core = GitHub().rate_limit().get("resources", {}).get("core", {})
            add("ok", "GitHub reachable", f"{core.get('remaining')}/{core.get('limit')} requests left this hour")
            tested = 0
            for m in site_only:
                repo = github_repo_of(m)
                if not repo or tested >= 3:
                    continue
                tested += 1
                try:
                    rel, asset = GitHub().find_forever(repo, "release", str(cfg.get("interface") or DEFAULT_INTERFACE))
                    add("ok" if asset else "info", f"GitHub: {m['name']}",
                        f"{repo}: " + (f"Forever file {asset['name']} in {rel.get('tag_name')}" if asset
                                       else "no Forever build in recent releases → uses CurseForge's download page"))
                except UserError as e:
                    if "not found" in str(e):
                        add("info", f"GitHub: {m['name']}", f"{repo}: the GitHub link on CurseForge leads nowhere "
                            f"(moved or deleted) → Wago or CurseForge's download page is used instead")
                    else:
                        add("warn", f"GitHub: {m['name']}", f"{repo}: {e}")
        except UserError as e:
            add("warn", "GitHub reachable", str(e))
        wg = self.wago()
        if wg:
            try:
                hits = wg.search("bags")
                linked = [r for r in self.store.addons.values() if r.get("wago")]
                with_rel = [r for r in linked if r.get("wago_latest")]
                add("ok", "Wago key", f"works — test search ({wg.gv}) found {len(hits)} addons; {len(linked)} of your "
                    f"addons are linked to Wago, {len(with_rel)} with a Forever release there")
                newer = [r["name"] for r in self.store.addons.values() if self.update_source(r) == "wago"]
                if newer:
                    add("info", "Newer on Wago", ", ".join(newer))
            except UserError as e:
                add("fail", "Wago key", str(e))
        elif (cfg.get("wago_key") or "").strip():
            add("info", "Wago", "turned off in Settings")
        # Does our folder fingerprint match CurseForge's? Compare with addons installed from CurseForge.
        try:
            checked = matched = 0
            ad = Path(d)
            for k, r in list(self.store.addons.items()):
                if checked >= 4 or r.get("source") == "github" or not r.get("installed_file_id") \
                        or not self._folders_present(r):
                    continue
                f = cf.file(int(k), r["installed_file_id"])
                mods = {mm.get("name"): mm.get("fingerprint") for mm in f.get("modules") or []}
                for folder, fp in mods.items():
                    if folder in r["folders"] and fp:
                        checked += 1
                        matched += fp in folder_fingerprints(ad / folder)
                        break
            if checked:
                add("ok" if matched == checked else "warn", "Fingerprint method",
                    f"{matched}/{checked} installed addons fingerprint exactly like CurseForge"
                    + ("" if matched == checked else " — recognising hand-installed addons may miss some"))
            n, total = self.op_scan(quiet=True)
            add("ok" if n or not total else "info", "Hand-installed addons", f"{n} recognised among {total} unmanaged folders")
        except UserError as e:
            add("warn", "Hand-installed addons", str(e))
        counts = collections.Counter(self.status_of(r) for r in self.store.addons.values())
        add("info", "Managed addons", ", ".join(f"{v} {k}" for k, v in sorted(counts.items())) or "none")
        self._finish_diag()

    def _finish_diag(self):
        res = self.diag["results"]
        self.diag["running"] = False
        bad = sum(1 for r in res if r["status"] == "fail")
        warn = sum(1 for r in res if r["status"] == "warn")
        self.log(f"Diagnostics finished: {bad} problem(s), {warn} warning(s).", "ok" if not bad else "warn")

    # -- export / import ---------------------------------------------------------- #
    def export_list(self) -> dict:
        items = []
        for k, r in sorted(self.store.addons.items(), key=lambda kv: (kv[1].get("name") or "").lower()):
            if is_wago_key(k):
                items.append({"wago": (r.get("wago") or {}).get("id"), "name": r.get("name"), "source": "wago",
                              "version": r.get("installed_version"), "pinned": bool(r.get("pinned")), "url": r.get("url")})
                continue
            items.append({"id": int(k), "name": r.get("name"), "version": r.get("installed_version"),
                          "file_id": r.get("installed_file_id"), "pinned": bool(r.get("pinned")),
                          "source": r.get("source") or "curseforge", "url": r.get("url")})
        return {"app": APP_NAME, "format": 1, "exported_at": now_iso(), "addons": items}

    SHARE_PREFIX = "WFA1."

    def share_code(self) -> str:
        """The whole list as one line of text, to paste in Discord or into the app on another PC."""
        items = []
        for k, r in sorted(self.store.addons.items(), key=lambda kv: (kv[1].get("name") or "").lower()):
            if is_wago_key(k):
                if (r.get("wago") or {}).get("id"):
                    items.append("w:" + str(r["wago"]["id"]))
            else:
                fid = r.get("installed_file_id") if r.get("pinned") else None
                items.append([int(k), int(fid)] if fid else int(k))
        raw = zlib.compress(json.dumps(items, separators=(",", ":")).encode("utf-8"), 9)
        return self.SHARE_PREFIX + base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    def _items_from_code(self, text: str) -> list[dict]:
        body = re.sub(r"\s+", "", text)[len(self.SHARE_PREFIX):]
        try:
            dec = zlib.decompressobj()
            raw = dec.decompress(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)), 1 << 20)
            assert not dec.unconsumed_tail          # refuse anything that unpacks to more than 1 MB
            data = json.loads(raw.decode("utf-8"))
            assert isinstance(data, list)
        except Exception:
            raise UserError("That share code is damaged — copy the whole line again.")
        out = []
        for it in data[:2000]:
            if isinstance(it, str) and it.startswith("w:"):
                out.append({"wago": it[2:], "name": it[2:]})
            elif isinstance(it, int):
                out.append({"id": it})
            elif isinstance(it, list) and it and all(isinstance(x, int) for x in it):
                out.append({"id": it[0], "file_id": it[1] if len(it) > 1 else None, "pinned": len(it) > 1})
        return out

    def op_import(self, data: bytes, allow_links: bool = False):
        text = data.decode("utf-8-sig", "replace").strip()
        if text.startswith(self.SHARE_PREFIX):
            items = self._items_from_code(text)
        else:
            try:
                doc = json.loads(text)
                items = doc["addons"]
                assert isinstance(items, list)
            except Exception:
                if allow_links and text and not text.startswith(("{", "[")):
                    return self.op_add(text)          # a plain list of links or names
                raise UserError("That isn't an addon list exported by this app.")
        refs, wago_ids = [], []
        for it in items:
            if not isinstance(it, dict):
                continue
            if it.get("wago"):
                wid = str(it["wago"])
                if not any(str((r.get("wago") or {}).get("id")) == wid and self._folders_present(r)
                           for r in self.store.addons.values()):
                    wago_ids.append(wid)
                continue
            try:
                mid = int(it["id"])
            except (KeyError, TypeError, ValueError):
                continue
            if str(mid) in self.store.addons and self._folders_present(self.store.addons[str(mid)]):
                continue
            fid = it.get("file_id") if it.get("pinned") else None
            refs.append({"kind": "id", "id": mid, "file": int(fid) if fid else None, "raw": it.get("name") or str(mid)})
        if not refs and not wago_ids:
            self.log("Everything in that list is already installed.", "ok")
            return
        self.log(f"Importing {len(refs) + len(wago_ids)} addon(s)…")
        if refs:
            self._add_refs(refs)
        if wago_ids and not self.wago():
            self.log(f"{len(wago_ids)} addon(s) in the list are only on Wago — add your Wago key in Settings "
                     f"and import again to get them.", "warn")
            return
        for wid in wago_ids:
            try:
                self.op_add_wago(wid)
            except UserError as e:
                self.log(f"Wago addon {wid}: {e}", "error")

    # -- Wago ----------------------------------------------------------------- #
    def wago(self) -> Wago | None:
        cfg = self.store.config
        if not cfg.get("use_wago", True) or not (cfg.get("wago_key") or "").strip():
            return None
        return Wago(cfg["wago_key"], cfg.get("wago_game_version") or "forever")

    def _link_wago(self, wg: Wago, rec: dict, slugs: dict | None, allow_search: bool) -> bool:
        """Find this addon on Wago: its .toc's X-Wago-ID, the same slug, or a search by name."""
        d = self.store.config.get("addons_dir")
        if d and rec.get("primary"):
            wid = read_toc(Path(d) / rec["primary"]).get("wago_id")
            if wid:
                rec["wago"] = {"id": wid, "via": "toc"}
                return True
        if slugs and rec.get("slug") in slugs and (slugs[rec["slug"]] or {}).get("id"):
            rec["wago"] = {"id": slugs[rec["slug"]]["id"], "via": "slug"}
            return True
        if allow_search and rec.get("name"):
            for r in wg.search(rec["name"])[:10]:
                if same_name(r.get("display_name") or r.get("name"), rec["name"]) and (r.get("id") or r.get("slug")):
                    rec["wago"] = {"id": r.get("id") or r.get("slug"), "via": "search",
                                   "url": r.get("website_url")}
                    return True
        return False

    def _wago_newer(self, rec: dict) -> bool:
        wl = rec.get("wago_latest")
        if not wl or not self.wago():
            return False
        if rec.get("source") == "wago":
            wi = rec.get("wago_installed") or {}
            c = version_cmp(wl.get("label"), rec.get("installed_version") or wi.get("label"))
            if c is not None:
                return c > 0
            a, b = _ts(wl.get("created_at")), _ts(wi.get("created_at"))
            return bool(a and b and a > b and norm_version(wl.get("label")) != norm_version(wi.get("label")))
        c = version_cmp(wl.get("label"), rec.get("installed_version"))
        if c is not None:      # the higher version number wins, whichever site got it first
            return c > 0
        inst = _ts(rec.get("installed_file_date") or rec.get("local_date"))
        latest = _ts(wl.get("created_at"))
        return bool(inst and latest and latest > inst + 600
                    and norm_version(wl.get("label")) != norm_version(rec.get("installed_version")))

    def _cf_newer_by_date(self, rec: dict) -> bool:
        c = version_cmp(rec.get("latest_version"), rec.get("installed_version"))
        if c is not None and rec.get("latest_file_id"):
            return c > 0
        a, b = _ts(rec.get("latest_date")), _ts(rec.get("installed_file_date") or rec.get("local_date"))
        return bool(rec.get("latest_file_id") and a and b and a > b + 600
                    and norm_version(rec.get("latest_version")) != norm_version(rec.get("installed_version")))

    def update_source(self, rec: dict) -> str | None:
        """Where the newest version comes from: 'wago', 'curseforge', 'github' or None."""
        wago_new = self._wago_newer(rec)
        src = rec.get("source") or "curseforge"
        if src == "github":
            li = (rec.get("gh_latest") or {}).get("release_id")
            ii = (rec.get("gh_installed") or {}).get("release_id")
            gh_new = bool(li and ii and li > ii)
            if gh_new and wago_new:
                return "wago" if (_ts(rec["wago_latest"]["created_at"]) or 0) >= (_ts((rec.get("gh_latest") or {}).get("date")) or 0) else "github"
            return "github" if gh_new else ("wago" if wago_new else None)
        if src == "wago":
            cf_new = self._cf_newer_by_date(rec) and not rec.get("distribution_disabled")
        else:
            lid, iid = rec.get("latest_file_id"), rec.get("installed_file_id")
            cf_new = bool(lid and iid and lid > iid) or bool(
                lid and not iid and rec.get("latest_sha1") and rec.get("installed_sha1")
                and rec["latest_sha1"].lower() != rec["installed_sha1"].lower()) or bool(
                lid and not iid and not rec.get("installed_sha1") and self._cf_newer_by_date(rec))
        if cf_new and wago_new:
            if rec.get("distribution_disabled"):
                return "wago"
            c = version_cmp(rec["wago_latest"].get("label"), rec.get("latest_version"))
            if c:
                return "wago" if c > 0 else "curseforge"
            return "wago" if (_ts(rec["wago_latest"]["created_at"]) or 0) > (_ts(rec.get("latest_date")) or 0) + 600 else "curseforge"
        if cf_new:
            return "curseforge"
        return "wago" if wago_new else None

    def _try_wago(self, mod: dict, rec: dict, pinned: bool) -> bool:
        """Install from Wago (used when the author only allows CurseForge's own site). True if it worked."""
        wg = self.wago()
        if not wg:
            return False
        try:
            if not rec.get("wago"):
                try:
                    slugs = wg.slugs()
                except UserError:
                    slugs = {}
                if not self._link_wago(wg, rec, slugs, allow_search=True):
                    return False
            payload = wg.addon(rec["wago"]["id"])
            if rec["wago"].get("via") in ("slug", "search") and not same_name(
                    payload.get("display_name") or payload.get("name"), rec.get("name")):
                rec.pop("wago", None)
                return False
            rel = Wago.pick_release(payload, self.store.config.get("channel", "release"))
            if not rel:
                self.log(f"{mod['name']}: no WoW Forever release on Wago.", "warn")
                return False
            rec["wago"]["url"] = payload.get("website_url") or rec["wago"].get("url")
            rec["wago_latest"] = rel
            self._install_from_wago(mod, rec, rel, pinned)
            return True
        except UserError as e:
            self.log(f"{mod['name']}: Wago didn't work ({e}) — trying the next source.", "warn")
            return False

    def _install_from_wago(self, mod: dict, rec: dict, rel: dict, pinned: bool = False):
        wg = self.wago()
        if not wg:
            raise UserError("no Wago key in Settings")
        addons_dir = self.addons_dir()
        key = str(mod["id"])
        wanted = str(self.store.config.get("interface") or DEFAULT_INTERFACE)
        self.log(f"Downloading {mod['name']} {rel['label']} from Wago…")
        tmp_dir = Path(tempfile.mkdtemp(prefix="wfam-wago-", dir=self.store.dir))
        try:
            zp = tmp_dir / "addon.zip"
            digest = wg.download(rel, zp)
            if not zip_supports_interface(zp, wanted):
                raise UserError(f"the Wago file {rel['label']} isn't built for WoW Forever (interface {wanted})")
            owners = self._owners(exclude_key=key)
            prev = self._archive_current(key, rec, digest)
            analysis = install_zip_into(addons_dir, zp, rec.get("folders") or [], set(owners))
            self._push_previous(key, rec, prev)
        finally:
            rmtree(tmp_dir)
        with self.store.lock:
            rec.update({
                "folders": analysis["folders"],
                "primary": pick_primary(analysis["folders"], analysis["toc_folders"], rec.get("slug", ""), rec["name"]),
                "installed_file_id": None, "installed_version": rel["label"], "installed_file_name": None,
                "installed_file_date": rel["created_at"],
                "installed_release_type": {"stable": 1, "beta": 2, "alpha": 3}.get(rel.get("channel"), 1),
                "installed_sha1": digest, "installed_at": now_iso(), "manual": False, "pinned": pinned,
                "source": "wago", "wago_installed": {"label": rel["label"], "created_at": rel["created_at"]},
            })
            for k in ("gh_installed",):
                rec.pop(k, None)
            rec.pop("awaiting", None)
            self.store.save_state()
        n = len(analysis["folders"])
        self.log(f"Installed {mod['name']} {rel['label']} from Wago ({n} folder{'s' if n != 1 else ''}).", "ok")

    def _check_wago(self, recs: dict, channel: str):
        wg = self.wago()
        if not wg:
            return
        try:
            todo = [k for k, r in recs.items() if not r.get("wago")
                    and time.time() - float(r.get("wago_tried_at") or 0) > 7 * 86400]
            slugs = {}
            if todo:
                try:
                    slugs = wg.slugs()
                except UserError:
                    slugs = {}
            searches = 0
            for k in todo:
                r = recs[k]
                allow = searches < 10 and bool(r.get("distribution_disabled"))
                searches += allow
                try:
                    self._link_wago(wg, r, slugs, allow_search=allow)
                except UserError:
                    pass
                r["wago_tried_at"] = time.time()
            ids = {}
            for k, r in recs.items():
                if r.get("wago"):
                    ids.setdefault(str(r["wago"]["id"]), []).append(k)
            if not ids:
                return
            data = wg.recents(list(ids))
            for wid, payload in data.items():
                for k in ids.get(str(wid), []):
                    r = recs[k]
                    if r["wago"].get("via") in ("slug", "search") and payload.get("name") and not same_name(
                            payload.get("display_name") or payload.get("name"), r.get("name")):
                        r.pop("wago", None)   # same slug, different addon
                        continue
                    r["wago"]["url"] = payload.get("website_url") or r["wago"].get("url")
                    r["wago_latest"] = Wago.pick_release(payload, channel)
                    if is_wago_key(k):
                        r["latest_checked_at"] = time.time()
                        if not r["wago_latest"]:
                            self.log(f"{r.get('name')}: no WoW Forever release on Wago right now.", "warn")
        except UserError as e:
            self.log(f"Wago: {e}", "warn")

    def op_add_wago(self, wago_id: str):
        """Install an addon picked on Wago. If CurseForge has the same addon for WoW Forever it is
        installed from there and Wago is kept as a second source — whichever has the higher version
        number is used. Otherwise it is installed and kept updated from Wago alone."""
        wg = self.wago()
        if not wg:
            raise UserError("Add your Wago key in Settings first.")
        key = wago_key(wago_id)
        for k, r in self.store.addons.items():
            if str((r.get("wago") or {}).get("id")) == str(wago_id) and self._folders_present(r):
                self.log(f"{r.get('name')} is already installed.", "ok")
                return
        payload = wg.addon(wago_id)
        name = payload.get("display_name") or payload.get("name") or str(wago_id)
        slug = payload.get("slug") or ""
        channel = self.store.config.get("channel", "release")
        rel = Wago.pick_release(payload, channel)
        url = payload.get("website_url") or f"https://addons.wago.io/addons/{slug or wago_id}"
        mod = None
        if slug and (self.store.config.get("api_key") or "").strip():
            try:
                cf = self.client()
                type_id = self.ensure_type(cf)
                mod = next((m for m in cf.search_slug(slug) if same_name(m.get("name"), name)), None)
                if mod and not pick_latest(cf.files(mod["id"], type_id), type_id, channel):
                    mod = None          # on CurseForge, but not for WoW Forever
            except UserError:
                mod = None
        if mod:
            self.log(f"{name} is on CurseForge too — installing from there, with Wago as a second source.")
            self._add_refs([{"kind": "id", "id": mod["id"], "file": None, "raw": name}])
            rec = self.store.addons.get(str(mod["id"]))
            if not rec or not self._folders_present(rec):
                if not rel:
                    return
                self.log(f"{name}: trying Wago instead.")
            else:
                with self.store.lock:
                    rec["wago"] = {"id": str(wago_id), "via": "direct", "url": url}
                    rec["wago_latest"] = rel
                    self.store.save_state()
                if rel and self.update_source(rec) == "wago" and not rec.get("pinned"):
                    self.log(f"Wago has a newer version of {name} ({rel['label']}) — using that.")
                    self._install_from_wago({"id": mod["id"], "name": rec["name"]}, rec, rel)
                return
        if not rel:
            raise UserError(f"{name} has no WoW Forever release on Wago ({channel} channel).")
        self.backup_wtf()
        with self.store.lock:
            rec = self.store.addons.setdefault(key, {})
            rec.update({"name": name, "slug": slug, "url": url, "summary": payload.get("summary") or "",
                        "authors": [x if isinstance(x, str) else (x.get("name") or "") for x in payload.get("authors") or []],
                        "logo": payload.get("thumbnail_image") or payload.get("logo") or "",
                        "wago": {"id": str(wago_id), "via": "direct", "url": url}, "wago_latest": rel,
                        "source": "wago", "latest_checked_at": time.time()})
        try:
            self._install_from_wago({"id": key, "name": name}, rec, rel)
        except UserError:
            with self.store.lock:
                if not rec.get("folders"):
                    self.store.addons.pop(key, None)
                    self.store.save_state()
            raise

    def _update_wago_only(self, key: str, rec: dict):
        rel = rec.get("wago_latest")
        if self.status_of(rec) == "missing" or not rel:
            wg = self.wago()
            if not wg:
                raise UserError("needs your Wago key (Settings)")
            rel = Wago.pick_release(wg.addon(rec["wago"]["id"]), self.store.config.get("channel", "release"))
            if not rel:
                raise UserError("no WoW Forever release on Wago right now.")
            rec["wago_latest"] = rel
        self._install_from_wago({"id": key, "name": rec.get("name") or key}, rec, rel, rec.get("pinned", False))

    # -- GitHub --------------------------------------------------------------- #
    def _try_github(self, mod: dict, rec: dict, pinned: bool) -> bool:
        """Install a CurseForge-site-only addon from its GitHub releases. True if it worked."""
        if not self.store.config.get("use_github", True):
            return False
        repo = github_repo_of(mod)
        if not repo:
            return False
        wanted = str(self.store.config.get("interface") or DEFAULT_INTERFACE)
        try:
            rel, asset = GitHub().find_forever(repo, self.store.config.get("channel", "release"), wanted)
            if not asset:
                self.log(f"{mod['name']}: its GitHub page ({repo}) has no WoW Forever build — "
                         f"using CurseForge's download page instead.", "warn")
                return False
            self._install_from_github(mod, rec, repo, rel, asset, pinned)
            return True
        except UserError as e:
            self.log(f"{mod['name']}: GitHub didn't work ({e}) — using CurseForge's download page instead.", "warn")
            return False

    def _install_from_github(self, mod, rec, repo, rel, asset, pinned=False, prefetched=None):
        addons_dir = self.addons_dir()
        key = str(mod["id"])
        tag = rel.get("name") or rel.get("tag_name") or asset["name"]
        wanted = str(self.store.config.get("interface") or DEFAULT_INTERFACE)
        tmp_dir = Path(tempfile.mkdtemp(prefix="wfam-gh-", dir=self.store.dir))
        try:
            if prefetched:
                zp, digest = prefetched
            else:
                self.log(f"Downloading {mod['name']} {tag} from GitHub ({repo})…")
                zp = tmp_dir / "addon.zip"
                digest = GitHub().download(asset, zp)
            if not zip_supports_interface(zp, wanted):
                raise UserError(f"the GitHub release {tag} isn't built for WoW Forever (interface {wanted})")
            owners = self._owners(exclude_key=key)
            prev = self._archive_current(key, rec, digest)
            analysis = install_zip_into(addons_dir, zp, rec.get("folders") or [], set(owners))
            self._push_previous(key, rec, prev)
        finally:
            rmtree(tmp_dir)
        info = {"release_id": rel["id"], "tag": rel.get("tag_name"), "name": tag, "asset": asset["name"],
                "date": rel.get("published_at"), "url": rel.get("html_url"), "prerelease": bool(rel.get("prerelease"))}
        with self.store.lock:
            rec.update({
                "folders": analysis["folders"],
                "primary": pick_primary(analysis["folders"], analysis["toc_folders"], rec.get("slug", ""), rec["name"]),
                "installed_file_id": None, "installed_version": tag, "installed_file_name": asset["name"],
                "installed_file_date": rel.get("published_at"), "installed_release_type": 2 if rel.get("prerelease") else 1,
                "installed_sha1": digest, "installed_at": now_iso(), "manual": False, "pinned": pinned,
                "source": "github", "github": repo, "gh_installed": info,
            })
            rec.pop("wago_installed", None)
            if not rec.get("gh_latest") or rec["gh_latest"]["release_id"] <= rel["id"]:
                rec["gh_latest"] = info
            self._set_latest_github(rec)
            rec.pop("awaiting", None)
            self.store.save_state()
        n = len(analysis["folders"])
        self.log(f"Installed {mod['name']} {tag} from GitHub ({n} folder{'s' if n != 1 else ''}).", "ok")

    @staticmethod
    def _set_latest_github(rec: dict):
        gl = rec.get("gh_latest") or {}
        rec.update(latest_file_id=None, latest_sha1=None, latest_version=gl.get("name"),
                   latest_date=gl.get("date"), latest_release_type=2 if gl.get("prerelease") else 1,
                   latest_checked_at=now_iso())

    # -- operations --------------------------------------------------------- #
    def op_add(self, refs_text: str):
        texts = split_refs(refs_text)
        if not texts:
            raise UserError("Paste at least one CurseForge link.")
        refs: list[dict] = []
        for t in texts:
            try:
                refs.append(parse_ref(t))
            except UserError as e:
                self.log(str(e), "error")
        self._add_refs(refs)

    def _add_refs(self, refs: list[dict]):
        cf = self.client()
        self.addons_dir()
        type_id = self.ensure_type(cf)
        self.backup_wtf()
        queue = list(refs)
        seen: set[int] = set()
        while queue:
            ref = queue.pop(0)
            try:
                mod = self._resolve_mod(cf, ref)
                if mod["id"] in seen:
                    continue
                seen.add(mod["id"])
                f, pinned = self._choose_file(cf, mod, ref.get("file"), type_id)
                rec = self.store.addons.get(str(mod["id"]))
                if (rec and rec.get("installed_file_id") == f["id"] and not ref.get("file")
                        and self._folders_present(rec)):
                    self.log(f"{mod['name']} is already installed and up to date.", "ok")
                    deps = []
                else:
                    deps = self._install(cf, mod, f, pinned=pinned)
                    if pinned:
                        self.log(f"{mod['name']} is pinned to this exact file (you linked it). "
                                 f"Unpin it to get updates.", "warn")
                if self.store.config.get("auto_dependencies", True):
                    for dep in deps:
                        if str(dep) not in self.store.addons and dep not in seen:
                            self.log(f"{mod['name']} needs another addon (id {dep}) — adding it.")
                            queue.append({"kind": "id", "id": dep, "file": None, "raw": f"dependency {dep}"})
            except UserError as e:
                self.log(f"{ref['raw']}: {e}", "error")

    def _hydrate_installed(self, cf: CurseForge, recs: dict):
        """Fetch the version names of the installed (and saved previous) CurseForge files."""
        want = set()
        for r in recs.values():
            if (r.get("source") or "curseforge") == "curseforge" and r.get("installed_file_id") \
                    and not r.get("installed_file_date"):
                want.add(r["installed_file_id"])
            for pv in r.get("previous") or []:
                if (pv.get("source") or "curseforge") == "curseforge" and pv.get("installed_file_id") \
                        and not pv.get("installed_file_date"):
                    want.add(pv["installed_file_id"])
        if not want:
            return
        files = {}
        for c in chunks(sorted(want), 50):
            for f in cf.files_by_ids(c):
                files[f["id"]] = f

        def fill(target, f):
            target.update(installed_version=f.get("displayName") or f.get("fileName"),
                          installed_file_name=f.get("fileName"), installed_file_date=f.get("fileDate"),
                          installed_release_type=f.get("releaseType", 1))
        with self.store.lock:
            for r in recs.values():
                if r.get("installed_file_id") in files and (r.get("source") or "curseforge") == "curseforge":
                    fill(r, files[r["installed_file_id"]])
                for pv in r.get("previous") or []:
                    if pv.get("installed_file_id") in files and (pv.get("source") or "curseforge") == "curseforge":
                        fill(pv, files[pv["installed_file_id"]])

    def _reconcile_local(self, cf: "CurseForge", recs: dict):
        """Notice addons another tool (the Wago app, the CurseForge app, a manual copy) updated,
        and find out exactly which version is now on disk."""
        d = self.store.config.get("addons_dir")
        if not d or not Path(d).is_dir():
            return
        base = Path(d)
        changed: dict[str, list] = {}
        for k, r in recs.items():
            if not r.get("folders") or r.get("awaiting") or not self._folders_present(r):
                continue
            loc = r.get("local") or {}
            sig = local_signature(base, r)
            if loc.get("pending") or loc.get("sig") != sig:
                changed[k] = sig
        if not changed:
            return
        prints = run_parallel(lambda k: {fp for f in recs[k]["folders"] for fp in folder_fingerprints(base / f)},
                              list(changed))
        allp = sorted({fp for v in prints.values() if not isinstance(v, Exception) for fp in v})
        by_mod: dict[str, list] = {}
        if allp:
            data = cf.fingerprint_matches(allp)
            for m in data.get("exactMatches") or []:
                f = m.get("file") or {}
                mid = m.get("id") or f.get("modId")
                if mid and f.get("id"):
                    by_mod.setdefault(str(mid), []).append(f)
        for k, sig in changed.items():
            r = recs[k]
            with self.store.lock:
                self._identify_local(base, k, r, by_mod.get(k) or [], sig)

    def _identify_local(self, base: Path, k: str, r: dict, cf_files: list, sig: list):
        toc = read_toc(base / r["primary"]) if r.get("primary") else {}
        tv = toc.get("version") or ""
        was_pending = bool((r.get("local") or {}).get("pending"))
        before = r.get("installed_version")
        src = r.get("source") or "curseforge"
        folders = set(r.get("folders") or [])
        f = max(cf_files, key=lambda f: (folders <= {x.get("name") for x in f.get("modules") or []}, f["id"]),
                default=None)
        wl = r.get("wago_latest") or {}
        wi = r.get("wago_installed") or {}
        gl = r.get("gh_latest") or {}
        gi = r.get("gh_installed") or {}
        nv = norm_version(tv)

        def take_github(info):
            r.update(installed_file_id=None, installed_version=info.get("name") or info.get("tag"),
                     installed_file_name=info.get("asset"), installed_file_date=info.get("date"),
                     installed_release_type=2 if info.get("prerelease") else 1, installed_sha1=None,
                     source="github", gh_installed=info)
            r.pop("wago_installed", None)

        def take_wago(rel):
            r.update(installed_file_id=None, installed_version=rel["label"], installed_file_name=None,
                     installed_file_date=rel["created_at"], installed_sha1=None, source="wago",
                     installed_release_type={"stable": 1, "beta": 2, "alpha": 3}.get(rel.get("channel"), 1),
                     wago_installed={"label": rel["label"], "created_at": rel["created_at"]})
            r.pop("gh_installed", None)

        def take_cf(f):
            if r.get("installed_file_id") == f["id"] and src == "curseforge":
                return
            r.update(installed_file_id=f["id"], installed_version=f.get("displayName") or f.get("fileName"),
                     installed_file_name=f.get("fileName"), installed_file_date=f.get("fileDate"),
                     installed_release_type=f.get("releaseType", 1), installed_sha1=file_sha1(f),
                     source="curseforge", manual=False)
            r.pop("wago_installed", None)
            r.pop("gh_installed", None)
            present = [m["name"] for m in f.get("modules") or [] if m.get("name") and (base / m["name"]).is_dir()]
            if present and set(present) != folders:
                r["folders"] = present
                if r.get("primary") not in present:
                    r["primary"] = pick_primary(present, present, r.get("slug", ""), r.get("name", ""))

        def gh_match(info):
            return bool(nv and info and nv in (norm_version(info.get("tag")), norm_version(info.get("name"))))

        def wago_match(rel):
            return bool(nv and rel and rel.get("label") and norm_version(rel["label"]) == nv)

        how = None
        if src == "github" and gh_match(gi):
            how = "same"
        elif src == "github" and gh_match(gl):
            take_github(gl); how = "github"
        elif src == "wago" and wago_match(wi):
            how = "same"
        elif src == "wago" and wago_match(wl):
            take_wago(wl); how = "wago"
        elif f:
            take_cf(f); how = "curseforge"
        elif wago_match(wl):
            take_wago(wl); how = "wago"
        elif gh_match(gl):
            take_github(gl); how = "github"
        if how:
            r.pop("external", None)
            r.pop("local_date", None)
        else:
            # Unknown build: show what its .toc says and compare by date from now on.
            newest = max((s[3] for s in sig), default=0)
            r.update(installed_file_id=None, installed_sha1=None, installed_file_name=None,
                     installed_file_date=None, installed_version=tv or before, external=True,
                     local_date=datetime.fromtimestamp(newest / 1e9, timezone.utc).isoformat() if newest else None)
            r.pop("wago_installed", None)
            r.pop("gh_installed", None)
        r["local"] = {"at": install_token(r), "sig": local_signature(base, r), "toc": tv or None}
        after = r.get("installed_version")
        if how != "same" and not (was_pending and how) and norm_version(after) != norm_version(before):
            where = {"curseforge": "matches CurseForge's file", "wago": "matches Wago's release",
                     "github": "matches the GitHub release"}.get(how, "unknown build — compared by date from now on")
            self.log(f"{r.get('name') or k} was changed outside the manager (e.g. by the Wago app): "
                     f"now {after or '?'} ({where}).", "ok" if how else "warn")

    # -- updates of this program itself ------------------------------------------ #
    def update_repo(self) -> str:
        r = (self.store.config.get("update_repo") or UPDATE_REPO).strip().strip("/")
        return r if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", r or "") else ""

    @staticmethod
    def _quiet(fn, *args):
        try:
            fn(*args)
        except Exception:
            pass

    def check_self_update(self, notify: bool = True) -> dict | None:
        self._self_checked_at = time.time()
        repo = self.update_repo()
        if not repo:
            return None
        found = None
        try:
            rels = GitHub()._get(f"{GITHUB_API}/repos/{repo}/releases?per_page=5")
        except UserError:
            rels = []
        for rel in rels if isinstance(rels, list) else []:
            if rel.get("draft") or rel.get("prerelease"):
                continue
            ver = (rel.get("tag_name") or "").lstrip("vV")
            if version_tuple(ver) <= version_tuple(APP_VERSION):
                break
            asset = next((a for a in rel.get("assets") or []
                          if re.fullmatch(r"WoWForeverAddons-Setup-[\d.]+\.exe", a.get("name") or "")), None)
            if not asset:
                continue
            found = {"version": ver, "asset": asset, "notes": (rel.get("body") or "")[:4000],
                     "page": rel.get("html_url") or f"https://github.com/{repo}/releases"}
            break
        pushed = self._repo_update(repo)      # updates/latest.json in the repository itself
        if pushed and (not found or version_tuple(pushed["version"]) > version_tuple(found["version"])):
            found = pushed
        if not found:
            self.self_update = None
            return None
        if notify and (self.self_update or {}).get("version") != found["version"]:
            self.notify(f"WoW Forever Addons {found['version']} is available", "Open the program and click “Update now” "
                                                                               "at the bottom of the menu.")
        self.self_update = found
        return found

    def _repo_update(self, repo: str) -> dict | None:
        """A newer setup published as updates/latest.json + updates/<setup exe> on the main branch."""
        if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
            return None
        base = f"{GITHUB_RAW}/{repo}/main/updates"
        try:
            m = GitHub()._get(f"{base}/latest.json?t={int(time.time()) // 600}", accept="application/json")
        except UserError:
            return None
        if not isinstance(m, dict):
            return None
        ver, name, sha = str(m.get("version") or ""), str(m.get("file") or ""), str(m.get("sha256") or "").lower()
        if not (re.fullmatch(r"\d+(\.\d+){1,3}", ver) and re.fullmatch(r"WoWForeverAddons-Setup-[\d.]+\.exe", name)
                and re.fullmatch(r"[0-9a-f]{64}", sha) and str(m.get("size") or "").isdigit()):
            return None
        if version_tuple(ver) <= version_tuple(APP_VERSION):
            return None
        return {"version": ver, "notes": str(m.get("notes") or "")[:4000],
                "page": f"https://github.com/{repo}/tree/main/updates",
                "asset": {"name": name, "browser_download_url": f"{base}/{name}", "digest": "sha256:" + sha,
                          "size": int(m["size"])}}

    def op_self_update(self, background: bool = False):
        if os.name != "nt" or not host_exe().is_file():
            raise UserError("Updating itself works in the installed program (Windows).")
        info = self.self_update or self.check_self_update(notify=False)
        if not info:
            self.log("You already have the newest version.", "ok")
            return
        tmp = Path(tempfile.gettempdir()) / f"wfam-update-{info['version']}.exe"
        self.log(f"Downloading WoW Forever Addons {info['version']}…")
        GitHub().download(info["asset"], tmp)
        with open(tmp, "rb") as f:
            if f.read(2) != b"MZ" or tmp.stat().st_size != int(info["asset"].get("size") or tmp.stat().st_size):
                tmp.unlink(missing_ok=True)
                raise UserError("The downloaded update is damaged — try again later.")
        self.log("Installing the update — the program restarts in a moment.", "ok")
        subprocess.Popen([str(tmp), "/quiet", "/restart"] + (["/tray"] if background else []), cwd=str(tmp.parent),
                         close_fds=True,
                         creationflags=0x00000008 | 0x00000200)   # detached: it closes and replaces this program

    def notify(self, title: str, text: str):
        with self.store.lock:
            self._notice_id += 1
            self.notices.append({"id": self._notice_id, "t": time.time(), "title": title, "text": text[:240]})
            del self.notices[:-20]

    def op_background(self, update: bool | None = None, report_nothing: bool = False):
        """Check for updates and (by default) install them, then report with a tray notification.
        Never opens download pages: that would pop up browser windows out of nowhere."""
        cfg = self.store.config
        update = cfg.get("bg_auto_update", True) if update is None else update
        self.op_check()
        recs = self.store.addons
        ready = [k for k, r in recs.items() if self.status_of(r) == "update"]
        manual = [k for k, r in recs.items() if self.status_of(r) == "update_manual"]
        names = lambda ks: ", ".join(recs[k].get("name") or k for k in ks)
        plural = lambda n, w: f"{n} {w}{'' if n == 1 else 's'}"
        if update and ready:
            before = {k: (recs[k].get("installed_file_id"), recs[k].get("installed_version")) for k in ready}
            was = self.interactive
            self.interactive = False
            try:
                self.op_update(ready)
            finally:
                self.interactive = was
            done = [k for k in ready if k in recs and self.status_of(recs[k]) != "update"
                    and (recs[k].get("installed_file_id"), recs[k].get("installed_version")) != before[k]]
            failed = [k for k in ready if k not in done]
            if done:
                self.notify(plural(len(done), "addon") + " updated", names(done))
            if failed:
                self.notify("Some updates didn't install", names(failed) + " — open the manager for details.")
            self._last_available = ()
        elif ready:
            if report_nothing or tuple(sorted(ready)) != self._last_available:
                self.notify(plural(len(ready), "update") + " available", names(ready) + " — click to open the manager.")
            self._last_available = tuple(sorted(ready))
        if manual and (report_nothing or update):
            self.notify(plural(len(manual), "addon") + " need a download from CurseForge",
                        names(manual) + " — open the manager and click Update.")
        if report_nothing and not ready and not manual:
            self.notify("Everything is up to date", plural(len(recs), "addon") + " checked.")

    def op_check(self):
        self.last_check_at = time.time()
        cf = self.client()
        type_id = self.ensure_type(cf)
        channel = self.store.config.get("channel", "release")
        recs = self.store.addons
        if not recs:
            self.log("No managed addons yet — paste some CurseForge links first.")
            return
        ids = [int(k) for k in recs if not is_wago_key(k)]
        mods: dict[int, dict] = {}
        for c in chunks(ids, 50):
            for m in cf.mods(c):
                mods[m["id"]] = m
        self._hydrate_installed(cf, recs)
        pending: dict[int, int] = {}
        scan: list[int] = []
        for mid in ids:
            m = mods.get(mid)
            rec = recs[str(mid)]
            if not m:
                self.log(f"{rec.get('name', mid)} is no longer available on CurseForge.", "warn")
                continue
            with self.store.lock:
                self._apply_meta(rec, m)
            idx = [x for x in (m.get("latestFilesIndexes") or [])
                   if x.get("gameVersionTypeId") == type_id and x.get("releaseType", 1) in CHANNELS.get(channel, {1})]
            if idx:
                pending[mid] = max(x["fileId"] for x in idx)
            else:
                scan.append(mid)
        files: dict[int, dict] = {}
        for c in chunks(list(pending.values()), 50):
            for f in cf.files_by_ids(c):
                files[f["id"]] = f
        for mid, fid in pending.items():
            if fid in files:
                with self.store.lock:
                    self._set_latest(recs[str(mid)], files[fid])
            else:
                scan.append(mid)
        gh_keys = [k for k, r in recs.items() if r.get("source") == "github" and r.get("github")]
        wanted = str(self.store.config.get("interface") or DEFAULT_INTERFACE)

        def lookup(item):
            kind, k = item
            if kind == "cf":
                return pick_latest(cf.files(k, type_id), type_id, channel)
            return GitHub().find_forever(recs[k]["github"], channel, wanted)

        found = run_parallel(lookup, [("cf", mid) for mid in scan] + [("gh", k) for k in gh_keys])
        for mid in scan:
            f = found.get(("cf", mid))
            if isinstance(f, Exception):
                self.log(f"{recs[str(mid)].get('name')}: {f}", "warn")
                continue
            with self.store.lock:
                self._set_latest(recs[str(mid)], f)
            if not f:
                self.log(f"{recs[str(mid)].get('name')}: no WoW Forever file on the {channel} channel.", "warn")
        for k in gh_keys:
            r = recs[k]
            got = found.get(("gh", k))
            if isinstance(got, Exception):
                self.log(f"{r['name']}: couldn't check GitHub ({got}).", "warn")
                continue
            rel, asset = got
            with self.store.lock:
                if rel:
                    r["gh_latest"] = {"release_id": rel["id"], "tag": rel.get("tag_name"),
                                      "name": rel.get("name") or rel.get("tag_name"), "asset": asset["name"],
                                      "date": rel.get("published_at"), "url": rel.get("html_url"),
                                      "prerelease": bool(rel.get("prerelease"))}
                else:
                    self.log(f"{r['name']}: no WoW Forever build in its recent GitHub releases.", "warn")
                self._set_latest_github(r)
        self.store.save_state()
        self._check_wago(recs, channel)
        self.store.save_state()
        try:
            self._reconcile_local(cf, recs)
        except UserError as e:
            self.log(f"Couldn't check for changes made outside the manager: {e}", "warn")
        self.store.save_state()
        try:
            self.op_scan(quiet=True, cached_only=True)
        except UserError:
            pass
        self.store.state["last_check"] = time.time()
        self.store.save_state()
        if time.time() - getattr(self, "_self_checked_at", 0) > 600:   # the same button also looks for a new program version
            self._self_checked_at = time.time()
            threading.Thread(target=lambda: self._quiet(self.check_self_update, False), daemon=True).start()
        updates = [r["name"] + (" (Wago)" if self.update_source(r) == "wago" else "")
                   for r in recs.values() if self.status_of(r) in ("update", "update_manual")]
        if updates:
            self.log(f"{len(updates)} update{'s' if len(updates) != 1 else ''} available: {', '.join(sorted(updates))}.", "ok")
        else:
            self.log("Everything is up to date.", "ok")

    def op_update(self, keys: list[str] | None = None):
        recs = self.store.addons
        before = {k: (r.get("installed_file_id"), r.get("installed_at"), r.get("installed_version")) for k, r in recs.items()}
        try:
            self._op_update(keys)
        finally:
            changed = [k for k, r in recs.items() if k in before and r.get("previous")
                       and (r.get("installed_file_id"), r.get("installed_at"), r.get("installed_version")) != before[k]]
            if changed:
                with self.store.lock:
                    self.store.state["last_batch"] = {"at": now_iso(), "keys": changed}
                    self.store.save_state()

    def op_undo_batch(self):
        """Put back the versions you had before the last update (each gets pinned)."""
        batch = self.store.state.get("last_batch") or {}
        keys = [k for k in batch.get("keys") or [] if k in self.store.addons]
        if not keys:
            raise UserError("There's no recent update to undo.")
        done = []
        for k in keys:
            try:
                self.op_revert(k)
                done.append(self.store.addons[k].get("name") or k)
            except UserError as e:
                self.log(f"{self.store.addons[k].get('name') or k}: {e}", "warn")
        with self.store.lock:
            self.store.state.pop("last_batch", None)
            self.store.save_state()
        if done:
            self.log(f"Undid the last update: {', '.join(done)} (pinned — unpin when fixed versions are out).", "ok")

    def _op_update(self, keys: list[str] | None = None):
        self._opened = 0
        cf = self.client()
        type_id = self.ensure_type(cf)
        recs = self.store.addons
        if keys is None:
            wanted = ("update", "missing", "update_manual") if (self.store.config.get("auto_manual", True)
                                                                and self.interactive) else ("update", "missing")
            targets = [k for k, r in recs.items() if self.status_of(r) in wanted]
            manual = [r["name"] for r in recs.values() if self.status_of(r) == "update_manual" and
                      "update_manual" not in wanted]
            if manual:
                self.log(f"Needs a manual download: {', '.join(manual)}.", "warn")
            if not targets:
                self.log("Nothing to update.", "ok")
                return
        else:
            targets = [k for k in keys if k in recs]
        self.backup_wtf()
        # One request for all the addons' details and one for all their files…
        mods: dict[str, dict] = {}
        for c in chunks([int(k) for k in targets if not is_wago_key(k)], 50):
            for m in cf.mods(c):
                mods[str(m["id"])] = m
        want: dict[str, int] = {}
        for key in targets:
            rec = recs[key]
            fid = rec.get("latest_file_id")
            if self.status_of(rec) == "missing" and rec.get("installed_file_id") and rec.get("pinned"):
                fid = rec["installed_file_id"]
            if fid:
                want[key] = fid
        files: dict[int, dict] = {}
        for c in chunks(list(want.values()), 50):
            for f in cf.files_by_ids(c):
                files[f["id"]] = f
        # …then download everything that can be fetched directly, several at a time…
        tmp_root = Path(tempfile.mkdtemp(prefix="wfam-upd-", dir=self.store.dir))

        channel = self.store.config.get("channel", "release")
        iface = str(self.store.config.get("interface") or DEFAULT_INTERFACE)

        def prefetch(key):
            rec = recs[key]
            m = mods.get(key)
            if (rec.get("source") == "github" and rec.get("github") and m and m.get("allowModDistribution") is False
                    and self.store.config.get("use_github", True)):
                rel, asset = GitHub().find_forever(rec["github"], channel, iface)
                if not asset:
                    return None
                zp = tmp_root / f"{key}-gh.zip"
                return ("gh", rel, asset, zp, GitHub().download(asset, zp))
            f = files.get(want.get(key))
            if not m or not f or m.get("allowModDistribution") is False:
                return None
            url = f.get("downloadUrl") or cf.download_url(m["id"], f["id"])
            zp = tmp_root / f"{key}.zip"
            return zp, cf.download(url, zp, file_sha1(f))

        direct = [k for k in targets if (k in want or recs[k].get("source") == "github")
                  and self.update_source(recs[k]) != "wago"]
        if len(direct) > 1:
            self.log(f"Downloading {len(direct)} updates…")
        pre = run_parallel(prefetch, direct, workers=4)
        # …and install them one at a time (installing touches the AddOns folder).
        try:
            for key in targets:
                rec = recs[key]
                try:
                    if is_wago_key(key):
                        self._update_wago_only(key, rec)
                        continue
                    mod = mods.get(key) or cf.mod(int(key))
                    if self.update_source(rec) == "wago" and self.status_of(rec) != "missing":
                        try:
                            with self.store.lock:
                                self._apply_meta(rec, mod)
                            self._install_from_wago(mod, rec, rec["wago_latest"], rec.get("pinned", False))
                            continue
                        except UserError as e:
                            self.log(f"{rec.get('name', key)}: Wago didn't work ({e}) — trying CurseForge.", "warn")
                    got = pre.get(key)
                    if rec.get("source") == "github" and mod.get("allowModDistribution") is False:
                        with self.store.lock:
                            self._apply_meta(rec, mod)
                        if isinstance(got, tuple) and got[0] == "gh" and got[3].exists():
                            try:
                                self._install_from_github(mod, rec, rec["github"], got[1], got[2],
                                                          rec.get("pinned", False), prefetched=(got[3], got[4]))
                                continue
                            except UserError as e:
                                self.log(f"{rec['name']}: GitHub didn't work ({e}) — trying CurseForge.", "warn")
                        elif self._try_github(mod, rec, rec.get("pinned", False)):
                            continue
                    f = files.get(want.get(key)) if key in want else None
                    if not f and key in want:
                        f = cf.file(int(key), want[key])
                    if not f:
                        f = pick_latest(cf.files(int(key), type_id), type_id, self.store.config.get("channel", "release"))
                        if not f:
                            raise UserError("no WoW Forever file available.")
                    ok = isinstance(got, tuple) and len(got) == 2 and got[0].exists()
                    self._install(cf, mod, f, pinned=rec.get("pinned", False), prefetched=got if ok else None)
                except UserError as e:
                    self.log(f"{rec.get('name', key)}: {e}", "error")
        finally:
            rmtree(tmp_root)

    def op_check_and_update(self):
        self.op_check()
        self.op_update(None)

    def op_remove(self, key: str):
        recs = self.store.addons
        rec = recs.get(key)
        if not rec:
            raise UserError("That addon isn't managed.")
        addons_dir = self.addons_dir()
        self.backup_wtf()
        others = self._owners(exclude_key=key)
        removed = 0
        for name in rec.get("folders") or []:
            if name in others:
                self.log(f"Kept {name} — {', '.join(others[name])} still uses it.")
                continue
            if not safe_folder_name(name):
                continue
            p = addons_dir / name
            if p.is_dir():
                rmtree(p)
                removed += 1
        with self.store.lock:
            del recs[key]
            self.store.save_state()
        rmtree(self.store.dir / "versions" / key)
        self.log(f"Removed {rec.get('name', key)} ({removed} folder{'s' if removed != 1 else ''} deleted).", "ok")

    def op_fix_deps(self, key: str):
        """Install what an addon says it needs: first the requirements CurseForge lists for the
        installed file, then (for anything still missing) a CurseForge project with that exact name."""
        rec = self.store.addons.get(key)
        if not rec:
            raise UserError("That addon isn't managed.")
        cf = self.client()
        ids = []
        if rec.get("installed_file_id"):
            try:
                f = cf.file(int(key), rec["installed_file_id"])
                ids = [d["modId"] for d in f.get("dependencies") or [] if d.get("relationType") == REQUIRED_DEPENDENCY]
            except UserError:
                pass
        todo = [str(i) for i in ids if str(i) not in self.store.addons or not self._folders_present(self.store.addons[str(i)])]
        if todo:
            self._add_refs([{"kind": "id", "id": int(i), "file": None, "raw": f"dependency {i}"} for i in todo])
        d = Path(self.store.config["addons_dir"])
        present = {p.name.lower() for p in d.iterdir() if p.is_dir()}
        still = []
        for folder in rec.get("folders") or []:
            for dep in read_toc(d / folder).get("deps") or []:
                if dep.lower() not in present and dep not in still:
                    still.append(dep)
        found_any = False
        type_id = self.ensure_type(cf)
        for dep in still:
            mods, _ = cf.search(dep, type_id, 2, 0, 10)
            hit = next((m for m in mods if same_name(m.get("name"), dep) or (m.get("slug") or "") == dep.lower()), None)
            if hit:
                found_any = True
                self._add_refs([{"kind": "id", "id": hit["id"], "file": None, "raw": dep}])
            else:
                self.log(f"{rec['name']} needs “{dep}”, which I couldn't find on CurseForge for WoW Forever. "
                         f"Try the Browse tab, or the addon's own page.", "warn")
        if not todo and not found_any and not still:
            self.log(f"{rec['name']}: nothing is missing.", "ok")

    def op_set_pin(self, key: str, pinned: bool):
        rec = self.store.addons.get(key)
        if not rec:
            raise UserError("That addon isn't managed.")
        with self.store.lock:
            rec["pinned"] = bool(pinned)
            self.store.save_state()
        self.log(f"{rec['name']} {'pinned — it will be skipped by Update all' if pinned else 'unpinned'}.", "ok")

    def op_install_downloaded(self, key: str, path: Path, digest: str):
        rec = self.store.addons.get(key)
        if not rec:
            return
        aw = rec.get("awaiting") or {}
        self.op_install_zip(path.read_bytes(), path.name, key)
        with self.store.lock:
            if aw.get("sha1") and aw["sha1"].lower() == digest and rec.get("installed_file_id") != aw.get("file_id"):
                rec.update(installed_file_id=aw["file_id"], installed_version=aw.get("version"),
                           installed_file_date=aw.get("date"))
            rec.pop("awaiting", None)
            self.store.save_state()

    def op_cancel_wait(self, key: str):
        rec = self.store.addons.get(key)
        if rec and rec.pop("awaiting", None):
            with self.store.lock:
                self.store.save_state()
            self.log(f"Stopped waiting for {rec['name']}.")

    def downloads_dir(self) -> Path:
        d = self.store.config.get("downloads_dir")
        return Path(d) if d else default_downloads_dir()

    def op_install_zip(self, data: bytes, filename: str, key: str | None):
        addons_dir = self.addons_dir()
        self.backup_wtf()
        digest = hashlib.sha1(data).hexdigest()
        tmp_dir = Path(tempfile.mkdtemp(prefix="wfam-zip-", dir=self.store.dir))
        try:
            zp = tmp_dir / "upload.zip"
            zp.write_bytes(data)
            if key:
                rec = self.store.addons.get(key)
                if not rec:
                    raise UserError("That addon isn't managed.")
                owners = self._owners(exclude_key=key)
                prev = self._archive_current(key, rec, digest)
                analysis = install_zip_into(addons_dir, zp, rec.get("folders") or [], set(owners))
                self._push_previous(key, rec, prev)
                matches_latest = rec.get("latest_sha1") and rec["latest_sha1"].lower() == digest
                primary = pick_primary(analysis["folders"], analysis["toc_folders"], rec.get("slug", ""), rec["name"])
                toc = read_toc(addons_dir / primary) if primary else {}
                with self.store.lock:
                    rec.update({
                        "folders": analysis["folders"], "primary": primary,
                        "installed_file_id": rec.get("latest_file_id") if matches_latest else None,
                        "installed_version": (rec.get("latest_version") if matches_latest else None)
                        or toc.get("version") or filename,
                        "installed_file_name": filename, "installed_file_date": rec.get("latest_date") if matches_latest else None,
                        "installed_sha1": digest, "installed_at": now_iso(), "manual": True,
                    })
                    self.store.save_state()
                aw = rec.get("awaiting") or {}
                if aw.get("sha1") and aw["sha1"].lower() == digest:
                    rec.update(installed_file_id=aw["file_id"], installed_version=aw.get("version"),
                               installed_file_date=aw.get("date"))
                    matches_latest = True
                rec.pop("awaiting", None)
                self.store.save_state()
                note = "" if matches_latest else " (couldn't match it to the newest CurseForge file — check the version)"
                self.log(f"Installed {rec['name']} from {filename}{note}.", "ok" if matches_latest else "warn")
            else:
                owners = self._owners()
                analysis = analyze_zip(zp)
                clash = [f for f in analysis["folders"] if f in owners]
                if clash:
                    raise UserError(f"{', '.join(clash)} belongs to {owners[clash[0]][0]}. Use "
                                    f"“Install zip…” on that addon's row instead.")
                install_zip_into(addons_dir, zp, [], set(owners))
                self.log(f"Extracted {filename} → {', '.join(analysis['folders'])} (not tracked for updates).", "ok")
        finally:
            rmtree(tmp_dir)
        if self.store.config.get("delete_after_install", True):
            self.cleanup_downloads(data, digest, filename)

    def cleanup_downloads(self, data: bytes, digest: str, filename: str):
        """After an install, delete that zip from Downloads, plus any folder it was unpacked
        into there. Only exact copies are touched: the zip must have the same SHA-1 and a
        folder must contain exactly the zip's files with identical contents."""
        d = self.downloads_dir()
        if not d.is_dir():
            return
        removed: list[str] = []
        names = {Path(filename).stem.lower()}
        try:
            entries = list(d.iterdir())
        except OSError:
            return
        for p in entries:
            try:
                if p.is_file() and p.suffix.lower() == ".zip" and p.stat().st_size == len(data) \
                        and sha1_of_file(p) == digest:
                    names.add(p.stem.lower())
                    p.unlink()
                    removed.append(p.name)
            except OSError:
                pass
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                expected = {i.filename.replace("\\", "/"): hashlib.sha1(zf.read(i)).hexdigest()
                            for i in zf.infolist() if not i.is_dir()}
        except Exception:
            expected = {}
        tops = {k.split("/")[0].lower() for k in expected}
        for p in entries:
            try:
                if not p.is_dir() or p.name.lower() not in names | tops:
                    continue
                # "Extract all" → Downloads/<zip name>/<zip contents>;  "Extract here" → Downloads/<top folder>/...
                want = expected if p.name.lower() in names else {
                    k.split("/", 1)[1]: v for k, v in expected.items()
                    if "/" in k and k.split("/")[0].lower() == p.name.lower()}
                if want and _dir_matches(p, want):
                    rmtree(p)
                    removed.append(p.name + "\\")
            except OSError:
                pass
        if removed:
            self.log(f"Cleaned up Downloads: removed {', '.join(removed)}.")

    # -- views -------------------------------------------------------------- #
    def _folders_present(self, rec: dict) -> bool:
        d = self.store.config.get("addons_dir")
        folders = rec.get("folders") or []
        return bool(d and folders) and all((Path(d) / f).is_dir() for f in folders)

    def status_of(self, rec: dict) -> str:
        if rec.get("awaiting"):
            return "awaiting"
        if not rec.get("folders"):
            return "manual" if rec.get("distribution_disabled") else "not_installed"
        if not self._folders_present(rec):
            return "manual" if rec.get("distribution_disabled") and rec.get("source") not in ("github", "wago") else "missing"
        if rec.get("pinned"):
            return "pinned"
        src = self.update_source(rec)
        if src == "curseforge" and rec.get("distribution_disabled"):
            return "update_manual"
        if src:
            return "update"
        return "ok" if rec.get("latest_checked_at") else "unchecked"

    def follow_game_interface(self):
        cfg = self.store.config
        found = game_interface(cfg.get("addons_dir"))
        if found and cfg.get("interface_auto", True) and str(cfg.get("interface")) != found:
            old = cfg.get("interface")
            self.store.update_config(interface=found)
            self.log(f"WoW Forever now reports interface {found} (was {old}) — addons are checked against that.")
        return found

    def snapshot(self, since: int = 0) -> dict:
        found = self.follow_game_interface()
        cfg = self.store.config
        key = cfg.get("api_key") or ""
        d = cfg.get("addons_dir") or ""
        addons_dir = Path(d) if d else None
        dir_ok = bool(addons_dir and addons_dir.is_dir())
        wanted = str(cfg.get("interface") or "")
        managed = []
        with self.store.lock:
            recs = {k: dict(v) for k, v in self.store.addons.items()}
        try:
            present = {p.name.lower() for p in addons_dir.iterdir() if p.is_dir()} if dir_ok else set()
        except OSError:
            present = set()
        for k, r in recs.items():
            toc = read_toc(addons_dir / r["primary"]) if dir_ok and r.get("primary") else {}
            loc = r.get("local") or {}
            # Another tool (e.g. the Wago app) changed it since: show what's on disk until the next check.
            outside = bool(r.get("external")) or bool(
                not loc.get("pending") and loc.get("toc") and toc.get("version") and toc["version"] != loc["toc"])
            if outside and not r.get("external"):
                r["installed_version"] = toc["version"]
            missing = []
            if dir_ok:
                for folder in r.get("folders") or []:
                    t = toc if folder == r.get("primary") else read_toc(addons_dir / folder)
                    missing += [d for d in t.get("deps") or [] if d.lower() not in present and d not in missing]
            compat = compat_of(toc.get("interfaces") or [], wanted) if toc.get("loads", True) else "mismatch"
            managed.append({
                "key": k, "name": r.get("name") or toc.get("title") or k,
                "url": r.get("url") or f"https://www.curseforge.com/projects/{k}", "logo": r.get("logo"),
                "authors": r.get("authors") or [], "summary": r.get("summary"),
                "installed_version": r.get("installed_version") or toc.get("version"),
                "installed_file_id": r.get("installed_file_id"),
                "installed_date": r.get("installed_file_date"), "installed_at": r.get("installed_at"),
                "installed_release_type": RELEASE_TYPES.get(r.get("installed_release_type") or 1),
                "latest_version": (r.get("wago_latest") or {}).get("label") if is_wago_key(k) else r.get("latest_version"),
                "latest_file_id": r.get("latest_file_id"),
                "latest_date": (r.get("wago_latest") or {}).get("created_at") if is_wago_key(k) else r.get("latest_date"),
                "checked_at": r.get("latest_checked_at"), "wago_only": is_wago_key(k),
                "latest_url": f"{r.get('url')}/files/{r['latest_file_id']}" if r.get("latest_file_id") else r.get("url"),
                "source": r.get("source") or "curseforge", "github": r.get("github"),
                "update_from": self.update_source(r) if self.status_of(r) in ("update", "update_manual") else None,
                "wago_url": (r.get("wago") or {}).get("url"),
                "wago_latest": (r.get("wago_latest") or {}).get("label") if r.get("wago") else None,
                "github_url": ((r.get("gh_latest") or {}).get("url")
                               or (f"https://github.com/{r['github']}" if r.get("github") else None)),
                "download_page": (r.get("awaiting") or {}).get("page")
                or (f"{r.get('url')}/download/{r['latest_file_id']}" if r.get("latest_file_id") else r.get("url")),
                "folders": r.get("folders") or [], "pinned": bool(r.get("pinned")), "manual": bool(r.get("manual")),
                "library": bool(r.get("library")),
                "external": outside,
                "distribution_disabled": bool(r.get("distribution_disabled")),
                "status": self.status_of(r),
                "toc_interfaces": toc.get("interfaces") or [],
                "compat": compat, "toc_file": toc.get("toc") or "", "missing_deps": missing,
                "previous_version": ((r.get("previous") or [{}])[0] or {}).get("installed_version")
                if r.get("previous") else None,
            })
        managed.sort(key=lambda a: (a["name"] or "").lower())

        unmanaged, hidden = [], []
        if dir_ok:
            owned = {f for r in recs.values() for f in (r.get("folders") or [])}
            groups: dict[str, dict] = {}
            try:
                dirs = sorted(p for p in addons_dir.iterdir() if p.is_dir())
            except OSError:
                dirs = []
            recognized = {}
            for mid, info in (self.store.state.get("recognized") or {}).items():
                for folder in info.get("folders") or []:
                    recognized[folder] = info
            for p in dirs:
                if p.name in owned or p.name.startswith(".") or p.name.startswith("Blizzard_"):
                    continue
                toc = read_toc(p)
                info = recognized.get(p.name)
                if info:
                    gkey = f"cf:{info['mod_id']}"
                    if gkey not in groups:
                        groups[gkey] = {"key": gkey, "title": info.get("name") or toc.get("title") or p.name,
                                        "version": info.get("version") or "", "project_id": info["mod_id"],
                                        "recognized": True, "forever": info.get("forever"),
                                        "interfaces": toc.get("interfaces") or [], "folders": [], "has_toc": bool(toc),
                                        "toc_file": toc.get("toc") or "", "loads": toc.get("loads", True)}
                    groups[gkey]["folders"].append(p.name)
                    continue
                gkey = f"pid:{toc['project_id']}" if toc.get("project_id") else f"dir:{p.name}"
                g = groups.get(gkey)
                if not g:
                    g = groups[gkey] = {"key": gkey, "title": toc.get("title") or p.name,
                                        "version": toc.get("version") or "", "project_id": toc.get("project_id"),
                                        "interfaces": toc.get("interfaces") or [], "folders": [],
                                        "has_toc": bool(toc), "toc_file": toc.get("toc") or "",
                                        "loads": toc.get("loads", True)}
                g["folders"].append(p.name)
                for dep in toc.get("deps") or []:
                    if dep.lower() not in present and dep not in g.setdefault("missing_deps", []):
                        g["missing_deps"].append(dep)
                if len(p.name) < len(g["folders"][0]) and toc:
                    g["title"] = toc.get("title") or g["title"]
            ignored_set = set(self.store.config.get("ignored_folders") or [])
            for g in groups.values():
                g["compat"] = compat_of(g["interfaces"], wanted) if g["loads"] else "mismatch"
                (hidden if all(f in ignored_set for f in g["folders"]) else unmanaged).append(g)
            unmanaged.sort(key=lambda g: g["title"].lower())
            hidden.sort(key=lambda g: g["title"].lower())

        return {
            "app": {"name": APP_NAME, "version": APP_VERSION},
            "config": {
                "has_key": bool(key), "key_hint": key[-4:] if len(key) >= 8 else "",
                "addons_dir": d, "addons_dir_ok": dir_ok,
                "addons_dir_is_forever": "forever" in d.lower(),
                "version_type_id": cfg.get("version_type_id"), "version_type_name": cfg.get("version_type_name"),
                "channel": cfg.get("channel"), "interface": wanted,
                "interface_detected": found, "interface_auto": bool(cfg.get("interface_auto", True)),
                "auto_dependencies": cfg.get("auto_dependencies", True),
                "auto_manual": cfg.get("auto_manual", True),
                "delete_after_install": cfg.get("delete_after_install", True),
                "use_github": cfg.get("use_github", True),
                "backup_wtf": cfg.get("backup_wtf", True),
                "backup_keep": cfg.get("backup_keep", 10),
                "auto_check": cfg.get("auto_check", True),
                "has_wago_key": bool((cfg.get("wago_key") or "").strip()),
                "wago_key_hint": (cfg.get("wago_key") or "")[-4:] if len(cfg.get("wago_key") or "") >= 8 else "",
                "use_wago": cfg.get("use_wago", True),
                "app_window": cfg.get("app_window", True),
                "hosted": self.hosted,
                "backup_dir": cfg.get("backup_dir") or "", "backup_dir_default": str(self.store.dir / "backups"),
                "update_repo": self.update_repo(),
                "addon_profiles": cfg.get("addon_profiles") or {},
                "tray": cfg.get("tray", True),
                "bg_check_hours": cfg.get("bg_check_hours", 3),
                "bg_auto_update": cfg.get("bg_auto_update", True),
                "self_update_dismissed": cfg.get("self_update_dismissed") or "",
                "autostart": autostart_enabled(),
                "wago_game_version": cfg.get("wago_game_version") or "forever",
                "downloads_dir": cfg.get("downloads_dir") or "",
                "downloads_default": str(self.downloads_dir()),
            },
            "managed": managed,
            "unmanaged": unmanaged,
            "self_update": {"version": self.self_update["version"], "notes": self.self_update["notes"],
                            "page": self.self_update["page"]} if self.self_update else None,
            "last_batch": self._batch_view(recs),
            "hidden_folders": hidden,
            "game_running": game_running(),
            "backups": self.list_backups(),
            "wtf_found": bool(self.wtf_dir() and self.wtf_dir().is_dir()),
            "diag": self.diag,
            "job": dict(self.job),
            "logs": [e for e in self.logs if e["seq"] > since],
            "log_seq": self._seq,
        }


# --------------------------------------------------------------------------- #
# Web UI server (localhost only)
# --------------------------------------------------------------------------- #
class DownloadWatcher:
    """Installs addons the user downloaded from CurseForge's website.

    For addons whose authors only allow downloads on CurseForge's own site, the
    manager opens the download page; this watches the Downloads folder and
    installs the zip once its SHA-1 matches the file CurseForge lists.
    """

    def __init__(self, manager: Manager):
        self.m = manager
        self.sizes: dict[str, tuple] = {}
        self.checked: dict[str, tuple] = {}
        self._last_change = 0.0

    def run(self):
        while True:
            # Look every 2 s while a download is awaited, otherwise every 20 s (woken early by Install).
            fast = any(r.get("awaiting") for r in list(self.m.store.addons.values()))
            if self.sizes and not fast:
                fast = time.time() - self._last_change < 10
            self.m.watch_wake.wait(2 if fast else 20)
            self.m.watch_wake.clear()
            try:
                self.scan()
            except Exception:
                self.m.log(traceback.format_exc(), "debug")

    def wanted(self) -> dict[str, tuple[str, int | None]]:
        out = {}
        now = time.time()
        with self.m.store.lock:
            for k, r in self.m.store.addons.items():
                aw = r.get("awaiting")
                if aw and now - aw.get("since", now) > AWAIT_SECONDS:
                    r.pop("awaiting", None)
                    self.m.store.save_state()
                    self.m.log(f"Stopped waiting for the {r['name']} download (30 min).", "warn")
                    aw = None
                if aw and aw.get("sha1"):
                    out[aw["sha1"].lower()] = (k, aw.get("length"))
                elif (r.get("distribution_disabled") and r.get("source") not in ("github", "wago") and r.get("latest_sha1")
                      and (r.get("installed_sha1") or "").lower() != r["latest_sha1"].lower()):
                    out.setdefault(r["latest_sha1"].lower(), (k, None))
        return out

    def scan(self):
        if not self.m.store.config.get("auto_manual", True):
            return
        wanted = self.wanted()
        if set(wanted) != getattr(self, "_last", None):
            self._last = set(wanted)
            self.checked.clear()   # something new to look for: re-check zips already in Downloads
        if not wanted:
            self.sizes.clear()
            return
        d = self.m.downloads_dir()
        if not d.is_dir():
            return
        now = time.time()
        lengths = {ln for _, ln in wanted.values()}
        try:
            entries = [e for e in os.scandir(d) if e.name.lower().endswith(".zip")]
        except OSError:
            return
        for e in entries:
            p = Path(e.path)
            try:
                if not e.is_file():
                    continue
                st = e.stat()
            except OSError:
                continue
            if now - st.st_mtime > 6 * 3600:
                continue
            sig = (st.st_size, st.st_mtime)
            key = str(p)
            if self.sizes.get(key) != sig:   # still being written, or new: look again next round
                self.sizes[key] = sig
                self._last_change = now
                continue
            if self.checked.get(key) == sig:
                continue
            if None not in lengths and st.st_size not in lengths:
                self.checked[key] = sig
                continue
            digest = sha1_of_file(p)
            hit = wanted.get(digest)
            if not hit:
                self.checked[key] = sig
                continue
            try:
                name = self.m.store.addons[hit[0]]["name"]
                self.m.run_job(f"Installing {name} from Downloads", self.m.op_install_downloaded, hit[0], p, digest)
                self.checked[key] = sig
            except BusyError:
                pass  # another task is running; try again on the next scan
            return


class App:
    def __init__(self, manager: Manager):
        self.m = manager
        self.port = None
        self.server: ThreadingHTTPServer | None = None
        self.bye_at = 0.0

    def allowed_hosts(self):
        return {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}

    def handle_post(self, path: str, body: dict, raw: bytes, headers) -> dict:
        m = self.m
        if path == "/api/settings":
            changes = {}
            if body.get("api_key"):
                changes["api_key"] = clean_key(body["api_key"])
            if body.get("clear_api_key"):
                changes["api_key"] = ""
            if body.get("wago_key"):
                changes["wago_key"] = clean_key(body["wago_key"])
            if body.get("clear_wago_key"):
                changes["wago_key"] = ""
            if body.get("wago_game_version"):
                changes["wago_game_version"] = re.sub(r"[^a-z0-9_-]", "", str(body["wago_game_version"]).lower()) or "forever"
            if "addons_dir" in body and body["addons_dir"].strip():
                p = normalize_addons_dir(body["addons_dir"])
                if str(p) != m.store.config.get("addons_dir") and m.job["running"]:
                    raise BusyError("Wait for the current task to finish before changing the folder.")
                if not p.is_dir():
                    p.mkdir(parents=True, exist_ok=True)
                changes["addons_dir"] = str(p)
            if "version_type_id" in body:
                v = body["version_type_id"]
                changes["version_type_id"] = int(v) if v not in (None, "", "auto") else None
                changes["version_type_name"] = body.get("version_type_name", "") if changes["version_type_id"] else ""
            if body.get("channel") in CHANNELS:
                changes["channel"] = body["channel"]
            if "self_update_dismissed" in body:
                changes["self_update_dismissed"] = re.sub(r"[^0-9.]", "", str(body["self_update_dismissed"]))[:20]
            if "interface" in body:
                typed = re.sub(r"[^0-9]", "", str(body["interface"]))
                found = game_interface(changes.get("addons_dir") or m.store.config.get("addons_dir"))
                changes["interface_auto"] = not typed or typed == found
                changes["interface"] = typed or found or DEFAULT_INTERFACE
            if "auto_dependencies" in body:
                changes["auto_dependencies"] = bool(body["auto_dependencies"])
            if "backup_keep" in body:
                changes["backup_keep"] = max(1, min(100, int(body["backup_keep"] or 10)))
            for flag in ("auto_manual", "delete_after_install", "use_github", "backup_wtf", "auto_check", "use_wago",
                         "app_window", "tray", "bg_auto_update"):
                if flag in body:
                    changes[flag] = bool(body[flag])
            if "backup_dir" in body and (body["backup_dir"] or "").strip() != (m.store.config.get("backup_dir") or ""):
                m.set_backup_dir(str(body["backup_dir"] or ""))
            if "update_repo" in body:
                changes["update_repo"] = str(body["update_repo"] or "").strip()
            if "bg_check_hours" in body:
                h = int(body["bg_check_hours"] or 0)
                changes["bg_check_hours"] = h if h in BG_CHECK_CHOICES else 3
            if "autostart" in body and bool(body["autostart"]) != autostart_enabled():
                set_autostart(bool(body["autostart"]))
            if "downloads_dir" in body:
                d = str(body["downloads_dir"] or "").strip().strip('"')
                if d and not Path(d).is_dir():
                    raise UserError(f"Downloads folder not found: {d}")
                changes["downloads_dir"] = os.path.normpath(d) if d else ""
            m.store.update_config(**changes)
            if set(body) - {"self_update_dismissed"}:
                m.log("Settings saved.", "ok")
            return {"ok": True}
        if path == "/api/test_key":
            raw_key = body.get("api_key") or m.store.config.get("api_key") or ""
            key = clean_key(raw_key)
            if not key:
                raise UserError("Paste a key first.")
            problems = []
            if len(key) < 20:
                problems.append(f"it is only {len(key)} characters long — it looks cut off")
            hint = (" Also: " + "; ".join(problems) + ".") if problems else ""
            try:
                types = CurseForge(key).version_types()
            except CurseForgeError as e:
                return {"ok": False, "message": f"{e}{hint} (key length {len(key)}, starts with {key[:4]}…)"}
            forever = [t.get("name") for t in types if "forever" in (t.get("name") or "").lower()]
            return {"ok": True, "message": f"Key works. CurseForge lists {len(types)} WoW versions"
                                           + (f", including {', '.join(forever)}." if forever else
                                              " but none called “Forever” — pick one under game version.")}
        if path == "/api/search":
            return self._search(m, body)
        if path == "/api/categories":
            cats = m.client().categories()
            return {"categories": sorted(({"id": c["id"], "name": c.get("name") or str(c["id"])} for c in cats
                                          if not c.get("isClass") and c.get("name")), key=lambda c: c["name"].lower())}
        if path == "/api/test_wago":
            key = clean_key(body.get("wago_key") or m.store.config.get("wago_key") or "")
            if not key:
                raise UserError("Paste your Wago key first.")
            gv = m.store.config.get("wago_game_version") or "forever"
            try:
                hits = Wago(key, gv).search("bags")
            except UserError as e:
                return {"ok": False, "message": f"{str(e)[:1].upper()}{str(e)[1:]}. (key length {len(key)})"}
            return {"ok": True, "message": f"Wago key works — a test search for WoW Forever ({gv}) addons found "
                                           f"{len(hits)} result{'s' if len(hits) != 1 else ''}."}
        if path == "/api/detect":
            return {"dirs": detect_addon_dirs()}
        if path == "/api/version_types":
            types = m.client().version_types()
            types = [{"id": t["id"], "name": t.get("name") or t.get("slug") or str(t["id"])} for t in types]
            types.sort(key=lambda t: ("forever" not in t["name"].lower(), t["name"].lower()))
            return {"types": types}
        if path == "/api/add":
            m.run_job("Installing addons", m.op_add, body.get("text", ""))
            return {"ok": True}
        if path == "/api/check":
            m.run_job("Checking for updates", m.op_check)
            return {"ok": True}
        if path == "/api/update_all":
            m.run_job("Updating all addons", m.op_check_and_update)
            return {"ok": True}
        if path == "/api/update":
            m.run_job("Updating", m.op_update, [str(body.get("key"))])
            return {"ok": True}
        if path == "/api/fix_deps":
            m.run_job("Installing missing requirements", m.op_fix_deps, str(body.get("key")))
            return {"ok": True}
        if path == "/api/open_url":
            # Links clicked in the desktop app window open in the normal web browser.
            u = str(body.get("url") or "")
            parts = urllib.parse.urlsplit(u)
            if parts.scheme not in ("http", "https") or not parts.netloc or len(u) > 2000:
                raise UserError("That link can't be opened.")
            webbrowser.open(u)
            return {"ok": True}
        if path == "/api/shortcuts":
            create_shortcuts()
            m.log("Added “WoW Forever Addons” to your desktop and Start menu. Right-click it to pin it to the taskbar.", "ok")
            return {"ok": True}
        if path == "/api/backup_now":
            m.run_job("Backing up addon settings", m.op_backup_now)
            return {"ok": True}
        if path == "/api/restore":
            m.run_job("Restoring addon settings", m.op_restore_wtf, str(body.get("name")))
            return {"ok": True}
        if path == "/api/revert":
            m.run_job("Reverting", m.op_revert, str(body.get("key")))
            return {"ok": True}
        if path == "/api/changelog":
            return m.changelog(str(body.get("key")))
        if path == "/api/scan":
            m.run_job("Looking up hand-installed addons", m.op_scan)
            return {"ok": True}
        if path == "/api/adopt":
            m.run_job("Taking over addon", m.op_adopt, int(body.get("mod_id")))
            return {"ok": True}
        if path == "/api/import":
            m.run_job("Importing addon list", m.op_import, raw)
            return {"ok": True}
        if path == "/api/import_text":
            text = str(body.get("text") or "")[:200000]
            if not text.strip():
                raise UserError("Paste a share code first.")
            m.run_job("Importing addon list", m.op_import, text.encode("utf-8"), True)
            return {"ok": True}
        if path == "/api/share_code":
            return {"code": m.share_code(), "count": len(m.store.addons)}
        if path == "/api/add_wago":
            m.run_job("Installing from Wago", m.op_add_wago, str(body.get("id") or ""))
            return {"ok": True}
        if path == "/api/diagnose":
            m.run_job("Running diagnostics", m.op_diagnose)
            return {"ok": True}
        if path == "/api/cancel_wait":
            m.op_cancel_wait(str(body.get("key")))
            return {"ok": True}
        if path == "/api/remove":
            m.run_job("Removing addon", m.op_remove, str(body.get("key")))
            return {"ok": True}
        if path == "/api/pin":
            m.run_job("Pin", m.op_set_pin, str(body.get("key")), bool(body.get("pinned")))
            return {"ok": True}
        if path == "/api/manage":
            m.run_job("Taking over addon", m.op_add, str(int(body.get("project_id"))))
            return {"ok": True}
        if path == "/api/install_zip":
            name = urllib.parse.unquote(headers.get("X-Filename") or "addon.zip")
            key = headers.get("X-Mod-Key") or None
            m.run_job(f"Installing {name}", m.op_install_zip, raw, name, key)
            return {"ok": True}
        if path == "/api/open_folder":
            d = m.store.config.get("addons_dir")
            if d and Path(d).is_dir():
                if os.name == "nt":
                    os.startfile(d)  # type: ignore[attr-defined]
                elif sys.platform == "darwin":
                    os.spawnlp(os.P_NOWAIT, "open", "open", d)
                else:
                    os.spawnlp(os.P_NOWAIT, "xdg-open", "xdg-open", d)
            return {"ok": True}
        if path == "/api/self_update":
            m.run_job("Updating WoW Forever Addons", m.op_self_update)
            return {"ok": True}
        if path == "/api/check_self_update":
            info = m.check_self_update(notify=False)
            return {"update": info is not None, "version": info["version"] if info else APP_VERSION,
                    "current": APP_VERSION}
        if path == "/api/undo_batch":
            m.run_job("Undoing the last update", m.op_undo_batch)
            return {"ok": True}
        if path == "/api/wago_search":
            res, hidden = m.wago_search(str(body.get("q") or ""), bool(body.get("all")))
            return {"results": res, "hidden": hidden, "all": bool(body.get("all"))}
        if path == "/api/details":
            return m.details(int(body.get("id")))
        if path == "/api/file_changelog":
            return {"text": m.file_changelog(int(body.get("mod_id")), int(body.get("file_id")))}
        if path == "/api/install_file":
            m.run_job("Installing a specific version", m.op_install_file, int(body.get("mod_id")), int(body.get("file_id")))
            return {"ok": True}
        if path == "/api/ignore":
            m.set_ignored(list(body.get("folders") or []), bool(body.get("ignore", True)))
            return {"ok": True}
        if path == "/api/characters_save":
            m.run_job("Saving characters' addons", m.op_save_characters, list(body.get("changes") or []))
            return {"ok": True}
        if path == "/api/profile":
            m.save_profile(str(body.get("name") or ""), body.get("states"))
            return {"ok": True}
        if path == "/api/tray_check":
            m.run_job("Checking for updates", m.op_background, None, True)
            return {"ok": True}
        if path == "/api/tray_update":
            m.run_job("Updating all addons", m.op_background, True, True)
            return {"ok": True}
        if path == "/api/quit":
            threading.Thread(target=lambda: (time.sleep(0.3), self.server.shutdown()), daemon=True).start()
            return {"ok": True}
        raise UserError("Unknown action.")

    def _search(self, m: Manager, body: dict) -> dict:
        cf = m.client()
        type_id = m.ensure_type(cf)
        sort = int(body.get("sort") or 2)
        if sort not in (2, 3, 4, 6, 11, 12):
            sort = 2
        index = min(max(0, int(body.get("index") or 0)), 9950)
        query = str(body.get("q") or "").strip()[:100]
        cat = body.get("category") or None
        show_all = bool(body.get("all"))
        # CurseForge's own game-version filter lets through addons that have no WoW Forever file at all,
        # so each result is checked here too. Fetch a few more pages so a filtered page isn't nearly empty.
        results, hidden, idx, total, mods, rounds = [], 0, index, 0, [], 0
        while True:
            mods, pag = cf.search(query, type_id, sort, idx, 50, int(cat) if cat else None)
            total = int(pag.get("totalCount") or 0)
            rounds += 1
            idx += len(mods)
            for mod in mods:
                fv = has_forever_file(mod, type_id)
                if not fv and not show_all:
                    hidden += 1
                    continue
                results.append(self._search_result(mod, fv))
            if show_all or len(results) >= 24 or not mods or idx >= min(total, 10000) or rounds >= 4:
                break
        # CurseForge only pages through the first 10,000 results.
        next_index = idx if mods and idx < min(total, 10000) else None
        return {"results": results, "total": total, "next": next_index, "hidden": hidden, "all": show_all}

    @staticmethod
    def _search_result(mod: dict, forever: bool) -> dict:
        return {
            "id": mod["id"], "name": mod.get("name") or str(mod["id"]),
            "summary": mod.get("summary") or "",
            "authors": [a.get("name") for a in (mod.get("authors") or []) if a.get("name")][:2],
            "logo": (mod.get("logo") or {}).get("thumbnailUrl") or "",
            "downloads": int(mod.get("downloadCount") or 0),
            "updated": mod.get("dateModified") or mod.get("dateReleased"),
            "url": Manager._mod_url(mod),
            "categories": [c.get("name") for c in (mod.get("categories") or []) if c.get("name")][:2],
            "manual_only": mod.get("allowModDistribution") is False,
            "github": github_repo_of(mod),
            "forever": forever,
        }

    def make_handler(self):
        app = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "WFAM"
            protocol_version = "HTTP/1.1"
            disable_nagle_algorithm = True

            def log_message(self, *a):
                pass

            def _send(self, code, payload, ctype="application/json; charset=utf-8"):
                data = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                self.end_headers()
                self.wfile.write(data)

            def _host_ok(self):
                return self.headers.get("Host", "") in app.allowed_hosts()

            def do_GET(self):
                if not self._host_ok():
                    return self._send(403, {"error": "forbidden"})
                app.m.last_contact = time.time()
                url = urllib.parse.urlsplit(self.path)
                if url.path == "/":
                    return self._send(200, PAGE_HTML.encode("utf-8"), "text/html; charset=utf-8")
                if url.path == "/api/state":
                    app.m.last_ui = time.time()   # the page itself is showing
                    q = urllib.parse.parse_qs(url.query)
                    since = int((q.get("since") or ["0"])[0] or 0)
                    snap = app.m.snapshot(since)
                    core = {k: v for k, v in snap.items() if k not in ("logs", "log_seq")}
                    etag = hashlib.sha1(json.dumps(core, sort_keys=True, default=str).encode()).hexdigest()[:16]
                    if (q.get("etag") or [""])[0] == etag and not snap["logs"]:
                        return self._send(200, {"same": True, "etag": etag})   # nothing changed: no redraw needed
                    snap["etag"] = etag
                    return self._send(200, snap)
                if url.path in ("/favicon.png", "/favicon.ico", "/icon.png"):
                    if not hasattr(app, "_icon"):
                        app._icon = make_icon_png(64)
                    self.send_response(200)
                    self.send_header("Content-Type", "image/png")
                    self.send_header("Content-Length", str(len(app._icon)))
                    self.send_header("Cache-Control", "max-age=86400")
                    self.end_headers()
                    self.wfile.write(app._icon)
                    return
                if url.path == "/api/characters":
                    return self._send(200, app.m.characters())
                if url.path == "/api/export":
                    data = json.dumps(app.m.export_list(), indent=2).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Disposition",
                                     f'attachment; filename="wow-forever-addons-{time.strftime("%Y-%m-%d")}.json"')
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                if url.path == "/api/notices":
                    q = urllib.parse.parse_qs(url.query)
                    after = int((q.get("after") or ["0"])[0] or 0)
                    with app.m.store.lock:
                        items = [n for n in app.m.notices if n["id"] > after]
                    ui = getattr(app.m, "last_ui", 0)
                    return self._send(200, {"notices": items, "last": app.m._notice_id,
                                            "ui_age": (time.time() - ui) if ui else -1,
                                            "tray": bool(app.m.store.config.get("tray", True)),
                                            "busy": bool(app.m.job["running"])})
                if url.path == "/api/ping":
                    return self._send(200, {"app": APP_NAME, "version": APP_VERSION})
                return self._send(404, {"error": "not found"})

            def do_POST(self):
                origin = self.headers.get("Origin")
                if urllib.parse.urlsplit(self.path).path == "/api/bye":
                    # The window is closing (sent with navigator.sendBeacon, which can't add our header).
                    if self._host_ok() and (not origin or origin in {f"http://{h}" for h in app.allowed_hosts()}):
                        self.rfile.read(int(self.headers.get("Content-Length") or 0))
                        app.bye_at = time.time()
                        return self._send(200, {"ok": True})
                    return self._send(403, {"error": "forbidden"})
                if (not self._host_ok() or self.headers.get("X-WFAM") != "1"
                        or (origin and origin not in {f"http://{h}" for h in app.allowed_hosts()})):
                    return self._send(403, {"error": "forbidden"})
                app.m.last_contact = time.time()
                length = int(self.headers.get("Content-Length") or 0)
                if length > MAX_DOWNLOAD_BYTES:
                    return self._send(413, {"error": "too large"})
                raw = self.rfile.read(length) if length else b""
                body = {}
                if "json" in (self.headers.get("Content-Type") or ""):
                    try:
                        body = json.loads(raw.decode("utf-8") or "{}")
                    except ValueError:
                        return self._send(400, {"error": "bad json"})
                try:
                    return self._send(200, app.handle_post(urllib.parse.urlsplit(self.path).path, body, raw,
                                                           self.headers))
                except BusyError as e:
                    return self._send(409, {"error": str(e)})
                except UserError as e:
                    return self._send(400, {"error": str(e)})
                except Exception as e:
                    app.m.log(traceback.format_exc(), "debug")
                    return self._send(500, {"error": f"Unexpected error: {e}"})

        return Handler

    def serve(self, port: int, open_browser: bool = True, idle_exit: bool = True, ready_file: str | None = None,
              host_pid: int = 0):
        self.m.hosted = bool(host_pid)
        def ready(url: str):
            if ready_file:   # tell the desktop window where to connect
                tmp = Path(ready_file + ".tmp")
                tmp.write_text(url, encoding="utf-8")
                os.replace(tmp, ready_file)
        Server = type("Server", (ThreadingHTTPServer,), {"allow_reuse_address": os.name != "nt",
                                                          "daemon_threads": True})
        handler = self.make_handler()
        local = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # never route localhost via a proxy
        base = f"http://127.0.0.1:{port}"
        try:
            self.server = Server(("127.0.0.1", port), handler)
        except OSError:
            info = {}
            try:
                with local.open(base + "/api/ping", timeout=2) as r:
                    info = json.loads(r.read().decode() or "{}")
            except Exception:
                pass
            if info.get("app") == APP_NAME and info.get("version") == APP_VERSION:
                # Same version already running: just show it.
                ready(base + "/")
                if open_browser:
                    open_window(base + "/", self.m.store.config.get("app_window", True))
                return
            if info.get("app") == APP_NAME:
                # An older/newer copy is running: ask it to quit, then take over its port.
                print("Closing the other running copy of the manager…", flush=True)
                try:
                    local.open(urllib.request.Request(base + "/api/quit", data=b"{}", method="POST", headers={
                        "X-WFAM": "1", "Content-Type": "application/json"}), timeout=3).close()
                except Exception:
                    pass
            for _ in range(20):
                try:
                    self.server = Server(("127.0.0.1", port), handler)
                    break
                except OSError:
                    time.sleep(0.4)
            else:
                self.server = Server(("127.0.0.1", 0), handler)
        self.port = self.server.server_address[1]
        ready(f"http://127.0.0.1:{self.port}/")
        if self.port == port:   # we're the only copy running: safe to tidy up after earlier crashes
            def tidy():
                try:
                    self.m.cleanup_leftovers()
                except Exception:
                    pass
            threading.Thread(target=tidy, daemon=True).start()
        url = f"http://127.0.0.1:{self.port}/"
        print(f"{APP_NAME} running at {url}  (close with the Quit button)", flush=True)
        if open_browser:
            threading.Timer(0.3, lambda: open_window(url, self.m.store.config.get("app_window", True))).start()
        self.m.interactive = True
        threading.Thread(target=DownloadWatcher(self.m).run, daemon=True).start()
        if os.name == "nt":
            def brand():
                vals = _window_branding_values()
                start = time.time()
                while True:
                    brand_app_windows(vals)
                    time.sleep(1.5 if time.time() - start < 60 else 10)
            threading.Thread(target=brand, daemon=True).start()

        def auto_check():
            time.sleep(1.5)
            st, cfg = self.m.store, self.m.store.config
            if cfg.get("api_key") and cfg.get("addons_dir") and st.addons:
                try:
                    if host_pid:   # desktop program: check, install per settings, and notify
                        self.m.run_job("Checking for updates", self.m.op_background)
                    else:
                        self.m.run_job("Checking for updates", self.m.op_check)
                except BusyError:
                    pass
        threading.Thread(target=auto_check, daemon=True).start()
        if host_pid:
            def self_update_loop():
                time.sleep(min(20.0, SELF_UPDATE_EVERY))
                while True:
                    try:   # never installs by itself: it only tells you, and you choose when
                        self.m.check_self_update(notify=True)
                    except Exception:
                        pass
                    time.sleep(SELF_UPDATE_EVERY)
            threading.Thread(target=self_update_loop, daemon=True).start()

            def scheduler():
                # Background checks while the desktop program runs (also when it sits in the tray).
                hour = float(os.environ.get("WFAM_TEST_HOUR_SECONDS") or 3600)   # tests shorten the clock
                while True:
                    time.sleep(min(30, hour / 120))
                    cfg = self.m.store.config
                    hours = cfg.get("bg_check_hours", 3)
                    if not hours or not cfg.get("api_key") or not cfg.get("addons_dir") or not self.m.store.addons:
                        continue
                    if time.time() - self.m.last_check_at < hours * hour:
                        continue
                    try:
                        self.m.run_job("Automatic update check", self.m.op_background)
                    except BusyError:
                        pass
            threading.Thread(target=scheduler, daemon=True).start()
        if idle_exit or host_pid:
            def watchdog():
                while True:
                    time.sleep(1)
                    if self.m.job["running"]:
                        continue
                    now = time.time()
                    # Window closed: quit a few seconds later unless it reconnects (a reload does).
                    closed = self.bye_at and now - self.bye_at > 6 and self.m.last_contact <= self.bye_at + 0.05
                    if host_pid:   # the desktop program decides; quit if it is gone (e.g. crashed)
                        if closed or not pid_alive(host_pid):
                            self.server.shutdown()
                            return
                        continue
                    if closed or now - self.m.last_contact > IDLE_EXIT_SECONDS:
                        self.server.shutdown()
                        return
            threading.Thread(target=watchdog, daemon=True).start()
        try:
            self.server.serve_forever(poll_interval=0.5)
        finally:
            self.server.server_close()


# --------------------------------------------------------------------------- #
# Page
# --------------------------------------------------------------------------- #
PAGE_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>WoW Forever Addons</title>
<link rel="icon" type="image/png" href="/favicon.png">
<style>
:root{
  --bg:#0e1014; --side:#0b0d10; --panel:#151820; --panel2:#1b1f28; --hover:#1d222c; --line:#242a34; --line2:#30374a;
  --text:#e8eaef; --muted:#97a0b0; --dim:#677082;
  --gold:#e3b75a; --gold2:#f2cf83; --green:#57c38a; --amber:#e8a43c; --red:#e5605a; --blue:#6aa5f5;
  --r:10px; --sw:228px;
}
*{box-sizing:border-box}
[hidden]{display:none !important}
html,body{margin:0;height:100%;overflow:hidden;background:var(--bg);color:var(--text);
  font:14px/1.45 "Segoe UI Variable Text","Segoe UI",system-ui,-apple-system,Roboto,sans-serif;
  -webkit-user-select:none;user-select:none;-webkit-font-smoothing:antialiased}
input,textarea,.sel,.desc,.cl,.logbox,.dd{-webkit-user-select:text;user-select:text}
img{-webkit-user-drag:none}
a{color:var(--gold2);text-decoration:none;cursor:pointer} a:hover{text-decoration:underline}
button{font:inherit;color:inherit;cursor:pointer}
::-webkit-scrollbar{width:12px;height:12px}
::-webkit-scrollbar-thumb{background:#262c38;border-radius:10px;border:3px solid transparent;background-clip:padding-box}
::-webkit-scrollbar-thumb:hover{background:#343c4c;background-clip:padding-box;border:3px solid transparent}
::-webkit-scrollbar-track{background:transparent}
:focus-visible{outline:2px solid #e3b75a99;outline-offset:1px}
input[type=checkbox]{accent-color:var(--gold);width:15px;height:15px}

/* ---- app frame ---- */
.app{display:flex;height:100vh}
.side{width:var(--sw);flex:none;background:var(--side);border-right:1px solid var(--line);display:flex;flex-direction:column;padding:16px 12px 12px}
.brand{display:flex;align-items:center;gap:11px;padding:4px 8px 18px}
.mark{width:36px;height:36px;border-radius:9px;background:linear-gradient(140deg,#f3d48d,#b9822f);display:grid;place-items:center;color:#1b1406;font-weight:800;font-size:18px;box-shadow:0 0 0 1px #00000040 inset,0 4px 14px #e3b75a22;flex:none}
.bt{font-weight:700;font-size:15px;letter-spacing:.2px;line-height:1.15}
.bs{color:var(--dim);font-size:12px}
nav{display:flex;flex-direction:column;gap:2px}
.tab{display:flex;align-items:center;gap:11px;background:none;border:0;border-radius:8px;padding:9px 10px;color:var(--muted);font-weight:600;text-align:left;position:relative;transition:background .12s,color .12s}
.tab svg{width:18px;height:18px;flex:none;stroke:currentColor;fill:none;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round;opacity:.9}
.tab:hover{background:#151922;color:var(--text)}
.tab.on{background:#1a1f29;color:var(--text)}
.tab.on::before{content:"";position:absolute;left:-12px;top:8px;bottom:8px;width:3px;border-radius:0 3px 3px 0;background:var(--gold)}
.tab .lbl{flex:1}
.tab .c{background:var(--panel2);border:1px solid var(--line2);border-radius:999px;padding:0 7px;font-size:11.5px;color:var(--muted);font-weight:600}
.tab .c.hot{background:#e8a43c22;border-color:#e8a43c55;color:var(--amber)}
.side .grow{flex:1}
.selfupd{margin:0 4px 8px;background:linear-gradient(140deg,#e3b75a26,#e3b75a10);border:1px solid #e3b75a66;border-radius:10px;padding:10px 12px;font-size:12.5px;color:var(--gold2)}
.selfupd b{display:block;color:var(--text);font-size:13px;margin-bottom:6px}
.srcsw{display:inline-flex;border:1px solid var(--line2);border-radius:9px;padding:3px;gap:3px;background:var(--panel)}
.srcsw button{background:none;border:0;border-radius:6px;padding:6px 12px;color:var(--muted);font-weight:600}
.srcsw button.on{background:#e3b75a22;color:var(--gold2)}
.bl{display:flex;gap:10px;align-items:center;padding:9px 0;border-bottom:1px solid var(--line)}
.bl:last-child{border-bottom:0}
.bl .n{flex:1;min-width:0}
.bl .v{color:var(--dim);font-size:12.5px;font-variant-numeric:tabular-nums}
.sfoot{border-top:1px solid var(--line);margin-top:10px;padding:12px 8px 0;display:flex;flex-direction:column;gap:6px}
.path{color:var(--dim);font-size:11.5px;line-height:1.35;word-break:break-all;cursor:default}
.path b{color:var(--muted);font-weight:600}
.sfoot .row2{display:flex;align-items:center;gap:8px}
.ver{color:var(--dim);font-size:11.5px;flex:1}
main{flex:1;min-width:0;display:flex;flex-direction:column}
.top{display:flex;align-items:center;gap:10px;padding:18px 26px 14px}
.top h1{font-size:21px;margin:0;font-weight:700;letter-spacing:.1px}
.top .sub{color:var(--dim);font-size:12.5px;margin-left:4px}
.top .sp{flex:1}
.scroll{flex:1;overflow:auto;padding:4px 26px 28px}
.view{animation:fade .16s ease-out}
@keyframes fade{from{opacity:0;transform:translateY(3px)}to{opacity:1;transform:none}}

/* ---- controls ---- */
.btn{display:inline-flex;align-items:center;gap:7px;background:var(--panel2);border:1px solid var(--line2);border-radius:8px;padding:8px 14px;font-weight:600;white-space:nowrap;transition:background .12s,border-color .12s,filter .12s;line-height:1.2}
.btn:hover{background:#232936;border-color:#3d4556;text-decoration:none}
.btn:disabled{opacity:.45;cursor:default}
.btn.primary{background:var(--gold);border-color:var(--gold);color:#1d1606}
.btn.primary:hover{background:var(--gold2);border-color:var(--gold2)}
.btn.small{padding:5px 11px;font-size:12.5px;border-radius:7px}
.btn.ghost{background:transparent;border-color:transparent}
.btn.ghost:hover{background:#1c212b;border-color:var(--line2)}
.btn.icon{padding:6px 9px;font-size:15px;line-height:1}
.btn.danger:hover{border-color:var(--red);color:var(--red)}
.btn svg{width:15px;height:15px;stroke:currentColor;fill:none;stroke-width:2;stroke-linecap:round;stroke-linejoin:round}
.search{background:var(--panel);border:1px solid var(--line2);border-radius:8px;padding:8px 12px 8px 34px;color:var(--text);font:inherit;min-width:220px;
  background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='16' height='16' viewBox='0 0 24 24' fill='none' stroke='%23677082' stroke-width='2' stroke-linecap='round'%3E%3Ccircle cx='11' cy='11' r='7'/%3E%3Cpath d='m20 20-3.5-3.5'/%3E%3C/svg%3E");background-repeat:no-repeat;background-position:11px 50%}
.search:focus,textarea:focus,.field input:focus,.field select:focus,select:focus{outline:2px solid #e3b75a44;border-color:var(--gold)}
select{background:var(--panel);border:1px solid var(--line2);border-radius:8px;padding:8px 10px;color:var(--text);font:inherit}
.bar{display:flex;align-items:center;gap:10px;margin:0 0 14px;flex-wrap:wrap}
.bar .sp{flex:1}
.fchips{display:flex;gap:6px;flex-wrap:wrap}
.stat{display:inline-flex;align-items:center;gap:8px;background:transparent;border:1px solid var(--line2);border-radius:999px;padding:6px 13px;color:var(--muted);font-weight:600;font-size:13px;transition:all .12s}
.stat b{font-variant-numeric:tabular-nums;color:var(--text);font-weight:700}
.stat:hover{border-color:#3d4556;color:var(--text)}
.stat.on{background:#e3b75a18;border-color:#e3b75a80;color:var(--gold2)}
.stat.upd b{color:var(--amber)} .stat.bad b{color:var(--red)}
.stat.zero{opacity:.55}
.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;overflow:hidden}
.empty{padding:44px 24px;text-align:center;color:var(--muted)}
.empty b{color:var(--text);font-size:15px}
.empty .acts2{display:flex;gap:8px;justify-content:center;margin-top:14px}
.banner{background:#e8a43c12;border:1px solid #e8a43c45;color:#f3c983;border-radius:10px;padding:9px 14px;margin:0 26px 12px;font-size:13px}
.banner.in{margin:0 0 14px}
h2{font-size:13px;margin:26px 0 10px;color:var(--muted);font-weight:650;display:flex;align-items:center;gap:8px;text-transform:uppercase;letter-spacing:.5px}
h2 .c{background:var(--panel2);border:1px solid var(--line2);border-radius:999px;padding:0 8px;font-size:11.5px;letter-spacing:0}
.muted{color:var(--muted)} .dim{color:var(--dim)}
.linkbtn{background:none;border:0;padding:0;color:var(--gold2);font-size:12px;cursor:pointer}
.linkbtn:hover{text-decoration:underline}

/* ---- addon list ---- */
table{width:100%;border-collapse:collapse}
th{font-size:11px;text-transform:uppercase;letter-spacing:.6px;color:var(--dim);font-weight:650;text-align:left;padding:10px 14px;border-bottom:1px solid var(--line);background:#13161d}
td{padding:10px 14px;border-bottom:1px solid var(--line);vertical-align:middle}
tbody tr:last-child td{border-bottom:0}
tr.arow{cursor:pointer}
tr.arow:hover td{background:var(--hover)}
tr.grp td{background:#12151b;cursor:pointer;color:var(--muted);font-size:12.5px;font-weight:600;padding:9px 14px}
tr.grp:hover td{color:var(--text)}
.nm{display:flex;gap:12px;align-items:center;min-width:230px}
.logo{width:38px;height:38px;border-radius:9px;background:var(--panel2);flex:none;object-fit:cover;display:grid;place-items:center;color:var(--gold);font-weight:700;border:1px solid var(--line)}
.nm .t{font-weight:650;color:var(--text)}
.nm .s{color:var(--dim);font-size:12px;max-width:min(60vw,820px);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#managedCard th:nth-child(2),#managedCard th:nth-child(3){width:19%}
#managedCard th:nth-child(4){width:190px}
#managedCard th:nth-child(5){width:clamp(130px,20%,330px)}
#managedCard table{table-layout:fixed}
#managedCard .vv,#managedCard .nm .s{max-width:100%}
#managedCard .nm{min-width:0}
.vv{font-variant-numeric:tabular-nums;max-width:320px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.vv .d{color:var(--dim);font-size:12px}
.vv .arrow{color:var(--amber);font-weight:600}
.pill{display:inline-flex;align-items:center;gap:6px;font-size:12px;font-weight:600;padding:3px 10px;border-radius:999px;white-space:nowrap;border:1px solid transparent}
.pill::before{content:"";width:7px;height:7px;border-radius:50%;background:currentColor}
.p-ok{color:var(--green);background:#57c38a12;border-color:#57c38a30}
.p-update,.p-update_manual{color:var(--amber);background:#e8a43c14;border-color:#e8a43c40}
.p-manual,.p-missing{color:var(--red);background:#e5605a14;border-color:#e5605a40}
.p-pinned{color:var(--blue);background:#6aa5f514;border-color:#6aa5f540}
.p-unchecked,.p-not_installed{color:var(--muted);background:#ffffff08;border-color:var(--line2)}
.compat{font-size:11px;color:var(--amber);margin-top:3px}
.acts{display:flex;gap:6px;justify-content:flex-end;align-items:center;flex-wrap:wrap}
.tag{font-size:11.5px;font-weight:600;padding:2px 8px;border-radius:999px;border:1px solid var(--line2);color:var(--muted);white-space:nowrap}
.tag.ok{color:var(--green);border-color:#57c38a40;background:#57c38a12}
.tag.warn{color:var(--amber);border-color:#e8a43c40;background:#e8a43c12}
.tag.gold{color:var(--gold2);border-color:#e3b75a50;background:#e3b75a12}

/* ---- browse ---- */
.bsearch{display:flex;gap:10px;margin-bottom:12px;flex-wrap:wrap}
.bsearch .search{flex:1;min-width:260px;font-size:15px;padding:10px 12px 10px 36px}
.bmeta{color:var(--dim);font-size:12.5px;margin:0 2px 10px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:12px}
.res{display:flex;gap:14px;padding:14px;border:1px solid var(--line);border-radius:12px;background:var(--panel);cursor:pointer;transition:border-color .12s,background .12s;min-height:112px}
.res:hover{border-color:#3a4252;background:#171b23}
.res .logo{width:52px;height:52px;border-radius:11px}
.res .body{flex:1;min-width:0;display:flex;flex-direction:column}
.res .t{font-weight:650;font-size:14.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.res .by{color:var(--dim);font-size:12px}
.res .sum{color:var(--muted);font-size:12.5px;margin-top:3px;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;flex:1}
.res .meta{color:var(--dim);font-size:11.5px;margin-top:6px;display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.res .meta .sp{flex:1}
.more{display:flex;justify-content:center;padding:16px}

/* ---- characters / backups / misc ---- */
.mx{font-size:13px}
.mx td,.mx th{text-align:center;padding:7px 6px}
.mx td:first-child,.mx th:first-child{text-align:left;padding-left:14px;position:sticky;left:0;background:var(--panel);z-index:1}
.mx th:first-child{background:#13161d}
.mx th{min-width:92px}
.mx th .cn{display:block;color:var(--text);text-transform:none;letter-spacing:0;font-size:12.5px}
.mx th small{display:block;font-weight:400;text-transform:none;letter-spacing:0;color:var(--dim)}
.mx td.cell{cursor:pointer;font-weight:700}
.mx td.cell:hover{background:var(--hover)}
.mx .on{color:var(--green)} .mx .off{color:var(--red)} .mx .def{color:var(--dim)}
.mx td.chg{box-shadow:inset 0 0 0 2px #e3b75a90;border-radius:6px}
.mxwrap{overflow:auto;max-height:calc(100vh - 230px)}
.mxwrap thead th{position:sticky;top:0;z-index:2}
.mxwrap thead th:first-child{z-index:3}
.bk{display:flex;align-items:center;gap:12px;padding:12px 16px;border-bottom:1px solid var(--line)}
.bk:last-child{border-bottom:0}
.bk .n{flex:1;font-variant-numeric:tabular-nums}
.cl{white-space:pre-wrap;font-size:13px;line-height:1.55;color:var(--text);max-height:55vh;overflow:auto;background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:12px 14px}
.dg{display:flex;gap:10px;padding:7px 0;border-bottom:1px solid var(--line);font-size:13px}
.dg:last-child{border-bottom:0}
.dg .i{width:18px;flex:none;text-align:center}
.dg .ok{color:var(--green)} .dg .warn{color:var(--amber)} .dg .fail{color:var(--red)} .dg .info{color:var(--blue)}
.dg .tt{font-weight:600;min-width:210px}
.dg .dd{color:var(--muted);word-break:break-word}

/* ---- status bar ---- */
.drawer{border-top:1px solid var(--line);background:#0c0e12}
.dbar{padding:8px 26px;display:flex;gap:12px;align-items:center;cursor:pointer}
.dbar .last{flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:var(--muted);font-size:12.5px}
.spin{width:14px;height:14px;border:2px solid #e3b75a40;border-top-color:var(--gold);border-radius:50%;animation:sp .8s linear infinite;flex:none}
@keyframes sp{to{transform:rotate(360deg)}}
.dot{width:9px;height:9px;border-radius:50%;background:var(--dim);flex:none}
.dot.ok{background:var(--green)} .dot.err{background:var(--red)}
.logbox{padding:0 26px 12px;max-height:220px;overflow:auto;font:12px/1.6 "Cascadia Mono",ui-monospace,Consolas,monospace;display:none}
.drawer.open .logbox{display:block}
.lg .tm{color:var(--dim);margin-right:8px}
.lg.error{color:var(--red)} .lg.warn{color:var(--amber)} .lg.ok{color:var(--green)} .lg.head{color:var(--gold2);margin-top:6px} .lg.debug{display:none}

/* ---- popover menu ---- */
.pop{position:fixed;z-index:60;min-width:200px;background:#1a1e27;border:1px solid var(--line2);border-radius:10px;padding:5px;box-shadow:0 14px 40px #000a;animation:fade .1s ease-out}
.pop button,.pop a{display:flex;width:100%;align-items:center;gap:9px;background:none;border:0;border-radius:7px;padding:7px 10px;text-align:left;color:var(--text);font-size:13px;text-decoration:none}
.pop button:hover,.pop a:hover{background:#252b37;text-decoration:none}
.pop button:disabled{opacity:.4}
.pop .danger{color:#ff9a95}
.pop hr{border:0;border-top:1px solid var(--line2);margin:4px 2px}
.pop .hd{color:var(--dim);font-size:11px;text-transform:uppercase;letter-spacing:.5px;padding:6px 10px 3px}

/* ---- dialogs ---- */
.modal{position:fixed;inset:0;background:#05070ab8;display:none;align-items:flex-start;justify-content:center;z-index:50;padding:7vh 16px;overflow:auto;animation:fade .12s ease-out}
.modal.show{display:flex}
.dlg{width:100%;max-width:620px;background:var(--panel);border:1px solid var(--line2);border-radius:14px;padding:22px 24px;box-shadow:0 20px 60px #000c}
.dlg h3{margin:0 0 4px;font-size:18px}
.dlg .sub{color:var(--muted);margin:0 0 18px;font-size:13px}
/* Settings is a page of its own, not a pop-up */
#mSettings{left:var(--sw);background:var(--bg);padding:0;z-index:35;align-items:stretch;justify-content:flex-start;animation:none}
#mSettings .dlg{max-width:none;border:0;border-radius:0;box-shadow:none;background:transparent;padding:22px 26px 40px;min-height:100%}
#mSettings .inner{max-width:980px}
#mSettings .foot{position:sticky;bottom:0;background:linear-gradient(transparent,var(--bg) 30%);padding:22px 0 6px;margin-top:0}
.sect{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:16px 18px 4px;margin-bottom:14px}
.sect h4{margin:0 0 12px;font-size:12px;text-transform:uppercase;letter-spacing:.6px;color:var(--dim)}
.field{margin-bottom:16px}
.field label{display:block;font-weight:600;margin-bottom:6px;font-size:13px}
.field label.ck{font-weight:500;display:flex;gap:9px;align-items:flex-start;margin-top:6px}
.field label.ck input{width:auto;margin:3px 0 0}
.field .help{color:var(--dim);font-size:12px;margin-top:5px}
.field input,.field select{width:100%;background:var(--bg);color:var(--text);border:1px solid var(--line2);border-radius:8px;padding:9px 11px;font:inherit}
.row{display:flex;gap:8px}
.row>*{flex:1}
.row>.btn{flex:none}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}
.chip{background:var(--panel2);border:1px solid var(--line2);border-radius:7px;padding:5px 9px;font-size:12px;cursor:pointer;text-align:left}
.chip:hover{border-color:var(--gold)}
.chip b{color:var(--gold2)}
.foot{display:flex;justify-content:flex-end;gap:10px;margin-top:8px}
.steps{counter-reset:s;margin:0 0 18px;padding:0;list-style:none}
.steps li{counter-increment:s;position:relative;padding:0 0 10px 32px;color:var(--muted);font-size:13px}
.steps li::before{content:counter(s);position:absolute;left:0;top:0;width:22px;height:22px;border-radius:50%;background:var(--panel2);border:1px solid var(--line2);display:grid;place-items:center;font-size:12px;color:var(--gold2)}
.toast{position:fixed;top:16px;left:50%;transform:translateX(-50%);background:#2a1616;border:1px solid var(--red);color:#ffd3d0;padding:9px 16px;border-radius:9px;z-index:80;display:none;max-width:90vw}
textarea{width:100%;display:block;min-height:110px;resize:vertical;background:var(--bg);color:var(--text);border:1px solid var(--line2);border-radius:8px;padding:10px 12px;font:inherit}
#askIn{width:100%;background:var(--bg);color:var(--text);border:1px solid var(--line2);border-radius:8px;padding:9px 11px;font:inherit;margin:-6px 0 16px}

/* ---- detail panel ---- */
.shade{position:fixed;inset:0;background:#05070a99;z-index:45;opacity:0;pointer-events:none;transition:opacity .18s}
.shade.show{opacity:1;pointer-events:auto}
.sheet{position:fixed;top:0;right:0;bottom:0;width:min(620px,94vw);background:var(--panel);border-left:1px solid var(--line2);z-index:46;transform:translateX(102%);transition:transform .22s cubic-bezier(.2,.8,.2,1);display:flex;flex-direction:column;box-shadow:-20px 0 60px #0008}
.sheet.show{transform:none}
.shd{padding:20px 22px 14px;border-bottom:1px solid var(--line);display:flex;gap:14px;align-items:flex-start}
.shd .logo{width:64px;height:64px;border-radius:14px;font-size:24px}
.shd .ttl{font-size:19px;font-weight:700;line-height:1.25}
.shd .by{color:var(--dim);font-size:12.5px;margin-top:2px}
.shd .x{margin-left:auto;flex:none}
.sacts{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px;align-items:center}
.smeta{display:flex;gap:6px;flex-wrap:wrap;margin-top:10px}
.stabs{display:flex;gap:4px;padding:0 18px;border-bottom:1px solid var(--line)}
.stab{background:none;border:0;border-bottom:2px solid transparent;padding:10px 10px;color:var(--muted);font-weight:600;margin-bottom:-1px}
.stab:hover{color:var(--text)} .stab.on{color:var(--text);border-bottom-color:var(--gold)}
.sbody{flex:1;overflow:auto;padding:16px 22px 30px}
.shots{display:flex;gap:8px;overflow-x:auto;padding-bottom:6px;margin-bottom:14px}
.shots img{height:104px;border-radius:8px;border:1px solid var(--line);cursor:zoom-in;flex:none;background:var(--panel2)}
.desc{font-size:13.5px;line-height:1.6;color:#d5d9e1;word-wrap:break-word}
.desc img{max-width:100%;height:auto;border-radius:6px}
.desc h1,.desc h2,.desc h3,.desc h4{color:var(--text);text-transform:none;letter-spacing:0;font-size:15px;margin:18px 0 8px;display:block}
.desc h1{font-size:18px} .desc h2{font-size:16.5px}
.desc a{color:var(--gold2)}
.desc pre,.desc code{background:var(--bg);border-radius:5px;padding:1px 5px;font-size:12.5px}
.desc pre{padding:10px;overflow:auto}
.desc table{width:auto} .desc td{border:1px solid var(--line);padding:4px 8px}
.desc blockquote{border-left:3px solid var(--line2);margin:8px 0;padding:2px 12px;color:var(--muted)}
.fl{border:1px solid var(--line);border-radius:10px;overflow:hidden}
.fr{display:flex;gap:10px;align-items:center;padding:10px 12px;border-bottom:1px solid var(--line)}
.fr:last-child{border-bottom:0}
.fr .n{flex:1;min-width:0}
.fr .n b{font-weight:600;display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.fr .n span{color:var(--dim);font-size:12px}
.fr .cl{margin-top:8px;max-height:220px}
.kv{display:grid;grid-template-columns:150px 1fr;gap:8px 14px;font-size:13px}
.kv .k{color:var(--dim)}
.lightbox{position:fixed;inset:0;background:#000d;z-index:70;display:none;align-items:center;justify-content:center;cursor:zoom-out}
.lightbox.show{display:flex}
.lightbox img{max-width:92vw;max-height:90vh;border-radius:8px}

@media (max-width:1250px){.hide-s{display:none} #managedCard th:nth-child(2){width:24%}}
@media (max-width:900px){:root{--sw:64px} .side .lbl,.side .c,.brand>div:last-child,.sfoot .path,.sfoot .ver{display:none} .tab{justify-content:center} .hide-s{display:none}}
</style>
</head>
<body>
<div class="toast" id="toast"></div>
<div class="app">
  <aside class="side">
    <div class="brand"><div class="mark">F</div><div><div class="bt">WoW Forever</div><div class="bs">Addon Manager</div></div></div>
    <nav>
      <button class="tab on" data-v="installed"><svg viewBox="0 0 24 24"><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/></svg><span class="lbl">My addons</span><span class="c" id="tabN">0</span></button>
      <button class="tab" data-v="browse"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="m15.5 8.5-2 5-5 2 2-5z"/></svg><span class="lbl">Browse</span></button>
      <button class="tab" data-v="chars"><svg viewBox="0 0 24 24"><circle cx="9" cy="8" r="3.5"/><path d="M3 20c.6-3.4 3.1-5.5 6-5.5s5.4 2.1 6 5.5"/><path d="M16 4.6a3.5 3.5 0 0 1 0 6.8M18 14.8c1.7.7 2.8 2.5 3 5.2"/></svg><span class="lbl">Characters</span></button>
      <button class="tab" data-v="backups"><svg viewBox="0 0 24 24"><path d="M3 12a9 9 0 1 0 3-6.7"/><path d="M3 4v5h5"/><path d="M12 7v5l3 2"/></svg><span class="lbl">Backups</span></button>
    </nav>
    <div class="grow"></div>
    <div class="selfupd" id="selfUpd" hidden></div>
    <nav><button class="tab" id="bSettings" data-v="settings"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/></svg><span class="lbl">Settings</span></button></nav>
    <div class="sfoot">
      <div class="path" id="path">—</div>
      <div class="row2"><span class="ver" id="verTxt"></span><button class="btn ghost small" id="bQuit" title="Stop the manager">Quit</button></div>
      <button class="linkbtn" id="bSelfChk" style="margin-top:6px" title="Look for a new version of this program">Check for program update</button>
    </div>
  </aside>

  <main>
    <div class="top">
      <h1 id="vTitle">My addons</h1><span class="sub" id="vSub"></span>
      <span class="sp"></span>
      <button class="btn" id="bCheck"><svg viewBox="0 0 24 24"><path d="M21 12a9 9 0 1 1-2.6-6.4"/><path d="M21 4v5h-5"/></svg>Check for updates</button>
      <button class="btn primary" id="bUpdAll">Update all</button>
    </div>
    <div class="banner" id="gameWarn" hidden>⚠ WoW is running. Changes take effect after <b>/reload</b> or restarting the game, and Windows may refuse to replace files the game has open.</div>
    <div class="scroll" id="scroll">

      <div id="vInstalled" class="view">
        <div class="bar">
          <div class="fchips">
            <button class="stat on" data-f="all">All <b id="sAll">0</b></button>
            <button class="stat upd" data-f="updates">Updates <b id="sUpd">0</b></button>
            <button class="stat bad" data-f="problems">Need attention <b id="sBad">0</b></button>
            <button class="stat" data-f="unmanaged">Other folders <b id="sUn">0</b></button>
          </div>
          <span class="sp"></span>
          <input id="q" class="search" placeholder="Search my addons" autocomplete="off" spellcheck="false">
          <button class="btn primary" id="bAddOpen"><svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg>Add addons</button>
          <button class="btn icon" id="bMoreIns" title="More">⋯</button>
        </div>
        <div class="card" id="managedCard"></div>
        <h2 id="unH">Other folders in AddOns <span class="c" id="unC">0</span><span style="flex:1"></span><button class="btn small" id="bScan" title="Recognise hand-installed addons by their files (CurseForge fingerprints)">Look up on CurseForge</button></h2>
        <div class="card" id="unCard"></div>
        <div id="hidBox" style="margin-top:10px"></div>
      </div>

      <div id="vBrowse" class="view" hidden>
        <div class="bar" id="srcBar" hidden><div class="srcsw"><button class="on" data-src="cf">CurseForge</button><button data-src="wago">Wago</button></div><span class="dim" id="srcHint" style="font-size:12.5px"></span></div>
        <div class="bsearch">
          <input id="bq" class="search" placeholder="Search WoW Forever addons — bags, quests, bosses, auction…" autocomplete="off" spellcheck="false">
          <select id="bcat"><option value="">All categories</option></select>
          <select id="wcat" hidden><option value="">All categories</option></select>
          <select id="wsort" hidden><option value="pop">Most popular</option><option value="dl">Most downloaded</option><option value="new">Recently updated</option><option value="name">Name</option></select>
          <select id="bsort">
            <option value="2">Most popular</option>
            <option value="6">Most downloaded</option>
            <option value="3">Recently updated</option>
            <option value="11">Newest</option>
            <option value="4">Name A–Z</option>
          </select>
        </div>
        <div class="bmeta" id="bmeta"></div>
        <div id="bres"></div>
        <div class="more" id="bmore" hidden><button class="btn" id="bMoreBtn">Show more</button></div>
      </div>

      <div id="vChars" class="view" hidden>
        <div class="banner in" id="chGame" hidden>⚠ WoW is running. The game saves AddOns.txt again when you log out, so change characters at the character select screen or with the game closed.</div>
        <div class="bar">
          <span class="muted" style="font-size:13px">Click a cell to switch an addon on or off for that character. <span class="dim">○ = not set yet (uses the addon's default)</span></span>
          <span class="sp"></span>
          <label class="dim" style="font-size:13px"><input type="checkbox" id="chAll" style="margin-right:6px;vertical-align:-2px">Show libraries &amp; modules</label>
          <button class="btn small" id="chReload">Reload</button>
          <button class="btn small" id="chDiscard" hidden>Discard</button>
          <button class="btn small primary" id="chSave" hidden>Save changes</button>
        </div>
        <div class="card" id="chBox"></div>
        <div id="profBox" style="margin-top:14px"></div>
      </div>

      <div id="vBackups" class="view" hidden>
        <p class="muted" style="margin:0 0 14px;max-width:820px">Every addon keeps its settings (profiles, Questie progress, bars…) in your <b>WTF</b> folder, not in AddOns. A copy is saved automatically before addons are installed, updated or removed (at most once per 10 minutes). Restoring needs WoW to be closed.</p>
        <div class="bar"><span class="sp"></span><button class="btn small" id="bBackupNow">Back up now</button></div>
        <div class="card" id="bkList"></div>
      </div>
    </div>

    <div class="drawer" id="drawer">
      <div class="dbar" id="dbar"><span class="dot" id="dDot"></span><span class="last" id="dLast">Ready.</span><span class="dim" style="font-size:12px" id="dTog">Show activity ▲</span></div>
      <div class="logbox" id="logbox"></div>
    </div>
  </main>
</div>

<!-- detail panel -->
<div class="shade" id="shade"></div>
<div class="sheet" id="detail" aria-hidden="true">
  <div class="shd" id="dHead"></div>
  <div class="stabs" id="dTabs">
    <button class="stab on" data-t="about">Overview</button>
    <button class="stab" data-t="versions">Versions</button>
    <button class="stab" data-t="info">Details</button>
  </div>
  <div class="sbody" id="dBody"></div>
</div>
<div class="lightbox" id="lightbox"><img id="lbImg" alt=""></div>
<div class="pop" id="pop" hidden></div>

<div class="modal" id="mAdd"><div class="dlg">
  <h3>Add addons</h3>
  <p class="sub">Paste CurseForge links or names — one or many. Or find them in <a href="#" id="toBrowse">Browse</a>.</p>
  <textarea id="links" rows="4" placeholder="https://www.curseforge.com/wow/addons/questie&#10;https://www.curseforge.com/wow/addons/details"></textarea>
  <div class="dim" style="font-size:12px;margin-top:8px">Installs the newest WoW Forever file of each. Required libraries are added automatically. A link to a specific file installs that exact version.</div>
  <div class="foot" style="margin-top:16px"><button class="btn" id="addCancel">Cancel</button><button class="btn primary" id="bAdd">Install</button></div>
</div></div>

<div class="modal" id="mBatch"><div class="dlg" style="max-width:600px">
  <h3 id="btT">Updated</h3><p class="sub" id="btS"></p>
  <div id="btL" style="max-height:50vh;overflow:auto"></div>
  <div class="foot" style="margin-top:16px"><button class="btn ghost danger" id="btUndo" style="margin-right:auto" title="Put back the versions you had before (they get pinned)">Undo this update</button><button class="btn primary" id="btClose">Close</button></div>
</div></div>

<div class="modal" id="mShare"><div class="dlg" style="max-width:620px">
  <h3>Share or move your addon list</h3>
  <p class="sub">One line of text with all your addons. Paste it to a friend, or into this program on another PC.</p>
  <textarea id="shCode" readonly rows="3" spellcheck="false" style="width:100%;font-family:ui-monospace,Consolas,monospace;font-size:12px"></textarea>
  <div class="foot" style="margin-top:8px;justify-content:flex-start"><button class="btn primary" id="shCopy">Copy code</button><span class="dim" id="shCount" style="margin-left:10px;align-self:center"></span></div>
  <h3 style="margin-top:22px">Install from a code</h3>
  <p class="sub">Paste a share code (or a list of CurseForge links). Addons you already have are skipped.</p>
  <textarea id="shIn" rows="3" spellcheck="false" placeholder="WFA1.…" style="width:100%;font-family:ui-monospace,Consolas,monospace;font-size:12px"></textarea>
  <div class="foot" style="margin-top:8px"><button class="btn" id="shClose">Close</button><button class="btn primary" id="shGo">Install these addons</button></div>
</div></div>

<div class="modal" id="mAsk"><div class="dlg" style="max-width:460px">
  <h3 id="askT"></h3><p class="sub" id="askB" style="white-space:pre-line"></p>
  <input id="askIn" hidden autocomplete="off" spellcheck="false">
  <div class="foot"><button class="btn" id="askNo">Cancel</button><button class="btn primary" id="askYes">OK</button></div>
</div></div>

<input type="file" id="file" accept=".zip,application/zip" hidden>
<input type="file" id="importFile" accept=".json,application/json" hidden>
<a id="bExport" href="/api/export" download hidden>Export</a>

<div class="modal" id="mLog"><div class="dlg" style="max-width:720px">
  <h3 id="clT">What's new</h3><p class="sub"><a id="clU" target="_blank" rel="noopener">Open on the web ↗</a></p>
  <div class="cl" id="clB">Loading…</div>
  <div class="foot"><button class="btn" id="clClose">Close</button></div>
</div></div>

<div class="modal" id="mDiag"><div class="dlg" style="max-width:820px">
  <h3>Diagnostics</h3><p class="sub">Checks every part against the real CurseForge and GitHub. Nothing is installed; one small addon is downloaded to a temporary folder and deleted. Your API key is never included in the report.</p>
  <div id="dgBody"></div>
  <div class="foot"><button class="btn" id="dgCopy">Copy report</button><button class="btn" id="dgRun">Run again</button><button class="btn primary" id="dgClose">Close</button></div>
</div></div>

<div class="modal" id="mSettings">
  <div class="dlg"><div class="inner">
    <h3 id="sTitle" style="font-size:21px">Settings</h3>
    <p class="sub" id="sSub">Where your addons live, how they're updated, and how the program behaves.</p>
    <ol class="steps" id="setupSteps" hidden>
      <li>Get a free API key: sign in at <a href="https://console.curseforge.com/" target="_blank" rel="noopener">console.curseforge.com</a> → API keys, and paste it below.</li>
      <li>Pick your <b>_forever_\Interface\AddOns</b> folder (Detect usually finds it).</li>
      <li>Save, then add addons from Browse or by pasting links.</li>
    </ol>
    <div class="sect"><h4>Game</h4>
      <div class="field">
        <label for="fDir">WoW Forever AddOns folder</label>
        <div class="row"><input id="fDir" spellcheck="false" autocomplete="off" placeholder="C:\Program Files (x86)\World of Warcraft\_forever_\Interface\AddOns"><button class="btn" id="bDetect">Detect</button></div>
        <div class="chips" id="dirChips"></div>
        <div class="help">You can also paste the World of Warcraft or _forever_ folder — the rest is filled in.</div>
      </div>
      <div class="row">
        <div class="field">
          <label for="fChan">Release channel</label>
          <select id="fChan"><option value="release">Release only (recommended)</option><option value="beta">Release + Beta</option><option value="alpha">Everything (incl. Alpha)</option></select>
        </div>
        <div class="field">
          <label for="fIf">Game interface number</label>
          <input id="fIf" inputmode="numeric" autocomplete="off" placeholder="Automatic">
          <div class="help" id="fIfHelp">Used to flag addons made for another WoW version.</div>
        </div>
      </div>
    </div>
    <div class="sect" id="hostBox" hidden><h4>Program</h4>
      <div class="field">
        <label class="ck"><input type="checkbox" id="fTray">Keep running in the system tray when I close the window</label>
        <label class="ck" style="align-items:center">Check for updates
          <select id="fBgH" style="width:auto;padding:5px 8px"><option value="0">never</option><option value="1">every hour</option><option value="3">every 3 hours</option><option value="6">every 6 hours</option><option value="12">every 12 hours</option><option value="24">once a day</option></select></label>
        <label class="ck"><input type="checkbox" id="fBgUp">Install updates automatically (otherwise just notify me)</label>
        <label class="ck"><input type="checkbox" id="fAuto">Start with Windows (in the tray)</label>
        <div class="help">Background checks run while the program is open or in the tray. Right-click the tray icon to check now or quit.</div>
        <div class="help" id="selfRepo" style="margin-top:10px"></div>
      </div>
    </div>
    <div class="sect"><h4>Updates &amp; downloads</h4>
      <div class="field"><label class="ck"><input type="checkbox" id="fDeps">Install required libraries automatically</label>
        <label class="ck"><input type="checkbox" id="fAutoMan">Addons that only allow downloads on CurseForge's site: open the download page and install the zip when it lands in Downloads</label>
        <label class="ck"><input type="checkbox" id="fGh">…but first try the addon's GitHub releases (no browser needed)</label>
        <label class="ck"><input type="checkbox" id="fDel">Delete the zip from Downloads after installing it</label></div>
      <div class="field">
        <label for="fDl">Downloads folder</label>
        <input id="fDl" spellcheck="false" autocomplete="off">
        <div class="help">Where your browser saves downloads. Leave empty for the Windows default.</div>
      </div>
      <div class="field">
        <label class="ck" style="align-items:center"><input type="checkbox" id="fBk">Back up addon settings (WTF folder) before changing addons — keep the newest <input id="fBkN" inputmode="numeric" style="width:56px;padding:3px 6px;margin:0 2px"> copies</label>
      </div>
      <div class="field">
        <label for="fBkDir">Backups folder</label>
        <input id="fBkDir" spellcheck="false" autocomplete="off">
        <div class="help">Put it in OneDrive (or another synced folder) to keep your addon settings safe even if the PC breaks. Existing backups are moved. Leave empty for the default.</div>
      </div>
    </div>
    <div class="sect"><h4>Accounts</h4>
      <div class="field">
        <label for="fKey">CurseForge API key</label>
        <div class="row"><input id="fKey" type="password" autocomplete="off" spellcheck="false"><button class="btn" id="bTestKey">Test key</button></div>
        <div class="help" id="keyResult" style="display:none"></div>
        <div class="help" id="keyHelp">Stored only on this PC, in your user profile.</div>
      </div>
      <div class="field">
        <label for="fWago">Wago Addons key <span class="dim" style="font-weight:400">(optional — from addons.wago.io/patreon)</span></label>
        <div class="row"><input id="fWago" type="password" autocomplete="off" spellcheck="false"><button class="btn" id="bTestWago">Test</button></div>
        <div class="help" id="wagoResult" style="display:none"></div>
        <label class="ck"><input type="checkbox" id="fUseWago">Use Wago: compare versions there, update from it when it's newer, and get addons that CurseForge only offers on its own site</label>
      </div>
      <div class="field">
        <label for="fType">CurseForge game version</label>
        <div class="row"><select id="fType"><option value="auto">Automatic (WoW Forever)</option></select><button class="btn" id="bTypes">Load list</button></div>
        <div class="help">Which CurseForge version tag counts as “Forever”. Automatic is right unless CurseForge renames it.</div>
      </div>
    </div>
    <div class="sect" id="winBox"><h4>Window</h4>
      <div class="field">
        <label class="ck"><input type="checkbox" id="fWin">Open in its own window (no browser tabs or address bar) — takes effect next start</label>
        <div style="margin-top:8px"><button class="btn small" id="bShortcut">Add to desktop &amp; Start menu</button></div>
      </div>
    </div>
    <div class="foot"><button class="btn ghost" id="bDiag" style="margin-right:auto">Run diagnostics</button><button class="btn" id="bCancel">Cancel</button><button class="btn primary" id="bSave">Save</button></div>
  </div></div>
</div>

<script>
"use strict";
const $ = s => document.querySelector(s);
let S = null, logSeq = 0, filter = "all", drawerOpen = false, zipKey = null, wasRunning = false, firstLoad = true;
let view = "installed", libsOpen = false, hidOpen = false;

function h(tag, attrs, ...kids){
  const e = document.createElement(tag);
  for (const [k,v] of Object.entries(attrs||{})){
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else e.setAttribute(k, v === true ? "" : v);
  }
  for (const k of kids.flat()){ if (k === null || k === undefined || k === false) continue; e.append(k.nodeType ? k : document.createTextNode(String(k))); }
  return e;
}
function ask(title, body, yes, input, no){
  return new Promise(res=>{
    $("#askT").textContent=title; $("#askB").textContent=body; $("#askYes").textContent=yes||"OK"; $("#askNo").textContent=no||"Cancel";
    const inp=$("#askIn"); inp.hidden = input===undefined; inp.value = input||"";
    const m=$("#mAsk"); m.classList.add("show");
    const done=v=>{ m.classList.remove("show"); $("#askYes").onclick=$("#askNo").onclick=m.onclick=inp.onkeydown=null; res(v); };
    $("#askYes").onclick=()=>done(input===undefined ? true : inp.value.trim()||false); $("#askNo").onclick=()=>done(false); m.onclick=e=>{ if(e.target===m) done(false); };
    inp.onkeydown=e=>{ if(e.key==="Enter") $("#askYes").click(); };
    (input===undefined ? $("#askYes") : inp).focus();
  });
}
function toast(msg){ const t=$("#toast"); t.textContent=msg; t.style.display="block"; clearTimeout(toast._t); toast._t=setTimeout(()=>t.style.display="none",5000); }
async function post(path, body, extraHeaders){
  const isBlob = body instanceof Blob;
  const r = await fetch(path,{method:"POST",headers:Object.assign({"X-WFAM":"1"}, isBlob?{"Content-Type":"application/octet-stream"}:{"Content-Type":"application/json"}, extraHeaders||{}),body:isBlob?body:JSON.stringify(body||{})});
  const j = await r.json().catch(()=>({}));
  if (!r.ok) throw new Error(j.error || ("HTTP "+r.status));
  refresh(true);
  return j;
}
const act = (path, body, h2) => post(path, body, h2).catch(e => toast(e.message));
function fmtDate(iso){ if(!iso) return ""; const d=new Date(iso); if (isNaN(d)) return ""; return d.toLocaleDateString(undefined,{year:"numeric",month:"short",day:"numeric"}); }
function ago(iso){ if(!iso) return "never"; const s=(Date.now()-new Date(iso))/1000; if(s<90) return "just now"; if(s<3600) return Math.round(s/60)+" min ago"; if(s<86400) return Math.round(s/3600)+" h ago"; return Math.round(s/86400)+" d ago"; }
function fmtNum(n){ if (n>=1e6) return (n/1e6).toFixed(n>=1e7?0:1)+"M"; if (n>=1e3) return (n/1e3).toFixed(n>=1e4?0:1)+"k"; return String(n); }
function fmtSize(b){ if(!b) return ""; return b>=1048576 ? (b/1048576).toFixed(1)+" MB" : Math.max(1,Math.round(b/1024))+" KB"; }
function logoEl(src, name, cls){ const fb=()=>h("div",{class:cls||"logo"},(name||"?")[0]); return src ? h("img",{class:cls||"logo",src,alt:"",loading:"lazy",referrerpolicy:"no-referrer",onerror:function(){this.replaceWith(fb())}}) : fb(); }

function clientName(n){
  n = parseInt(n,10); if (!n) return "another client";
  if (n >= 16000 && n < 17000){ const cur = parseInt((S && S.config.interface) || "16001",10); return n < cur ? "an older WoW Forever patch" : n > cur ? "a newer WoW Forever patch" : "WoW Forever"; }
  if (n < 20000) return "Classic Era";
  if (n < 30000) return "TBC Anniversary";
  if (n < 40000) return "Titan-Reforged Classic";
  if (n < 50000) return "Cataclysm Classic";
  if (n < 60000) return "MoP Classic";
  if (n >= 100000) return "Retail";
  return "another client";
}
function compatNote(ifs, file){
  const names = [...new Set(ifs.map(clientName))].join(" / ");
  return h("div",{class:"compat",title:`${file||".toc"} says Interface ${ifs.join(", ")}. WoW Forever expects ${(S && S.config.interface) || "16001"}, so the game may list it as out of date or not load it. Check for updates — a newer version may fix it.`}, "⚠ Made for " + names + " (" + ifs.join(", ") + ")");
}
const STATUS = {
  ok:["Up to date","ok"], update:["Update available","update"], update_manual:["Update — manual download","update_manual"],
  manual:["Manual download needed","manual"], missing:["Folders missing","missing"], pinned:["Pinned","pinned"],
  unchecked:["Not checked yet","unchecked"], not_installed:["Not installed","not_installed"],
  awaiting:["Waiting for download…","pinned"]
};
const isUpd = a => a.status==="update"||a.status==="update_manual";
const isBad = a => a.status==="manual"||a.status==="missing"||a.compat==="mismatch"||(a.missing_deps||[]).length>0;
const rank = a => isUpd(a) ? 0 : isBad(a) ? 1 : a.status==="awaiting" ? 2 : 3;

// ---------- popover menus ----------
function openMenu(anchor, items){
  const pop = $("#pop"); pop.replaceChildren();
  for (const it of items){
    if (!it) continue;
    if (it === "-"){ pop.append(h("hr")); continue; }
    if (it.head){ pop.append(h("div",{class:"hd"}, it.head)); continue; }
    const el = it.href ? h("a",{href:it.href, target:it.download?null:"_blank", download:it.download||null, class:it.danger?"danger":null}, it.label)
                       : h("button",{class:it.danger?"danger":null, disabled:!!it.disabled}, it.label);
    el.addEventListener("click", e=>{ closeMenu(); if (it.run){ e.preventDefault(); it.run(); } });
    pop.append(el);
  }
  pop.hidden = false;
  const r = anchor.getBoundingClientRect(), w = pop.offsetWidth, hh = pop.offsetHeight;
  pop.style.left = Math.max(8, Math.min(innerWidth - w - 8, r.right - w)) + "px";
  pop.style.top = (r.bottom + 6 + hh > innerHeight ? Math.max(8, r.top - hh - 6) : r.bottom + 6) + "px";
}
function closeMenu(){ $("#pop").hidden = true; }
document.addEventListener("mousedown", e=>{ if (!$("#pop").hidden && !e.target.closest("#pop")) closeMenu(); }, true);
$("#scroll").addEventListener("scroll", closeMenu);
window.addEventListener("resize", closeMenu);

// ---------- main render ----------
function render(){
  const c = S.config, busy = S.job.running;
  const d = c.addons_dir || "";
  const short = d ? d.split(/[\\/]/).filter(Boolean).slice(-3).join("\\") : "";
  $("#path").replaceChildren(d ? h("span",{title:d}, h("b",{},"AddOns: "), "…\\"+short) : "No AddOns folder chosen yet");
  $("#verTxt").textContent = "Version " + (S.app && S.app.version || "");
  $("#bQuit").hidden = !!c.hosted;
  const m = S.managed;
  const nIns = m.filter(a=>a.folders.length).length, nUpd = m.filter(isUpd).length, nBad = m.filter(isBad).length;
  $("#sAll").textContent = nIns; $("#sUpd").textContent = nUpd; $("#sBad").textContent = nBad; $("#sUn").textContent = S.unmanaged.length;
  for (const [sel,n] of [[".stat.upd",nUpd],[".stat.bad",nBad],['.stat[data-f="unmanaged"]',S.unmanaged.length]]) $(sel).classList.toggle("zero", !n);
  $("#tabN").textContent = nIns; $("#tabN").classList.toggle("hot", nUpd>0);
  if (nUpd) $("#tabN").textContent = nIns + " · " + nUpd + "↑";
  $("#gameWarn").hidden = !S.game_running || view==="chars";
  $("#chGame").hidden = !S.game_running;
  $("#bScan").disabled = busy;
  renderBackups(); renderDiag();
  if (!busy && sending !== null) sending = null;
  if (!busy) pumpQueue();
  renderBrowse();
  document.querySelectorAll(".stat").forEach(e=>e.classList.toggle("on", e.dataset.f===filter));
  const nU = m.filter(a=>a.status==="update").length;
  $("#bUpdAll").textContent = nU ? `Update all (${nU})` : "Update all";
  for (const id of ["#bCheck","#bUpdAll","#bAdd"]) $(id).disabled = busy;
  if (view==="installed"){ const checked = m.map(a=>a.checked_at).filter(Boolean).sort().pop(); $("#vSub").textContent = m.length ? "Last check: " + ago(checked) : ""; }
  if (DT.id) renderDetail();
  renderSelfUpdate(); checkBatch();

  const q = $("#q").value.trim().toLowerCase();
  let rows = m.filter(a => !q || (a.name||"").toLowerCase().includes(q) || a.folders.join(" ").toLowerCase().includes(q) || (a.authors||[]).join(" ").toLowerCase().includes(q));
  if (filter==="updates") rows = rows.filter(isUpd);
  if (filter==="problems") rows = rows.filter(isBad);
  if (filter==="unmanaged") rows = [];
  rows.sort((a,b)=> rank(a)-rank(b) || (a.name||"").localeCompare(b.name||""));

  const card = $("#managedCard");
  card.hidden = filter==="unmanaged";
  if (!c.has_key || !c.addons_dir_ok){
    card.replaceChildren(h("div",{class:"empty"}, h("b",{},"Finish setup to start"), h("br"), "Add your CurseForge API key and pick the WoW Forever AddOns folder.", h("div",{class:"acts2"}, h("button",{class:"btn primary",onclick:()=>openSettings()},"Open settings"))));
  } else if (!m.length){
    card.replaceChildren(h("div",{class:"empty"}, h("b",{},"No addons yet"), h("br"), "Find addons in Browse, or paste CurseForge links. Addons you copied into AddOns by hand are listed below.",
      h("div",{class:"acts2"}, h("button",{class:"btn primary",onclick:()=>showView("browse")},"Browse addons"), h("button",{class:"btn",onclick:openAdd},"Paste links"))));
  } else if (!rows.length){
    card.replaceChildren(h("div",{class:"empty"},"Nothing matches this filter."));
  } else {
    const main = [], libs = [];
    for (const a of rows) ((a.library && rank(a)===3 && !q) ? libs : main).push(a);
    const tb = h("tbody");
    for (const a of main) tb.append(addonRow(a, busy));
    if (libs.length){
      tb.append(h("tr",{class:"grp",onclick:()=>{ libsOpen=!libsOpen; render(); }}, h("td",{colspan:5}, (libsOpen?"▾ ":"▸ ") + "Libraries ", h("span",{class:"dim"},"("+libs.length+") — shared code other addons need"))));
      if (libsOpen) for (const a of libs) tb.append(addonRow(a, busy));
    }
    card.replaceChildren(h("table",{}, h("thead",{}, h("tr",{}, h("th",{},"Addon"), h("th",{},"Installed"), h("th",{class:"hide-s"},"Latest for Forever"), h("th",{},"Status"), h("th",{}))), tb));
  }

  // other folders
  const un = S.unmanaged, hid = S.hidden_folders || [];
  $("#unC").textContent = un.length;
  const showUn = (filter==="all"||filter==="unmanaged") && c.addons_dir_ok;
  $("#unH").style.display = showUn && (un.length || filter==="unmanaged") ? "" : "none";
  $("#unCard").style.display = $("#unH").style.display;
  if (!un.length){
    $("#unCard").replaceChildren(h("div",{class:"empty"},"Every folder in AddOns is managed here."));
  } else {
    const tb = h("tbody");
    for (const g of un){
      tb.append(h("tr",{},
        h("td",{}, h("div",{style:"font-weight:600"}, g.title), h("div",{class:"dim",style:"font-size:12px"}, g.recognized ? (g.forever===false ? "✓ recognised on CurseForge — but this copy isn't the WoW Forever version" : "✓ recognised on CurseForge") : (g.has_toc ? g.folders.join(", ") : "no .toc file — WoW will ignore this folder"))),
        h("td",{}, h("span",{class:"vv"}, g.version || "—")),
        h("td",{class:"hide-s"}, g.compat==="mismatch" ? compatNote(g.interfaces, g.toc_file) : h("span",{class:"dim"}, g.interfaces.join(", ")||"—"),
          (g.missing_deps||[]).length ? h("div",{class:"compat",style:"color:var(--red)"},"⚠ Needs: "+g.missing_deps.join(", ")) : null),
        h("td",{}, h("div",{class:"acts"}, g.recognized
          ? h("button",{class:"btn small primary",disabled:busy,title:g.forever===false?"Install the WoW Forever version and track it for updates":"Track it for updates — nothing is downloaded",onclick:()=>act("/api/adopt",{mod_id:g.project_id})}, g.forever===false ? "Install Forever version" : "Manage")
          : g.project_id
          ? h("button",{class:"btn small",disabled:busy,title:"Reinstall the WoW Forever version from CurseForge and track it for updates",onclick:()=>act("/api/manage",{project_id:g.project_id})},"Manage with updates")
          : null,
          h("button",{class:"btn small ghost",title:"You look after this one yourself (e.g. Zygor): hide it here",onclick:()=>act("/api/ignore",{folders:g.folders,ignore:true})},"Hide")))
      ));
    }
    $("#unCard").replaceChildren(h("table",{}, h("thead",{}, h("tr",{}, h("th",{},"Folder / title"), h("th",{},"Version (.toc)"), h("th",{class:"hide-s"},"Interface"), h("th",{}))), tb));
  }
  const hb = $("#hidBox");
  hb.style.display = showUn ? "" : "none";
  if (!hid.length) hb.replaceChildren();
  else hb.replaceChildren(h("button",{class:"linkbtn",style:"color:var(--dim)",onclick:()=>{ hidOpen=!hidOpen; render(); }}, (hidOpen?"▾ ":"▸ ") + hid.length + " hidden folder" + (hid.length>1?"s":"") + " you look after yourself"),
    hidOpen ? h("div",{class:"card",style:"margin-top:8px"}, ...hid.map(g=>h("div",{class:"bk"}, h("div",{class:"n"}, h("b",{},g.title), h("span",{class:"dim",style:"margin-left:8px;font-size:12px"}, g.folders.join(", "))), h("button",{class:"btn small",onclick:()=>act("/api/ignore",{folders:g.folders,ignore:false})},"Show again")))) : null);

  // status bar
  const lb = $("#logbox");
  for (const e of S.logs){
    if (e.seq <= logSeq) continue;
    lb.append(h("div",{class:"lg "+e.level}, h("span",{class:"tm"},e.t), e.msg));
    logSeq = e.seq;
  }
  while (lb.children.length > 400) lb.firstChild.remove();
  lb.scrollTop = lb.scrollHeight;
  const vis = [...lb.querySelectorAll(".lg:not(.debug)")];
  const last = vis[vis.length-1];
  $("#dLast").textContent = busy ? (S.job.title + "… " + (last?last.textContent.slice(8):"")) : (last ? last.textContent.slice(8) : "Ready.");
  const dot = $("#dDot");
  if (busy){ if (!dot.classList.contains("spin")) dot.className="spin"; }
  else dot.className = "dot " + (S.job.ok===false ? "err" : S.job.ok ? "ok" : "");
  if (busy && !wasRunning){ setDrawer(true); }
  wasRunning = busy;
}

function addonRow(a, busy){
  const [label, cls] = STATUS[a.status] || [a.status,"unchecked"];
  const acts = [];
  if (a.status==="update"||a.status==="missing") acts.push(h("button",{class:"btn small primary",disabled:busy,onclick:()=>act("/api/update",{key:a.key})}, a.status==="missing"?"Reinstall":"Update"));
  if (a.status==="awaiting"){
    acts.push(h("a",{class:"btn small",href:a.download_page,target:"_blank",rel:"noopener",title:"Opens CurseForge's download page again"},"Download page ↗"));
    acts.push(h("button",{class:"btn small",disabled:busy,onclick:()=>pickZip(a.key)},"Install zip…"));
    acts.push(h("button",{class:"btn small ghost",onclick:()=>act("/api/cancel_wait",{key:a.key})},"Cancel"));
  }
  if (a.status==="manual"||a.status==="update_manual"){
    if (S.config.auto_manual) acts.push(h("button",{class:"btn small primary",disabled:busy,title:"Opens CurseForge's download page; the addon installs by itself when the download finishes",onclick:()=>act("/api/update",{key:a.key})}, a.status==="manual"?"Download & install":"Download & update"));
    else acts.push(h("a",{class:"btn small",href:a.download_page,target:"_blank",rel:"noopener"},"Download page ↗"));
    acts.push(h("button",{class:"btn small",disabled:busy,onclick:()=>pickZip(a.key)},"Install zip…"));
  }
  if ((a.missing_deps||[]).length) acts.push(h("button",{class:"btn small primary",disabled:busy,title:"Install what this addon needs",onclick:()=>act("/api/fix_deps",{key:a.key})},"Install missing"));
  acts.push(h("button",{class:"btn small ghost more",title:"More",onclick:e=>openMenu(e.currentTarget, rowMenu(a, busy))},"⋯"));
  const latest = a.latest_version
    ? h("div",{class:"vv",title:a.latest_version}, a.latest_version, h("div",{class:"d"}, fmtDate(a.latest_date)))
    : h("div",{class:"vv dim"}, a.checked_at ? "no Forever file" : "—");
  const inst = a.installed_version ? h("div",{class:"vv",title:a.installed_version}, a.installed_version,
      h("div",{class:"d"}, (a.source==="github"?"GitHub · ":"") + (a.source==="wago"?"Wago · ":"") + (a.external?"changed outside the manager · ":"") + (a.manual?"manual zip · ":"") + (a.installed_release_type!=="Release"? a.installed_release_type+" · ":"") + fmtDate(a.installed_date)))
    : h("span",{class:"dim"},"—");
  const tr = h("tr",{class:"arow"},
    h("td",{}, h("div",{class:"nm"}, logoEl(a.logo, a.name), h("div",{style:"min-width:0"}, h("div",{class:"t"}, a.name), h("div",{class:"s",title:a.summary||""}, a.summary || (a.authors||[]).slice(0,2).join(", "))))),
    h("td",{}, inst),
    h("td",{class:"hide-s"}, latest),
    h("td",{}, h("span",{class:"pill p-"+cls}, label + (a.update_from==="wago" ? " · Wago" : "")), a.compat==="mismatch" ? compatNote(a.toc_interfaces, a.toc_file) : null,
      (a.missing_deps||[]).length ? h("div",{class:"compat",style:"color:var(--red)",title:"The addon's .toc says it can't load without these"},"⚠ Needs: "+a.missing_deps.join(", ")) : null),
    h("td",{}, h("div",{class:"acts"}, acts)));
  tr.addEventListener("click", e=>{ if (!e.target.closest("button,a") && !a.wago_only) openDetail(a.key); });
  return tr;
}
function rowMenu(a, busy){
  return [
    a.wago_only ? null : {label:"Details…", run:()=>openDetail(a.key)},
    a.latest_version && !a.wago_only && ["update","update_manual","ok","pinned"].includes(a.status) ? {label:"What's new", run:()=>showChangelog(a.key)} : null,
    "-",
    {label: a.pinned ? "Unpin — allow updates again" : "Pin — keep this version", disabled:busy, run:()=>act("/api/pin",{key:a.key,pinned:!a.pinned})},
    a.previous_version ? {label:"Revert to "+a.previous_version, disabled:busy, run:async ()=>{ if (await ask(`Revert ${a.name}?`, `Goes back to ${a.previous_version} and pins it so Update all skips it. You can switch back the same way.`, "Revert")) act("/api/revert",{key:a.key}); }} : null,
    {label:"Install from a zip…", disabled:busy, run:()=>pickZip(a.key)},
    a.url ? {label: a.wago_only ? "Open on Wago ↗" : "Open on CurseForge ↗", href:a.url} : null,
    a.wago_url && !a.wago_only ? {label:"Open on Wago ↗", href:a.wago_url} : null,
    "-",
    {label:"Remove", danger:true, disabled:busy, run:async ()=>{ if (await ask(`Remove ${a.name}?`, a.folders.length ? `These folders will be deleted from AddOns:\n${a.folders.join("\n")}` : "It will no longer be tracked.", "Remove")) act("/api/remove",{key:a.key}); }},
  ];
}

// ---------- updates of the program itself ----------
function renderSelfUpdate(){
  const u = S.self_update, box = $("#selfUpd");
  if (!u) selfOffered = "";
  box.hidden = !u;
  if (!u) return;
  offerSelfUpdate(u);
  if (box.dataset.v === u.version + String(S.job.running)) return;
  box.dataset.v = u.version + String(S.job.running);
  box.replaceChildren(h("b",{},"Version " + u.version + " is ready"), h("button",{class:"btn small primary",disabled:S.job.running,onclick:async ()=>{
    if (await ask("Update WoW Forever Addons to " + u.version + "?", (u.notes ? u.notes.slice(0,700) + "\n\n" : "") + "The program closes, updates itself and opens again. Your addons and settings stay as they are.", "Update now")) act("/api/self_update");
  }},"Update now"), " ", h("a",{href:u.page,style:"font-size:12px"},"What's new ↗"));
}
// ---------- what the last update changed ----------
let batchSeen = null;
function checkBatch(){
  const b = S.last_batch, at = b ? b.at : "";
  if (batchSeen === null){ batchSeen = at; return; }   // don't pop up old news when the page opens
  if (at && at !== batchSeen && !S.job.running){ batchSeen = at; showBatch(); }
  if (!at) batchSeen = "";
}
function showBatch(){
  const b = S.last_batch; if (!b) return;
  $("#btT").textContent = b.items.length === 1 ? "1 addon updated" : b.items.length + " addons updated";
  $("#btS").textContent = new Date(b.at).toLocaleString(undefined,{dateStyle:"medium",timeStyle:"short"}) + " · if something breaks, “Undo this update” puts the earlier versions back.";
  $("#btL").replaceChildren(...b.items.map(it=>h("div",{class:"bl"}, h("div",{class:"n"}, h("b",{},it.name), h("div",{class:"v"}, (it.from||"?") + "  →  " + (it.to||"?"))),
    h("button",{class:"btn small ghost",onclick:()=>{ $("#mBatch").classList.remove("show"); showChangelog(it.key); }},"What's new"))));
  $("#mBatch").classList.add("show");
}
$("#btClose").onclick = ()=>$("#mBatch").classList.remove("show");
$("#mBatch").addEventListener("click", e=>{ if (e.target.id==="mBatch") $("#mBatch").classList.remove("show"); });
$("#btUndo").onclick = async ()=>{ $("#mBatch").classList.remove("show"); if (await ask("Undo the last update?", "Puts back the versions you had before for: " + S.last_batch.items.map(i=>i.name).join(", ") + ".\n\nThey get pinned so Update all leaves them alone — unpin them when fixed versions are out.", "Undo")) act("/api/undo_batch"); };

// ---------- detail panel ----------
const DT = {id:null, data:null, error:"", tab:"about", cl:{}, loading:false};
function openDetail(id){
  id = String(id);
  if (DT.id !== id){ DT.data = null; DT.error = ""; DT.cl = {}; DT.tab = "about"; }
  DT.id = id; DT.loading = true;
  $("#detail").classList.add("show"); $("#shade").classList.add("show"); $("#detail").setAttribute("aria-hidden","false");
  renderDetail();
  post("/api/details",{id:+id}).then(d=>{ if (DT.id===id){ DT.data=d; DT.loading=false; renderDetail(); } })
    .catch(e=>{ if (DT.id===id){ DT.error=e.message; DT.loading=false; renderDetail(); } });
}
function closeDetail(){ DT.id = null; $("#detail").classList.remove("show"); $("#shade").classList.remove("show"); $("#detail").setAttribute("aria-hidden","true"); }
const OKTAGS = new Set("P BR B STRONG I EM U S UL OL LI A H1 H2 H3 H4 H5 H6 IMG SPAN DIV BLOCKQUOTE PRE CODE HR TABLE THEAD TBODY TR TD TH SMALL SUP SUB CENTER FIGURE FIGCAPTION DL DT DD".split(" "));
const DROP = new Set("SCRIPT STYLE IFRAME OBJECT EMBED FORM INPUT BUTTON TEXTAREA SELECT SVG MATH LINK META NOSCRIPT TEMPLATE VIDEO AUDIO SOURCE FRAME FRAMESET BASE".split(" "));
function safeUrl(u){ try{ const x = new URL(u, "https://www.curseforge.com/"); return /^https?:$/.test(x.protocol) ? x.href : null; }catch(e){ return null; } }
// The description is HTML written by the addon's author: rebuilt from scratch with only harmless parts.
function sanitize(html){
  const doc = new DOMParser().parseFromString(html||"", "text/html");
  const walk = (node, out) => {
    for (const ch of [...node.childNodes]){
      if (ch.nodeType === 3){ out.append(ch.textContent); continue; }
      if (ch.nodeType !== 1) continue;
      const tag = ch.tagName;
      if (DROP.has(tag)) continue;
      if (!OKTAGS.has(tag)){ walk(ch, out); continue; }
      const el = document.createElement(tag==="CENTER" ? "DIV" : tag);
      if (tag==="A"){ const u = safeUrl(ch.getAttribute("href")); if (u) el.setAttribute("href", u); }
      if (tag==="IMG"){ const u = safeUrl(ch.getAttribute("src")); if (!u) continue; el.src = u; el.loading = "lazy"; el.referrerPolicy = "no-referrer"; el.alt = ch.getAttribute("alt")||""; el.onclick = ()=>lightbox(u); }
      if (tag==="TD"||tag==="TH"){ const cs = ch.getAttribute("colspan"); if (cs && /^\d+$/.test(cs)) el.setAttribute("colspan", cs); }
      walk(ch, el); out.append(el);
    }
  };
  const box = h("div",{class:"desc"}); walk(doc.body, box); return box;
}
function lightbox(u){ $("#lbImg").src = u; $("#lightbox").classList.add("show"); }
function renderDetail(){
  if (!DT.id) return;
  const d = DT.data, a = S ? S.managed.find(x=>x.key===DT.id) : null, busy = S && S.job.running;
  const name = d ? d.name : (a ? a.name : (B.results.find(r=>String(r.id)===DT.id)||{}).name || "Loading…");
  const logo = d ? d.logo : (a ? a.logo : (B.results.find(r=>String(r.id)===DT.id)||{}).logo);
  const acts = [];
  if (a && a.folders.length){
    if (a.status==="update") acts.push(h("button",{class:"btn primary",disabled:busy,onclick:()=>act("/api/update",{key:a.key})},"Update to "+(a.latest_version||"latest")));
    else acts.push(h("span",{class:"pill p-"+(STATUS[a.status]||["","unchecked"])[1]}, (STATUS[a.status]||[a.status])[0]));
  } else if (a && a.status==="awaiting") acts.push(h("span",{class:"tag warn"},"Waiting for download…"));
  else if (queue.includes(+DT.id) || sending===+DT.id) acts.push(h("button",{class:"btn primary",disabled:true},"Installing…"));
  else acts.push(h("button",{class:"btn primary",disabled:busy || !(S && S.config.has_key),onclick:()=>installFromBrowse(+DT.id)},"Install"));
  if (d && d.url) acts.push(h("a",{class:"btn",href:d.url},"CurseForge ↗"));
  if (d && d.source) acts.push(h("a",{class:"btn",href:d.source},"Source ↗"));
  if (a) acts.push(h("button",{class:"btn icon",title:"More",onclick:e=>openMenu(e.currentTarget, rowMenu(a, busy))},"⋯"));
  const meta = [];
  if (d){ meta.push(h("span",{class:"tag"},"⬇ "+fmtNum(d.downloads))); if (d.updated) meta.push(h("span",{class:"tag"},"Updated "+fmtDate(d.updated))); for (const c of d.categories.slice(0,3)) meta.push(h("span",{class:"tag"},c)); if (d.manual_only) meta.push(h("span",{class:"tag warn",title:"The author only allows downloads on CurseForge's own site"},"CurseForge-site download")); }
  $("#dHead").replaceChildren(logoEl(logo, name),
    h("div",{style:"flex:1;min-width:0"}, h("div",{class:"ttl"}, name), h("div",{class:"by"}, d && d.authors.length ? "by "+d.authors.join(", ") : (a && a.authors ? "by "+a.authors.join(", ") : "")),
      h("div",{class:"sacts"}, acts), meta.length ? h("div",{class:"smeta"}, meta) : null),
    h("button",{class:"btn icon ghost x",title:"Close (Esc)",onclick:closeDetail},"✕"));
  document.querySelectorAll(".stab").forEach(t=>t.classList.toggle("on", t.dataset.t===DT.tab));
  const body = $("#dBody");
  if (DT.error){ body.replaceChildren(h("div",{class:"empty"}, h("b",{},"Couldn't load the details"), h("br"), DT.error)); return; }
  if (!d){ body.replaceChildren(h("div",{class:"empty"}, h("span",{class:"spin",style:"display:inline-block;vertical-align:middle;margin-right:8px"}), "Loading…")); return; }
  if (DT.tab==="about"){
    const kids = [];
    if (d.summary) kids.push(h("p",{class:"muted",style:"margin:0 0 14px;font-size:14px"}, d.summary));
    if (d.screenshots.length) kids.push(h("div",{class:"shots"}, ...d.screenshots.map(s=>h("img",{src:s.thumb||s.url,alt:s.title,title:s.title,loading:"lazy",referrerpolicy:"no-referrer",onclick:()=>lightbox(s.url)}))));
    kids.push(d.description ? sanitize(d.description) : h("div",{class:"dim"},"No description."));
    body.replaceChildren(...kids);
  } else if (DT.tab==="versions"){
    if (!d.files.length){ body.replaceChildren(h("div",{class:"empty"},"No WoW Forever files.")); return; }
    body.replaceChildren(h("p",{class:"dim",style:"margin:0 0 10px;font-size:12.5px"},"Installing a specific version pins it, so Update all leaves it alone until you unpin it."),
      h("div",{class:"fl"}, ...d.files.map(f=>{
        const cl = DT.cl[f.id];
        return h("div",{class:"fr",style:"flex-wrap:wrap"},
          h("div",{class:"n"}, h("b",{title:f.name}, f.name), h("span",{}, fmtDate(f.date) + (f.size ? " · "+fmtSize(f.size) : ""))),
          f.type!=="Release" ? h("span",{class:"tag warn"}, f.type) : null,
          f.installed ? h("span",{class:"tag ok"},"Installed") : null,
          h("button",{class:"btn small ghost",onclick:()=>toggleCl(f.id)}, cl ? "Hide notes" : "What's new"),
          !f.installed && !d.manual_only ? h("button",{class:"btn small",disabled:busy,onclick:async ()=>{ if (await ask(`Install ${f.name}?`, "This exact version is installed and pinned, so Update all skips it until you unpin it.", "Install")) act("/api/install_file",{mod_id:d.id,file_id:f.id}); }},"Install") : null,
          cl ? h("div",{class:"cl",style:"flex-basis:100%"}, cl) : null);
      })));
  } else {
    const kv = [["Project ID", d.id], ["Released", fmtDate(d.released)], ["Last updated", fmtDate(d.updated)], ["Downloads", d.downloads.toLocaleString()], ["Categories", d.categories.join(", ")||"—"]];
    if (a){ kv.push(["Installed", (a.installed_version||"—") + (a.source && a.source!=="curseforge" ? " (from "+a.source+")" : "")]); kv.push(["Folders", a.folders.join(", ")||"—"]); if ((a.missing_deps||[]).length) kv.push(["Missing", a.missing_deps.join(", ")]); if (a.toc_interfaces) kv.push(["Interface (.toc)", (a.toc_interfaces||[]).join(", ")||"—"]); }
    const links = [d.url && ["CurseForge", d.url], d.source && ["Source code", d.source], d.issues && ["Report a problem", d.issues], d.wiki && ["Wiki", d.wiki]].filter(Boolean);
    body.replaceChildren(h("div",{class:"kv"}, ...kv.flatMap(([k,v])=>[h("div",{class:"k"},k), h("div",{class:"dd"}, String(v))]),
      h("div",{class:"k"},"Links"), h("div",{}, ...links.map(([t,u],i)=>[i?" · ":"", h("a",{href:u},t+" ↗")]))));
  }
}
async function toggleCl(fid){
  if (DT.cl[fid]){ delete DT.cl[fid]; renderDetail(); return; }
  DT.cl[fid] = "Loading…"; renderDetail();
  try{ const r = await post("/api/file_changelog",{mod_id:DT.data.id,file_id:fid}); DT.cl[fid] = r.text; }catch(e){ DT.cl[fid] = e.message; }
  renderDetail();
}
document.querySelectorAll(".stab").forEach(t=>t.onclick=()=>{ DT.tab=t.dataset.t; renderDetail(); $("#dBody").scrollTop=0; });
$("#shade").onclick = closeDetail;
$("#lightbox").onclick = ()=>$("#lightbox").classList.remove("show");

// ---------- Browse ----------
const B = {q:"", sort:"2", cat:"", results:[], total:0, next:null, loading:false, error:"", loaded:false, seq:0, catsLoaded:false, src:"cf", all:false, hidden:0};
const queue = [];
let sending = null;
async function bSearch(append){
  if (B.src === "wago") return wSearch();
  if (B.loading && append) return;
  const my = ++B.seq;
  B.loading = true; B.error = "";
  if (!append){ B.results = []; B.next = null; }
  renderBrowse();
  try{
    const r = await fetch("/api/search",{method:"POST",headers:{"X-WFAM":"1","Content-Type":"application/json"},
      body:JSON.stringify({q:B.q, sort:B.sort, category:B.cat, index: append ? B.next : 0, all:B.all})});
    const j = await r.json().catch(()=>({}));
    if (my !== B.seq) return;
    if (!r.ok) throw new Error(j.error || ("HTTP "+r.status));
    B.results = append ? B.results.concat(j.results) : j.results;
    B.hidden = (append ? B.hidden : 0) + (j.hidden || 0);
    B.total = j.total; B.next = j.next; B.loaded = true;
  }catch(e){ if (my === B.seq) B.error = e.message; }
  if (my === B.seq){ B.loading = false; renderBrowse(); }
}
async function loadCats(){
  if (B.catsLoaded) return;
  try{
    const r = await post("/api/categories",{});
    const sel = $("#bcat");
    for (const c of r.categories) sel.append(h("option",{value:c.id}, c.name));
    B.catsLoaded = true;
  }catch(e){}
}
function installFromBrowse(id){
  if (queue.includes(id) || sending === id) return;
  queue.push(id); pumpQueue(); renderBrowse(); if (DT.id) renderDetail();
}
async function pumpQueue(){
  if (sending !== null || !queue.length || !S || S.job.running) return;
  const id = queue.shift();
  sending = id;
  try{ await post("/api/add",{text:String(id)}); }
  catch(e){ toast(e.message); sending = null; }
  renderBrowse();
}
async function wSearch(){
  const my = ++B.seq;
  B.loading = true; B.error = ""; B.results = []; B.next = null; renderBrowse();
  if (B.q.length === 1){ B.loading = false; B.loaded = false; renderBrowse(); return; }
  try{ const r = await post("/api/wago_search",{q:B.q, all:B.all}); if (my !== B.seq) return; B.results = r.results; B.hidden = r.hidden || 0; B.total = r.results.length; B.loaded = true; }
  catch(e){ if (my === B.seq) B.error = e.message; }
  if (my === B.seq){ B.loading = false; renderBrowse(); }
}
const W = {cat:"", sort:"pop"};
function wagoCats(){
  const sel = $("#wcat"), names = [...new Set(B.results.flatMap(r=>r.categories||[]))].sort((a,b)=>a.localeCompare(b));
  if (W.cat && !names.includes(W.cat)) names.unshift(W.cat);
  const key = names.join("|");
  if (sel.dataset.k !== key){ sel.dataset.k = key; sel.replaceChildren(h("option",{value:""},"All categories"), ...names.map(n=>h("option",{value:n}, n))); }
  sel.value = W.cat; sel.hidden = !names.length;
}
function wagoShown(){
  let list = B.results.filter(r => !W.cat || (r.categories||[]).includes(W.cat));
  const by = {dl:(a,b)=>b.downloads-a.downloads, new:(a,b)=>String(b.updated||"").localeCompare(String(a.updated||"")), name:(a,b)=>a.name.localeCompare(b.name)}[W.sort];
  return by ? [...list].sort(by) : list;
}
function renderWago(box){
  if (B.error){ box.replaceChildren(h("div",{class:"card"}, h("div",{class:"empty"}, h("b",{},"Wago search failed"), h("br"), B.error))); return; }
  const shown = wagoShown();
  if (!shown.length){ box.replaceChildren(h("div",{class:"card"}, h("div",{class:"empty"}, B.loading ? (B.q ? "Searching Wago…" : "Loading Wago's popular addons…") : (B.q.length === 1 ? "Type one more letter…" : W.cat ? "Nothing in that category here." : "Nothing on Wago matches that.")))); return; }
  box.replaceChildren(h("div",{class:"grid"}, ...shown.map(r=>{
    const busyNow = S && S.job.running;
    const btn = r.installed ? h("span",{class:"tag ok"},"Installed ✓")
      : h("button",{class:"btn small primary",disabled:busyNow,title:"If CurseForge has it for WoW Forever too, it's installed from there and Wago is kept as a second source — whichever has the higher version number wins. Otherwise it's installed and updated from Wago.",onclick:e=>{ e.stopPropagation(); act("/api/add_wago",{id:r.id}); }},"Install");
    return h("div",{class:"res",title:r.summary},
      logoEl(r.logo, r.name),
      h("div",{class:"body"}, h("div",{class:"t"}, r.name), h("div",{class:"by"}, r.authors.length ? "by "+r.authors.join(", ") : ""),
        h("div",{class:"sum"}, r.summary),
        h("div",{class:"meta"}, r.downloads ? h("span",{}, "⬇ "+fmtNum(r.downloads)) : null, r.updated ? h("span",{}, fmtDate(r.updated)) : null, h("a",{href:r.url,onclick:e=>e.stopPropagation()},"Wago ↗"), noForever(r), h("span",{class:"sp"}), btn)));
  })));
}
function noForever(r){
  return r.forever === false ? h("span",{class:"tag warn",title:"No WoW Forever build is listed for this addon — it was probably made for another version of WoW and may not work."},"no Forever version") : null;
}
function setMeta(text){
  const el = $("#bmeta"); el.replaceChildren(text);
  if (!text) return;
  const flip = (label)=>h("button",{class:"linkbtn",onclick:()=>{ B.all = !B.all; bSearch(false); }}, label);
  if (B.all) el.append(" · ", flip("Only WoW Forever addons"));
  else if (B.hidden) el.append(` · ${B.hidden} for other WoW versions hidden · `, flip("Show them"));
}
function renderBrowse(){
  if ($("#vBrowse").hidden) return;
  const wagoOk = !!(S && S.config.has_wago_key && S.config.use_wago);
  $("#srcBar").hidden = !wagoOk;
  if (!wagoOk && B.src === "wago"){ B.src = "cf"; }
  document.querySelectorAll(".srcsw button").forEach(b=>b.classList.toggle("on", b.dataset.src===B.src));
  $("#vTitle").textContent = B.src === "wago" ? "Browse Wago" : "Browse CurseForge";
  $("#bcat").hidden = $("#bsort").hidden = B.src === "wago";
  $("#wsort").hidden = B.src !== "wago";
  if (B.src === "wago") wagoCats(); else $("#wcat").hidden = true;
  $("#srcHint").textContent = B.src === "wago" ? "Addons that are on CurseForge too get both sources — the higher version number wins. Wago-only addons are installed and updated from Wago." : "";
  if (B.src === "wago"){ renderWago($("#bres")); const n = wagoShown().length; setMeta(B.loaded && !B.error ? n + " addon" + (n===1?"":"s") + (B.q ? ` on Wago matching “${B.q}”` : " — popular on Wago") + (W.cat ? " in " + W.cat : "") : ""); $("#bmore").hidden = true; return; }
  const box = $("#bres");
  const managed = new Map((S ? S.managed : []).map(a=>[String(a.key), a]));
  if (S && (!S.config.has_key || !S.config.addons_dir_ok)){
    box.replaceChildren(h("div",{class:"card"}, h("div",{class:"empty"}, h("b",{},"Finish setup first"), h("div",{class:"acts2"}, h("button",{class:"btn primary",onclick:()=>openSettings()},"Open settings")))));
    $("#bmeta").textContent=""; $("#bmore").hidden=true; return;
  }
  if (B.error){ box.replaceChildren(h("div",{class:"card"}, h("div",{class:"empty"}, h("b",{},"Search failed"), h("br"), B.error))); }
  else if (!B.results.length){
    box.replaceChildren(h("div",{class:"card"}, h("div",{class:"empty"}, B.loading ? "Searching CurseForge…" : (B.loaded ? "No WoW Forever addons match that search." : ""))));
  } else {
    box.replaceChildren(h("div",{class:"grid"}, ...B.results.map(r=>{
      const a = managed.get(String(r.id));
      let btn;
      if (a && a.folders.length) btn = h("span",{class:"tag ok"}, a.status==="update" ? "Installed · update ready" : "Installed ✓");
      else if (a && a.status==="awaiting") btn = h("span",{class:"tag warn",title:"Finish the download on CurseForge's page — it installs by itself"},"Waiting for download…");
      else if (sending===r.id) btn = h("button",{class:"btn small",disabled:true},"Installing…");
      else if (queue.includes(r.id)) btn = h("button",{class:"btn small",disabled:true},"Queued");
      else btn = h("button",{class:"btn small primary",onclick:e=>{ e.stopPropagation(); installFromBrowse(r.id); }},"Install");
      const via = r.manual_only ? (S && ((r.github && S.config.use_github) || (S.config.has_wago_key && S.config.use_wago))
          ? h("span",{class:"tag ok",title:"The author only allows downloads on CurseForge's own site, but publishes releases elsewhere ("+(r.github||"Wago")+"). If a WoW Forever build is there, it is installed from there; otherwise CurseForge's download page opens."}, S.config.has_wago_key && S.config.use_wago ? "tries Wago & GitHub first" : "tries GitHub first")
          : h("span",{class:"tag",title:"The author only allows downloads on CurseForge's own site. Install opens that page and the addon installs by itself when the download finishes."},"downloads via CurseForge site")) : null;
      const card = h("div",{class:"res",title:r.summary},
        logoEl(r.logo, r.name),
        h("div",{class:"body"},
          h("div",{class:"t"}, r.name),
          h("div",{class:"by"}, r.authors.length ? "by "+r.authors.join(", ") : ""),
          h("div",{class:"sum"}, r.summary),
          h("div",{class:"meta"}, h("span",{}, "⬇ "+fmtNum(r.downloads)), r.updated ? h("span",{}, fmtDate(r.updated)) : null, via, noForever(r), h("span",{class:"sp"}), btn)));
      card.addEventListener("click", e=>{ if (!e.target.closest("button,a")) openDetail(r.id); });
      return card;
    })));
  }
  setMeta(B.loaded && !B.error ? (B.all ? (B.total >= 10000 ? "10,000+ addons" : `${B.total.toLocaleString()} addon${B.total===1?"":"s"}`) + " on CurseForge, all game versions"
                                         : "WoW Forever addons") + (B.q ? ` matching “${B.q}”` : "") : "");
  $("#bmore").hidden = !(B.next !== null && !B.error);
  $("#bMoreBtn").disabled = B.loading; $("#bMoreBtn").textContent = B.loading ? "Loading…" : "Show more";
}

// ---------- backups / changelog / diagnostics ----------
function renderBackups(){
  if ($("#vBackups").hidden) return;
  const busy = S.job.running;
  $("#bBackupNow").disabled = busy || !S.wtf_found;
  const box = $("#bkList");
  if (!S.wtf_found){ box.replaceChildren(h("div",{class:"empty"},"No WTF folder yet — it appears after you've logged in to WoW Forever once.")); return; }
  if (!S.backups.length){ box.replaceChildren(h("div",{class:"empty"},"No backups yet. One is made automatically the next time addons change, or click “Back up now”.")); return; }
  box.replaceChildren(...S.backups.map((b,i)=>h("div",{class:"bk"},
    h("div",{class:"n"}, new Date(b.date).toLocaleString(undefined,{dateStyle:"medium",timeStyle:"short"}), i===0 ? h("span",{class:"tag ok",style:"margin-left:10px"},"newest") : null),
    h("span",{class:"dim"}, b.size),
    h("button",{class:"btn small",disabled:busy,onclick:async ()=>{ if (await ask("Restore addon settings?", `Replaces your current WTF folder with the copy from ${new Date(b.date).toLocaleString()}. WoW must be closed. Your current settings are backed up first, so this can be undone.`, "Restore")) act("/api/restore",{name:b.name}); }},"Restore"))));
}
async function showChangelog(key){
  $("#clT").textContent = "What's new"; $("#clB").textContent = "Loading…"; $("#clU").removeAttribute("href");
  $("#mLog").classList.add("show");
  try{ const r = await post("/api/changelog",{key}); $("#clT").textContent = r.title; $("#clB").textContent = r.text; if (r.url) $("#clU").href = r.url; }
  catch(e){ $("#clB").textContent = e.message; }
}
const DGI = {ok:"✔", warn:"!", fail:"✖", info:"i"};
function renderDiag(){
  if (!$("#mDiag").classList.contains("show")) return;
  const d = S.diag, box = $("#dgBody");
  if (!d){ box.replaceChildren(h("div",{class:"muted"},"Starting…")); return; }
  const rows = d.results.map(r=>h("div",{class:"dg"}, h("span",{class:"i "+r.status}, DGI[r.status]||"·"), h("span",{class:"tt"}, r.title), h("span",{class:"dd"}, r.detail)));
  if (d.running) rows.push(h("div",{class:"dg"}, h("span",{class:"spin"}), h("span",{class:"dd"},"Running…")));
  box.replaceChildren(...rows);
  $("#dgRun").disabled = S.job.running;
}
function diagText(){
  const d = S.diag; if (!d) return "";
  return "WoW Forever Addon Manager — diagnostics " + d.at + "\n" + d.results.map(r=>`[${r.status.toUpperCase()}] ${r.title}: ${r.detail}`).join("\n");
}

// ---------- characters ----------
let CH = null, chEdits = {};   // chEdits["i|folder"] = true/false
async function loadChars(){
  $("#chBox").replaceChildren(h("div",{class:"empty"},"Reading your WTF folder…"));
  try{ CH = await (await fetch("/api/characters")).json(); }catch(e){ CH = null; }
  chEdits = {};
  renderChars();
}
const chKey = (i, folder) => i + "|" + folder;
function chState(a, i){ const k = chKey(i, a.folder); return k in chEdits ? chEdits[k] : a.on[i]; }
function renderChars(){
  const box = $("#chBox"), n = Object.keys(chEdits).length;
  $("#chSave").hidden = $("#chDiscard").hidden = !n;
  $("#chSave").textContent = n ? `Save changes (${n})` : "Save changes";
  $("#chSave").disabled = !!(S && S.job.running);
  renderProfiles();
  if (!CH){ box.replaceChildren(h("div",{class:"empty"},"Couldn't read the WTF folder.")); return; }
  if (!CH.wtf_found || !CH.characters.length){ box.replaceChildren(h("div",{class:"empty"},"No characters found yet — they appear after you've logged in to them once.")); return; }
  const all = $("#chAll").checked;
  const rows = chRows(all);
  const head = h("tr",{}, h("th",{},"Addon"), ...CH.characters.map((c,i)=>h("th",{title:c.account+" · "+c.realm + (c.has_file?"":" (no AddOns.txt yet)")},
    h("span",{class:"cn"}, c.name), h("small",{}, c.realm), h("button",{class:"linkbtn",style:"font-size:11px;margin-top:2px",onclick:e=>openMenu(e.currentTarget, charMenu(i))},"options ▾"))));
  const body = rows.map(a=>h("tr",{}, h("td",{}, h("div",{style:"font-weight:600"}, a.title), h("div",{class:"dim",style:"font-size:12px"}, a.folder + (a.default==="disabled" ? " · off by default" : ""))),
    ...a.states.map((st,i)=>{
      const on = chState(a, i), changed = chKey(i,a.folder) in chEdits;
      const txt = changed ? (on?"✓":"✗") : (st==="enabled" ? "✓" : st==="disabled" ? "✗" : "○");
      return h("td",{class:"cell " + (changed||st ? (on?"on":"off") : "def") + (changed?" chg":""), title:(on?"on":"off")+" — click to switch", onclick:()=>{ const k=chKey(i,a.folder); const v=!on; if (v===a.on[i]) delete chEdits[k]; else chEdits[k]=v; renderChars(); }}, txt);
    })));
  const counts = h("tr",{}, h("td",{class:"dim"},"Switched on"), ...CH.characters.map((c,i)=>h("td",{class:"dim"}, String(rows.filter(a=>chState(a,i)).length)+"/"+rows.length)));
  box.replaceChildren(h("div",{class:"mxwrap"}, h("table",{class:"mx"}, h("thead",{}, head), h("tbody",{}, ...body, counts))));
}
function chRows(all){ return CH.addons.filter(a => all || a.addon === null || a.addon === undefined || !CH.addons.some(b => b !== a && b.addon && b.addon === a.addon && b.folder.length < a.folder.length)); }
function setChar(i, fn){ for (const a of CH.addons){ const v = fn(a); if (v===undefined) continue; const k = chKey(i,a.folder); if (v===a.on[i]) delete chEdits[k]; else chEdits[k]=v; } renderChars(); }
function charMenu(i){
  const others = CH.characters.map((c,j)=>j).filter(j=>j!==i);
  const profs = Object.keys((S && S.config.addon_profiles) || {});
  return [
    {head: CH.characters[i].name},
    {label:"Switch everything on", run:()=>setChar(i, ()=>true)},
    {label:"Switch everything off", run:()=>setChar(i, ()=>false)},
    others.length ? "-" : null,
    ...others.map(j=>({label:"Copy from "+CH.characters[j].name+" ("+CH.characters[j].realm+")", run:()=>setChar(i, a=>chState(a,j))})),
    profs.length ? "-" : null,
    ...profs.map(p=>({label:"Use profile “"+p+"”", run:()=>{ const st = S.config.addon_profiles[p]; setChar(i, a=> a.folder.toLowerCase() in st ? st[a.folder.toLowerCase()] : undefined); }})),
    "-",
    {label:"Save as profile…", run:async ()=>{ const name = await ask("Save as profile", "Saves which addons "+CH.characters[i].name+" has switched on, so you can use the same set on other characters.", "Save", ""); if (!name) return; const st = {}; for (const a of CH.addons) st[a.folder] = chState(a,i); act("/api/profile",{name, states:st}); }},
  ];
}
function renderProfiles(){
  const box = $("#profBox"), profs = Object.keys((S && S.config.addon_profiles) || {});
  if (!profs.length){ box.replaceChildren(h("div",{class:"dim",style:"font-size:12.5px"},"Tip: “options ▾” under a character saves its addon set as a profile, copies another character's set, or switches everything on or off.")); return; }
  box.replaceChildren(h("h2",{style:"margin-top:8px"},"Profiles"), h("div",{class:"card"}, ...profs.map(p=>{ const st = S.config.addon_profiles[p], n = Object.values(st).filter(Boolean).length;
    return h("div",{class:"bk"}, h("div",{class:"n"}, h("b",{},p), h("span",{class:"dim",style:"margin-left:8px;font-size:12px"}, n+" addons on")), h("button",{class:"btn small ghost danger",onclick:async ()=>{ if (await ask(`Delete profile “${p}”?`, "Characters keep their current addons.", "Delete")) act("/api/profile",{name:p, states:null}); }},"Delete")); })));
}
$("#chSave").onclick = async ()=>{
  const by = {};
  for (const [k,v] of Object.entries(chEdits)){ const [i, folder] = [k.slice(0,k.indexOf("|")), k.slice(k.indexOf("|")+1)]; (by[i] = by[i] || {})[folder] = v; }
  const changes = Object.entries(by).map(([i, states])=>Object.assign({}, CH.characters[+i], {states}));
  try{ await post("/api/characters_save",{changes}); chEdits = {}; setTimeout(loadChars, 900); }catch(e){ toast(e.message); }
};
$("#chDiscard").onclick = ()=>{ chEdits = {}; renderChars(); };

// ---------- navigation ----------
const TITLES = {installed:"My addons", browse:"Browse CurseForge", chars:"Characters", backups:"Backups"};
function showView(v){
  if (v==="settings"){ openSettings(); return; }
  closeSettings(true);
  view = v; closeMenu();
  document.querySelectorAll(".side .tab").forEach(t=>t.classList.toggle("on", t.dataset.v===v));
  $("#vTitle").textContent = TITLES[v]; $("#vSub").textContent = "";
  $("#vInstalled").hidden = v!=="installed"; $("#vBrowse").hidden = v!=="browse"; $("#vBackups").hidden = v!=="backups"; $("#vChars").hidden = v!=="chars";
  $("#scroll").scrollTop = 0;
  if (v==="chars") loadChars();
  if (v==="backups") renderBackups();
  if (v==="browse"){ loadCats(); if (!B.loaded && !B.loading) bSearch(false); else renderBrowse(); setTimeout(()=>$("#bq").focus(),0); }
  if (S) render();
}
function setDrawer(open){ drawerOpen=open; $("#drawer").classList.toggle("open",open); $("#dTog").textContent = open?"Hide activity ▼":"Show activity ▲"; if (open){ const lb=$("#logbox"); lb.scrollTop=lb.scrollHeight; } }

let timer=null;
async function refresh(soon){
  clearTimeout(timer);
  if (soon){ timer=setTimeout(refresh,250); return; }
  try{
    const r = await fetch("/api/state?since="+logSeq+"&etag="+(S && S.etag || ""));
    const j = await r.json();
    if (!j.same){ S = j; render(); if (view==="chars") renderChars(); }
    if (firstLoad){ firstLoad=false; if (!S.config.has_key || !S.config.addons_dir_ok) openSettings(true); }
  }catch(e){ $("#dLast").textContent = "Lost connection to the manager — is it still running?"; }
  timer = setTimeout(refresh, S && S.job.running ? 900 : (document.hidden ? 30000 : 5000));
}
document.addEventListener("visibilitychange", ()=>{ if (!document.hidden) refresh(); });

// ---------- settings (a page of its own) ----------
function openSettings(setup){
  const c = S.config;
  closeDetail(); closeMenu();
  document.querySelectorAll(".side .tab").forEach(t=>t.classList.toggle("on", t.dataset.v==="settings"));
  $("#setupSteps").hidden = !(setup===true);
  $("#sTitle").textContent = setup===true ? "Welcome — quick setup" : "Settings";
  $("#keyResult").style.display="none"; $("#wagoResult").style.display="none";
  $("#fWago").value = ""; $("#fWago").placeholder = c.has_wago_key ? "Saved" + (c.wago_key_hint?" (ends in "+c.wago_key_hint+")":"") + " — leave empty to keep" : "Paste your Wago key";
  $("#fUseWago").checked = c.use_wago !== false;
  $("#fKey").value = ""; $("#fKey").placeholder = c.has_key ? "Saved" + (c.key_hint?" (ends in "+c.key_hint+")":"") + " — leave empty to keep" : "Paste your API key";
  $("#fDir").value = c.addons_dir || "";
  const sel = $("#fType");
  if (c.version_type_id && ![...sel.options].some(o=>o.value==String(c.version_type_id))) sel.append(h("option",{value:c.version_type_id}, (c.version_type_name||"id "+c.version_type_id)));
  sel.value = c.version_type_id ? String(c.version_type_id) : "auto";
  $("#fChan").value = c.channel || "release";
  $("#fIf").value = c.interface_auto ? "" : (c.interface || "");
  $("#fIf").placeholder = c.interface_detected ? "Automatic — " + c.interface_detected : "Automatic — " + (c.interface || "16001");
  $("#fIfHelp").textContent = c.interface_detected
    ? "Read from the game itself, so it follows WoW Forever patches. Type a number only to override it."
    : "Read from the game once you've started WoW Forever. Until then " + (c.interface || "16001") + " is used.";
  $("#fDeps").checked = c.auto_dependencies !== false;
  $("#fAutoMan").checked = c.auto_manual !== false;
  $("#fDel").checked = c.delete_after_install !== false;
  $("#fGh").checked = c.use_github !== false;
  $("#fBk").checked = c.backup_wtf !== false;
  $("#fWin").checked = c.app_window !== false; $("#fBkN").value = c.backup_keep || 10;
  $("#hostBox").hidden = !c.hosted; $("#winBox").hidden = !!c.hosted;
  $("#fTray").checked = c.tray !== false; $("#fBgH").value = String(c.bg_check_hours ?? 3);
  $("#fBgUp").checked = c.bg_auto_update !== false; $("#fAuto").checked = !!c.autostart;
  $("#fDl").value = c.downloads_dir || ""; $("#fDl").placeholder = c.downloads_default || "";
  $("#fBkDir").value = c.backup_dir || ""; $("#fBkDir").placeholder = c.backup_dir_default || "";
  $("#selfRepo").replaceChildren(...(c.update_repo ? ["Program updates come from ", h("a",{href:"https://github.com/"+c.update_repo}, "github.com/"+c.update_repo), " (checked every 6 hours and when you click Check for updates). ",
    h("button",{class:"linkbtn",onclick:async ()=>{ try{ const r = await post("/api/check_self_update",{}); toast(r.update ? "A new version is ready — see the menu." : "You have the newest version."); }catch(e){ toast(e.message); } }},"Check now")] : []));
  $("#mSettings").classList.add("show"); $("#mSettings").scrollTop = 0;
  if (setup===true && !c.addons_dir) detect();
}
function closeSettings(silent){
  if (!$("#mSettings").classList.contains("show")) return;
  $("#mSettings").classList.remove("show");
  if (!silent) document.querySelectorAll(".side .tab").forEach(t=>t.classList.toggle("on", t.dataset.v===view));
}
async function detect(){
  const box = $("#dirChips"); box.replaceChildren(h("span",{class:"dim",style:"font-size:12px"},"Searching…"));
  try{
    const r = await post("/api/detect",{});
    box.replaceChildren();
    if (!r.dirs.length){ box.append(h("span",{class:"dim",style:"font-size:12px"},"No World of Warcraft folder found automatically — paste the path.")); return; }
    for (const d of r.dirs) box.append(h("button",{class:"chip",onclick:()=>{$("#fDir").value=d.path;}}, h("b",{},d.flavor), " ", d.path, d.forever?"":" (not Forever)"));
    const best = r.dirs.find(d=>d.flavor==="_forever_");
    if (best && !$("#fDir").value) $("#fDir").value = best.path;
  }catch(e){ box.replaceChildren(h("span",{class:"dim"},e.message)); }
}
async function loadTypes(){
  const key = $("#fKey").value.trim();
  try{
    if (key) await post("/api/settings",{api_key:key});
    const r = await post("/api/version_types",{});
    const sel = $("#fType"), cur = sel.value;
    sel.replaceChildren(h("option",{value:"auto"},"Automatic (WoW Forever)"), ...r.types.map(t=>h("option",{value:t.id}, t.name+"  (id "+t.id+")")));
    sel.value = [...sel.options].some(o=>o.value===cur) ? cur : "auto";
  }catch(e){ toast(e.message); }
}
async function saveSettings(){
  const sel = $("#fType");
  const body = {addons_dir:$("#fDir").value, channel:$("#fChan").value, interface:$("#fIf").value,
    version_type_id: sel.value==="auto" ? null : sel.value, version_type_name: sel.value==="auto" ? "" : sel.options[sel.selectedIndex].text.replace(/\s+\(id \d+\)$/,""),
    auto_dependencies:$("#fDeps").checked, auto_manual:$("#fAutoMan").checked,
    delete_after_install:$("#fDel").checked, downloads_dir:$("#fDl").value, use_github:$("#fGh").checked,
    backup_wtf:$("#fBk").checked, backup_keep:$("#fBkN").value, backup_dir:$("#fBkDir").value,
    use_wago:$("#fUseWago").checked, app_window:$("#fWin").checked};
  if (S.config && S.config.hosted) Object.assign(body, {tray:$("#fTray").checked, bg_check_hours:+$("#fBgH").value,
    bg_auto_update:$("#fBgUp").checked, autostart:$("#fAuto").checked});
  const wk = $("#fWago").value.trim(); if (wk) body.wago_key = wk;
  const key = $("#fKey").value.trim(); if (key) body.api_key = key;
  try{ await post("/api/settings", body); closeSettings(); }
  catch(e){ toast(e.message); }
}

// ---------- add addons ----------
function openAdd(){ $("#mAdd").classList.add("show"); setTimeout(()=>$("#links").focus(),0); }
function closeAdd(){ $("#mAdd").classList.remove("show"); }
function pickZip(key){ zipKey = key || null; $("#file").value=""; $("#file").click(); }
$("#file").addEventListener("change", ()=>{
  const f = $("#file").files[0]; if (!f) return;
  const hdr = {"X-Filename": encodeURIComponent(f.name)}; if (zipKey) hdr["X-Mod-Key"] = zipKey;
  act("/api/install_zip", f, hdr);
});

$("#bAddOpen").onclick = openAdd;
$("#addCancel").onclick = closeAdd;
$("#mAdd").addEventListener("click", e=>{ if (e.target.id==="mAdd") closeAdd(); });
$("#toBrowse").onclick = e=>{ e.preventDefault(); closeAdd(); showView("browse"); };
$("#bAdd").onclick = ()=>{ const t=$("#links").value.trim(); if(!t){ $("#links").focus(); return; } act("/api/add",{text:t}).then(r=>{ if (r){ $("#links").value=""; closeAdd(); } }); };
$("#links").addEventListener("keydown", e=>{ if (e.key==="Enter" && (e.ctrlKey||e.metaKey)) $("#bAdd").click(); });
$("#bMoreIns").onclick = e=>openMenu(e.currentTarget, [
  S && S.last_batch ? {label:"Show the last update ("+S.last_batch.items.length+")", run:showBatch} : null,
  S && S.last_batch ? {label:"Undo the last update", disabled:S.job.running, run:()=>$("#btUndo").onclick()} : null,
  S && S.last_batch ? "-" : null,
  {label:"Install from a zip file…", disabled:S && S.job.running, run:()=>pickZip(null)},
  {label:"Share / move my list…", run:openShare},
  {label:"Import a list file…", run:()=>{ $("#importFile").value=""; $("#importFile").click(); }},
  {label:"Export my list", href:"/api/export", download:"wow-forever-addons.json"},
  "-",
  {label:"Look up hand-installed addons", disabled:S && S.job.running, run:()=>act("/api/scan")},
  {label:"Open the AddOns folder", run:()=>act("/api/open_folder")},
]);
$("#bCheck").onclick = ()=>act("/api/check");
$("#bUpdAll").onclick = ()=>act("/api/update_all");
$("#bCancel").onclick = ()=>closeSettings();
$("#bSave").onclick = saveSettings;
$("#bDetect").onclick = detect;
$("#bTypes").onclick = loadTypes;
$("#bTestWago").onclick = async ()=>{
  const out = $("#wagoResult"); out.style.display="block"; out.style.color="var(--muted)"; out.textContent="Testing…";
  try{ const r = await post("/api/test_wago",{wago_key:$("#fWago").value}); out.style.color = r.ok ? "var(--green)" : "var(--red)"; out.textContent = (r.ok?"✔ ":"✖ ") + r.message; }
  catch(e){ out.style.color="var(--red)"; out.textContent="✖ "+e.message; }
};
$("#bTestKey").onclick = async ()=>{
  const out = $("#keyResult"); out.style.display="block"; out.style.color="var(--muted)"; out.textContent="Testing…";
  try{ const r = await post("/api/test_key",{api_key:$("#fKey").value}); out.style.color = r.ok ? "var(--green)" : "var(--red)"; out.textContent = (r.ok?"✔ ":"✖ ") + r.message; }
  catch(e){ out.style.color="var(--red)"; out.textContent="✖ "+e.message; }
};
$("#dbar").onclick = ()=>setDrawer(!drawerOpen);
document.querySelectorAll(".side .tab").forEach(t=>t.onclick=()=>showView(t.dataset.v));
let bTimer=null;
$("#bq").addEventListener("input", ()=>{ clearTimeout(bTimer); bTimer=setTimeout(()=>{ B.q=$("#bq").value.trim(); bSearch(false); }, 350); });
$("#wcat").addEventListener("change", ()=>{ W.cat = $("#wcat").value; renderBrowse(); });
$("#wsort").addEventListener("change", ()=>{ W.sort = $("#wsort").value; renderBrowse(); });
$("#bq").addEventListener("keydown", e=>{ if (e.key==="Enter"){ clearTimeout(bTimer); B.q=$("#bq").value.trim(); bSearch(false); } });
$("#bsort").onchange = ()=>{ B.sort=$("#bsort").value; bSearch(false); };
$("#bcat").onchange = ()=>{ B.cat=$("#bcat").value; bSearch(false); };
$("#bMoreBtn").onclick = ()=>bSearch(true);
document.querySelectorAll(".srcsw button").forEach(b=>b.onclick=()=>{ if (B.src===b.dataset.src) return; B.src=b.dataset.src; B.results=[]; B.loaded=false; B.q=$("#bq").value.trim(); bSearch(false); $("#bq").focus(); });
$("#bBackupNow").onclick = ()=>act("/api/backup_now");
$("#chReload").onclick = loadChars;
$("#chAll").onchange = renderChars;
$("#bShortcut").onclick = ()=>act("/api/shortcuts");
window.addEventListener("pagehide", ()=>{ try{ navigator.sendBeacon("/api/bye", ""); }catch(e){} });
// In the desktop program there is no browser around the page: outside links open in your web browser.
document.addEventListener("click", e=>{
  const a = e.target && e.target.closest && e.target.closest("a[href]");
  if (!a) return;
  let u; try { u = new URL(a.href, location.href); } catch(_) { return; }
  if (u.origin === location.origin || !/^https?:$/.test(u.protocol)) return;
  if (window.chrome && window.chrome.webview){ e.preventDefault(); post("/api/open_url", {url:u.href}).catch(()=>{}); }
}, true);
$("#bScan").onclick = ()=>act("/api/scan");
async function openShare(){
  $("#shCode").value = "…"; $("#shIn").value = ""; $("#shCount").textContent = "";
  $("#mShare").classList.add("show");
  try{ const r = await post("/api/share_code",{}); $("#shCode").value = r.code; $("#shCount").textContent = r.count + " addon" + (r.count===1?"":"s"); }
  catch(e){ $("#shCode").value = e.message; }
}
$("#shCopy").onclick = async ()=>{ const t = $("#shCode").value; try{ await navigator.clipboard.writeText(t); }catch(e){ $("#shCode").select(); document.execCommand("copy"); } $("#shCopy").textContent = "Copied ✓"; setTimeout(()=>$("#shCopy").textContent="Copy code", 2000); };
$("#shClose").onclick = ()=>$("#mShare").classList.remove("show");
$("#mShare").addEventListener("click", e=>{ if (e.target.id==="mShare") $("#mShare").classList.remove("show"); });
$("#shGo").onclick = ()=>{ const t = $("#shIn").value.trim(); if (!t) return; $("#mShare").classList.remove("show"); act("/api/import_text",{text:t}); };
$("#importFile").addEventListener("change", ()=>{ const f=$("#importFile").files[0]; if (f) act("/api/import", f); });
$("#clClose").onclick = ()=>$("#mLog").classList.remove("show");
$("#mLog").addEventListener("click", e=>{ if (e.target.id==="mLog") $("#mLog").classList.remove("show"); });
$("#bDiag").onclick = ()=>{ $("#mDiag").classList.add("show"); renderDiag(); if (!S.diag || !S.diag.running) act("/api/diagnose"); };
$("#dgRun").onclick = ()=>act("/api/diagnose");
$("#dgClose").onclick = ()=>$("#mDiag").classList.remove("show");
$("#dgCopy").onclick = async ()=>{ const t = diagText(); try{ await navigator.clipboard.writeText(t); $("#dgCopy").textContent="Copied ✓"; }catch(e){ const ta=h("textarea",{style:"position:fixed;opacity:0"}); ta.value=t; document.body.append(ta); ta.select(); document.execCommand("copy"); ta.remove(); $("#dgCopy").textContent="Copied ✓"; } setTimeout(()=>$("#dgCopy").textContent="Copy report",2000); };
$("#q").addEventListener("input", render);
document.querySelectorAll(".stat").forEach(e=>e.onclick=()=>{ filter = (filter===e.dataset.f && filter!=="all") ? "all" : e.dataset.f; render(); });
// A new program version is offered once (Update now / Later). It is never installed without asking.
let selfOffered = "";
async function offerSelfUpdate(u){
  if (selfOffered === u.version || S.config.self_update_dismissed === u.version || S.job.running || document.querySelector(".modal.show")) return;
  selfOffered = u.version;
  const yes = await ask("WoW Forever Addons " + u.version + " is available", (u.notes ? u.notes.slice(0,700) + "\n\n" : "") + "Update now? The program closes, updates itself and opens again. Your addons and settings stay as they are.\n\nChoose Later to keep this version — you can update any time from the box in the menu.", "Update now", undefined, "Later");
  if (yes) act("/api/self_update");
  else { try{ await post("/api/settings",{self_update_dismissed:u.version}); }catch(e){} }
}
async function selfCheck(){
  const b = $("#bSelfChk"); b.disabled = true; b.textContent = "Checking…";
  try{
    const r = await post("/api/check_self_update",{});
    if (r.update){ await refresh(); const u = S.self_update;
      if (u && await ask("Update WoW Forever Addons to " + u.version + "?", (u.notes ? u.notes.slice(0,700) + "\n\n" : "") + "The program closes, updates itself and opens again. Your addons and settings stay as they are.", "Update now")) act("/api/self_update"); }
    else toast("You have the newest version (" + r.current + ").");
  }catch(e){ toast(e.message); }
  b.disabled = false; b.textContent = "Check for program update";
}
$("#bSelfChk").onclick = selfCheck;
$("#bQuit").onclick = async ()=>{ if(!(await ask("Stop the addon manager?", "Your addons stay installed. Start it again any time from the desktop or Start menu shortcut.", "Quit"))) return; try{ await post("/api/quit",{}); }catch(e){} clearTimeout(timer); document.body.replaceChildren(h("div",{class:"empty",style:"padding-top:20vh"}, h("b",{},"The addon manager has stopped."), h("br"), "You can close this window. Start it again from the desktop or Start menu shortcut.")); };
// keyboard: Esc closes the top-most thing, Ctrl+F searches
document.addEventListener("keydown", e=>{
  if (e.key==="Escape"){
    if (!$("#pop").hidden) return closeMenu();
    if ($("#lightbox").classList.contains("show")) return $("#lightbox").classList.remove("show");
    for (const id of ["#mAsk","#mLog","#mDiag","#mAdd","#mBatch"]) if ($(id).classList.contains("show")){ if (id==="#mAsk") $("#askNo").click(); else $(id).classList.remove("show"); return; }
    if (DT.id) return closeDetail();
    if ($("#mSettings").classList.contains("show") && S.config.has_key && S.config.addons_dir_ok) return closeSettings();
  }
  if ((e.ctrlKey||e.metaKey) && e.key.toLowerCase()==="f"){ e.preventDefault(); (view==="browse" ? $("#bq") : $("#q")).focus(); }
});
document.addEventListener("contextmenu", e=>{ if (!e.target.closest("input,textarea,.desc,.cl,.logbox")) e.preventDefault(); });
refresh();
</script>
</body>
</html>
"""


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def cli_list(m: Manager):
    snap = m.snapshot()
    if not snap["managed"]:
        print("No managed addons.")
    for a in snap["managed"]:
        print(f"{a['name']:<32} {a['installed_version'] or '-':<28} {a['status']}")
    if snap["unmanaged"]:
        print(f"\n{len(snap['unmanaged'])} other folder group(s) in AddOns: " +
              ", ".join(g["title"] for g in snap["unmanaged"]))


def main(argv=None):
    ap = argparse.ArgumentParser(description=APP_NAME)
    ap.add_argument("--list", action="store_true", help="list installed addons")
    ap.add_argument("--check", action="store_true", help="check for updates")
    ap.add_argument("--update-all", action="store_true", help="check and install all updates")
    ap.add_argument("--add", nargs="+", metavar="URL", help="install addons from CurseForge links")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--no-browser", action="store_true", help="don't open the browser")
    ap.add_argument("--no-idle-exit", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--ready-file", metavar="FILE", help=argparse.SUPPRESS)   # used by the desktop app window
    ap.add_argument("--host-pid", type=int, default=0, help=argparse.SUPPRESS)  # the desktop program's process
    ap.add_argument("--write-icon", metavar="FILE", help=argparse.SUPPRESS)
    ap.add_argument("--cleanup", action="store_true", help=argparse.SUPPRESS)   # used by Uninstall.cmd
    args = ap.parse_args(argv)
    if args.write_icon:
        Path(args.write_icon).write_bytes(make_ico())
        return 0
    if args.cleanup:
        for p in Manager(Store(), echo=True).cleanup_leftovers(min_age=0):
            print("removed", p)
        return 0

    cli = args.list or args.check or args.update_all or args.add
    store = Store()
    m = Manager(store, echo=bool(cli))
    if cli:
        ok = True
        if args.add:
            ok &= m.run_job("Installing addons", m.op_add, "\n".join(args.add), background=False)
        if args.update_all:
            ok &= m.run_job("Updating all addons", m.op_check_and_update, background=False)
        elif args.check:
            ok &= m.run_job("Checking for updates", m.op_check, background=False)
        if args.list:
            if store.config.get("api_key") and store.addons and not (args.update_all or args.check or args.add):
                m.run_job("Loading addon info", m.op_check, background=False)
            cli_list(m)
        return 0 if ok else 1
    App(m).serve(args.port, open_browser=not args.no_browser, idle_exit=not args.no_idle_exit,
                 ready_file=args.ready_file, host_pid=args.host_pid)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:
        msg = traceback.format_exc()
        try:
            (data_dir() / "error.log").write_text(msg, "utf-8")
        except Exception:
            pass
        if os.name == "nt" and (sys.stdout is None or "pythonw" in sys.executable.lower()):
            try:
                import ctypes
                ctypes.windll.user32.MessageBoxW(None, msg[-1500:], APP_NAME + " — error", 0x10)
            except Exception:
                pass
        else:
            print(msg, file=sys.stderr)
        sys.exit(1)
