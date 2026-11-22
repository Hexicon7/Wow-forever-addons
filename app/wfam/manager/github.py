"""Addons and newer versions from GitHub releases."""
from __future__ import annotations

import tempfile
from pathlib import Path

from ..addonfiles import install_zip_into, pick_primary, zip_supports_interface
from ..config import DEFAULT_INTERFACE
from ..errors import UserError
from ..sources import GitHub, github_repo_of
from ..util import now_iso, rmtree


class GitHubMixin:
    def _try_github(self, mod: dict, rec: dict, pinned: bool) -> bool:
        """Install a CurseForge-site-only addon from its GitHub releases. True if it worked."""
        if not self.store.config.get("use_github", True):
            return False
        repo = github_repo_of(mod)
        if not repo:
            return False
        wanted = str(self.store.config.get("interface") or DEFAULT_INTERFACE)
        try:
            rel, asset = GitHub().find_forever(repo, self.store.config.get("channel", "release"), wanted)
            if not asset:
                self.log(f"{mod['name']}: its GitHub page ({repo}) has no WoW Forever build — "
                         f"using CurseForge's download page instead.", "warn")
                return False
            self._install_from_github(mod, rec, repo, rel, asset, pinned)
            return True
        except UserError as e:
            self.log(f"{mod['name']}: GitHub didn't work ({e}) — using CurseForge's download page instead.", "warn")
            return False

    def _install_from_github(self, mod, rec, repo, rel, asset, pinned=False, prefetched=None):
        addons_dir = self.addons_dir()
        key = str(mod["id"])
        tag = rel.get("name") or rel.get("tag_name") or asset["name"]
        wanted = str(self.store.config.get("interface") or DEFAULT_INTERFACE)
        tmp_dir = Path(tempfile.mkdtemp(prefix="wfam-gh-", dir=self.store.dir))
        try:
            if prefetched:
                zp, digest = prefetched
            else:
                self.log(f"Downloading {mod['name']} {tag} from GitHub ({repo})…")
                zp = tmp_dir / "addon.zip"
                digest = GitHub().download(asset, zp)
            if not zip_supports_interface(zp, wanted):
                raise UserError(f"the GitHub release {tag} isn't built for WoW Forever (interface {wanted})")
            owners = self._owners(exclude_key=key)
            prev = self._archive_current(key, rec, digest)
            analysis = install_zip_into(addons_dir, zp, rec.get("folders") or [], set(owners))
            self._push_previous(key, rec, prev)
        finally:
            rmtree(tmp_dir)
        info = {"release_id": rel["id"], "tag": rel.get("tag_name"), "name": tag, "asset": asset["name"],
                "date": rel.get("published_at"), "url": rel.get("html_url"), "prerelease": bool(rel.get("prerelease"))}
        with self.store.lock:
            rec.update({
                "folders": analysis["folders"],
                "primary": pick_primary(analysis["folders"], analysis["toc_folders"], rec.get("slug", ""), rec["name"]),
                "installed_file_id": None, "installed_version": tag, "installed_file_name": asset["name"],
                "installed_file_date": rel.get("published_at"), "installed_release_type": 2 if rel.get("prerelease") else 1,
                "installed_sha1": digest, "installed_at": now_iso(), "manual": False, "pinned": pinned,
                "source": "github", "github": repo, "gh_installed": info,
            })
            rec.pop("wago_installed", None)
            if not rec.get("gh_latest") or rec["gh_latest"]["release_id"] <= rel["id"]:
                rec["gh_latest"] = info
            self._set_latest_github(rec)
            rec.pop("awaiting", None)
            self.store.save_state()
        n = len(analysis["folders"])
        self.log(f"Installed {mod['name']} {tag} from GitHub ({n} folder{'s' if n != 1 else ''}).", "ok")

    @staticmethod
    def _set_latest_github(rec: dict):
        gl = rec.get("gh_latest") or {}
        rec.update(latest_file_id=None, latest_sha1=None, latest_version=gl.get("name"),
                   latest_date=gl.get("date"), latest_release_type=2 if gl.get("prerelease") else 1,
                   latest_checked_at=now_iso())
