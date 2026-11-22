"""Recognising hand-installed addons, and per-character addon lists."""
from __future__ import annotations

import hashlib
import re
import threading
import traceback
from pathlib import Path

from ..addonfiles import (
    folder_fingerprints,
    folder_signature,
    parse_addons_txt,
    pick_primary,
    read_toc,
)
from ..config import COMPANION_FOLDER
from ..errors import UserError
from ..sources import Wago, file_type_ids
from ..util import exact_name, file_sha1, now_iso


class LocalFoldersMixin:
    def unmanaged_folders(self) -> list[Path]:
        d = self.store.config.get("addons_dir")
        if not d or not Path(d).is_dir():
            return []
        owned = {f for r in self.store.addons.values() for f in (r.get("folders") or [])} | {COMPANION_FOLDER}
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
            self._scan_wago(folders, {f for v in found.values() for f in v["folders"]})
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
        n_wago = self._scan_wago(folders, {f for v in found.values() for f in v["folders"]})
        if not quiet:
            self.log(f"Recognised {len(found)} addon(s) ({n_folders} of {len(folders)} unmanaged folders) on CurseForge"
                     + (f", {n_wago} more on Wago" if n_wago else "") + ".", "ok")
        return len(found), len(folders)

    def _scan_wago(self, folders: list[Path], known: set[str]) -> int:
        """Folders CurseForge didn't recognise: look them up on Wago by name (once per folder change)."""
        wg = self.wago()
        if not wg:
            return 0
        with self.store.lock:
            rec = self.store.state.setdefault("wago_recognized", {})
            seen = self.store.state.setdefault("wago_seen", {})
        present = {f.name for f in folders}
        for name in [n for n in list(rec) if n not in present]:
            rec.pop(name, None)
        asked = 0
        for f in folders:
            if f.name in known or f.name in rec:
                continue
            sig = folder_signature(f)
            if seen.get(f.name) == sig or asked >= 15:
                continue
            seen[f.name] = sig
            t = read_toc(f)
            title = t.get("title") or f.name
            if not t or re.match(r"^(Lib[A-Z0-9-]|Ace3\b)", f.name):
                continue                                   # libraries come with the addons that use them
            asked += 1
            try:
                hits = wg.search(title)[:10]
                hit = next((a for a in hits if exact_name(a.get("display_name") or a.get("name"), title)
                            or (a.get("slug") or "").lower() == f.name.lower()), None)
                if not hit:
                    continue
                rel = Wago.pick_release(wg.addon(hit["id"]), self.store.config.get("channel", "release"))
                if rel:
                    rec[f.name] = {"id": str(hit["id"]), "name": hit.get("display_name") or hit.get("name"),
                                   "version": rel.get("label")}
            except UserError:
                continue
        with self.store.lock:
            self.store.save_state()
        return sum(1 for v in rec.values() if v)

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
