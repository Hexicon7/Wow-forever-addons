"""Export / import and share codes."""
from __future__ import annotations

import base64
import json
import re
import zlib

from ..config import APP_NAME, TUKUI_KEY_PREFIX, is_tukui_key, is_wago_key
from ..errors import UserError
from ..util import now_iso


class SharingMixin:
    def export_list(self) -> dict:
        items = []
        for k, r in sorted(self.store.addons.items(), key=lambda kv: (kv[1].get("name") or "").lower()):
            if is_tukui_key(k):
                items.append({"tukui": k[len(TUKUI_KEY_PREFIX):], "name": r.get("name"), "source": "tukui",
                              "version": r.get("installed_version"), "pinned": bool(r.get("pinned")), "url": r.get("url")})
                continue
            if is_wago_key(k):
                items.append({"wago": (r.get("wago") or {}).get("id"), "name": r.get("name"), "source": "wago",
                              "version": r.get("installed_version"), "pinned": bool(r.get("pinned")), "url": r.get("url")})
                continue
            items.append({"id": int(k), "name": r.get("name"), "version": r.get("installed_version"),
                          "file_id": r.get("installed_file_id"), "pinned": bool(r.get("pinned")),
                          "source": r.get("source") or "curseforge", "url": r.get("url")})
        return {"app": APP_NAME, "format": 1, "exported_at": now_iso(), "addons": items}

    SHARE_PREFIX = "WFA1."

    def share_code(self) -> str:
        """The whole list as one line of text, to paste in Discord or into the app on another PC."""
        items = []
        for k, r in sorted(self.store.addons.items(), key=lambda kv: (kv[1].get("name") or "").lower()):
            if is_tukui_key(k):
                items.append("t:" + k[len(TUKUI_KEY_PREFIX):])
            elif is_wago_key(k):
                if (r.get("wago") or {}).get("id"):
                    items.append("w:" + str(r["wago"]["id"]))
            else:
                fid = r.get("installed_file_id") if r.get("pinned") else None
                items.append([int(k), int(fid)] if fid else int(k))
        raw = zlib.compress(json.dumps(items, separators=(",", ":")).encode("utf-8"), 9)
        return self.SHARE_PREFIX + base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    def _items_from_code(self, text: str) -> list[dict]:
        body = re.sub(r"\s+", "", text)[len(self.SHARE_PREFIX):]
        try:
            dec = zlib.decompressobj()
            raw = dec.decompress(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)), 1 << 20)
            assert not dec.unconsumed_tail          # refuse anything that unpacks to more than 1 MB
            data = json.loads(raw.decode("utf-8"))
            assert isinstance(data, list)
        except Exception:
            raise UserError("That share code is damaged — copy the whole line again.")
        out = []
        for it in data[:2000]:
            if isinstance(it, str) and it.startswith("w:"):
                out.append({"wago": it[2:], "name": it[2:]})
            elif isinstance(it, str) and it.startswith("t:"):
                out.append({"tukui": it[2:], "name": it[2:]})
            elif isinstance(it, int):
                out.append({"id": it})
            elif isinstance(it, list) and it and all(isinstance(x, int) for x in it):
                out.append({"id": it[0], "file_id": it[1] if len(it) > 1 else None, "pinned": len(it) > 1})
        return out

    def op_import(self, data: bytes, allow_links: bool = False):
        text = data.decode("utf-8-sig", "replace").strip()
        if text.startswith(self.SHARE_PREFIX):
            items = self._items_from_code(text)
        else:
            try:
                doc = json.loads(text)
                items = doc["addons"]
                assert isinstance(items, list)
            except Exception:
                if allow_links and text and not text.startswith(("{", "[")):
                    return self.op_add(text)          # a plain list of links or names
                raise UserError("That isn't an addon list exported by this app.")
        refs, wago_ids, tukui_slugs = [], [], []
        for it in items:
            if not isinstance(it, dict):
                continue
            if it.get("tukui"):
                slug = str(it["tukui"]).lower()
                key = TUKUI_KEY_PREFIX + slug
                if re.fullmatch(r"[a-z0-9_-]{1,40}", slug) and not (key in self.store.addons
                                                                     and self._folders_present(self.store.addons[key])):
                    tukui_slugs.append(slug)
                continue
            if it.get("wago"):
                wid = str(it["wago"])
                if not any(str((r.get("wago") or {}).get("id")) == wid and self._folders_present(r)
                           for r in self.store.addons.values()):
                    wago_ids.append(wid)
                continue
            try:
                mid = int(it["id"])
            except (KeyError, TypeError, ValueError):
                continue
            if str(mid) in self.store.addons and self._folders_present(self.store.addons[str(mid)]):
                continue
            fid = it.get("file_id") if it.get("pinned") else None
            refs.append({"kind": "id", "id": mid, "file": int(fid) if fid else None, "raw": it.get("name") or str(mid)})
        if not refs and not wago_ids and not tukui_slugs:
            self.log("Everything in that list is already installed.", "ok")
            return
        self.log(f"Importing {len(refs) + len(wago_ids) + len(tukui_slugs)} addon(s)…")
        if refs:
            self._add_refs(refs)
        for slug in tukui_slugs:
            try:
                self.op_add_tukui(slug)
            except UserError as e:
                self.log(f"{slug}: {e}", "error")
        if wago_ids and not self.wago():
            self.log(f"{len(wago_ids)} addon(s) in the list are only on Wago — add your Wago key in Settings "
                     f"and import again to get them.", "warn")
            return
        for wid in wago_ids:
            try:
                self.op_add_wago(wid)
            except UserError as e:
                self.log(f"Wago addon {wid}: {e}", "error")
