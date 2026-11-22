"""Checking, updating, removing and pinning addons."""
from __future__ import annotations

import hashlib
import io
import tempfile
import threading
import time
import zipfile
from pathlib import Path

from ..addonfiles import (
    _dir_matches,
    analyze_zip,
    install_zip_into,
    pick_primary,
    read_toc,
    sha1_of_file,
)
from ..config import (
    CHANNELS,
    DEFAULT_INTERFACE,
    REQUIRED_DEPENDENCY,
    is_tukui_key,
    is_wago_key,
)
from ..errors import UserError
from ..net import run_parallel
from ..sources import GitHub, pick_latest
from ..system import default_downloads_dir
from ..util import chunks, file_sha1, now_iso, rmtree, safe_folder_name, same_name


class UpdatesMixin:
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
            if done and report_nothing:     # you asked (tray): say what happened. Otherwise stay quiet —
                self.notify(plural(len(done), "addon") + " updated", names(done))   # the window shows "✓ N updated"
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
        try:
            self.write_companion()       # drop auras you've imported in game since
        except OSError:
            pass
        cf = self.client()
        type_id = self.ensure_type(cf)
        channel = self.store.config.get("channel", "release")
        recs = self.store.addons
        if not recs:
            self.log("No managed addons yet — paste some CurseForge links first.")
            return
        ids = [int(k) for k in recs if str(k).isdigit()]
        mods: dict[int, dict] = {}
        warm = getattr(self, "_meta_cache", None)
        if warm and time.time() - warm[0] < 120 and all(i in warm[1] for i in ids):
            mods = dict(warm[1])          # fetched a moment ago at start-up
        else:
            for c in chunks(ids, 50):
                for m in cf.mods(c):
                    mods[m["id"]] = m
        self._meta_cache = None
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
            r = recs[str(mid)]
            with self.store.lock:
                self._set_latest(r, f)
                if f:
                    r.pop("no_cf_forever", None)
                elif r.get("no_cf_forever") != channel:
                    r["no_cf_forever"] = channel            # say it once, not at every check
                    self.log(f"{r.get('name')}: CurseForge has no WoW Forever file on the {channel} channel — "
                             f"keeping the one you have (Wago and GitHub are checked too).", "info")
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
        self._check_tukui(recs)
        looked = 0
        for k, r in list(recs.items()):
            if str(k).isdigit() and r.get("github") and r.get("source") != "github" and looked < 20:
                before = r.get("gh_alt_checked_at")
                self._check_github_alt(r)
                looked += r.get("gh_alt_checked_at") != before
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
        for c in chunks([int(k) for k in targets if str(k).isdigit()], 50):
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
                  and self.update_source(recs[k]) != "wago"
                  and not (self.update_source(recs[k]) == "github" and recs[k].get("source") != "github")]
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
                    if is_tukui_key(key):
                        self._update_tukui(key, rec)
                        continue
                    mod = mods.get(key) or cf.mod(int(key))
                    if (self.update_source(rec) == "github" and rec.get("source") != "github"
                            and self.status_of(rec) != "missing"):
                        try:
                            with self.store.lock:
                                self._apply_meta(rec, mod)
                            self._install_github_alt(mod, rec)
                            continue
                        except UserError as e:
                            self.log(f"{rec.get('name', key)}: GitHub didn't work ({e}) — trying the others.", "warn")
                            rec.pop("gh_alt", None)
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
