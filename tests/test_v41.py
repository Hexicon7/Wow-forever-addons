# Self-update detection, update summary + undo, backups folder, Wago browsing.
import os, sys, json, time, subprocess, tempfile, urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
sys.path.insert(0, str(Path(__file__).parent)); import fake_cf
fake_cf.start(); T = fake_cf.toc; tmp = Path(tempfile.mkdtemp())
forever = tmp / "World of Warcraft" / "_forever_"; addons = forever / "Interface" / "AddOns"; addons.mkdir(parents=True)
(forever/"WTF"/"Account"/"A").mkdir(parents=True); (forever/"WTF"/"Account"/"A"/"x.lua").write_text("x")
(tmp/"data").mkdir()
(tmp/"data"/"config.json").write_text(json.dumps({"api_key": fake_cf.KEY, "addons_dir": str(addons), "auto_check": False,
    "update_repo": "fake/wfam", "wago_key": fake_cf.WAGO_KEY, "use_wago": True}))
rel = fake_cf.gh_release("fake/wfam", "v9.9.9", {"WoWForeverAddons-Setup-9.9.9.exe": {"x.txt": "y"}})
rel["body"] = "Big new things"
fake_cf.gh_release("fake/wfam", "v1.0.0", {"WoWForeverAddons-Setup-1.0.0.exe": {"x.txt": "y"}})   # older: ignored
fake_cf.GH["fake/wfam"].reverse()  # newest first again
fake_cf.wago_addon("QSwago01", "questie", "Questie"); fake_cf.wago_release("QSwago01", "v12.6.0", {"Questie/Questie.toc": T("Questie", "12.6.0")}, created="2026-10-01T10:00:00Z")
env = dict(os.environ, WFAM_API_BASE=fake_cf.BASE, WFAM_DATA_DIR=str(tmp/"data"), WFAM_GITHUB_API=fake_cf.BASE + "/gh",
           WFAM_WAGO_BASE=fake_cf.BASE + "/wago")
app = subprocess.Popen([sys.executable, "/home/claude/app/WoWForeverAddons.pyw", "--no-browser", "--port", "47981"], env=env)
time.sleep(1.2); fails = 0
B = "http://127.0.0.1:47981"
def check(c, msg):
    global fails; print(("PASS " if c else "FAIL ") + msg); fails += 0 if c else 1
def post(p, b=None):
    try: return json.loads(urllib.request.urlopen(urllib.request.Request(B+p, data=json.dumps(b or {}).encode(), headers={"X-WFAM":"1","Content-Type":"application/json"}, method="POST")).read())
    except urllib.error.HTTPError as e: return json.loads(e.read())
idle = "() => !document.querySelector('#bAdd').disabled && !document.querySelector('#dDot').classList.contains('spin')"
try:
  with sync_playwright() as p:
    b = p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-proxy-server"])
    pg = b.new_page(viewport={"width": 1400, "height": 900}); errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto(B + "/"); time.sleep(1)
    # self-update: found, shown in the menu
    r = post("/api/check_self_update"); time.sleep(5.5)
    check(r.get("update") is True, "newer program version found on GitHub")
    t = pg.inner_text("#selfUpd") if pg.locator("#selfUpd").is_visible() else ""
    check("9.9.9" in t and "Update now" in t, "sidebar offers the update: " + t.replace("\n", " "))
    r = post("/api/self_update"); time.sleep(1.5)
    logs = " ".join(l["msg"] for l in json.loads(urllib.request.urlopen(B+"/api/state").read())["logs"][-3:])
    check("installed program" in logs or "Windows" in logs, "outside the installed Windows program it refuses: " + logs[-120:])
    # backups folder
    post("/api/backup_now"); time.sleep(2)
    old = list((tmp/"data"/"backups").glob("WTF-*.zip")); newdir = tmp/"OneDrive"/"WoW backups"
    r = post("/api/settings", {"backup_dir": str(newdir)}); time.sleep(0.5)
    moved = list(newdir.glob("WTF-*.zip"))
    check(len(old) == 1 and len(moved) == 1 and not list((tmp/"data"/"backups").glob("WTF-*.zip")), f"backups moved to the new folder ({len(old)} -> {len(moved)})")
    st = json.loads(urllib.request.urlopen(B+"/api/state").read())
    check(len(st["backups"]) == 1 and st["config"]["backup_dir"] == str(newdir), "backups listed from the new folder")
    check("error" in post("/api/settings", {"backup_dir": "relative\\path"}), "relative backup folder refused")
    # install, publish, Update all -> summary pops up, undo puts it back
    post("/api/add", {"text": "questie bugsack"}); pg.wait_for_function(idle, timeout=30000); time.sleep(1)
    fake_cf.add_file(1001, "v12.8.0", {"Questie/Questie.toc": T("Questie", "12.8.0")})
    fake_cf.add_file(1002, "v12.2.0", {"BugSack/BugSack.toc": T("BugSack", "12.2.0")})
    pg.click("#bCheck"); time.sleep(0.5); pg.wait_for_function(idle, timeout=30000); time.sleep(1)
    pg.click("#bUpdAll"); time.sleep(0.5); pg.wait_for_function(idle, timeout=30000); time.sleep(1.5)
    check(pg.locator("#mBatch.show").count() == 1, "update summary pops up after Update all")
    t = pg.inner_text("#btL"); check("Questie" in t and "v12.6.0" in t and "v12.8.0" in t and "BugSack" in t, "summary lists old → new: " + t.replace("\n", " | "))
    pg.screenshot(path="/home/claude/shots/v41-batch.png")
    pg.click("#btUndo"); pg.click("#askYes"); time.sleep(0.5); pg.wait_for_function(idle, timeout=30000); time.sleep(1.5)
    st = json.loads(urllib.request.urlopen(B+"/api/state").read())
    q = next(a for a in st["managed"] if a["key"] == "1001"); bs = next(a for a in st["managed"] if a["key"] == "1002")
    check(q["installed_version"] == "v12.6.0" and q["pinned"] and bs["installed_version"] == "v12.1.7" and bs["pinned"], f"undo restored both and pinned them: {q['installed_version']} {bs['installed_version']}")
    check(st["last_batch"] is None and "12.6.0" in (addons/"Questie"/"Questie.toc").read_text(), "files are the old ones again, batch cleared")
    # Wago browsing
    pg.click(".tab[data-v=browse]"); time.sleep(0.8)
    check(pg.locator("#srcBar").is_visible(), "CurseForge / Wago switch shown with a Wago key")
    pg.click(".srcsw button[data-src=wago]"); pg.fill("#bq", "ques"); time.sleep(1.5)
    t = pg.inner_text("#bres"); check("Questie" in t and "Installed" in t, "Wago search finds Questie (marked installed): " + t.replace("\n", " ")[:100])
    pg.screenshot(path="/home/claude/shots/v41-wago.png")
    check(not errs, f"no JS errors: {errs}")
finally:
    try: post("/api/quit")
    except Exception: pass
    app.wait(5)
print("FAILURES:", fails)
