# 3.8: Wago-only addons, higher version number wins between CurseForge and Wago, share codes,
# interface number read from the game.
import os, sys, json, time, subprocess, tempfile, urllib.request, shutil
from pathlib import Path
from playwright.sync_api import sync_playwright
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
sys.path.insert(0, str(Path(__file__).parent)); import fake_cf
fake_cf.start(); T = fake_cf.toc
tmp = Path(tempfile.mkdtemp()); past = "2026-10-01T10:00:00Z"; later = "2026-10-20T10:00:00Z"
APP = os.environ.get("WFAM_APP", "/home/claude/app/WoWForeverAddons.pyw")
fake_cf.wago_addon("WOonly01", "wago-only-thing", "Wago Only Thing")
fake_cf.wago_release("WOonly01", "1.0.0", {"WagoOnly/WagoOnly.toc": T("Wago Only Thing", "1.0.0")}, created=past)
fake_cf.wago_addon("QSwago01", "questie", "Questie")
fake_cf.wago_release("QSwago01", "v12.6.0", {"Questie/Questie.toc": T("Questie", "12.6.0")}, created=past)
fake_cf.wago_addon("NOforev1", "no-forever", "No Forever")       # no release for Forever
fails = 0
def check(c, m):
    global fails; print(("PASS " if c else "FAIL ") + m); fails += 0 if c else 1

class Inst:
    def __init__(self, name, port, extra=None):
        self.root = tmp/name; self.a = self.root/"WoW"/"_forever_"/"Interface"/"AddOns"; self.a.mkdir(parents=True)
        (self.root/"d").mkdir()
        cfg = {"api_key": fake_cf.KEY, "addons_dir": str(self.a), "auto_check": False, "wago_key": fake_cf.WAGO_KEY, "use_wago": True}
        cfg.update(extra or {})
        (self.root/"d"/"config.json").write_text(json.dumps(cfg))
        self.env = dict(os.environ, WFAM_API_BASE=fake_cf.BASE, WFAM_DATA_DIR=str(self.root/"d"))
        self.B = f"http://127.0.0.1:{port}"; self.port = port; self.p = None
    def start(self):
        self.p = subprocess.Popen([sys.executable, APP, "--no-browser", "--port", str(self.port)], env=self.env, stdout=subprocess.DEVNULL); time.sleep(1.2)
    def stop(self):
        self.p.terminate(); self.p.wait()
    def post(self, p, b=None):
        try: return json.loads(urllib.request.urlopen(urllib.request.Request(self.B+p, data=json.dumps(b or {}).encode(), headers={"X-WFAM":"1","Content-Type":"application/json"}, method="POST")).read())
        except urllib.error.HTTPError as e: return json.loads(e.read())
    def st(self): return json.loads(urllib.request.urlopen(self.B+"/api/state").read())
    def idle(self):
        time.sleep(0.25)
        while self.st()["job"]["running"]: time.sleep(0.2)
    def rec(self, k): return next((x for x in self.st()["managed"] if x["key"] == str(k)), None)
    def logs(self): return " | ".join(l["msg"] for l in self.st()["logs"])

