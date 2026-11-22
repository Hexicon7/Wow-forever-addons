"""ForeverAuras (WeakAuras for WoW Forever) from wago.io."""
from __future__ import annotations

import os
import re
from pathlib import Path

from ..config import COMPANION_FOLDER, DEFAULT_INTERFACE
from ..errors import UserError
from ..sources import AURA_CAT_NAMES, WagoAuras, lua_str
from ..util import now_iso, rmtree


class AurasMixin:
    def aura_search(self, q: str, category: str, sort: str, page: int) -> dict:
        r = WagoAuras().search(q, category, sort, page)
        pending = self.store.state.get("auras") or {}
        pend_ids = {v.get("id") for v in pending.values()}
        hits = []
        for h in r.get("hits") or []:
            if not isinstance(h, dict) or not h.get("id") or h.get("hidden"):
                continue
            hits.append({"id": str(h["id"]), "slug": h.get("slug") or h["id"], "name": h.get("name") or h["id"],
                         "author": h.get("userName") or "", "version": h.get("versionString") or "",
                         "summary": re.sub(r"\s+", " ", (h.get("descriptionSanitized") or h.get("description") or ""))[:300],
                         "thumb": h.get("thumbnailStatic") or h.get("thumbnail") or "",
                         "installs": int(h.get("installs") or 0), "stars": int(h.get("stars") or 0),
                         "date": h.get("timestamp") or 0,
                         "categories": [AURA_CAT_NAMES[c] for c in h.get("categories") or [] if c in AURA_CAT_NAMES][:3],
                         "url": f"https://wago.io/{h.get('slug') or h['id']}",
                         "pending": str(h["id"]) in pend_ids})
        total = int(r.get("total") or 0)
        page = max(0, int(page or 0))
        return {"results": hits, "total": total, "next": page + 1 if hits and (page + 1) * 20 < total else None}

    def _auras_dir(self) -> Path:
        p = self.store.dir / "auras"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def foreverauras_installed(self) -> bool:
        d = self.store.config.get("addons_dir")
        return bool(d) and (Path(d) / "ForeverAuras").is_dir()

    def op_aura_add(self, aura_id: str):
        """Put an aura in ForeverAuras' "ready to install" list (the in-game Companion list)."""
        aura_id = str(aura_id or "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{2,64}", aura_id):
            raise UserError("That doesn't look like a wago.io aura.")
        self.addons_dir()
        wa = WagoAuras()
        det = wa.details(aura_id)
        game = str(det.get("game") or "").lower()
        if game != "forever" and "FOREVER" not in str(det.get("type") or "").upper():
            raise UserError(f"{det.get('name') or aura_id} isn't made for WoW Forever (ForeverAuras).")
        vers = ((det.get("versions") or {}).get("versions") or [{}])
        latest = max(vers, key=lambda v: int(v.get("version") or 0)) if vers else {}
        encoded = wa.encoded(aura_id, latest.get("versionString"))
        if not encoded.startswith("!"):
            raise UserError("wago.io didn't send an import string for that aura.")
        slug = str(det.get("slug") or aura_id)
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", slug):
            slug = aura_id
        if slug in self._imported_aura_slugs() or aura_id in self._imported_aura_slugs():
            self.log(f"{det.get('name') or slug} is already in ForeverAuras in game.", "ok")
            return
        (self._auras_dir() / f"{aura_id}.txt").write_text(encoded, "utf-8")
        note = (latest.get("changelog") or {}).get("text") if isinstance(latest.get("changelog"), dict) else ""
        with self.store.lock:
            self.store.state.setdefault("auras", {})[slug] = {
                "id": aura_id, "name": str(det.get("name") or slug)[:120],
                "author": str((det.get("user") or {}).get("name") or "")[:60],
                "version": int(latest.get("version") or 1), "semver": str(latest.get("versionString") or ""),
                "note": str(note or "")[:300], "added_at": now_iso()}
            self.store.save_state()
        self.write_companion()
        hint = "" if self.foreverauras_installed() else " ForeverAuras itself isn't in your AddOns folder yet — install it first."
        self.log(f"{det.get('name') or slug} is ready in game: log in (or type /reload) and open /fa — "
                 f"it's at the top under “Ready to install”.{hint}", "ok")

    def aura_remove(self, slug: str):
        with self.store.lock:
            gone = (self.store.state.get("auras") or {}).pop(str(slug), None)
            self.store.save_state()
        if gone:
            try:
                (self._auras_dir() / f"{gone['id']}.txt").unlink()
            except OSError:
                pass
        self.write_companion()

    def aura_code(self, ident: str) -> str:
        """The import string, to paste in game by hand (/fa → Import)."""
        ident = str(ident or "")
        a = (self.store.state.get("auras") or {}).get(ident)
        if a:
            try:
                return (self._auras_dir() / f"{a['id']}.txt").read_text("utf-8")
            except OSError:
                ident = a["id"]
        if not re.fullmatch(r"[A-Za-z0-9_.-]{2,80}", ident):
            raise UserError("That doesn't look like a wago.io aura.")
        wa = WagoAuras()
        det = wa.details(ident)
        if str(det.get("game") or "").lower() != "forever" and "FOREVER" not in str(det.get("type") or "").upper():
            raise UserError(f"{det.get('name') or ident} isn't made for WoW Forever (ForeverAuras).")
        code = wa.encoded(ident)
        if not code.startswith("!"):
            raise UserError("wago.io didn't send an import string for that aura.")
        return code

    def _imported_aura_slugs(self) -> set[str]:
        """Slugs of wago.io auras already imported in ForeverAuras on any account (from its saved data)."""
        d = self.store.config.get("addons_dir")
        found: set[str] = set()
        if not d:
            return found
        wtf = Path(d).parent.parent / "WTF" / "Account"
        try:
            files = list(wtf.glob("*/SavedVariables/ForeverAurasSaved.lua"))
        except OSError:
            return found
        for f in files:
            try:
                text = f.read_text("utf-8", errors="replace")
            except OSError:
                continue
            found |= set(re.findall(r'wago\.io/([A-Za-z0-9_.-]+)', text))
        return found

    def write_companion(self):
        """(Re)write the small ForeverAurasCompanion addon that hands ForeverAuras the auras you picked.
        Auras already imported in game are dropped from it."""
        d = self.store.config.get("addons_dir")
        if not d or not Path(d).is_dir():
            return
        auras = self.store.state.get("auras") or {}
        folder = Path(d) / COMPANION_FOLDER
        if not auras and not folder.exists():
            return
        done = self._imported_aura_slugs() & set(auras) if auras else set()
        if done:
            with self.store.lock:
                for slug in done:
                    a = auras.pop(slug, None)
                    if a:
                        try:
                            (self._auras_dir() / f"{a['id']}.txt").unlink()
                        except OSError:
                            pass
                self.store.save_state()
        if not auras:
            if folder.is_dir():
                rmtree(folder)
            return
        lines = ["-- Written by WoW Forever Addons. Auras you picked on wago.io, waiting to be imported.",
                 "ForeverAurasCompanion = {", "  WeakAuras = {", "    slugs = {},", "    stash = {"]
        for slug, a in sorted(auras.items()):
            try:
                enc = (self._auras_dir() / f"{a['id']}.txt").read_text("utf-8")
            except OSError:
                continue
            lines.append(f"      [{lua_str(slug)}] = {{ name = {lua_str(a.get('name') or slug)}, "
                         f"author = {lua_str(a.get('author') or '')}, encoded = {lua_str(enc)}, "
                         f"wagoVersion = {lua_str(str(a.get('version') or 1))}, wagoSemver = {lua_str(a.get('semver') or '')}, "
                         f"versionNote = {lua_str(a.get('note') or '')} }},")
        lines += ["    },", "  },", "}", ""]
        iface = str(self.store.config.get("interface") or DEFAULT_INTERFACE)
        toc = (f"## Interface: {iface}\n## Title: ForeverAuras Companion\n"
               f"## Notes: Auras picked in WoW Forever Addons, ready to import in ForeverAuras.\n"
               f"## Author: WoW Forever Addons\n## Version: 1\n## Dependencies: ForeverAuras\n\ndata.lua\n")
        folder.mkdir(exist_ok=True)
        for name, content in (("ForeverAurasCompanion.toc", toc), ("data.lua", "\n".join(lines))):
            tmp = folder / (name + ".tmp")
            tmp.write_text(content, "utf-8")
            os.replace(tmp, folder / name)
