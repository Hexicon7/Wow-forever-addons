"""Adding addons: links, ids and folders already on disk."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from ..addonfiles import (
    folder_fingerprints,
    install_token,
    local_signature,
    pick_primary,
    read_toc,
)
from ..errors import UserError
from ..net import run_parallel
from ..sources import parse_ref, split_refs
from ..util import chunks, file_sha1, norm_version

if TYPE_CHECKING:
    from ..sources import CurseForge


class AddingMixin:
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
