"""The local web server the window talks to: pages, /api/state and every action."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import threading
import time
import traceback
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .addonfiles import game_interface
from .config import (
    APP_NAME,
    APP_VERSION,
    BG_CHECK_CHOICES,
    CHANNELS,
    DEFAULT_INTERFACE,
    IDLE_EXIT_SECONDS,
    MAX_DOWNLOAD_BYTES,
    SELF_UPDATE_EVERY,
)
from .errors import BusyError, CurseForgeError, UserError
from .manager import Manager
from .sources import (
    AURA_CATEGORIES,
    CurseForge,
    Wago,
    file_type_ids,
    github_repo_of,
    has_forever_file,
    pick_latest,
)
from .system import (
    _window_branding_values,
    autostart_enabled,
    brand_app_windows,
    create_shortcuts,
    detect_addon_dirs,
    make_icon_png,
    normalize_addons_dir,
    open_window,
    pid_alive,
    set_autostart,
)
from .util import clean_key, key_kind
from .watcher import DownloadWatcher
from .webui import TYPES, asset


class App:
    def __init__(self, manager: Manager):
        self.m = manager
        self.port = None
        self.server: ThreadingHTTPServer | None = None
        self.bye_at = 0.0

    def allowed_hosts(self):
        return {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}

    def handle_post(self, path: str, body: dict, raw: bytes, headers) -> dict:
        m = self.m
        if path == "/api/settings":
            changes = {}
            if body.get("api_key"):
                changes["api_key"] = clean_key(body["api_key"])
            if body.get("clear_api_key"):
                changes["api_key"] = ""
            if body.get("wago_key"):
                changes["wago_key"] = clean_key(body["wago_key"])
            if body.get("clear_wago_key"):
                changes["wago_key"] = ""
            if body.get("wago_game_version"):
                changes["wago_game_version"] = re.sub(r"[^a-z0-9_-]", "", str(body["wago_game_version"]).lower()) or "forever"
            if "addons_dir" in body and body["addons_dir"].strip():
                p = normalize_addons_dir(body["addons_dir"])
                if str(p) != m.store.config.get("addons_dir") and m.job["running"]:
                    raise BusyError("Wait for the current task to finish before changing the folder.")
                if not p.is_dir():
                    p.mkdir(parents=True, exist_ok=True)
                changes["addons_dir"] = str(p)
            if "version_type_id" in body:
                v = body["version_type_id"]
                changes["version_type_id"] = int(v) if v not in (None, "", "auto") else None
                changes["version_type_name"] = body.get("version_type_name", "") if changes["version_type_id"] else ""
            if body.get("channel") in CHANNELS:
                changes["channel"] = body["channel"]
            if "sync_dir" in body:
                v = str(body["sync_dir"] or "").strip().strip('"')
                if v and not Path(v).is_dir():
                    raise UserError("That sync folder doesn't exist. Make it first (e.g. inside OneDrive or Dropbox).")
                changes["sync_dir"] = v
            if "ui_scale" in body:
                v = str(body["ui_scale"])
                if v not in ("90", "100", "110", "125", "system", "fit"):
                    raise UserError("Unknown size.")
                changes["ui_scale"] = v
            if "self_update_dismissed" in body:
                changes["self_update_dismissed"] = re.sub(r"[^0-9.]", "", str(body["self_update_dismissed"]))[:20]
            if "interface" in body:
                typed = re.sub(r"[^0-9]", "", str(body["interface"]))
                found = game_interface(changes.get("addons_dir") or m.store.config.get("addons_dir"))
                changes["interface_auto"] = not typed or typed == found
                changes["interface"] = typed or found or DEFAULT_INTERFACE
            if "auto_dependencies" in body:
                changes["auto_dependencies"] = bool(body["auto_dependencies"])
            if "backup_keep" in body:
                changes["backup_keep"] = max(1, min(100, int(body["backup_keep"] or 3)))
            for flag in ("auto_manual", "delete_after_install", "use_github", "backup_wtf", "auto_check", "use_wago",
                         "app_window", "tray", "bg_auto_update"):
                if flag in body:
                    changes[flag] = bool(body[flag])
            if "backup_dir" in body and (body["backup_dir"] or "").strip() != (m.store.config.get("backup_dir") or ""):
                m.set_backup_dir(str(body["backup_dir"] or ""))
            if "update_repo" in body:
                changes["update_repo"] = str(body["update_repo"] or "").strip()
            if "bg_check_hours" in body:
                h = int(body["bg_check_hours"] or 0)
                changes["bg_check_hours"] = h if h in BG_CHECK_CHOICES else 3
            if "autostart" in body and bool(body["autostart"]) != autostart_enabled():
                set_autostart(bool(body["autostart"]))
            if "downloads_dir" in body:
                d = str(body["downloads_dir"] or "").strip().strip('"')
                if d and not Path(d).is_dir():
                    raise UserError(f"Downloads folder not found: {d}")
                changes["downloads_dir"] = os.path.normpath(d) if d else ""
            m.store.update_config(**changes)
            if set(body) - {"self_update_dismissed"}:
                m.log("Settings saved.", "ok")
            return {"ok": True}
        if path == "/api/test_key":
            raw_key = body.get("api_key") or m.store.config.get("api_key") or ""
            key = clean_key(raw_key)
            if not key:
                raise UserError("Paste a key first.")
            problems = []
            if len(key) < 20:
                problems.append(f"it is only {len(key)} characters long — it looks cut off")
            hint = (" Also: " + "; ".join(problems) + ".") if problems else ""
            try:
                types = CurseForge(key).version_types()
            except CurseForgeError as e:
                return {"ok": False, "message": f"{e}{hint} (key length {len(key)}, starts with {key[:4]}…)"}
            forever = [t.get("name") for t in types if "forever" in (t.get("name") or "").lower()]
            return {"ok": True, "message": f"Key works. CurseForge lists {len(types)} WoW versions"
                                           + (f", including {', '.join(forever)}." if forever else
                                              " but none called “Forever” — pick one under game version.")}
        if path == "/api/search":
            return self._search(m, body)
        if path == "/api/categories":
            cats = m.client().categories()
            return {"categories": sorted(({"id": c["id"], "name": c.get("name") or str(c["id"])} for c in cats
                                          if not c.get("isClass") and c.get("name")), key=lambda c: c["name"].lower())}
        if path == "/api/save_key":
            # One paste box for both keys: recognise it, test it with its own site, save it if it works.
            key = clean_key(str(body.get("key") or ""))
            if not key:
                raise UserError("Paste a key first.")
            kind = key_kind(key)
            if kind == "curseforge":
                if len(key) < 50:
                    return {"ok": False, "kind": kind, "message": f"That CurseForge key looks cut off "
                            f"({len(key)} of 60 characters) — copy the whole key again."}
                try:
                    types = CurseForge(key).version_types()
                except CurseForgeError as e:
                    return {"ok": False, "kind": kind, "message": f"CurseForge didn't accept that key: {e}"}
                m.store.update_config(api_key=key)
                hint = " (tip: also add a Wago key to compare versions there)" if not m.store.config.get("wago_key") else ""
                return {"ok": True, "kind": kind, "message": f"CurseForge key saved ✓ — it works ({len(types)} WoW versions found).{hint}"}
            gv = m.store.config.get("wago_game_version") or "forever"
            try:
                Wago(key, gv).search("bags")
            except UserError as e:
                return {"ok": False, "kind": kind, "message": f"That isn't a CurseForge key (those start with $2a$10$), "
                        f"and Wago didn't accept it either: {e}"}
            m.store.update_config(wago_key=key, use_wago=True)
            return {"ok": True, "kind": kind, "message": "Wago key saved ✓ — it works. Wago is now compared with CurseForge."}
        if path == "/api/test_wago":
            key = clean_key(body.get("wago_key") or m.store.config.get("wago_key") or "")
            if not key:
                raise UserError("Paste your Wago key first.")
            gv = m.store.config.get("wago_game_version") or "forever"
            try:
                hits = Wago(key, gv).search("bags")
            except UserError as e:
                return {"ok": False, "message": f"{str(e)[:1].upper()}{str(e)[1:]}. (key length {len(key)})"}
            return {"ok": True, "message": f"Wago key works — a test search for WoW Forever ({gv}) addons found "
                                           f"{len(hits)} result{'s' if len(hits) != 1 else ''}."}
        if path == "/api/detect":
            return {"dirs": detect_addon_dirs()}
        if path == "/api/version_types":
            types = m.client().version_types()
            types = [{"id": t["id"], "name": t.get("name") or t.get("slug") or str(t["id"])} for t in types]
            types.sort(key=lambda t: ("forever" not in t["name"].lower(), t["name"].lower()))
            return {"types": types}
        if path == "/api/add":
            m.run_job("Installing addons", m.op_add, body.get("text", ""))
            return {"ok": True}
        if path == "/api/check":
            m.run_job("Checking for updates", m.op_check)
            return {"ok": True}
        if path == "/api/update_all":
            m.run_job("Updating all addons", m.op_check_and_update)
            return {"ok": True}
        if path == "/api/update":
            m.run_job("Updating", m.op_update, [str(body.get("key"))])
            return {"ok": True}
        if path == "/api/fix_deps":
            m.run_job("Installing missing requirements", m.op_fix_deps, str(body.get("key")))
            return {"ok": True}
        if path == "/api/open_url":
            # Links clicked in the desktop app window open in the normal web browser.
            u = str(body.get("url") or "")
            parts = urllib.parse.urlsplit(u)
            if parts.scheme not in ("http", "https") or not parts.netloc or len(u) > 2000:
                raise UserError("That link can't be opened.")
            webbrowser.open(u)
            return {"ok": True}
        if path == "/api/shortcuts":
            create_shortcuts()
            m.log("Added “WoW Forever Addons” to your desktop and Start menu. Right-click it to pin it to the taskbar.", "ok")
            return {"ok": True}
        if path == "/api/backup_now":
            m.run_job("Backing up addon settings", m.op_backup_now)
            return {"ok": True}
        if path == "/api/restore":
            m.run_job("Restoring addon settings", m.op_restore_wtf, str(body.get("name")))
            return {"ok": True}
        if path == "/api/revert":
            m.run_job("Reverting", m.op_revert, str(body.get("key")))
            return {"ok": True}
        if path == "/api/changelog":
            return m.changelog(str(body.get("key")))
        if path == "/api/ui_state":
            # Where the user was (view, sort, filters) so the next start opens the same way. Quiet: no log line.
            clean = {}
            for k, v in (body.get("state") or {}).items() if isinstance(body.get("state"), dict) else []:
                if isinstance(k, str) and re.fullmatch(r"[a-z_]{1,20}", k) and isinstance(v, (str, int)) and len(clean) < 20:
                    clean[k] = re.sub(r"[^\w .:-]", "", str(v))[:40]
            m.store.config["ui_state"] = clean
            m.store.save_config()
            return {"ok": True}
        if path == "/api/scan":
            m.run_job("Looking up hand-installed addons", m.op_scan)
            return {"ok": True}
        if path == "/api/adopt":
            m.run_job("Taking over addon", m.op_adopt, int(body.get("mod_id")))
            return {"ok": True}
        if path == "/api/import":
            m.run_job("Importing addon list", m.op_import, raw)
            return {"ok": True}
        if path == "/api/import_text":
            text = str(body.get("text") or "")[:200000]
            if not text.strip():
                raise UserError("Paste a share code first.")
            m.run_job("Importing addon list", m.op_import, text.encode("utf-8"), True)
            return {"ok": True}
        if path == "/api/share_code":
            return {"code": m.share_code(), "count": len(m.store.addons)}
        if path == "/api/install_best":
            cf_id = body.get("id")
            cf_id = int(cf_id) if str(cf_id or "").isdigit() else None
            wid = str(body.get("wago_id") or "") or None
            if not cf_id and not wid:
                raise UserError("Nothing to install.")
            m.run_job("Installing", m.op_install_best, cf_id, wid)
            return {"ok": True}
        if path == "/api/tukui_list":
            return {"results": m.tukui_list()}
        if path == "/api/add_tukui":
            m.run_job("Installing from tukui.org", m.op_add_tukui, str(body.get("slug") or ""))
            return {"ok": True}
        if path == "/api/aura_search":
            return m.aura_search(str(body.get("q") or ""), str(body.get("category") or ""),
                                 str(body.get("sort") or "installs"), int(body.get("page") or 0))
        if path == "/api/aura_add":
            m.run_job("Getting the aura from wago.io", m.op_aura_add, str(body.get("id") or ""))
            return {"ok": True}
        if path == "/api/aura_remove":
            m.aura_remove(str(body.get("slug") or ""))
            return {"ok": True}
        if path == "/api/aura_code":
            return {"code": m.aura_code(str(body.get("slug") or body.get("id") or ""))}
        if path == "/api/aura_categories":
            return {"groups": [{"name": g, "items": [{"id": i, "name": n} for i, n in items]} for g, items in AURA_CATEGORIES]}
        if path == "/api/add_wago":
            m.run_job("Installing from Wago", m.op_add_wago, str(body.get("id") or ""))
            return {"ok": True}
        if path == "/api/diagnose":
            m.run_job("Running diagnostics", m.op_diagnose)
            return {"ok": True}
        if path == "/api/cancel_wait":
            m.op_cancel_wait(str(body.get("key")))
            return {"ok": True}
        if path == "/api/remove":
            m.run_job("Removing addon", m.op_remove, str(body.get("key")))
            return {"ok": True}
        if path == "/api/setup_export":
            m.run_job("Saving your whole setup", m.op_setup_export)
            return {"ok": True}
        if path == "/api/setup_restore":
            if raw and not body:
                m.run_job("Restoring a setup", m.op_setup_restore, raw, None)
            else:
                m.run_job("Restoring a setup", m.op_setup_restore, None, str(body.get("file") or ""))
            return {"ok": True}
        if path == "/api/sync_dismiss":
            m.sync_dismiss(str(body.get("computer") or ""), str(body.get("created") or ""))
            return {"ok": True}
        if path == "/api/errors":
            key = str(body.get("key"))
            r = m.store.addons.get(key)
            if not r:
                raise UserError("Unknown addon.")
            return {"name": r.get("name") or key, "errors": m.errors_of(r, m.insights())[:10]}
        if path == "/api/errors_ignore":
            key = str(body.get("key"))
            with m.store.lock:
                r = m.store.addons.get(key)
                if not r:
                    raise UserError("Unknown addon.")
                r["errors_ok_at"] = time.time()
            m.store.save_state()
            return {"ok": True}
        if path == "/api/pin":
            m.run_job("Pin", m.op_set_pin, str(body.get("key")), bool(body.get("pinned")))
            return {"ok": True}
        if path == "/api/manage":
            m.run_job("Taking over addon", m.op_add, str(int(body.get("project_id"))))
            return {"ok": True}
        if path == "/api/install_zip":
            name = urllib.parse.unquote(headers.get("X-Filename") or "addon.zip")
            key = headers.get("X-Mod-Key") or None
            m.run_job(f"Installing {name}", m.op_install_zip, raw, name, key)
            return {"ok": True}
        if path == "/api/open_folder":
            d = m.store.config.get("addons_dir")
            if d and Path(d).is_dir():
                if os.name == "nt":
                    os.startfile(d)  # type: ignore[attr-defined]
                elif sys.platform == "darwin":
                    os.spawnlp(os.P_NOWAIT, "open", "open", d)
                else:
                    os.spawnlp(os.P_NOWAIT, "xdg-open", "xdg-open", d)
            return {"ok": True}
        if path == "/api/self_update":
            m.run_job("Updating WoW Forever Addons", m.op_self_update)
            return {"ok": True}
        if path == "/api/check_self_update":
            info = m.check_self_update(notify=False)
            return {"update": info is not None, "version": info["version"] if info else APP_VERSION,
                    "current": APP_VERSION}
        if path == "/api/undo_batch":
            m.run_job("Undoing the last update", m.op_undo_batch)
            return {"ok": True}
        if path == "/api/wago_search":
            res, hidden = m.wago_search(str(body.get("q") or ""), bool(body.get("all")))
            return {"results": res, "hidden": hidden, "all": bool(body.get("all"))}
        if path == "/api/details":
            return m.details(int(body.get("id")))
        if path == "/api/file_changelog":
            return {"text": m.file_changelog(int(body.get("mod_id")), int(body.get("file_id")))}
        if path == "/api/install_file":
            m.run_job("Installing a specific version", m.op_install_file, int(body.get("mod_id")), int(body.get("file_id")))
            return {"ok": True}
        if path == "/api/ignore":
            m.set_ignored(list(body.get("folders") or []), bool(body.get("ignore", True)))
            return {"ok": True}
        if path == "/api/characters_save":
            m.run_job("Saving characters' addons", m.op_save_characters, list(body.get("changes") or []))
            return {"ok": True}
        if path == "/api/profile":
            m.save_profile(str(body.get("name") or ""), body.get("states"))
            return {"ok": True}
        if path == "/api/tray_check":
            m.run_job("Checking for updates", m.op_background, None, True)
            return {"ok": True}
        if path == "/api/tray_update":
            m.run_job("Updating all addons", m.op_background, True, True)
            return {"ok": True}
        if path == "/api/quit":
            threading.Thread(target=lambda: (time.sleep(0.3), self.server.shutdown()), daemon=True).start()
            return {"ok": True}
        raise UserError("Unknown action.")

    def _search(self, m: Manager, body: dict) -> dict:
        cf = m.client()
        type_id = m.ensure_type(cf)
        sort = int(body.get("sort") or 2)
        if sort not in (2, 3, 4, 6, 11, 12):
            sort = 2
        index = min(max(0, int(body.get("index") or 0)), 9950)
        query = str(body.get("q") or "").strip()[:100]
        cat = body.get("category") or None
        show_all = bool(body.get("all"))
        # CurseForge's own game-version filter lets through addons that have no WoW Forever file at all,
        # so each result is checked here too. Fetch a few more pages so a filtered page isn't nearly empty.
        results, hidden, idx, total, mods, rounds = [], 0, index, 0, [], 0
        while True:
            mods, pag = cf.search(query, type_id, sort, idx, 50, int(cat) if cat else None)
            total = int(pag.get("totalCount") or 0)
            rounds += 1
            idx += len(mods)
            for mod in mods:
                fv = has_forever_file(mod, type_id)
                if not fv and not show_all:
                    hidden += 1
                    continue
                res = self._search_result(mod, fv)
                f = pick_latest([x for x in mod.get("latestFiles") or [] if type_id in file_type_ids(x)], type_id,
                                m.store.config.get("channel", "release"))
                res["cf_version"] = (f.get("displayName") or f.get("fileName")) if f else None
                res["cf_date"] = f.get("fileDate") if f else None
                res["slug"] = mod.get("slug") or ""
                results.append(res)
            if show_all or len(results) >= 24 or not mods or idx >= min(total, 10000) or rounds >= 4:
                break
        # CurseForge only pages through the first 10,000 results.
        next_index = idx if mods and idx < min(total, 10000) else None
        wago_only = m.merge_wago(results, query if index == 0 and not cat else None, show_all)
        return {"results": results + wago_only, "total": total, "next": next_index, "hidden": hidden, "all": show_all}

    @staticmethod
    def _search_result(mod: dict, forever: bool) -> dict:
        return {
            "id": mod["id"], "name": mod.get("name") or str(mod["id"]),
            "summary": mod.get("summary") or "",
            "authors": [a.get("name") for a in (mod.get("authors") or []) if a.get("name")][:2],
            "logo": (mod.get("logo") or {}).get("thumbnailUrl") or "",
            "downloads": int(mod.get("downloadCount") or 0),
            "updated": mod.get("dateModified") or mod.get("dateReleased"),
            "url": Manager._mod_url(mod),
            "categories": [c.get("name") for c in (mod.get("categories") or []) if c.get("name")][:2],
            "manual_only": mod.get("allowModDistribution") is False,
            "github": github_repo_of(mod),
            "forever": forever,
        }

    def make_handler(self):
        app = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "WFAM"
            protocol_version = "HTTP/1.1"
            disable_nagle_algorithm = True

            def log_message(self, *a):
                pass

            def _send(self, code, payload, ctype="application/json; charset=utf-8"):
                data = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                self.end_headers()
                self.wfile.write(data)

            def _host_ok(self):
                return self.headers.get("Host", "") in app.allowed_hosts()

            def do_GET(self):
                if not self._host_ok():
                    return self._send(403, {"error": "forbidden"})
                app.m.last_contact = time.time()
                url = urllib.parse.urlsplit(self.path)
                if url.path in ("/", "/app.css", "/app.js"):
                    name = "index.html" if url.path == "/" else url.path[1:]
                    body = asset(name)
                    self.send_response(200)
                    self.send_header("Content-Type", TYPES[name])
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "no-cache" if name == "index.html"
                                     else "public, max-age=31536000, immutable")
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if url.path == "/api/state":
                    app.m.last_ui = time.time()   # the page itself is showing
                    q = urllib.parse.parse_qs(url.query)
                    since = int((q.get("since") or ["0"])[0] or 0)
                    snap = app.m.snapshot(since)
                    core = {k: v for k, v in snap.items() if k not in ("logs", "log_seq")}
                    etag = hashlib.sha1(json.dumps(core, sort_keys=True, default=str).encode()).hexdigest()[:16]
                    if (q.get("etag") or [""])[0] == etag and not snap["logs"]:
                        return self._send(200, {"same": True, "etag": etag})   # nothing changed: no redraw needed
                    snap["etag"] = etag
                    return self._send(200, snap)
                if url.path in ("/favicon.png", "/favicon.ico", "/icon.png"):
                    if not hasattr(app, "_icon"):
                        app._icon = make_icon_png(64)
                    self.send_response(200)
                    self.send_header("Content-Type", "image/png")
                    self.send_header("Content-Length", str(len(app._icon)))
                    self.send_header("Cache-Control", "max-age=86400")
                    self.end_headers()
                    self.wfile.write(app._icon)
                    return
                if url.path == "/api/characters":
                    return self._send(200, app.m.characters())
                if url.path == "/api/export":
                    data = json.dumps(app.m.export_list(), indent=2).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Disposition",
                                     f'attachment; filename="wow-forever-addons-{time.strftime("%Y-%m-%d")}.json"')
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                if url.path == "/api/notices":
                    q = urllib.parse.parse_qs(url.query)
                    after = int((q.get("after") or ["0"])[0] or 0)
                    with app.m.store.lock:
                        items = [n for n in app.m.notices if n["id"] > after]
                    ui = getattr(app.m, "last_ui", 0)
                    return self._send(200, {"notices": items, "last": app.m._notice_id,
                                            "ui_age": (time.time() - ui) if ui else -1,
                                            "tray": bool(app.m.store.config.get("tray", True)),
                                            "ui_scale": str(app.m.store.config.get("ui_scale") or "100"),
                                            "busy": bool(app.m.job["running"])})
                if url.path == "/api/ping":
                    return self._send(200, {"app": APP_NAME, "version": APP_VERSION})
                return self._send(404, {"error": "not found"})

            def do_POST(self):
                origin = self.headers.get("Origin")
                if urllib.parse.urlsplit(self.path).path == "/api/bye":
                    # The window is closing (sent with navigator.sendBeacon, which can't add our header).
                    if self._host_ok() and (not origin or origin in {f"http://{h}" for h in app.allowed_hosts()}):
                        self.rfile.read(int(self.headers.get("Content-Length") or 0))
                        app.bye_at = time.time()
                        return self._send(200, {"ok": True})
                    return self._send(403, {"error": "forbidden"})
                if (not self._host_ok() or self.headers.get("X-WFAM") != "1"
                        or (origin and origin not in {f"http://{h}" for h in app.allowed_hosts()})):
                    return self._send(403, {"error": "forbidden"})
                app.m.last_contact = time.time()
                length = int(self.headers.get("Content-Length") or 0)
                if length > MAX_DOWNLOAD_BYTES:
                    return self._send(413, {"error": "too large"})
                raw = self.rfile.read(length) if length else b""
                body = {}
                if "json" in (self.headers.get("Content-Type") or ""):
                    try:
                        body = json.loads(raw.decode("utf-8") or "{}")
                    except ValueError:
                        return self._send(400, {"error": "bad json"})
                try:
                    return self._send(200, app.handle_post(urllib.parse.urlsplit(self.path).path, body, raw,
                                                           self.headers))
                except BusyError as e:
                    return self._send(409, {"error": str(e)})
                except UserError as e:
                    return self._send(400, {"error": str(e)})
                except Exception as e:
                    app.m.log(traceback.format_exc(), "debug")
                    return self._send(500, {"error": f"Unexpected error: {e}"})

        return Handler

    def serve(self, port: int, open_browser: bool = True, idle_exit: bool = True, ready_file: str | None = None,
              host_pid: int = 0):
        self.m.hosted = bool(host_pid)
        def ready(url: str):
            if ready_file:   # tell the desktop window where to connect
                tmp = Path(ready_file + ".tmp")
                tmp.write_text(url, encoding="utf-8")
                os.replace(tmp, ready_file)
        Server = type("Server", (ThreadingHTTPServer,), {"allow_reuse_address": os.name != "nt",
                                                          "daemon_threads": True})
        handler = self.make_handler()
        local = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # never route localhost via a proxy
        base = f"http://127.0.0.1:{port}"
        try:
            self.server = Server(("127.0.0.1", port), handler)
        except OSError:
            info = {}
            try:
                with local.open(base + "/api/ping", timeout=2) as r:
                    info = json.loads(r.read().decode() or "{}")
            except Exception:
                pass
            if info.get("app") == APP_NAME and info.get("version") == APP_VERSION:
                # Same version already running: just show it.
                ready(base + "/")
                if open_browser:
                    open_window(base + "/", self.m.store.config.get("app_window", True))
                return
            if info.get("app") == APP_NAME:
                # An older/newer copy is running: ask it to quit, then take over its port.
                print("Closing the other running copy of the manager…", flush=True)
                try:
                    local.open(urllib.request.Request(base + "/api/quit", data=b"{}", method="POST", headers={
                        "X-WFAM": "1", "Content-Type": "application/json"}), timeout=3).close()
                except Exception:
                    pass
            for _ in range(20):
                try:
                    self.server = Server(("127.0.0.1", port), handler)
                    break
                except OSError:
                    time.sleep(0.4)
            else:
                self.server = Server(("127.0.0.1", 0), handler)
        self.port = self.server.server_address[1]
        ready(f"http://127.0.0.1:{self.port}/")
        if self.port == port:   # we're the only copy running: safe to tidy up after earlier crashes
            def tidy():
                try:
                    self.m.cleanup_leftovers()
                except Exception:
                    pass
            threading.Thread(target=tidy, daemon=True).start()
        url = f"http://127.0.0.1:{self.port}/"
        print(f"{APP_NAME} running at {url}  (close with the Quit button)", flush=True)
        if open_browser:
            threading.Timer(0.3, lambda: open_window(url, self.m.store.config.get("app_window", True))).start()
        self.m.interactive = True
        threading.Thread(target=DownloadWatcher(self.m).run, daemon=True).start()
        if os.name == "nt":
            def brand():
                vals = _window_branding_values()
                start = time.time()
                while True:
                    brand_app_windows(vals)
                    time.sleep(1.5 if time.time() - start < 60 else 10)
            threading.Thread(target=brand, daemon=True).start()

        def auto_check():
            time.sleep(1.5)
            st, cfg = self.m.store, self.m.store.config
            if cfg.get("api_key") and cfg.get("addons_dir") and st.addons:
                try:
                    if host_pid:   # desktop program: check, install per settings, and notify
                        self.m.run_job("Checking for updates", self.m.op_background)
                    else:
                        self.m.run_job("Checking for updates", self.m.op_check)
                except BusyError:
                    pass
        self.m.meta_ready = False
        threading.Thread(target=self.m.warm_meta, daemon=True).start()
        threading.Thread(target=lambda: self.m._quiet(self.m.tidy_storage), daemon=True).start()
        threading.Thread(target=auto_check, daemon=True).start()

        def sync_loop():
            tick = float(os.environ.get("WFAM_SYNC_TICK") or 60)
            while True:
                time.sleep(tick)
                self.m._quiet(self.m.sync_tick)
        threading.Thread(target=sync_loop, daemon=True).start()
        if host_pid:
            def self_update_loop():
                time.sleep(min(20.0, SELF_UPDATE_EVERY))
                while True:
                    try:   # never installs by itself: it only tells you, and you choose when
                        self.m.check_self_update(notify=True)
                    except Exception:
                        pass
                    time.sleep(SELF_UPDATE_EVERY)
            threading.Thread(target=self_update_loop, daemon=True).start()

            def scheduler():
                # Background checks while the desktop program runs (also when it sits in the tray).
                hour = float(os.environ.get("WFAM_TEST_HOUR_SECONDS") or 3600)   # tests shorten the clock
                while True:
                    time.sleep(min(30, hour / 120))
                    cfg = self.m.store.config
                    hours = cfg.get("bg_check_hours", 3)
                    if not hours or not cfg.get("api_key") or not cfg.get("addons_dir") or not self.m.store.addons:
                        continue
                    if time.time() - self.m.last_check_at < hours * hour:
                        continue
                    try:
                        self.m.run_job("Automatic update check", self.m.op_background)
                    except BusyError:
                        pass
            threading.Thread(target=scheduler, daemon=True).start()
        if idle_exit or host_pid:
            def watchdog():
                while True:
                    time.sleep(1)
                    if self.m.job["running"]:
                        continue
                    now = time.time()
                    # Window closed: quit a few seconds later unless it reconnects (a reload does).
                    closed = self.bye_at and now - self.bye_at > 6 and self.m.last_contact <= self.bye_at + 0.05
                    if host_pid:   # the desktop program decides; quit if it is gone (e.g. crashed)
                        if closed or not pid_alive(host_pid):
                            self.server.shutdown()
                            return
                        continue
                    if closed or now - self.m.last_contact > IDLE_EXIT_SECONDS:
                        self.server.shutdown()
                        return
            threading.Thread(target=watchdog, daemon=True).start()
        try:
            self.server.serve_forever(poll_interval=0.5)
        finally:
            self.server.server_close()
