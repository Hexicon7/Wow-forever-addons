"""Settings (config.json) and the list of managed addons (state.json)."""
from __future__ import annotations

import hashlib
import os
import threading
from pathlib import Path

from .addonfiles import install_token, local_signature, read_toc
from .config import CF_FIELDS, CF_INSTALLED_FIELDS, DEFAULT_CONFIG, own_source_key
from .util import data_dir, load_json, save_json


# --------------------------------------------------------------------------- #
# Persistent state
# --------------------------------------------------------------------------- #
class Store:
    def __init__(self):
        self.dir = data_dir()
        self.lock = threading.RLock()
        self.config_path = self.dir / "config.json"
        self.config = dict(DEFAULT_CONFIG)
        self.config.update(load_json(self.config_path, {}))
        self._load_state()
        if self.state.get("addons"):
            self.save_state()   # older versions stored CurseForge data: rewrite without it

    def _state_path(self) -> Path:
        d = self.config.get("addons_dir") or ""
        key = os.path.normcase(os.path.abspath(d)) if d else "none"
        return self.dir / f"addons-{hashlib.sha1(key.encode()).hexdigest()[:12]}.json"

    def _load_state(self):
        self.state = load_json(self._state_path(), {})
        self.state.setdefault("addons_dir", self.config.get("addons_dir"))
        self.state.setdefault("addons", {})
        if not self.state.get("local_tracking"):
            # Addons installed by older versions: verify once what is really on disk.
            for r in self.state["addons"].values():
                r.setdefault("local", {"pending": True})
            self.state["local_tracking"] = 1

    def _stamp_local(self):
        """Remember how each addon's folders looked right after the manager installed it, so a
        later change by another tool (Wago app, CurseForge app, by hand) can be noticed."""
        d = self.config.get("addons_dir")
        if not d:
            return
        base = Path(d)
        for r in self.state["addons"].values():
            loc = r.get("local")
            if not r.get("folders") or not r.get("installed_at"):
                continue
            if loc is None or (not loc.get("pending") and loc.get("at") != install_token(r)):
                toc = read_toc(base / r["primary"]) if r.get("primary") else {}
                r["local"] = {"at": install_token(r), "sig": local_signature(base, r), "toc": toc.get("version")}
                r.pop("external", None)
                r.pop("local_date", None)

    @property
    def addons(self) -> dict:
        return self.state["addons"]

    def save_config(self):
        with self.lock:
            save_json(self.config_path, {k: v for k, v in self.config.items() if k != "version_type_name"})

    def save_state(self):
        with self.lock:
            try:
                self._stamp_local()
            except Exception:
                pass
            save_json(self._state_path(), self._persistable())

    def _persistable(self) -> dict:
        """What goes on disk. CurseForge's API terms forbid saving or caching data from the API,
        so only identifiers are kept (which project, which file, which folders, pins, source);
        names, versions and update info are fetched again each time the manager starts."""
        out = {k: v for k, v in self.state.items() if k not in ("addons", "recognized", "fp_query")}
        addons = {}
        for key, r in self.state["addons"].items():
            if own_source_key(key):      # nothing from CurseForge in these
                d = {k: v for k, v in r.items() if k not in ("latest_checked_at", "awaiting")}
                addons[key] = d
                continue
            d = {k: v for k, v in r.items() if k not in CF_FIELDS}
            if (r.get("source") or "curseforge") == "curseforge":
                for k in CF_INSTALLED_FIELDS:
                    d.pop(k, None)
            if r.get("previous"):
                d["previous"] = [{k: v for k, v in pv.items()
                                  if not ((pv.get("source") or "curseforge") == "curseforge" and k in CF_INSTALLED_FIELDS)}
                                 for pv in r["previous"]]
            addons[key] = d
        out["addons"] = addons
        return out

    def update_config(self, **changes):
        with self.lock:
            old_dir = self.config.get("addons_dir")
            old_filter = (self.config.get("channel"), self.config.get("version_type_id"))
            self.config.update(changes)
            self.save_config()
            if self.config.get("addons_dir") != old_dir:
                self._load_state()
            elif (self.config.get("channel"), self.config.get("version_type_id")) != old_filter:
                # "Latest" results were computed for another channel/version: forget them.
                for rec in self.addons.values():
                    for k in ("latest_file_id", "latest_version", "latest_date", "latest_sha1",
                              "latest_release_type", "latest_checked_at"):
                        rec.pop(k, None)
                self.save_state()
