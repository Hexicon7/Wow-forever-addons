"""The addon sites: CurseForge, GitHub, tukui.org, Wago (addons) and wago.io (WeakAuras)."""
from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.parse
from datetime import datetime
from pathlib import Path

from .config import (
    API_BASE,
    CHANNELS,
    FOREVER_FLAVORS,
    GITHUB_API,
    TUKUI_API,
    USER_AGENT,
    WAGO_BASE,
    WAGO_DATA,
    WOW_GAME_ID,
)
from .errors import CurseForgeError, DistributionDisabled, NetError, NotFound, UserError
from .net import http_call
from .util import chunks, clean_key


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


class TukuiOrg:
    """tukui.org's public addon API (ElvUI and Tukui). No key."""
    _cache: dict = {}

    @staticmethod
    def _host_ok(url: str) -> bool:
        u = urllib.parse.urlparse(url)
        host = (u.hostname or "").lower()
        own = (urllib.parse.urlparse(TUKUI_API).hostname or "").lower()
        if host == own and u.scheme in ("https", urllib.parse.urlparse(TUKUI_API).scheme):
            return True
        return u.scheme == "https" and (host == "tukui.org" or host.endswith(".tukui.org") or host in (
            "github.com", "codeload.github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com"))

    def _get(self, path: str):
        try:
            code, _h, body = http_call("GET", TUKUI_API + path, {"Accept": "application/json", "User-Agent": USER_AGENT},
                                       timeout=30, max_bytes=4 << 20)
        except NetError as e:
            raise UserError(f"couldn't reach tukui.org ({e.reason})")
        if code != 200:
            raise UserError(f"tukui.org returned HTTP {code}")
        try:
            return json.loads(body.decode("utf-8"))
        except ValueError:
            raise UserError("tukui.org sent something that isn't JSON")

    def addons(self) -> list[dict]:
        hit = TukuiOrg._cache.get("all")
        if hit and time.time() - hit[0] < 600:
            return hit[1]
        r = self._get("/addons")
        data = r if isinstance(r, list) else (r.get("data") or r.get("addons") or [] if isinstance(r, dict) else [])
        data = [a for a in data if isinstance(a, dict) and re.fullmatch(r"[A-Za-z0-9_-]{1,40}", str(a.get("slug") or ""))]
        for a in data:
            a["slug"] = str(a["slug"]).lower()
        TukuiOrg._cache["all"] = (time.time(), data)
        return data

    def addon(self, slug: str) -> dict:
        for a in self.addons():
            if a["slug"] == slug:
                return a
        raise UserError(f"tukui.org doesn't list “{slug}”")

    def download(self, url: str, dest: Path) -> str:
        if not url or not self._host_ok(url):
            raise UserError("tukui.org gave a download link outside tukui.org/GitHub — not downloading it")
        h = hashlib.sha1()
        try:
            code, _h, _b = http_call("GET", url, {"User-Agent": USER_AGENT}, dest=dest, hashers=(h,), timeout=120)
        except NetError as e:
            raise UserError(f"download from tukui.org failed ({e.reason})")
        if code != 200:
            raise UserError(f"tukui.org download returned HTTP {code}")
        return h.hexdigest()


# Wago's WeakAuras categories that exist for ForeverAuras (id -> name), from wago.io's own category list.
AURA_CATEGORIES = [
    ("Classes", [("cl11", "Druid"), ("cl3", "Hunter"), ("cl8", "Mage"), ("cl2", "Paladin"), ("cl5", "Priest"),
                 ("cl4", "Rogue"), ("cl7", "Shaman"), ("cl9", "Warlock"), ("cl1", "Warrior")]),
    ("Roles", [("role1", "Damage Dealing"), ("role2", "Healing"), ("role3", "Tanking"), ("role4", "Raid Leading")]),
    ("General", [("gen0", "General"), ("gen2", "Questing"), ("gen6", "Non-Combat"), ("gen10", "Reputation")]),
    ("Professions", [("prof1", "Gathering"), ("prof5", "Crafting"), ("prof14", "Secondary")]),
]
AURA_CAT_NAMES = {cid: name for _g, items in AURA_CATEGORIES for cid, name in items}
AURA_SORTS = ("installs", "date", "stars", "views", "bestmatchv3")


class WagoAuras:
    """wago.io's public WeakAuras API, limited to ForeverAuras (WoW Forever's WeakAuras).
    No key is needed or sent."""

    def _get(self, path: str, params: dict, raw: bool = False):
        url = f"{WAGO_DATA}{path}?{urllib.parse.urlencode(params)}"
        try:
            code, _h, body = http_call("GET", url, {"Accept": "text/plain" if raw else "application/json",
                                                    "User-Agent": USER_AGENT}, timeout=30, max_bytes=8 << 20)
        except NetError as e:
            raise UserError(f"couldn't reach wago.io ({e.reason})")
        if code == 404:
            raise UserError("that aura isn't on wago.io any more")
        if code in (401, 403):
            raise UserError("that aura is private on wago.io")
        if code != 200:
            raise UserError(f"wago.io returned HTTP {code}")
        if raw:
            return body.decode("utf-8", "replace").strip()
        try:
            return json.loads(body.decode("utf-8"))
        except ValueError:
            raise UserError("wago.io sent something that isn't JSON")

    def search(self, q: str, category: str, sort: str, page: int) -> dict:
        text = (q or "").strip()[:100]
        if category in AURA_CAT_NAMES:
            text = f"{text} category:{category}".strip()
        return self._get("/search/es", {"q": text, "expansion": "forever", "type": "weakaura",
                                        "page": max(0, min(int(page), 50)),
                                        "sort": sort if sort in AURA_SORTS else "installs"})

    def details(self, aura_id: str) -> dict:
        return self._get("/lookup/wago", {"id": aura_id})

    def encoded(self, aura_id: str, version: str | None = None) -> str:
        params = {"id": aura_id}
        if version:
            params["version"] = version
        return self._get("/api/raw/encoded", params, raw=True)


def lua_str(s: str) -> str:
    out = []
    for ch in str(s):
        o = ord(ch)
        if ch in ('"', "\\"):
            out.append("\\" + ch)
        elif ch == "\n":
            out.append("\\n")
        elif o < 32 or o == 127:
            out.append("\\%03d" % o)
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


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