A = Inst("a", 47961); A.start()
try:
    # --- Wago-only addon
    A.post("/api/add_wago", {"id": "WOonly01"}); A.idle()
    r = A.rec("wago-WOonly01")
    check(r and r["installed_version"] == "1.0.0" and r["source"] == "wago" and (A.a/"WagoOnly").is_dir(), f"Wago-only addon installed: {r and r['installed_version']}")
    check(r and r["wago_only"] and "addons.wago.io" in (r["url"] or ""), "it's marked Wago-only and links to Wago")
    A.post("/api/check"); A.idle()
    r = A.rec("wago-WOonly01"); check(r["status"] == "ok" and r["latest_version"] == "1.0.0", f"check: up to date ({r['status']}, latest {r['latest_version']})")
    fake_cf.wago_release("WOonly01", "1.1.0", {"WagoOnly/WagoOnly.toc": T("Wago Only Thing", "1.1.0")}, created=later)
    A.post("/api/check"); A.idle()
    r = A.rec("wago-WOonly01"); check(r["status"] == "update" and r["latest_version"] == "1.1.0", f"newer Wago release found ({r['status']})")
    A.post("/api/update", {"key": "wago-WOonly01"}); A.idle()
    r = A.rec("wago-WOonly01"); check(r["installed_version"] == "1.1.0" and "1.1.0" in (A.a/"WagoOnly"/"WagoOnly.toc").read_text(), "updated from Wago")
    check(r.get("previous_version") == "1.0.0", f"earlier version kept for revert: {r.get('previous_version')}")
    sr = A.post("/api/wago_search", {"q": "thing"})
    check(any(x["id"] == "WOonly01" and x["installed"] for x in sr["results"]), "Wago search shows it as installed")
    A.post("/api/add_wago", {"id": "NOforev1"}); A.idle()
    check(not A.rec("wago-NOforev1") and "no WoW Forever release" in A.logs(), "Wago addon without a Forever build is refused cleanly")
    # --- Wago addon that CurseForge has too: CurseForge + Wago as second source
    A.post("/api/add_wago", {"id": "QSwago01"}); A.idle()
    q = A.rec("1001")
    check(q and q["source"] == "curseforge" and q["installed_version"] == "v12.6.0" and not A.rec("wago-QSwago01"), f"Questie from Wago's list installs through CurseForge: {q and (q['source'], q['installed_version'])}")
    # Wago gets a higher number earlier, CurseForge a lower number later: the higher number wins
    fake_cf.wago_release("QSwago01", "v12.9.0", {"Questie/Questie.toc": T("Questie", "12.9.0")}, created="2026-11-01T10:00:00Z")
    fake_cf.add_file(1001, "v12.8.0", {"Questie/Questie.toc": T("Questie", "12.8.0")}, date="2026-11-05T10:00:00Z")
    A.post("/api/check"); A.idle()
    q = A.rec("1001"); check(q["update_from"] == "wago", f"higher version on Wago wins though CurseForge's is newer by date: {q['update_from']}")
    fake_cf.add_file(1001, "v12.10.0", {"Questie/Questie.toc": T("Questie", "12.10.0")}, date="2026-11-06T10:00:00Z")
    A.post("/api/check"); A.idle()
    q = A.rec("1001"); check(q["update_from"] == "curseforge", f"…and when CurseForge gets 12.10.0 it wins over 12.9.0: {q['update_from']}")
    A.post("/api/update", {"key": "1001"}); A.idle()
    q = A.rec("1001"); check(q["installed_version"] == "v12.10.0" and q["status"] == "ok", f"updated to 12.10.0 and then up to date ({q['status']})")
    # --- restart keeps the Wago-only addon's name
    A.stop(); A.start()
    r = A.rec("wago-WOonly01"); check(r and r["name"] == "Wago Only Thing" and r["installed_version"] == "1.1.0", "Wago-only addon survives a restart")
    # --- share code
    code = A.post("/api/share_code")
    check(code["code"].startswith("WFA1.") and code["count"] == 2, f"share code made ({len(code['code'])} chars)")
    Bi = Inst("b", 47962); Bi.start()
    try:
        Bi.post("/api/import_text", {"text": "  " + code["code"][:30] + "\n" + code["code"][30:] + " "}); Bi.idle()
        check(Bi.rec("1001") and Bi.rec("wago-WOonly01") and (Bi.a/"WagoOnly").is_dir() and (Bi.a/"Questie").is_dir(), "code pasted on another PC installs the same addons (even split over lines)")
        r = Bi.post("/api/import_text", {"text": "WFA1.garbage!!"}); Bi.idle()
        check("damaged" in Bi.logs(), "a broken code is refused")
        Bi.post("/api/import_text", {"text": "https://www.curseforge.com/wow/addons/bugsack"}); Bi.idle()
        check(Bi.rec("1002") is not None, "plain links work in the same box")
        exp = json.loads(urllib.request.urlopen(A.B + "/api/export").read())
        check(any(x.get("wago") == "WOonly01" for x in exp["addons"]), "the export file includes Wago-only addons")
    finally:
        Bi.stop()
    # --- interface number from the game
    wtf = A.root/"WoW"/"_forever_"/"WTF"; wtf.mkdir(parents=True)
    (wtf/"Config.wtf").write_text('SET locale "enUS"\nSET lastAddonVersion "16002"\nSET checkAddonVersion "1"\n')
    s = A.st(); check(s["config"]["interface"] == "16002" and s["config"]["interface_detected"] == "16002", f"interface follows the game: {s['config']['interface']}")
    q = A.rec("1001"); check(q["compat"] == "mismatch", "an addon made for 16001 is now flagged")
    A.post("/api/settings", {"interface": "16001"})
    (wtf/"Config.wtf").write_text('SET lastAddonVersion "16003"\n'); time.sleep(0.05)
    s = A.st(); check(s["config"]["interface"] == "16001" and not s["config"]["interface_auto"], "a number typed in Settings overrides the game")
    A.post("/api/settings", {"interface": ""})
    s = A.st(); check(s["config"]["interface"] == "16003" and s["config"]["interface_auto"], "clearing it goes back to automatic")
    # --- UI
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-proxy-server"])
        pg = b.new_page(viewport={"width": 1400, "height": 900}); errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(A.B + "/"); time.sleep(1.2)
        row = pg.locator("tr.arow", has_text="Wago Only Thing")
        row.locator("button.more").click(); time.sleep(0.3)
        menu = pg.inner_text("#pop")
        check("Open on Wago" in menu and "Details" not in menu and "CurseForge" not in menu, "Wago-only row menu: " + menu.replace("\n", " | "))
        pg.keyboard.press("Escape"); time.sleep(0.2); pg.mouse.click(5, 5); time.sleep(0.2)
        pg.click("#bMoreIns") if pg.locator("#bMoreIns").count() and pg.locator("#bMoreIns").is_visible() else None
        time.sleep(0.3)
        if not pg.locator("#pop >> text=Share / move my list…").count():
            pg.evaluate("openShare()")
        else:
            pg.click("#pop >> text=Share / move my list…")
        time.sleep(1)
        check(pg.locator("#mShare.show").count() == 1 and pg.input_value("#shCode").startswith("WFA1."), "share dialog shows the code")
        pg.screenshot(path="/home/claude/shots/v44-share.png")
        pg.click("#shClose"); time.sleep(0.2)
        pg.click("text=Settings"); time.sleep(0.6)
        check("Automatic" in (pg.get_attribute("#fIf", "placeholder") or ""), "settings shows the interface as automatic: " + (pg.get_attribute("#fIf", "placeholder") or ""))
        check(not errs, f"no page errors {errs}")
        b.close()
finally:
    A.stop()
print("FAILURES:", fails); sys.exit(1 if fails else 0)
