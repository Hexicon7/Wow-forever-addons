# One search for CurseForge + Wago (+ GitHub): one card per addon showing the newest version and where it's from;
# installing picks that source; the status column shows the version an update would bring.
import os, sys, json, time, subprocess, tempfile, urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
sys.path.insert(0, str(Path(__file__).parent)); import fake_cf
fake_cf.start(); T = fake_cf.toc; tmp = Path(tempfile.mkdtemp()); (tmp/"data").mkdir()
addons = tmp/"World of Warcraft"/"_forever_"/"Interface"/"AddOns"; addons.mkdir(parents=True)
fake_cf.wago_addon("QSwago01", "questie", "Questie", downloads=500)
fake_cf.wago_release("QSwago01", "v12.9.0", {"Questie/Questie.toc": T("Questie", "12.9.0")}, created="2026-11-02T10:00:00Z")
fake_cf.wago_addon("WOnly001", "only-on-wago", "Questie Helper Wago Only")
fake_cf.wago_release("WOnly001", "3.1.0", {"QHW/QHW.toc": T("QHW", "3.1.0")}, created="2026-11-03T10:00:00Z")
# DBM links a GitHub repo
fake_cf.MODS[1004]["links"]["sourceUrl"] = "https://github.com/fake/dbm"
(tmp/"data"/"config.json").write_text(json.dumps({"api_key": fake_cf.KEY, "addons_dir": str(addons), "auto_check": False,
    "wago_key": fake_cf.WAGO_KEY, "use_wago": True}))
env = dict(os.environ, WFAM_API_BASE=fake_cf.BASE, WFAM_DATA_DIR=str(tmp/"data"), WFAM_GITHUB_API=fake_cf.BASE + "/gh")
app = subprocess.Popen([sys.executable, os.environ.get("WFAM_APP", "/home/claude/app/WoWForeverAddons.pyw"), "--no-browser", "--port", "47989"], env=env)
time.sleep(1.2); fails = 0; B = "http://127.0.0.1:47989"
def check(c, msg):
    global fails; print(("PASS " if c else "FAIL ") + msg); fails += 0 if c else 1
def post(p, b=None):
    try: return json.loads(urllib.request.urlopen(urllib.request.Request(B+p, data=json.dumps(b or {}).encode(), headers={"X-WFAM":"1","Content-Type":"application/json"}, method="POST")).read())
    except urllib.error.HTTPError as e: return json.loads(e.read())
def st(): return json.loads(urllib.request.urlopen(B+"/api/state").read())
def idle():
    time.sleep(0.25)
    while st()["job"]["running"]: time.sleep(0.2)
def rec(k): return next((x for x in st()["managed"] if x["key"] == str(k)), None)
try:
    r = post("/api/search", {"q": "questie"})
    names = [x["name"] for x in r["results"]]
    q = next(x for x in r["results"] if x["name"] == "Questie")
    check(names.count("Questie") == 1, f"Questie appears once though it's on both sites: {names}")
    check(q["version"] == "v12.9.0" and q["version_from"] == "wago" and q["cf_version"] == "v12.6.0" and q["sources"][:2] == ["curseforge", "wago"],
          f"card shows the newest version and its source: {q['version']} from {q['version_from']} (CurseForge {q['cf_version']})")
    w = next((x for x in r["results"] if x.get("wago_id") == "WOnly001"), None)
    check(w and w["sources"] == ["wago"] and w["version"] == "3.1.0", "an addon only on Wago is in the same list")
    r = post("/api/search", {"q": "bugsack"})
    b_ = r["results"][0]
    check(b_["version_from"] == "curseforge" and b_["version"] == "v12.1.7", "CurseForge-only addon: its own version")
    # install picks the newest source
    post("/api/install_best", {"id": 1001, "wago_id": "QSwago01"}); idle()
    a = rec(1001)
    check(a and a["installed_version"] == "v12.9.0" and a["source"] == "wago", f"Install takes the newest (Wago v12.9.0): {a and (a['source'], a['installed_version'])}")
    post("/api/install_best", {"id": None, "wago_id": "WOnly001"}); idle()
    check(rec("wago-WOnly001") is not None, "Wago-only addon installs from the same list")
    # GitHub as third source
    post("/api/install_best", {"id": 1004}); idle()
    d = rec(1004); check(d and d["installed_version"] == "12.0.1" and d["source"] == "curseforge", "DBM from CurseForge")
    fake_cf.gh_release("fake/dbm", "12.0.5", {"DBM-12.0.5-forever.zip": {"DBM-Core/DBM-Core.toc": T("DBM Core", "12.0.5"),
                                                                       "DBM-StatusBarTimers/DBM-StatusBarTimers.toc": T("DBM Timers", "12.0.5")}})
    # the GitHub check is throttled to every 6 hours; a fresh install forces it — so reinstall-check by forcing via install_best
    post("/api/install_best", {"id": 1004}); idle()
    d = rec(1004)
    check(d["installed_version"] in ("12.0.5",) or "12.0.5" in (d["installed_version"] or ""), f"GitHub's newer DBM is used: {d['installed_version']} ({d['source']})")
    fake_cf.add_file(1004, "12.0.9", {"DBM-Core/DBM-Core.toc": T("DBM Core", "12.0.9"), "DBM-StatusBarTimers/DBM-StatusBarTimers.toc": T("DBM Timers", "12.0.9")})
    post("/api/check"); idle()
    d = rec(1004)
    check(d["status"] == "update" and d["update_from"] == "curseforge" and d["update_version"] == "12.0.9",
          f"CurseForge gets 12.0.9 later: update offered from CurseForge ({d['status']}, {d['update_from']}, {d['update_version']})")
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-proxy-server"])
        pg = b.new_page(viewport={"width": 1400, "height": 900}); errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(B + "/"); time.sleep(1.5)
        row = pg.inner_text("tr.arow:has-text('Deadly Boss Mods')")
        check("Update → 12.0.9 · CurseForge" in row, "status shows the new version and where from: " + row.replace("\n", " | "))
        pg.screenshot(path="/home/claude/shots/v49-status.png")
        pg.click("button.tab[data-v='browse']"); time.sleep(0.8)
        tabs = pg.locator(".srcsw button").all_inner_texts()
        check(tabs == ["Addons", "ElvUI & Tukui", "WeakAuras"], f"one Addons tab (no separate Wago tab): {tabs}")
        pg.fill("#bq", "questie"); time.sleep(2)
        card = pg.inner_text("#bres .res:has-text('Questie') >> nth=0")
        check("v12.9.0" in card and "Wago" in card and "also on CurseForge" in card, "card: " + card.replace("\n", " | "))
        check(pg.locator("#bres .res", has_text="Questie Helper Wago Only").count() == 1, "Wago-only addon card in the same results")
        pg.screenshot(path="/home/claude/shots/v49-browse.png")
        check(not errs, f"no page errors {errs}")
        b.close()
    post("/api/update", {"key": "1004"}); idle()
    d = rec(1004)
    check(d["installed_version"] == "12.0.9" and d["source"] == "curseforge" and d["status"] == "ok", f"back to CurseForge when it has the newest: {d['installed_version']} ({d['source']}, {d['status']})")
finally:
    app.terminate()
print("FAILURES:", fails); sys.exit(1 if fails else 0)
