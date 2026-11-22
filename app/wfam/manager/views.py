"""The snapshot the window draws from."""
from __future__ import annotations

from pathlib import Path

from ..addonfiles import compat_of, read_toc
from ..config import (
    APP_NAME,
    APP_VERSION,
    COMPANION_FOLDER,
    RELEASE_TYPES,
    is_tukui_key,
    is_wago_key,
)
from ..system import autostart_enabled, game_running


class ViewsMixin:
    def snapshot(self, since: int = 0) -> dict:
        found = self.follow_game_interface()
        ins = self.insights()
        cfg = self.store.config
        key = cfg.get("api_key") or ""
        d = cfg.get("addons_dir") or ""
        addons_dir = Path(d) if d else None
        dir_ok = bool(addons_dir and addons_dir.is_dir())
        wanted = str(cfg.get("interface") or "")
        managed = []
        with self.store.lock:
            recs = {k: dict(v) for k, v in self.store.addons.items()}
        try:
            present = {p.name.lower() for p in addons_dir.iterdir() if p.is_dir()} if dir_ok else set()
        except OSError:
            present = set()
        for k, r in recs.items():
            toc = read_toc(addons_dir / r["primary"]) if dir_ok and r.get("primary") else {}
            loc = r.get("local") or {}
            # Another tool (e.g. the Wago app) changed it since: show what's on disk until the next check.
            outside = bool(r.get("external")) or bool(
                not loc.get("pending") and loc.get("toc") and toc.get("version") and toc["version"] != loc["toc"])
            if outside and not r.get("external"):
                r["installed_version"] = toc["version"]
            missing, needs = [], []
            if dir_ok:
                for folder in r.get("folders") or []:
                    t = toc if folder == r.get("primary") else read_toc(addons_dir / folder)
                    missing += [d for d in t.get("deps") or [] if d.lower() not in present and d not in missing]
                    needs += [d for d in t.get("deps") or [] if d not in needs]
            compat = compat_of(toc.get("interfaces") or [], wanted) if toc.get("loads", True) else "mismatch"
            newest = self.newest_available(r)
            managed.append({
                "key": k, "name": r.get("name") or toc.get("title") or k,
                "url": r.get("url") or f"https://www.curseforge.com/projects/{k}",
                "logo": r.get("logo") or (r.get("wago") or {}).get("logo") or "",
                "authors": r.get("authors") or [], "summary": r.get("summary"),
                "installed_version": r.get("installed_version") or toc.get("version"),
                "installed_file_id": r.get("installed_file_id"),
                "installed_date": r.get("installed_file_date"), "installed_at": r.get("installed_at"),
                "installed_release_type": RELEASE_TYPES.get(r.get("installed_release_type") or 1),
                "latest_version": newest[0], "latest_date": newest[1], "latest_from": newest[2],
                "cf_version": r.get("latest_version") if str(k).isdigit() and r.get("source") != "github" else (r.get("cf_latest") or {}).get("version"),
                "latest_file_id": r.get("latest_file_id"),
                "checked_at": r.get("latest_checked_at"), "wago_only": is_wago_key(k), "tukui": is_tukui_key(k),
                "latest_url": f"{r.get('url')}/files/{r['latest_file_id']}" if r.get("latest_file_id") else r.get("url"),
                "source": r.get("source") or "curseforge", "github": r.get("github"),
                "update_from": self.update_source(r) if self.status_of(r) in ("update", "update_manual") else None,
                "update_version": self.label_of(r, self.update_source(r)) if self.status_of(r) in ("update", "update_manual") else None,
                "wago_url": (r.get("wago") or {}).get("url"),
                "wago_latest": (r.get("wago_latest") or {}).get("label") if r.get("wago") else None,
                "github_url": ((r.get("gh_latest") or {}).get("url")
                               or (f"https://github.com/{r['github']}" if r.get("github") else None)),
                "download_page": (r.get("awaiting") or {}).get("page")
                or (f"{r.get('url')}/download/{r['latest_file_id']}" if r.get("latest_file_id") else r.get("url")),
                "folders": r.get("folders") or [], "pinned": bool(r.get("pinned")), "manual": bool(r.get("manual")),
                "library": bool(r.get("library")),
                "external": outside,
                "distribution_disabled": bool(r.get("distribution_disabled")),
                "status": self.status_of(r),
                "toc_interfaces": toc.get("interfaces") or [],
                "compat": compat, "toc_file": toc.get("toc") or "", "missing_deps": missing, "deps": needs[:20],
                "previous_version": ((r.get("previous") or [{}])[0] or {}).get("installed_version")
                if r.get("previous") else None,
                "errors": self._error_summary(r, ins),
                "issues_url": r.get("issues_url") or (f"https://github.com/{r['github']}/issues" if r.get("github") else ""),
                "unused": bool(r.get("folders")) and all(f.lower() in ins["unused"] for f in r["folders"]),
            })
        managed.sort(key=lambda a: (a["name"] or "").lower())

        unmanaged, hidden = [], []
        if dir_ok:
            owned = {f for r in recs.values() for f in (r.get("folders") or [])} | {COMPANION_FOLDER}
            groups: dict[str, dict] = {}
            try:
                dirs = sorted(p for p in addons_dir.iterdir() if p.is_dir())
            except OSError:
                dirs = []
            recognized = {}
            for mid, info in (self.store.state.get("recognized") or {}).items():
                for folder in info.get("folders") or []:
                    recognized[folder] = info
            for p in dirs:
                if p.name in owned or p.name.startswith(".") or p.name.startswith("Blizzard_"):
                    continue
                toc = read_toc(p)
                info = recognized.get(p.name)
                if info:
                    gkey = f"cf:{info['mod_id']}"
                    if gkey not in groups:
                        groups[gkey] = {"key": gkey, "title": info.get("name") or toc.get("title") or p.name,
                                        "version": info.get("version") or "", "project_id": info["mod_id"],
                                        "recognized": True, "forever": info.get("forever"),
                                        "interfaces": toc.get("interfaces") or [], "folders": [], "has_toc": bool(toc),
                                        "toc_file": toc.get("toc") or "", "loads": toc.get("loads", True)}
                    groups[gkey]["folders"].append(p.name)
                    continue
                gkey = f"pid:{toc['project_id']}" if toc.get("project_id") else f"dir:{p.name}"
                g = groups.get(gkey)
                if not g:
                    g = groups[gkey] = {"key": gkey, "title": toc.get("title") or p.name,
                                        "version": toc.get("version") or "", "project_id": toc.get("project_id"),
                                        "interfaces": toc.get("interfaces") or [], "folders": [],
                                        "has_toc": bool(toc), "toc_file": toc.get("toc") or "",
                                        "loads": toc.get("loads", True)}
                g["folders"].append(p.name)
                for dep in toc.get("deps") or []:
                    if dep.lower() not in present and dep not in g.setdefault("missing_deps", []):
                        g["missing_deps"].append(dep)
                if len(p.name) < len(g["folders"][0]) and toc:
                    g["title"] = toc.get("title") or g["title"]
            # which managed addons use a folder (## Dependencies / ## OptionalDeps): a shared library
            used_by: dict[str, list[str]] = {}
            for r in recs.values():
                prim = r.get("primary") or (r.get("folders") or [None])[0]
                if not prim:
                    continue
                t = read_toc(addons_dir / prim)
                for dep in (t.get("deps") or []) + (t.get("optdeps") or []):
                    names = used_by.setdefault(dep.lower(), [])
                    if r.get("name") and r["name"] not in names:
                        names.append(r["name"])
            wrec = self.store.state.get("wago_recognized") or {}
            for g in groups.values():
                g["used_by"] = sorted({n for f in g["folders"] for n in used_by.get(f.lower(), [])})[:4]
                if not g.get("recognized"):
                    for f in g["folders"]:
                        t = read_toc(addons_dir / f)
                        hit = wrec.get(f) or ({"id": t["wago_id"]} if t.get("wago_id") else None)
                        if hit:
                            g["wago_id"], g["wago_name"], g["wago_version"] = hit["id"], hit.get("name"), hit.get("version")
                            break
            ignored_set = set(self.store.config.get("ignored_folders") or [])
            for g in groups.values():
                g["compat"] = compat_of(g["interfaces"], wanted) if g["loads"] else "mismatch"
                (hidden if all(f in ignored_set for f in g["folders"]) else unmanaged).append(g)
            unmanaged.sort(key=lambda g: g["title"].lower())
            hidden.sort(key=lambda g: g["title"].lower())

        return {
            "app": {"name": APP_NAME, "version": APP_VERSION},
            "config": {
                "has_key": bool(key), "key_hint": key[-4:] if len(key) >= 8 else "",
                "addons_dir": d, "addons_dir_ok": dir_ok,
                "addons_dir_is_forever": "forever" in d.lower(),
                "version_type_id": cfg.get("version_type_id"), "version_type_name": cfg.get("version_type_name"),
                "channel": cfg.get("channel"), "interface": wanted,
                "interface_detected": found, "interface_auto": bool(cfg.get("interface_auto", True)),
                "auto_dependencies": cfg.get("auto_dependencies", True),
                "auto_manual": cfg.get("auto_manual", True),
                "delete_after_install": cfg.get("delete_after_install", True),
                "use_github": cfg.get("use_github", True),
                "backup_wtf": cfg.get("backup_wtf", True),
                "backup_keep": cfg.get("backup_keep", 3),
                "auto_check": cfg.get("auto_check", True),
                "has_wago_key": bool((cfg.get("wago_key") or "").strip()),
                "wago_key_hint": (cfg.get("wago_key") or "")[-4:] if len(cfg.get("wago_key") or "") >= 8 else "",
                "use_wago": cfg.get("use_wago", True),
                "app_window": cfg.get("app_window", True),
                "hosted": self.hosted,
                "backup_dir": cfg.get("backup_dir") or "", "backup_dir_default": str(self.store.dir / "backups"),
                "update_repo": self.update_repo(),
                "addon_profiles": cfg.get("addon_profiles") or {},
                "tray": cfg.get("tray", True),
                "bg_check_hours": cfg.get("bg_check_hours", 3),
                "bg_auto_update": cfg.get("bg_auto_update", True),
                "self_update_dismissed": cfg.get("self_update_dismissed") or "",
                "ui_scale": str(cfg.get("ui_scale") or "100"),
                "ui_state": cfg.get("ui_state") if isinstance(cfg.get("ui_state"), dict) else {},
                "sync_dir": cfg.get("sync_dir") or "",
                "sync_dir_ok": bool((cfg.get("sync_dir") or "").strip() and Path(cfg["sync_dir"]).is_dir()),
                "autostart": autostart_enabled(),
                "wago_game_version": cfg.get("wago_game_version") or "forever",
                "downloads_dir": cfg.get("downloads_dir") or "",
                "downloads_default": str(self.downloads_dir()),
            },
            "managed": managed,
            "unmanaged": unmanaged,
            "auras_pending": [{"slug": k, "name": v.get("name"), "author": v.get("author"), "version": v.get("semver"),
                               "id": v.get("id"), "added_at": v.get("added_at")}
                              for k, v in sorted((self.store.state.get("auras") or {}).items(),
                                                 key=lambda kv: kv[1].get("added_at") or "")],
            "foreverauras": bool(dir_ok and (addons_dir / "ForeverAuras").is_dir()),
            "self_update": {"version": self.self_update["version"], "notes": self.self_update["notes"],
                            "page": self.self_update["page"]} if self.self_update else None,
            "last_batch": self._batch_view(recs),
            "hidden_folders": hidden,
            "sync_offer": self.sync_offer(),
            "sync_last": (self.store.state.get("sync_export") or {}).get("created") if self._sync_file() else None,
            "game_running": game_running(),
            "backups": self.list_backups(),
            "wtf_found": bool(self.wtf_dir() and self.wtf_dir().is_dir()),
            "diag": self.diag,
            "job": dict(self.job),
            "warming": not getattr(self, "meta_ready", True),
            "logs": [e for e in self.logs if e["seq"] > since],
            "log_seq": self._seq,
        }
