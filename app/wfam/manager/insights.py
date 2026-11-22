"""Lua errors from BugGrabber and addons that are off on every character."""
from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

from ..addonfiles import bug_errors


class InsightsMixin:
    def insights(self) -> dict:
        """Lua errors BugGrabber saved (per AddOns folder) and folders that are off on every character.
        Read again only when those files or the AddOns folder change, and at most every 20 s."""
        cached = getattr(self, "_ins", None)
        now = time.time()
        if cached and now - cached[0] < 20:
            return cached[2]
        w = self.wtf_dir()
        d = self.store.config.get("addons_dir") or ""
        try:
            folders = sorted(p.name.lower() for p in Path(d).iterdir() if p.is_dir()) if d and Path(d).is_dir() else []
        except OSError:
            folders = []
        sig = [len(folders), hash(tuple(folders))]
        if w and (w / "Account").is_dir():
            for f in [*(w / "Account").glob("*/SavedVariables/!BugGrabber.lua"), *(w / "Account").glob("*/*/*/AddOns.txt")]:
                try:
                    sig.append((str(f), f.stat().st_mtime_ns))
                except OSError:
                    pass
        if cached and cached[1] == sig:
            self._ins = (now, sig, cached[2])
            return cached[2]
        data = {"errors": {}, "unused": set()}
        try:
            data["errors"] = bug_errors(w, set(folders))
            ch = self.characters()
            if any(c["has_file"] for c in ch["characters"]):
                data["unused"] = {row["folder"].lower() for row in ch["addons"] if row["on"] and not any(row["on"])}
        except Exception:
            pass
        self._ins = (now, sig, data)
        return data

    @staticmethod
    def _installed_ts(r: dict) -> float:
        try:
            return datetime.fromisoformat(str(r.get("installed_at"))).timestamp()
        except (TypeError, ValueError):
            return 0.0

    def _error_summary(self, r: dict, ins: dict) -> dict | None:
        errs = self.errors_of(r, ins)
        if not errs:
            return None
        inst = self._installed_ts(r)
        after = errs if inst else []
        before = [e for e in self.errors_of(r, ins, everything=True) if inst and inst - 7 * 86400 < e["time"] < inst]
        return {"n": sum(e["count"] for e in errs), "last": errs[0]["time"],
                "message": errs[0]["message"].split("\n")[0][:160],
                # it started with the version you just got: worth offering to go back
                "since_update": bool(after) and not before and bool(r.get("previous"))}

    def errors_of(self, r: dict, ins: dict, everything: bool = False) -> list:
        """Errors of the version you have now (not older ones), minus those you chose to ignore."""
        cut = 0.0 if everything else max(float(r.get("errors_ok_at") or 0), self._installed_ts(r))
        out = [e for f in r.get("folders") or [] for e in ins["errors"].get(f.lower(), []) if e["time"] > cut or (not e["time"] and not cut)]
        return sorted(out, key=lambda e: -e["time"])
