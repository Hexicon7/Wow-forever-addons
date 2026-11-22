"""Status rules, tidying storage, warming addon info, following the game version."""
from __future__ import annotations

import time
from pathlib import Path

from ..addonfiles import game_interface
from ..config import COMPANION_FOLDER, KEEP_PREVIOUS_VERSIONS, entry_script
from ..util import chunks, fmt_size, rmtree


class HousekeepingMixin:
    def _folders_present(self, rec: dict) -> bool:
        d = self.store.config.get("addons_dir")
        folders = rec.get("folders") or []
        return bool(d and folders) and all((Path(d) / f).is_dir() for f in folders)

    def status_of(self, rec: dict) -> str:
        if rec.get("awaiting"):
            return "awaiting"
        if not rec.get("folders"):
            return "manual" if rec.get("distribution_disabled") else "not_installed"
        if not self._folders_present(rec):
            return "manual" if rec.get("distribution_disabled") and rec.get("source") not in ("github", "wago", "tukui") else "missing"
        if rec.get("pinned"):
            return "pinned"
        src = self.update_source(rec)
        if src == "curseforge" and rec.get("distribution_disabled"):
            return "update_manual"
        if src:
            return "update"
        return "ok" if rec.get("latest_checked_at") else "unchecked"

    def tidy_storage(self):
        """Keep the program's own files small: the newest WTF backups only (3 unless you chose otherwise),
        one earlier version per addon, nothing left over from addons you removed."""
        cfg = self.store.config
        if not cfg.get("storage_v2"):
            if int(cfg.get("backup_keep") or 10) == 10:      # 10 was the old default
                self.store.update_config(backup_keep=3)
            self.store.update_config(storage_v2=True)
        freed = 0
        keep = max(1, int(cfg.get("backup_keep") or 3))
        try:
            for old in sorted(self.backups_dir().glob("WTF-*.zip"), reverse=True)[keep:]:
                try:
                    freed += old.stat().st_size
                    old.unlink()
                except OSError:
                    pass
        except OSError:
            pass
        if self.store.state.get("auras"):           # "Add to game" was replaced by "Copy string"
            with self.store.lock:
                self.store.state.pop("auras", None)
            try:
                rmtree(self._auras_dir())
            except OSError:
                pass
        old_pyc = entry_script().with_name("WoWForeverAddons.pyc")   # compiled copy kept by versions before 3.18
        if old_pyc.is_file() and old_pyc.with_name("wfam.zip").is_file():
            try:
                old_pyc.unlink()
            except OSError:
                pass
        d = self.store.config.get("addons_dir")
        if d and (Path(d) / COMPANION_FOLDER).is_dir():
            rmtree(Path(d) / COMPANION_FOLDER)
        vroot = self.store.dir / "versions"
        with self.store.lock:
            for key, rec in self.store.addons.items():
                prev = rec.get("previous") or []
                if len(prev) > KEEP_PREVIOUS_VERSIONS:
                    rec["previous"] = prev[:KEEP_PREVIOUS_VERSIONS]
                d = vroot / key
                if d.is_dir():
                    wanted = {m.get("zip") for m in rec.get("previous") or []}
                    for z in d.glob("*.zip"):
                        if z.name not in wanted:
                            try:
                                freed += z.stat().st_size
                                z.unlink()
                            except OSError:
                                pass
            if vroot.is_dir():
                for d in vroot.iterdir():
                    if d.is_dir() and d.name not in self.store.addons:
                        try:
                            freed += sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
                        except OSError:
                            pass
                        rmtree(d)
            self.store.save_state()
        if freed > 1 << 20:
            self.log(f"Tidied up old backups and earlier versions ({fmt_size(freed)} freed).", "ok")

    def warm_meta(self):
        """Right at start-up: names, icons and descriptions for every addon in one request, so the list
        looks complete within a second instead of after the first full update check."""
        try:
            if not (self.store.config.get("api_key") or "").strip():
                return
            ids = [int(k) for k in self.store.addons if str(k).isdigit()]
            if not ids:
                return
            cf = self.client()
            mods = {}
            for c in chunks(ids, 50):
                for m in cf.mods(c):
                    mods[m["id"]] = m
            with self.store.lock:
                for mid, m in mods.items():
                    rec = self.store.addons.get(str(mid))
                    if rec:
                        self._apply_meta(rec, m)
            self._meta_cache = (time.time(), mods)
        except Exception:
            pass
        finally:
            self.meta_ready = True

    def follow_game_interface(self):
        cfg = self.store.config
        found = game_interface(cfg.get("addons_dir"))
        if found and cfg.get("interface_auto", True) and str(cfg.get("interface")) != found:
            old = cfg.get("interface")
            self.store.update_config(interface=found)
            self.log(f"WoW Forever now reports interface {found} (was {old}) — addons are checked against that.")
        return found
