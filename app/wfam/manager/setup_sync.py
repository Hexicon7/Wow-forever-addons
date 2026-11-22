"""The whole setup in one file, and the sync folder that keeps two PCs in step."""
from __future__ import annotations

import io
import json
import os
import platform
import re
import shutil
import subprocess
import time
import zipfile
from datetime import datetime
from pathlib import Path

from ..addonfiles import _safe_entries
from ..config import APP_VERSION
from ..errors import UserError
from ..system import game_running
from ..util import fmt_size, now_iso


class SetupSyncMixin:
    SETUP_PREFIX = "WoWForeverSetup"

    @staticmethod
    def computer_name() -> str:
        return re.sub(r"[^A-Za-z0-9_-]", "", os.environ.get("WFAM_COMPUTER") or platform.node() or os.environ.get("COMPUTERNAME") or "PC")[:40] or "PC"

    def _setup_sources(self):
        """(file on disk, name in the zip) for everything a setup holds besides the addon list."""
        w = self.wtf_dir()
        if w and (w / "Account").is_dir():
            for root, _dirs, files in os.walk(w / "Account"):
                for fn in files:
                    low = fn.lower()
                    if low.endswith((".bak", ".old", ".wfam-tmp")):
                        continue
                    fp = Path(root, fn)
                    yield fp, fp.relative_to(w.parent).as_posix()
        # hand-installed addons can't be downloaded again, so they go in too (not huge ones)
        for f in self.unmanaged_folders():
            files = [p for p in f.rglob("*") if p.is_file()]
            if sum(p.stat().st_size for p in files) > 60 * 1024 * 1024:
                continue
            for fp in files:
                yield fp, "AddOns/" + fp.relative_to(f.parent).as_posix()

    def write_setup(self, dest: Path) -> dict:
        info = {"format": 1, "app": APP_VERSION, "created": now_iso(), "computer": self.computer_name(),
                "share_code": self.share_code(), "addons": len(self.store.addons),
                "hand_installed": [f.name for f in self.unmanaged_folders()]}
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".part")
        skipped = 0
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            z.writestr("setup.json", json.dumps(info, indent=1))
            for fp, arc in self._setup_sources():
                try:
                    z.write(fp, arc)
                except OSError:
                    skipped += 1
        os.replace(tmp, dest)
        info["size"] = dest.stat().st_size
        info["skipped"] = skipped
        return info

    def _sync_file(self) -> Path | None:
        d = (self.store.config.get("sync_dir") or "").strip()
        return Path(d) / f"{self.SETUP_PREFIX}-{self.computer_name()}.zip" if d and Path(d).is_dir() else None

    def op_setup_export(self):
        sf = self._sync_file()
        if sf:
            dest = sf
        else:
            dl = Path(os.environ.get("WFAM_EXPORT_DIR") or (Path.home() / "Downloads"))
            dest = dl / f"{self.SETUP_PREFIX}-{self.computer_name()}-{time.strftime('%Y%m%d-%H%M')}.zip"
        info = self.write_setup(dest)
        if sf:
            self.store.state["sync_export"] = {"created": info["created"], "sig": self._sync_sig()}
            self.store.save_state()
        self.last_export = str(dest)
        self.log(f"Saved your whole setup ({info['addons']} addons + their settings, {fmt_size(info['size'])}) to {dest}"
                 + (f" — {info['skipped']} locked file(s) skipped" if info["skipped"] else "") + ".", "ok")
        if os.name == "nt" and not os.environ.get("WFAM_NO_EXPLORER"):
            try:
                subprocess.Popen(["explorer", "/select,", str(dest)])
            except OSError:
                pass

    def op_setup_restore(self, raw: bytes | None = None, path: str | None = None):
        if game_running():
            raise UserError("Close WoW first — the game rewrites addon settings when you log out.")
        if path:
            p = Path(path)
            d = (self.store.config.get("sync_dir") or "").strip()
            if not (d and p.parent.resolve() == Path(d).resolve() and p.name.startswith(self.SETUP_PREFIX) and p.is_file()):
                raise UserError("That setup file isn't in your sync folder.")
            src = open(p, "rb")
        else:
            src = io.BytesIO(raw or b"")
        addons_dir = self.addons_dir()
        w = self.wtf_dir()
        with src, zipfile.ZipFile(src) as z:
            try:
                info = json.loads(z.read("setup.json").decode("utf-8"))
                assert isinstance(info, dict) and info.get("format") == 1
            except (KeyError, ValueError, AssertionError):
                raise UserError("That file isn't a WoW Forever Addons setup.")
            entries = [(i, parts) for i, parts in _safe_entries(z) if not i.is_dir() and parts[0] != "setup.json"]
            if any(parts[0] not in ("WTF", "AddOns") or (parts[0] == "WTF" and (len(parts) < 3 or parts[1] != "Account"))
                   for _, parts in entries):
                raise UserError("That setup file has unexpected content — nothing was changed.")
            if w and w.is_dir():
                self.backup_wtf(force=True)     # so the restore can be undone from Backups
            n_set = n_hand = 0
            hand_new = {parts[1] for _, parts in entries if parts[0] == "AddOns" and len(parts) > 2
                        and not (addons_dir / parts[1]).exists()}
            for i, parts in entries:
                if parts[0] == "WTF":
                    target = addons_dir.parent.parent.joinpath(*parts)
                    n_set += 1
                elif parts[1] in hand_new:
                    target = addons_dir.joinpath(*parts[1:])
                    n_hand += 1
                else:
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                tmp = target.with_name(target.name + ".wfam-tmp")
                with z.open(i) as a, open(tmp, "wb") as b:
                    shutil.copyfileobj(a, b)
                os.replace(tmp, target)
        self.log(f"Restored the settings from {info.get('computer') or 'the setup file'} ({n_set} files)"
                 + (f" and {len(hand_new)} hand-installed addon(s)" if hand_new else "") + ". Installing its addons…", "ok")
        seen = self.store.state.setdefault("sync_seen", {})
        seen[str(info.get("computer"))] = info.get("created")
        self.store.save_state()
        if info.get("share_code"):
            self.op_import(info["share_code"].encode("utf-8"))

    def _sync_sig(self) -> list:
        """Changes when addon settings or the addon list change (folder times, not every file)."""
        w = self.wtf_dir()
        sig = sorted((k, r.get("installed_version")) for k, r in self.store.addons.items())
        if w and (w / "Account").is_dir():
            for p in [*(w / "Account").glob("*/SavedVariables"), *(w / "Account").glob("*/*/*")]:
                try:
                    if p.is_dir():
                        sig.append((p.relative_to(w).as_posix(), p.stat().st_mtime_ns))
                except OSError:
                    pass
        return [list(x) for x in sig]

    def sync_tick(self):
        """Keep this PC's setup file in the sync folder current: only when something changed, not while
        WoW runs or a task is busy, and at most every 10 minutes. Quiet: one line in the activity log."""
        sf = self._sync_file()
        if not sf or self.job.get("running") or game_running():
            return
        last = self.store.state.get("sync_export") or {}
        sig = self._sync_sig()
        if last.get("sig") == sig and sf.is_file():
            return
        try:
            if last.get("created") and time.time() - datetime.fromisoformat(last["created"]).timestamp() < 600:
                return
        except ValueError:
            pass
        info = self.write_setup(sf)
        self.store.state["sync_export"] = {"created": info["created"], "sig": sig}
        self.store.save_state()
        self.log(f"Saved your setup to the sync folder ({fmt_size(info['size'])}).")

    def sync_offer(self) -> dict | None:
        """A newer setup from another PC in the sync folder, if there is one (looked at every 30 s)."""
        cached = getattr(self, "_sync_offer", None)
        if cached and time.time() - cached[0] < 30:
            return cached[1]
        best = None
        d = (self.store.config.get("sync_dir") or "").strip()
        if d and Path(d).is_dir():
            mine = (self.store.state.get("sync_export") or {}).get("created") or ""
            seen = self.store.state.get("sync_seen") or {}
            for f in Path(d).glob(self.SETUP_PREFIX + "-*.zip"):
                if f.name == f"{self.SETUP_PREFIX}-{self.computer_name()}.zip":
                    continue
                try:
                    with zipfile.ZipFile(f) as z:
                        info = json.loads(z.read("setup.json").decode("utf-8"))
                except (OSError, KeyError, ValueError, zipfile.BadZipFile):
                    continue
                created, comp = str(info.get("created") or ""), str(info.get("computer") or f.stem)
                if created > mine and created > str(seen.get(comp) or "") and (not best or created > best["created"]):
                    best = {"computer": comp, "created": created, "file": str(f), "addons": info.get("addons")}
        self._sync_offer = (time.time(), best)
        return best

    def sync_dismiss(self, computer: str, created: str):
        self.store.state.setdefault("sync_seen", {})[computer] = created
        self.store.save_state()
        self._sync_offer = None
