"""Constants, defaults and the small rules about keys and sources."""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from .errors import UserError

APP_NAME = "WoW Forever Addon Manager"
APP_VERSION = "3.18.0"
API_BASE = os.environ.get("WFAM_API_BASE", "https://api.curseforge.com").rstrip("/")
GITHUB_API = os.environ.get("WFAM_GITHUB_API", "https://api.github.com").rstrip("/")
TUKUI_API = os.environ.get("WFAM_TUKUI_API", "https://api.tukui.org/v1").rstrip("/")   # ElvUI and Tukui
WAGO_DATA = os.environ.get("WFAM_WAGO_DATA", "https://data.wago.io").rstrip("/")   # wago.io (WeakAuras)
COMPANION_FOLDER = "ForeverAurasCompanion"     # ForeverAuras picks up auras waiting to be imported from here
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
    "backup_keep": 3,
    "auto_check": True,           # check for updates when the manager opens (if the last check is >30 min old)
    "wago_key": "",               # personal Wago Addons key (Wago Patreon) — enables Wago as a source
    "use_wago": True,
    "wago_game_version": "forever",
    "app_window": True,           # open in its own window (Edge/Chrome app mode) instead of a browser tab
    # Desktop program ("WoW Forever Addons.exe") only:
    "tray": True,                 # closing the window keeps the manager running in the system tray
    "bg_check_hours": 3,          # check for updates in the background every N hours (0 = never)
    "bg_auto_update": True,       # install the updates it finds (otherwise just show a notification)
    "ui_scale": "100",            # window size of text: "100" = real 100 % whatever Windows' scaling is
    "self_update_dismissed": "",  # program version the user chose "Later" for (not asked again)
}
BG_CHECK_CHOICES = (0, 1, 3, 6, 12, 24)
SELF_UPDATE_EVERY = float(os.environ.get("WFAM_SELF_UPDATE_EVERY") or 6 * 3600)
UPDATE_REPO = "Hexicon7/Wow-forever-addons"   # owner/repo on GitHub where new versions of this program are published
KEEP_PREVIOUS_VERSIONS = 1     # one earlier version per addon (enough for Revert / Undo, and light on disk)
WAGO_KEY_PREFIX = "wago-"      # addons that are only on Wago are stored under "wago-<Wago id>"


def is_wago_key(key: str) -> bool:
    return str(key).startswith(WAGO_KEY_PREFIX)


TUKUI_KEY_PREFIX = "tukui-"     # ElvUI / Tukui from tukui.org: "tukui-elvui", "tukui-tukui"


def is_tukui_key(key: str) -> bool:
    return str(key).startswith(TUKUI_KEY_PREFIX)


def own_source_key(key: str) -> bool:
    """Addons that don't come from CurseForge at all (their data isn't CurseForge's)."""
    return is_wago_key(key) or is_tukui_key(key)


def wago_key(wago_id: str) -> str:
    wid = str(wago_id or "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", wid):
        raise UserError("That doesn't look like a Wago addon.")
    return WAGO_KEY_PREFIX + wid
# Fields that hold data from the CurseForge API: kept in memory only, never written to disk.
CF_FIELDS = ("name", "slug", "url", "summary", "authors", "logo", "library", "distribution_disabled", "latest_file_id",
             "latest_version", "latest_date", "latest_sha1", "latest_release_type", "latest_checked_at", "awaiting",
             "cf_latest", "issues_url")
CF_INSTALLED_FIELDS = ("installed_version", "installed_file_name", "installed_file_date", "installed_release_type")
AWAIT_SECONDS = 30 * 60


def entry_script() -> Path:
    """The file that started the program: WoWForeverAddons.pyw, next to the desktop program and icon."""
    main = sys.modules.get("__main__")
    f = os.environ.get("WFAM_SCRIPT") or getattr(main, "__file__", None) or sys.argv[0]
    return Path(f).resolve()
