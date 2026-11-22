"""ElvUI and Tukui from tukui.org."""
from __future__ import annotations

import tempfile
import time
from pathlib import Path

from ..addonfiles import install_zip_into, pick_primary, zip_supports_interface
from ..config import DEFAULT_INTERFACE, TUKUI_KEY_PREFIX, is_tukui_key
from ..errors import UserError
from ..sources import TukuiOrg
from ..util import now_iso, rmtree


class TukuiMixin:
    def tukui_list(self) -> list[dict]:
        out = []
        for a in TukuiOrg().addons():
            key = TUKUI_KEY_PREFIX + a["slug"]
            rec = self.store.addons.get(key)
            out.append({"slug": a["slug"], "key": key, "name": a.get("name") or a["slug"], "author": a.get("author") or "",
                        "version": str(a.get("version") or ""), "date": a.get("last_update") or "",
                        "summary": a.get("small_desc") or "", "logo": a.get("logo_square_url") or a.get("logo_url") or "",
                        "url": a.get("web_url") or "https://tukui.org", "patch": str(a.get("patch") or ""),
                        "installed": bool(rec and self._folders_present(rec)),
                        "installed_version": (rec or {}).get("installed_version"),
                        "no_forever": a["slug"] in (self.store.state.get("tukui_no_forever") or {})})
        return out

    def op_add_tukui(self, slug: str):
        slug = str(slug or "").lower()
        info = TukuiOrg().addon(slug)
        key = TUKUI_KEY_PREFIX + slug
        name = info.get("name") or slug
        self.backup_wtf()
        with self.store.lock:
            rec = self.store.addons.get(key)
            new = rec is None
            if new:
                rec = {}
            rec.update({"name": name, "slug": slug, "url": info.get("web_url") or "https://tukui.org",
                        "summary": info.get("small_desc") or "", "authors": [info["author"]] if info.get("author") else [],
                        "logo": info.get("logo_square_url") or info.get("logo_url") or "", "source": "tukui",
                        "tukui_latest": self._tukui_latest(info), "latest_checked_at": time.time()})
        try:
            self._install_from_tukui(key, rec, info)
        except UserError:
            raise
        if new:
            with self.store.lock:
                self.store.addons[key] = rec
                self.store.save_state()

    @staticmethod
    def _tukui_latest(info: dict) -> dict:
        return {"version": str(info.get("version") or ""), "url": info.get("url") or "",
                "date": info.get("last_update") or "", "changelog_url": info.get("changelog_url") or ""}

    def _install_from_tukui(self, key: str, rec: dict, info: dict, pinned: bool = False):
        addons_dir = self.addons_dir()
        wanted = str(self.store.config.get("interface") or DEFAULT_INTERFACE)
        name, ver = info.get("name") or key, str(info.get("version") or "")
        self.log(f"Downloading {name} {ver} from tukui.org…")
        tmp_dir = Path(tempfile.mkdtemp(prefix="wfam-tukui-", dir=self.store.dir))
        try:
            zp = tmp_dir / "addon.zip"
            digest = TukuiOrg().download(info.get("url") or "", zp)
            if not zip_supports_interface(zp, wanted):
                with self.store.lock:
                    self.store.state.setdefault("tukui_no_forever", {})[info.get("slug") or key] = ver
                raise UserError(f"{name} {ver} isn't built for WoW Forever yet (interface {wanted}) — nothing was changed")
            with self.store.lock:
                (self.store.state.get("tukui_no_forever") or {}).pop(info.get("slug") or "", None)
            owners = self._owners(exclude_key=key)
            prev = self._archive_current(key, rec, digest) if rec.get("folders") else None
            analysis = install_zip_into(addons_dir, zp, rec.get("folders") or [], set(owners))
            if prev:
                self._push_previous(key, rec, prev)
        finally:
            rmtree(tmp_dir)
        with self.store.lock:
            rec.update({
                "folders": analysis["folders"],
                "primary": pick_primary(analysis["folders"], analysis["toc_folders"], rec.get("slug", ""), name),
                "installed_file_id": None, "installed_version": ver, "installed_file_name": None,
                "installed_file_date": info.get("last_update"), "installed_release_type": 1,
                "installed_sha1": digest, "installed_at": now_iso(), "manual": False, "pinned": pinned,
                "source": "tukui", "tukui_latest": self._tukui_latest(info),
            })
            rec.pop("awaiting", None)
            self.store.save_state()
        n = len(analysis["folders"])
        self.log(f"Installed {name} {ver} from tukui.org ({n} folder{'s' if n != 1 else ''}).", "ok")

    def _check_tukui(self, recs: dict):
        keys = [k for k in recs if is_tukui_key(k)]
        if not keys:
            return
        try:
            infos = {a["slug"]: a for a in TukuiOrg().addons()}
        except UserError as e:
            self.log(f"Couldn't check ElvUI/Tukui: {e}", "warn")
            return
        with self.store.lock:
            for k in keys:
                info = infos.get(k[len(TUKUI_KEY_PREFIX):])
                if info:
                    recs[k]["tukui_latest"] = self._tukui_latest(info)
                    recs[k]["latest_checked_at"] = time.time()

    def _update_tukui(self, key: str, rec: dict):
        info = TukuiOrg().addon(key[len(TUKUI_KEY_PREFIX):])
        self._install_from_tukui(key, rec, info, rec.get("pinned", False))
