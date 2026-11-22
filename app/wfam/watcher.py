"""Watches the Downloads folder for files the user fetched by hand."""
from __future__ import annotations

import os
import time
import traceback
from pathlib import Path
from typing import TYPE_CHECKING

from .addonfiles import sha1_of_file
from .config import AWAIT_SECONDS
from .errors import BusyError

if TYPE_CHECKING:
    from .manager import Manager


# --------------------------------------------------------------------------- #
# Web UI server (localhost only)
# --------------------------------------------------------------------------- #
class DownloadWatcher:
    """Installs addons the user downloaded from CurseForge's website.

    For addons whose authors only allow downloads on CurseForge's own site, the
    manager opens the download page; this watches the Downloads folder and
    installs the zip once its SHA-1 matches the file CurseForge lists.
    """

    def __init__(self, manager: Manager):
        self.m = manager
        self.sizes: dict[str, tuple] = {}
        self.checked: dict[str, tuple] = {}
        self._last_change = 0.0

    def run(self):
        while True:
            # Look every 2 s while a download is awaited, otherwise every 20 s (woken early by Install).
            fast = any(r.get("awaiting") for r in list(self.m.store.addons.values()))
            if self.sizes and not fast:
                fast = time.time() - self._last_change < 10
            self.m.watch_wake.wait(2 if fast else 20)
            self.m.watch_wake.clear()
            try:
                self.scan()
            except Exception:
                self.m.log(traceback.format_exc(), "debug")

    def wanted(self) -> dict[str, tuple[str, int | None]]:
        out = {}
        now = time.time()
        with self.m.store.lock:
            for k, r in self.m.store.addons.items():
                aw = r.get("awaiting")
                if aw and now - aw.get("since", now) > AWAIT_SECONDS:
                    r.pop("awaiting", None)
                    self.m.store.save_state()
                    self.m.log(f"Stopped waiting for the {r['name']} download (30 min).", "warn")
                    aw = None
                if aw and aw.get("sha1"):
                    out[aw["sha1"].lower()] = (k, aw.get("length"))
                elif (r.get("distribution_disabled") and r.get("source") not in ("github", "wago") and r.get("latest_sha1")
                      and (r.get("installed_sha1") or "").lower() != r["latest_sha1"].lower()):
                    out.setdefault(r["latest_sha1"].lower(), (k, None))
        return out

    def scan(self):
        if not self.m.store.config.get("auto_manual", True):
            return
        wanted = self.wanted()
        if set(wanted) != getattr(self, "_last", None):
            self._last = set(wanted)
            self.checked.clear()   # something new to look for: re-check zips already in Downloads
        if not wanted:
            self.sizes.clear()
            return
        d = self.m.downloads_dir()
        if not d.is_dir():
            return
        now = time.time()
        lengths = {ln for _, ln in wanted.values()}
        try:
            entries = [e for e in os.scandir(d) if e.name.lower().endswith(".zip")]
        except OSError:
            return
        for e in entries:
            p = Path(e.path)
            try:
                if not e.is_file():
                    continue
                st = e.stat()
            except OSError:
                continue
            if now - st.st_mtime > 6 * 3600:
                continue
            sig = (st.st_size, st.st_mtime)
            key = str(p)
            if self.sizes.get(key) != sig:   # still being written, or new: look again next round
                self.sizes[key] = sig
                self._last_change = now
                continue
            if self.checked.get(key) == sig:
                continue
            if None not in lengths and st.st_size not in lengths:
                self.checked[key] = sig
                continue
            digest = sha1_of_file(p)
            hit = wanted.get(digest)
            if not hit:
                self.checked[key] = sig
                continue
            try:
                name = self.m.store.addons[hit[0]]["name"]
                self.m.run_job(f"Installing {name} from Downloads", self.m.op_install_downloaded, hit[0], p, digest)
                self.checked[key] = sig
            except BusyError:
                pass  # another task is running; try again on the next scan
            return
