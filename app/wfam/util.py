"""Small helpers: files, JSON, versions, text."""
from __future__ import annotations

import html as htmlmod
import json
import os
import re
import shutil
import stat
import sys
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

from .config import API_BASE, KEY_HOSTS


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


def key_kind(key: str) -> str:
    """Which site a pasted key belongs to. CurseForge keys look like "$2a$10$…" (60 characters);
    anything else is treated as a Wago key — and each is only ever sent to its own site."""
    return "curseforge" if key.startswith("$2") else "wago"


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


def exact_name(a: str | None, b: str | None) -> bool:
    na, nb = re.sub(r"[^a-z0-9]", "", (a or "").lower()), re.sub(r"[^a-z0-9]", "", (b or "").lower())
    return bool(na) and na == nb


def same_name(a: str | None, b: str | None) -> bool:
    na, nb = re.sub(r"[^a-z0-9]", "", (a or "").lower()), re.sub(r"[^a-z0-9]", "", (b or "").lower())
    return bool(na and nb) and (na == nb or (len(na) > 4 and len(nb) > 4 and (na in nb or nb in na)))


def file_sha1(f: dict) -> str | None:
    for h in f.get("hashes") or []:
        if h.get("algo") == 1 and h.get("value"):
            return h["value"]
    return None


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
