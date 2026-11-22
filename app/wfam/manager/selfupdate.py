"""Updates of this program itself (offered, never installed by itself)."""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

from ..config import APP_VERSION, GITHUB_API, GITHUB_RAW, UPDATE_REPO
from ..errors import UserError
from ..sources import GitHub
from ..system import host_exe, version_tuple


class SelfUpdateMixin:
    def update_repo(self) -> str:
        r = (self.store.config.get("update_repo") or UPDATE_REPO).strip().strip("/")
        return r if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", r or "") else ""

    @staticmethod
    def _quiet(fn, *args):
        try:
            fn(*args)
        except Exception:
            pass

    def check_self_update(self, notify: bool = True) -> dict | None:
        self._self_checked_at = time.time()
        repo = self.update_repo()
        if not repo:
            return None
        found = None
        try:
            rels = GitHub()._get(f"{GITHUB_API}/repos/{repo}/releases?per_page=5")
        except UserError:
            rels = []
        for rel in rels if isinstance(rels, list) else []:
            if rel.get("draft") or rel.get("prerelease"):
                continue
            ver = (rel.get("tag_name") or "").lstrip("vV")
            if version_tuple(ver) <= version_tuple(APP_VERSION):
                break
            asset = next((a for a in rel.get("assets") or []
                          if re.fullmatch(r"WoWForeverAddons-Setup-[\d.]+\.exe", a.get("name") or "")), None)
            if not asset:
                continue
            found = {"version": ver, "asset": asset, "notes": (rel.get("body") or "")[:4000],
                     "page": rel.get("html_url") or f"https://github.com/{repo}/releases"}
            break
        pushed = self._repo_update(repo)      # updates/latest.json in the repository itself
        if pushed and (not found or version_tuple(pushed["version"]) > version_tuple(found["version"])):
            found = pushed
        if not found:
            self.self_update = None
            return None
        if notify and (self.self_update or {}).get("version") != found["version"]:
            self.notify(f"WoW Forever Addons {found['version']} is available", "Open the program and click “Update now” "
                                                                               "at the bottom of the menu.")
        self.self_update = found
        return found

    def _repo_update(self, repo: str) -> dict | None:
        """A newer setup published as updates/latest.json + updates/<setup exe> on the main branch."""
        if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
            return None
        base = f"{GITHUB_RAW}/{repo}/main/updates"
        try:
            m = GitHub()._get(f"{base}/latest.json?t={int(time.time()) // 600}", accept="application/json")
        except UserError:
            return None
        if not isinstance(m, dict):
            return None
        ver, name, sha = str(m.get("version") or ""), str(m.get("file") or ""), str(m.get("sha256") or "").lower()
        if not (re.fullmatch(r"\d+(\.\d+){1,3}", ver) and re.fullmatch(r"WoWForeverAddons-Setup-[\d.]+\.exe", name)
                and re.fullmatch(r"[0-9a-f]{64}", sha) and str(m.get("size") or "").isdigit()):
            return None
        if version_tuple(ver) <= version_tuple(APP_VERSION):
            return None
        return {"version": ver, "notes": str(m.get("notes") or "")[:4000],
                "page": f"https://github.com/{repo}/tree/main/updates",
                "asset": {"name": name, "browser_download_url": f"{base}/{name}", "digest": "sha256:" + sha,
                          "size": int(m["size"])}}

    def op_self_update(self, background: bool = False):
        if os.name != "nt" or not host_exe().is_file():
            raise UserError("Updating itself works in the installed program (Windows).")
        info = self.self_update or self.check_self_update(notify=False)
        if not info:
            self.log("You already have the newest version.", "ok")
            return
        tmp = Path(tempfile.gettempdir()) / f"wfam-update-{info['version']}.exe"
        self.log(f"Downloading WoW Forever Addons {info['version']}…")
        GitHub().download(info["asset"], tmp)
        with open(tmp, "rb") as f:
            if f.read(2) != b"MZ" or tmp.stat().st_size != int(info["asset"].get("size") or tmp.stat().st_size):
                tmp.unlink(missing_ok=True)
                raise UserError("The downloaded update is damaged — try again later.")
        self.log("Installing the update — the program restarts in a moment.", "ok")
        subprocess.Popen([str(tmp), "/quiet", "/restart"] + (["/tray"] if background else []), cwd=str(tmp.parent),
                         close_fds=True,
                         creationflags=0x00000008 | 0x00000200)   # detached: it closes and replaces this program

    def notify(self, title: str, text: str):
        with self.store.lock:
            self._notice_id += 1
            self.notices.append({"id": self._notice_id, "t": time.time(), "title": title, "text": text[:240]})
            del self.notices[:-20]
