"""Everything about addon files on disk: .toc files, zips, folder fingerprints, AddOns.txt and
SavedVariables (BugGrabber)."""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import struct
import tempfile
import time
import zipfile
from pathlib import Path

from .config import MAX_UNPACKED_BYTES
from .errors import InstallError
from .util import rmtree, safe_folder_name

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
        "optdeps": [x.strip() for x in (meta.get("optionaldeps") or "").split(",") if x.strip()][:40],
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


# -- reading SavedVariables (BugGrabber's error list) ------------------------------- #
_LUA_TOK = re.compile(r'\s+|--\[(=*)\[.*?\]\1\]|--[^\n]*|"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'|\[\[.*?\]\]'
                      r'|-?0x[0-9a-fA-F]+|-?\d+\.?\d*(?:[eE][-+]?\d+)?|[A-Za-z_][\w.]*|[{}\[\]=,;]', re.S)
_LUA_ESC = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", '"': '"', "'": "'", "a": "\a", "b": "\b", "f": "\f", "v": "\v", "\n": "\n"}


def _lua_unquote(t: str) -> str:
    if t.startswith("[["):
        return t[2:-2]
    body = t[1:-1]
    return re.sub(r'\\(\d{1,3}|.)', lambda m: chr(int(m.group(1)) % 256) if m.group(1).isdigit()
                  else _LUA_ESC.get(m.group(1), m.group(1)), body, flags=re.S)


def parse_lua_sv(text: str) -> dict:
    """The small part of Lua that SavedVariables files use: NAME = value, with tables, strings, numbers,
    true/false/nil. Anything it can't read ends the parse; what was read so far is returned."""
    toks = [m.group(0) for m in _LUA_TOK.finditer(text) if not m.group(0).isspace() and not m.group(0).startswith("--")]
    pos = 0

    def val():
        nonlocal pos
        t = toks[pos]; pos += 1
        if t == "{":
            tbl, arr = {}, []
            while toks[pos] != "}":
                if toks[pos] == "[":
                    pos += 1; k = val(); pos += 1          # ]
                    pos += 1                               # =
                    tbl[k] = val()
                elif pos + 1 < len(toks) and toks[pos + 1] == "=" and re.fullmatch(r"[A-Za-z_]\w*", toks[pos]):
                    k = toks[pos]; pos += 2; tbl[k] = val()
                else:
                    arr.append(val())
                if toks[pos] in (",", ";"):
                    pos += 1
            pos += 1
            if arr and not tbl:
                return arr
            for n, v in enumerate(arr, 1):
                tbl.setdefault(n, v)
            return tbl
        if t[0] in "\"'" or t.startswith("[["):
            return _lua_unquote(t)
        if t in ("true", "false"):
            return t == "true"
        if t == "nil":
            return None
        try:
            return int(t, 0) if re.fullmatch(r"-?(0x[0-9a-fA-F]+|\d+)", t) else float(t)
        except ValueError:
            raise ValueError(f"unexpected {t[:20]}")

    out = {}
    try:
        while pos < len(toks):
            name = toks[pos]
            if toks[pos + 1] != "=":
                break
            pos += 2
            out[name] = val()
    except (IndexError, ValueError):
        pass
    return out


_ERR_PATH = re.compile(r'(?:Interface[\\/]+Add[Oo]ns[\\/]+)?([^\\/:\s"\'\[\]()<>]+)[\\/][^:\n"\]]*?\.(?:lua|xml)\b', re.I)


def bug_errors(wtf: Path | None, folders: set, days: int = 14) -> dict:
    """Lua errors BugGrabber saved, per AddOns folder (lower-case): newest first, last `days` days only."""
    out: dict[str, list] = {}
    if not wtf or not (wtf / "Account").is_dir():
        return out
    cutoff = time.time() - days * 86400
    for f in (wtf / "Account").glob("*/SavedVariables/!BugGrabber.lua"):
        try:
            if f.stat().st_size > 8_000_000:
                continue
            db = parse_lua_sv(f.read_text("utf-8", errors="replace")).get("BugGrabberDB") or {}
        except OSError:
            continue
        errs = db.get("errors") or []
        if isinstance(errs, dict):
            errs = [errs[k] for k in sorted(k for k in errs if isinstance(k, int))]
        for e in errs:
            if not isinstance(e, dict):
                continue
            msg, stack = str(e.get("message") or ""), str(e.get("stack") or "")
            try:
                ts = time.mktime(time.strptime(str(e.get("time") or ""), "%Y/%m/%d %H:%M:%S"))
            except (ValueError, OverflowError):
                ts = 0
            if ts and ts < cutoff:
                continue
            folder = next((m.group(1).lower() for m in _ERR_PATH.finditer(msg + "\n" + stack)
                           if m.group(1).lower() in folders), None)
            if not folder:
                continue
            n = e.get("counter")
            out.setdefault(folder, []).append({"message": msg[:600], "stack": stack[:1500], "time": ts,
                                               "count": n if isinstance(n, int) and n > 0 else 1})
    for v in out.values():
        v.sort(key=lambda x: -x["time"])
    return out
