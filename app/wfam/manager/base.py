"""The core: logging, background jobs, prerequisites, addon info and installing files."""
from __future__ import annotations

import collections
import re
import tempfile
import threading
import time
import traceback
import webbrowser
from pathlib import Path
from typing import TYPE_CHECKING

from ..addonfiles import install_zip_into, is_library, pick_primary, read_toc
from ..config import RELEASE_TYPES, REQUIRED_DEPENDENCY, WOW_GAME_ID
from ..errors import BusyError, DistributionDisabled, NotFound, UserError
from ..sources import CurseForge, file_type_ids, github_repo_of, pick_latest
from ..util import file_sha1, now_iso, rmtree

if TYPE_CHECKING:
    from ..store import Store


class ManagerBase:
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
            "issues_url": ((mod.get("links") or {}).get("issuesUrl") or "").strip(),
        })
        repo = github_repo_of(mod)
        if repo and not rec.get("github"):
            rec["github"] = repo            # GitHub as a third place to look for a newer version

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
        # kept apart too: for an addon installed from GitHub, latest_* describe GitHub's release
        rec["cf_latest"] = {"version": rec["latest_version"], "file_id": f["id"], "date": f.get("fileDate")}

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
            for k in ("gh_installed", "gh_latest", "wago_installed", "gh_alt_installed"):
                rec.pop(k, None)
            if not rec.get("latest_file_id") or f["id"] >= rec["latest_file_id"]:
                self._set_latest(rec, f)
            self.store.save_state()
        n = len(analysis["folders"])
        self.log(f"Installed {mod['name']} {version} ({n} folder{'s' if n != 1 else ''}).", "ok")
        return [d["modId"] for d in (f.get("dependencies") or [])
                if d.get("relationType") == REQUIRED_DEPENDENCY and d.get("modId")]
