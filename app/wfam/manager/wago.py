"""Wago Addons: search, Wago-only addons and newer versions than CurseForge."""
from __future__ import annotations

import tempfile
import time
from pathlib import Path

from ..addonfiles import (
    install_zip_into,
    pick_primary,
    read_toc,
    zip_supports_interface,
)
from ..config import DEFAULT_INTERFACE, is_wago_key, wago_key
from ..errors import UserError
from ..sources import GitHub, Wago, _ts, pick_latest, wago_categories
from ..util import exact_name, norm_version, now_iso, rmtree, same_name, version_cmp


class WagoMixin:
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

    def label_of(self, rec: dict, src: str | None) -> str | None:
        """The version a source would install."""
        if src == "curseforge":
            if rec.get("source") == "github":
                return (rec.get("cf_latest") or {}).get("version")
            return rec.get("latest_version")
        if src == "wago":
            return (rec.get("wago_latest") or {}).get("label")
        if src == "tukui":
            return (rec.get("tukui_latest") or {}).get("version")
        if src == "github":
            gh = rec.get("gh_latest") if rec.get("source") == "github" else rec.get("gh_alt")
            return (gh or {}).get("name") or (gh or {}).get("tag")
        return None

    def newest_available(self, rec: dict) -> tuple[str | None, str | None, str | None]:
        """(version, date, source) of the newest version anywhere — CurseForge, Wago, GitHub or tukui.org."""
        src = rec.get("source") or "curseforge"
        cands = []
        if src == "tukui":
            tl = rec.get("tukui_latest") or {}
            cands.append((tl.get("version"), tl.get("date"), "tukui"))
        else:
            if src == "github":
                cfl = rec.get("cf_latest") or {}
                if cfl.get("file_id"):
                    cands.append((cfl.get("version"), cfl.get("date"), "curseforge"))
                gl = rec.get("gh_latest") or {}
                cands.append((gl.get("name") or gl.get("tag"), gl.get("date"), "github"))
            elif rec.get("latest_file_id"):
                cands.append((rec.get("latest_version"), rec.get("latest_date"), "curseforge"))
            wl = rec.get("wago_latest") or {}
            if wl.get("label"):
                cands.append((wl.get("label"), wl.get("created_at"), "wago"))
            ga = rec.get("gh_alt") if src != "github" and self.store.config.get("use_github", True) else None
            if ga:
                cands.append((ga.get("name") or ga.get("tag"), ga.get("date"), "github"))
        cands = [c for c in cands if c[0]]
        if not cands:
            return None, None, None
        up = self.update_source(rec)
        if up:      # what Update would install is by definition the newest
            hit = next((c for c in cands if c[2] == up), None)
            if hit:
                return hit
        inst = rec.get("installed_version")
        best = cands[0]
        for c in cands[1:]:
            cmp = version_cmp(c[0], best[0])
            if (cmp or 0) > 0 or (cmp is None and (_ts(c[1]) or 0) > (_ts(best[1]) or 0)):
                best = c
        # nothing newer than what's installed: show the installed version as the latest
        if inst and (version_cmp(best[0], inst) or 0) < 0:
            return inst, rec.get("installed_file_date"), src
        return best

    def update_source(self, rec: dict) -> str | None:
        """Where the newest version comes from — CurseForge, Wago or GitHub, whichever has the highest
        version number — or None when what's installed is the newest."""
        base = self._base_update_source(rec)
        src = rec.get("source") or "curseforge"
        if src == "tukui":
            return base
        inst = rec.get("installed_version")
        cfl = rec.get("cf_latest") or {}
        if src == "github" and rec.get("gh_alt_installed") and cfl.get("file_id") \
                and not rec.get("distribution_disabled"):
            c = version_cmp(cfl.get("version"), inst)     # GitHub was newest when installed; now CurseForge?
            if c and c > 0 and (base is None or (version_cmp(cfl.get("version"), self.label_of(rec, base)) or 0) > 0):
                base = "curseforge"
        gh = rec.get("gh_alt") if src != "github" else None
        if gh and self.store.config.get("use_github", True):
            gl = gh.get("name") or gh.get("tag")
            c = version_cmp(gl, inst)
            if c and c > 0 and (base is None or (version_cmp(gl, self.label_of(rec, base)) or 0) > 0):
                return "github"
        return base

    def _base_update_source(self, rec: dict) -> str | None:
        src = rec.get("source") or "curseforge"
        if src == "tukui":
            tl, inst = (rec.get("tukui_latest") or {}).get("version"), rec.get("installed_version")
            if not tl or not inst:
                return None
            c = version_cmp(tl, inst)
            if c is not None:
                return "tukui" if c > 0 else None
            return "tukui" if norm_version(tl) != norm_version(inst) else None
        wago_new = self._wago_newer(rec)
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
            for k in ("gh_installed", "gh_alt_installed"):
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
            todo = [k for k, r in recs.items() if not r.get("wago") and str(k).isdigit()
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
                    if payload.get("thumbnail_image"):
                        r["wago"]["logo"] = payload["thumbnail_image"]   # used when CurseForge has no icon
                    r["wago_latest"] = Wago.pick_release(payload, channel)
                    if is_wago_key(k):
                        r["latest_checked_at"] = time.time()
                        if not r["wago_latest"]:
                            self.log(f"{r.get('name')}: no WoW Forever release on Wago right now.", "warn")
        except UserError as e:
            self.log(f"Wago: {e}", "warn")

    def merge_wago(self, results: list[dict], query: str | None, show_all: bool) -> list[dict]:
        """One list for CurseForge and Wago: each CurseForge result gets Wago's version too, and the newer
        of the two is the one shown. With a search text, addons that are only on Wago are added (returned)."""
        for r in results:
            r.update(sources=["curseforge"] + (["github"] if r.get("github") else []),
                     version=r.get("cf_version"), version_from="curseforge", version_date=r.get("cf_date"))
        wg = self.wago()
        if not wg:
            return []
        try:
            slugs = wg.slugs()
        except UserError:
            slugs = {}
        hits = []
        if query and len(query) >= 2:
            try:
                hits = [a for a in wg.search(query)[:40] if a.get("id")]
            except UserError:
                hits = []
        by_slug = {(a.get("slug") or "").lower(): a for a in hits}
        link = {}
        for r in results:
            w = (slugs.get(r.get("slug")) or {}).get("id") or (by_slug.get((r.get("slug") or "").lower()) or {}).get("id")
            if not w:
                w = next((a["id"] for a in hits if exact_name(a.get("display_name") or a.get("name"), r["name"])), None)
            if w:
                link[r["id"]] = str(w)
        try:
            rec = wg.recents(list({*link.values(), *(str(a["id"]) for a in hits)})) if (link or hits) else {}
        except UserError:
            rec = {}
        # no icon on CurseForge? use Wago's
        wlogo = {str(a["id"]): a.get("thumbnail_image") for a in hits if a.get("thumbnail_image")}
        for r in results:
            wid = link.get(r["id"])
            if wid and not r.get("logo"):
                r["logo"] = wlogo.get(wid) or (rec.get(wid) or {}).get("thumbnail_image") or ""
        channel = self.store.config.get("channel", "release")
        allowed = {"release": ("stable",), "beta": ("stable", "beta"), "alpha": ("stable", "beta", "alpha")}.get(channel, ("stable",))

        def newest(wid):
            rr = ((rec.get(wid) or {}).get("recent_releases") or {})
            best = None
            for ch in allowed:
                x = rr.get(ch)
                if isinstance(x, dict) and x.get("created_at") and (not best or x["created_at"] > best["created_at"]):
                    best = x
            return best
        used = set()
        for r in results:
            wid = link.get(r["id"])
            nb = newest(wid) if wid else None
            if not nb:
                continue
            used.add(wid)
            r["wago_id"], r["wago_version"] = wid, nb.get("label")
            r["sources"].insert(1, "wago")
            c = version_cmp(nb.get("label"), r.get("cf_version"))
            newer = c > 0 if c is not None else (not r.get("cf_version") or (_ts(nb["created_at"]) or 0) > (_ts(r.get("cf_date")) or 0) + 600)
            if newer or not r.get("forever"):
                r.update(version=nb.get("label"), version_from="wago", version_date=nb.get("created_at"), forever=True)
        managed_ids = {str((x.get("wago") or {}).get("id")) for x in self.store.addons.values() if x.get("wago") and self._folders_present(x)}
        cf_names = [r["name"] for r in results]
        extra = []
        for a in hits:
            wid = str(a["id"])
            if wid in used or any(exact_name(a.get("display_name") or a.get("name"), n) for n in cf_names):
                continue
            nb = newest(wid)
            if not nb and not show_all:
                continue
            slug = a.get("slug") or ""
            extra.append({"id": "w:" + wid, "wago_id": wid, "name": a.get("display_name") or a.get("name") or slug,
                          "summary": a.get("summary") or "", "authors": [x if isinstance(x, str) else (x.get("name") or "")
                                                                       for x in a.get("authors") or []][:2],
                          "logo": a.get("thumbnail_image") or "", "downloads": int(a.get("download_count") or 0),
                          "updated": (nb or {}).get("created_at"), "url": a.get("website_url") or f"https://addons.wago.io/addons/{slug}",
                          "categories": wago_categories(a)[:2], "manual_only": False, "github": None, "forever": bool(nb),
                          "slug": slug, "sources": ["wago"], "version": (nb or {}).get("label"), "version_from": "wago",
                          "version_date": (nb or {}).get("created_at"), "installed_wago": wid in managed_ids})
        return extra

    def op_install_best(self, cf_id: int | None, wago_id: str | None):
        """Install an addon from whichever of CurseForge, Wago and GitHub has the newest version."""
        if not cf_id:
            return self.op_add_wago(str(wago_id or ""))
        self._add_refs([{"kind": "id", "id": int(cf_id), "file": None, "raw": str(cf_id)}])
        rec = self.store.addons.get(str(cf_id))
        if not rec or not self._folders_present(rec):
            if wago_id:
                self.log("Trying Wago instead…")
                return self.op_add_wago(str(wago_id))
            return
        wg = self.wago()
        if wago_id and wg:
            try:
                rel = Wago.pick_release(wg.addon(str(wago_id)), self.store.config.get("channel", "release"))
                with self.store.lock:
                    rec["wago"] = {"id": str(wago_id), "via": "direct", "url": (rec.get("wago") or {}).get("url")}
                    rec["wago_latest"] = rel
            except UserError:
                pass
        self._check_github_alt(rec, force=True)
        with self.store.lock:
            self.store.save_state()
        src = self.update_source(rec)
        if src in ("wago", "github") and not rec.get("pinned"):
            mod = {"id": int(cf_id), "name": rec["name"]}
            try:
                if src == "wago":
                    self.log(f"Wago has a newer {rec['name']} ({rec['wago_latest']['label']}) — using that.")
                    self._install_from_wago(mod, rec, rec["wago_latest"])
                else:
                    self._install_github_alt(mod, rec)
            except UserError as e:
                self.log(f"{rec['name']}: {e} — keeping the CurseForge version.", "warn")

    def _check_github_alt(self, rec: dict, force: bool = False):
        """GitHub as a third source for addons that link a GitHub repo (checked at most every 6 hours,
        GitHub only allows 60 lookups an hour without an account)."""
        repo = rec.get("github")
        if not repo or rec.get("source") == "github" or not self.store.config.get("use_github", True):
            return
        if not force and time.time() - float(rec.get("gh_alt_checked_at") or 0) < 6 * 3600:
            return
        try:
            rel, asset = GitHub().find_forever(repo, self.store.config.get("channel", "release"),
                                               str(self.store.config.get("interface") or DEFAULT_INTERFACE))
        except UserError:
            rec["gh_alt_checked_at"] = time.time()      # don't spend GitHub's hourly allowance retrying
            return
        with self.store.lock:
            rec["gh_alt_checked_at"] = time.time()
            rec["gh_alt"] = ({"release_id": rel["id"], "tag": rel.get("tag_name"), "name": rel.get("name") or rel.get("tag_name"),
                              "date": rel.get("published_at"), "url": rel.get("html_url")} if rel and asset else None)

    def _install_github_alt(self, mod: dict, rec: dict):
        rel, asset = GitHub().find_forever(rec["github"], self.store.config.get("channel", "release"),
                                           str(self.store.config.get("interface") or DEFAULT_INTERFACE))
        if not asset:
            raise UserError("GitHub has no WoW Forever build right now")
        self.log(f"GitHub has a newer {rec['name']} ({rel.get('name') or rel.get('tag_name')}) — using that.")
        self._install_from_github(mod, rec, rec["github"], rel, asset, rec.get("pinned", False))
        with self.store.lock:
            rec["gh_alt_installed"] = True
            rec.pop("gh_alt", None)
            self.store.save_state()

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
