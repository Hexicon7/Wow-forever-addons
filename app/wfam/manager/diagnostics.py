"""Diagnostics: checks every part against the real sites."""
from __future__ import annotations

import collections
import platform
import tempfile
from pathlib import Path

from ..addonfiles import folder_fingerprints, zip_supports_interface
from ..config import APP_NAME, APP_VERSION, DEFAULT_INTERFACE
from ..errors import UserError
from ..sources import GitHub, TukuiOrg, WagoAuras, github_repo_of, pick_latest
from ..system import game_running
from ..util import file_sha1, fmt_size, now_iso, rmtree


class DiagnosticsMixin:
    def op_diagnose(self):
        res: list[dict] = []

        def add(status, title, detail=""):
            res.append({"status": status, "title": title, "detail": detail})

        self.diag = {"at": now_iso(), "results": res, "running": True}
        cfg = self.store.config
        add("info", "App", f"{APP_NAME} {APP_VERSION} · Python {platform.python_version()} · {platform.platform()}")
        d = cfg.get("addons_dir") or ""
        if d and Path(d).is_dir():
            try:
                t = Path(d) / ".wfam-write-test"
                t.write_text("x")
                t.unlink()
                add("ok", "AddOns folder", f"{d} (writable)" + ("" if "_forever_" in d.lower() else
                                                                " — NOTE: not inside _forever_"))
            except OSError as e:
                add("fail", "AddOns folder", f"{d} is not writable: {e}")
        else:
            add("fail", "AddOns folder", "not set or missing")
        w = self.wtf_dir()
        add("ok" if w and w.is_dir() else "warn", "WTF folder (addon settings)",
            str(w) if w and w.is_dir() else "not found yet (appears after the first login)")
        dl = self.downloads_dir()
        add("ok" if dl.is_dir() else "warn", "Downloads folder", str(dl))
        add("info", "WoW running right now", "yes" if game_running() else "no")
        try:
            cf = self.client()
            types = cf.version_types()
            forever = [f"{t.get('name')} ({t.get('id')})" for t in types if "forever" in (t.get("name") or "").lower()]
            add("ok", "CurseForge API key", f"works — {len(types)} WoW game versions; Forever: {', '.join(forever) or 'none!'}")
            type_id = self.ensure_type(cf)
        except UserError as e:
            add("fail", "CurseForge API key", str(e))
            self._finish_diag()
            return
        site_only, allowed = [], []
        try:
            mods, pag = cf.search("", type_id, 2, 0, 50)
            tc = int(pag.get("totalCount") or 0)
            add("ok", "Catalog search", f"{'10,000+' if tc >= 10000 else tc} addons with a Forever version; most popular: "
                + ", ".join(m.get("name", "?") for m in mods[:5]))
            allowed = [m for m in mods if m.get("allowModDistribution") is not False]
            site_only = [m for m in mods if m.get("allowModDistribution") is False]
            gh = [m for m in site_only if github_repo_of(m)]
            add("info", "Download permissions (top 50)", f"{len(allowed)} direct · {len(site_only)} CurseForge-site only "
                f"({len(gh)} of those link a GitHub page)")
        except UserError as e:
            add("fail", "Catalog search", str(e))
        try:
            best = None
            for m in allowed[:12]:
                f = pick_latest(cf.files(m["id"], type_id), type_id, "release")
                if f and f.get("fileLength") and (not best or f["fileLength"] < best[1]["fileLength"]):
                    best = (m, f)
            if best:
                m, f = best
                url = f.get("downloadUrl") or cf.download_url(m["id"], f["id"])
                tmp_dir = Path(tempfile.mkdtemp(prefix="wfam-diag-", dir=self.store.dir))
                try:
                    zp = tmp_dir / "t.zip"
                    cf.download(url, zp, file_sha1(f))
                    ok_toc = zip_supports_interface(zp, str(cfg.get("interface") or DEFAULT_INTERFACE))
                    add("ok" if ok_toc else "warn", "Test download from CurseForge",
                        f"{m['name']} {f.get('displayName')} ({fmt_size(zp.stat().st_size)}): checksum OK, "
                        + ("Forever .toc found" if ok_toc else "but no .toc that Forever would load was found"))
                finally:
                    rmtree(tmp_dir)
            else:
                add("warn", "Test download from CurseForge", "no small addon with direct downloads found to test")
        except UserError as e:
            add("fail", "Test download from CurseForge", str(e))
        try:
            core = GitHub().rate_limit().get("resources", {}).get("core", {})
            add("ok", "GitHub reachable", f"{core.get('remaining')}/{core.get('limit')} requests left this hour")
            tested = 0
            for m in site_only:
                repo = github_repo_of(m)
                if not repo or tested >= 3:
                    continue
                tested += 1
                try:
                    rel, asset = GitHub().find_forever(repo, "release", str(cfg.get("interface") or DEFAULT_INTERFACE))
                    add("ok" if asset else "info", f"GitHub: {m['name']}",
                        f"{repo}: " + (f"Forever file {asset['name']} in {rel.get('tag_name')}" if asset
                                       else "no Forever build in recent releases → uses CurseForge's download page"))
                except UserError as e:
                    if "not found" in str(e):
                        add("info", f"GitHub: {m['name']}", f"{repo}: the GitHub link on CurseForge leads nowhere "
                            f"(moved or deleted) → Wago or CurseForge's download page is used instead")
                    else:
                        add("warn", f"GitHub: {m['name']}", f"{repo}: {e}")
        except UserError as e:
            add("warn", "GitHub reachable", str(e))
        wg = self.wago()
        if wg:
            try:
                hits = wg.search("bags")
                linked = [r for r in self.store.addons.values() if r.get("wago")]
                with_rel = [r for r in linked if r.get("wago_latest")]
                add("ok", "Wago key", f"works — test search ({wg.gv}) found {len(hits)} addons; {len(linked)} of your "
                    f"addons are linked to Wago, {len(with_rel)} with a Forever release there")
                newer = [r["name"] for r in self.store.addons.values() if self.update_source(r) == "wago"]
                if newer:
                    add("info", "Newer on Wago", ", ".join(newer))
            except UserError as e:
                add("fail", "Wago key", str(e))
        elif (cfg.get("wago_key") or "").strip():
            add("info", "Wago", "turned off in Settings")
        try:
            tk = TukuiOrg().addons()
            add("ok", "tukui.org reachable", f"{len(tk)} listed: " + ", ".join(a.get("name") or a["slug"] for a in tk[:6]))
        except Exception as e:
            add("warn", "tukui.org reachable", str(e))
        try:
            hits = (WagoAuras().search("", "", "installs", 0) or {}).get("hits") or []
            add("ok", "wago.io WeakAuras reachable", f"{len(hits)} ForeverAuras on the first page")
        except Exception as e:
            add("warn", "wago.io WeakAuras reachable", str(e))
        # Does our folder fingerprint match CurseForge's? Compare with addons installed from CurseForge.
        try:
            checked = matched = 0
            ad = Path(d)
            for k, r in list(self.store.addons.items()):
                if checked >= 4 or r.get("source") == "github" or not r.get("installed_file_id") \
                        or not self._folders_present(r):
                    continue
                f = cf.file(int(k), r["installed_file_id"])
                mods = {mm.get("name"): mm.get("fingerprint") for mm in f.get("modules") or []}
                for folder, fp in mods.items():
                    if folder in r["folders"] and fp:
                        checked += 1
                        matched += fp in folder_fingerprints(ad / folder)
                        break
            if checked:
                add("ok" if matched == checked else "warn", "Fingerprint method",
                    f"{matched}/{checked} installed addons fingerprint exactly like CurseForge"
                    + ("" if matched == checked else " — recognising hand-installed addons may miss some"))
            n, total = self.op_scan(quiet=True)
            add("ok" if n or not total else "info", "Hand-installed addons", f"{n} recognised among {total} unmanaged folders")
        except UserError as e:
            add("warn", "Hand-installed addons", str(e))
        counts = collections.Counter(self.status_of(r) for r in self.store.addons.values())
        add("info", "Managed addons", ", ".join(f"{v} {k}" for k, v in sorted(counts.items())) or "none")
        self._finish_diag()

    def _finish_diag(self):
        res = self.diag["results"]
        self.diag["running"] = False
        bad = sum(1 for r in res if r["status"] == "fail")
        warn = sum(1 for r in res if r["status"] == "warn")
        self.log(f"Diagnostics finished: {bad} problem(s), {warn} warning(s).", "ok" if not bad else "warn")
