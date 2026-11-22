"""Previous versions (Revert) and changelogs."""
from __future__ import annotations

import os
import time
import zipfile
from pathlib import Path

from ..addonfiles import _ADDONS_TXT_RE, install_zip_into, is_library
from ..config import KEEP_PREVIOUS_VERSIONS, is_tukui_key, is_wago_key
from ..errors import NotFound, UserError
from ..net import run_parallel
from ..sources import GitHub, wago_categories
from ..system import game_running
from ..util import html_to_text, now_iso, safe_folder_name


class VersionsMixin:
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
        if is_tukui_key(key):
            tl = rec.get("tukui_latest") or {}
            return {"title": f"{rec['name']} {tl.get('version') or rec.get('installed_version') or ''}",
                    "text": "Tukui.org keeps the changelog on its own page.",
                    "url": tl.get("changelog_url") or rec.get("url")}
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
