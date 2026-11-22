"""WTF backups (addon settings) and cleaning up after a crash or power cut."""
from __future__ import annotations

import os
import shutil
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from ..addonfiles import _safe_entries
from ..errors import UserError
from ..system import game_running
from ..util import fmt_size, load_json, rmtree, safe_folder_name


class BackupsMixin:
    def wtf_dir(self) -> Path | None:
        d = self.store.config.get("addons_dir")
        return Path(d).parent.parent / "WTF" if d else None

    # -- leftovers from a crash or power cut ------------------------------------ #
    def cleanup_leftovers(self, min_age: float = 120.0) -> list[str]:
        """Remove working files a crashed run left behind, restoring anything they still hold.

        Runs at startup once this copy owns the port, so no other copy is mid-install.
        Items younger than `min_age` seconds are left alone as an extra safety margin.
        """
        done: list[str] = []
        cutoff = time.time() - min_age

        def old(p: Path) -> bool:
            try:
                return p.stat().st_mtime < cutoff
            except OSError:
                return False

        def drop(p: Path):
            if p.is_dir():
                rmtree(p)
            else:
                try:
                    p.unlink()
                except OSError:
                    pass
            if not p.exists():
                done.append(str(p))

        # 1) Temporary download/extract folders and half-written backups in the data folder.
        for pat in ("wfam-*", "backups/*.part"):
            for p in self.store.dir.glob(pat):
                if old(p):
                    drop(p)
        # 2) Previous-version archives of addons that are no longer managed (any AddOns folder).
        keys: set[str] = set()
        for f in self.store.dir.glob("addons-*.json"):
            keys.update((load_json(f, {}).get("addons") or {}).keys())
        keys.update(self.store.addons.keys())
        vroot = self.store.dir / "versions"
        if vroot.is_dir():
            for p in vroot.iterdir():
                if p.is_dir() and p.name not in keys:
                    drop(p)
        # 3) Working folders inside the WoW folder.
        d = self.store.config.get("addons_dir")
        if d and Path(d).is_dir():
            addons = Path(d)
            interface = addons.parent
            for b in interface.glob(".wfam-backup-*"):
                if not b.is_dir() or not old(b):
                    continue
                for sub in list(b.iterdir()):   # an install stopped half-way: put the old folders back
                    if sub.is_dir() and safe_folder_name(sub.name) and not (addons / sub.name).exists():
                        try:
                            shutil.move(str(sub), str(addons / sub.name))
                            self.log(f"Restored {sub.name} from an install that was interrupted.", "warn")
                        except OSError:
                            pass
                drop(b)
            for s in interface.glob(".wfam-staging-*"):
                if old(s):
                    drop(s)
            t = addons / ".wfam-write-test"
            if t.exists():
                drop(t)
        w = self.wtf_dir()
        if w:
            for s in w.parent.glob(".wfam-wtf-*"):
                if old(s):
                    drop(s)
            o = w.with_name("WTF.wfam-old")
            if o.is_dir() and old(o):
                if not w.exists():   # a restore stopped between the two renames
                    try:
                        os.replace(o, w)
                        self.log("Put your WTF folder back after an interrupted restore.", "warn")
                    except OSError:
                        pass
                else:
                    drop(o)
        return done

    def backups_dir(self) -> Path:
        custom = (self.store.config.get("backup_dir") or "").strip()
        if custom:
            try:
                p = Path(custom)
                p.mkdir(parents=True, exist_ok=True)
                return p
            except OSError:
                pass   # e.g. a USB drive that isn't plugged in: fall back to the default
        p = self.store.dir / "backups"
        p.mkdir(exist_ok=True)
        return p

    def set_backup_dir(self, raw: str):
        """Keep WTF backups somewhere else (e.g. OneDrive). Existing backups move along."""
        old = self.backups_dir()
        raw = (raw or "").strip().strip('"')
        if raw:
            new = Path(os.path.expandvars(raw)).expanduser()
            if not new.is_absolute():
                raise UserError("Use a full folder path for backups, e.g. C:\\Users\\you\\OneDrive\\WoW backups.")
            try:
                new.mkdir(parents=True, exist_ok=True)
                (new / ".wfam-write-test").write_text("x")
                (new / ".wfam-write-test").unlink()
            except OSError as e:
                raise UserError(f"Can't save backups in {new}: {e}")
        else:
            new = self.store.dir / "backups"
            new.mkdir(exist_ok=True)
        if new.resolve() != old.resolve():
            moved = 0
            for z in old.glob("WTF-*.zip"):
                try:
                    shutil.move(str(z), str(new / z.name))
                    moved += 1
                except OSError:
                    pass
            if moved:
                self.log(f"Moved {moved} backup(s) to {new}.", "ok")
        self.store.update_config(backup_dir=str(new) if raw else "")

    def list_backups(self) -> list[dict]:
        out = []
        for p in sorted(self.backups_dir().glob("WTF-*.zip"), reverse=True):
            st = p.stat()
            out.append({"name": p.name, "size": fmt_size(st.st_size), "bytes": st.st_size,
                        "date": datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat()})
        return out

    def backup_wtf(self, force: bool = False):
        """Zip WTF (where every addon keeps its settings) before addons change.
        At most one automatic backup per 10 minutes; the newest `backup_keep` are kept."""
        if not force and not self.store.config.get("backup_wtf", True):
            return
        w = self.wtf_dir()
        if not w or not w.is_dir():
            return
        backups = self.list_backups()
        if not force and backups and time.time() - datetime.fromisoformat(backups[0]["date"]).timestamp() < 600:
            return
        bdir = self.backups_dir()
        final = bdir / f"WTF-{time.strftime('%Y%m%d-%H%M%S')}.zip"
        tmp = final.with_suffix(".part")
        skipped = 0
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as z:
            for root, _dirs, files in os.walk(w):
                for fn in files:
                    fp = Path(root, fn)
                    if fn.lower().endswith(".bak") and "savedvariables" in root.lower():
                        continue  # the game's own .bak copies
                    try:
                        z.write(fp, fp.relative_to(w.parent).as_posix())
                    except OSError:
                        skipped += 1
        os.replace(tmp, final)
        keep = max(1, int(self.store.config.get("backup_keep") or 3))
        for old in sorted(bdir.glob("WTF-*.zip"), reverse=True)[keep:]:
            try:
                old.unlink()
            except OSError:
                pass
        self.log(f"Backed up your addon settings (WTF, {fmt_size(final.stat().st_size)})"
                 + (f" — {skipped} locked file(s) skipped" if skipped else "") + ".")

    def op_backup_now(self):
        if not self.wtf_dir() or not self.wtf_dir().is_dir():
            raise UserError("No WTF folder found next to your AddOns folder yet (it appears after you log in once).")
        self.backup_wtf(force=True)

    def op_restore_wtf(self, name: str):
        if game_running():
            raise UserError("Close WoW first — the game rewrites these settings when you log out.")
        src = self.backups_dir() / Path(name).name
        w = self.wtf_dir()
        if not src.is_file() or not w:
            raise UserError("That backup doesn't exist.")
        with zipfile.ZipFile(src) as z:
            entries = list(_safe_entries(z))
            if not entries or any(parts[0] != "WTF" for _, parts in entries):
                raise UserError("That file isn't a WTF backup.")
            if w.is_dir():
                self.backup_wtf(force=True)   # so the restore itself can be undone
            staging = Path(tempfile.mkdtemp(prefix=".wfam-wtf-", dir=w.parent))
            try:
                for info, parts in entries:
                    if info.is_dir():
                        continue
                    target = staging.joinpath(*parts)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with z.open(info) as a, open(target, "wb") as b:
                        shutil.copyfileobj(a, b)
                old = w.with_name("WTF.wfam-old")
                rmtree(old)
                if w.exists():
                    os.replace(w, old)
                os.replace(staging / "WTF", w)
                rmtree(old)
            finally:
                rmtree(staging)
        self.log(f"Restored your addon settings from {src.name}.", "ok")
