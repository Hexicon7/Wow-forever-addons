# 3.17: the whole setup in one file (export / restore on another PC), a sync folder that keeps two PCs
# in step (offered, never automatic), and "Report ↗" in the Lua-errors dialog.
import os, sys, json, time, subprocess, tempfile, urllib.request, zipfile, shutil
from pathlib import Path
from playwright.sync_api import sync_playwright
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
sys.path.insert(0, str(Path(__file__).parent)); import fake_cf
fake_cf.start(); fake_cf.seed(); T = fake_cf.toc; tmp = Path(tempfile.mkdtemp())
fake_cf.MODS[1001]["links"]["issuesUrl"] = "https://github.com/Questie/Questie/issues"
APP = os.environ.get("WFAM_APP", "/home/claude/app/WoWForeverAddons.pyw")
sync = tmp/"OneDrive"/"WoW"; sync.mkdir(parents=True)
exports = tmp/"Downloads"; exports.mkdir()

def pc(name, port):
    game = tmp/name/"World of Warcraft"/"_forever_"; addons = game/"Interface"/"AddOns"; addons.mkdir(parents=True)
    data = tmp/name/"data"; data.mkdir()
    (data/"config.json").write_text(json.dumps({"api_key": fake_cf.KEY, "addons_dir": str(addons), "auto_check": False}))
    env = dict(os.environ, WFAM_API_BASE=fake_cf.BASE, WFAM_DATA_DIR=str(data), WFAM_COMPUTER=name,
               WFAM_EXPORT_DIR=str(exports), WFAM_SYNC_TICK="1", WFAM_NO_EXPLORER="1")
    proc = subprocess.Popen([sys.executable, APP, "--no-browser", "--port", str(port)], env=env)
    return game, addons, proc, f"http://127.0.0.1:{port}"
fails = 0
def check(c, msg):
    global fails; print(("PASS " if c else "FAIL ") + msg); fails += 0 if c else 1
def post(B, p, b=None, raw=None):
    data = raw if raw is not None else json.dumps(b or {}).encode()
    ct = "application/octet-stream" if raw is not None else "application/json"
    try: return json.loads(urllib.request.urlopen(urllib.request.Request(B+p, data=data, headers={"X-WFAM":"1","Content-Type":ct}, method="POST")).read())
    except urllib.error.HTTPError as e: return json.loads(e.read())
def st(B): return json.loads(urllib.request.urlopen(B+"/api/state").read())
def idle(B):
    time.sleep(0.3)
    while st(B)["job"]["running"]: time.sleep(0.2)
gA, aA, pA, A = pc("DESKTOP", 48001)
gB, aB, pB, Bb = pc("LAPTOP", 48002)
time.sleep(1.5)
try:
    post(A, "/api/add", {"text": "questie bugsack"}); idle(A)
    acc = gA/"WTF"/"Account"/"ME"
    (acc/"SavedVariables").mkdir(parents=True)
    (acc/"SavedVariables"/"Questie.lua").write_text('QuestieConfig = { ["mine"] = true }\n')
    (acc/"SavedVariables"/"Questie.lua.bak").write_text("old")
    (acc/"bindings-cache.wtf").write_text("bind X")
    (acc/"Realm"/"Hero").mkdir(parents=True); (acc/"Realm"/"Hero"/"AddOns.txt").write_text("Questie: enabled\n")
    (gA/"WTF"/"Config.wtf").write_text('SET accountName "secret"\n')
    (aA/"MyHandMade").mkdir(); (aA/"MyHandMade"/"MyHandMade.toc").write_text(T("MyHandMade", "1"))
    # 1) Export everything
    post(A, "/api/setup_export"); idle(A)
    files = list(exports.glob("WoWForeverSetup-DESKTOP-*.zip"))
    check(len(files) == 1, f"exported to Downloads: {[f.name for f in files]}")
    names = zipfile.ZipFile(files[0]).namelist()
    check("setup.json" in names and "WTF/Account/ME/SavedVariables/Questie.lua" in names and "WTF/Account/ME/Realm/Hero/AddOns.txt" in names
          and "AddOns/MyHandMade/MyHandMade.toc" in names, "the file has settings, keybinds, AddOns.txt and the hand-made addon")
    check(not any(n.endswith(".bak") for n in names) and "WTF/Config.wtf" not in names and not any(n.startswith("AddOns/Questie") for n in names),
          "…but not .bak files, Config.wtf (account name, graphics) or addons that can be downloaded again")
    info = json.loads(zipfile.ZipFile(files[0]).read("setup.json"))
    check(info["computer"] == "DESKTOP" and info["share_code"].startswith("WFA1."), "setup.json has the addon list")
    # 2) Restore on the other PC from the file
    post(Bb, "/api/setup_restore", raw=files[0].read_bytes()); idle(Bb)
    sB = st(Bb)
    names_b = sorted(a["name"] for a in sB["managed"])
    check({"Questie", "BugSack", "BugGrabber"} <= set(names_b), f"laptop installed the addons: {names_b}")
    check((gB/"WTF"/"Account"/"ME"/"SavedVariables"/"Questie.lua").read_text().startswith("QuestieConfig") and (aB/"MyHandMade"/"MyHandMade.toc").is_file(),
          "laptop got the settings and the hand-made addon")
    check(not (gB/"WTF"/"Config.wtf").exists(), "Config.wtf left alone")
    # a file that isn't a setup changes nothing
    r = post(Bb, "/api/setup_restore", raw=b"PK\x03\x04 not really"); idle(Bb)
    check(any(w in json.dumps(l) for l in st(Bb)["logs"][-3:] for w in ("isn't", "Unexpected", "zip")), "a bad file is refused")
    # 3) Sync folder
    r = post(A, "/api/settings", {"sync_dir": str(tmp/"nope")})
    check("doesn't exist" in (r.get("error") or ""), "a sync folder that doesn't exist is refused")
    post(A, "/api/settings", {"sync_dir": str(sync)}); post(Bb, "/api/settings", {"sync_dir": str(sync)})
    for _ in range(30):
        if (sync/"WoWForeverSetup-DESKTOP.zip").is_file() and (sync/"WoWForeverSetup-LAPTOP.zip").is_file(): break
        time.sleep(0.5)
    check((sync/"WoWForeverSetup-DESKTOP.zip").is_file() and (sync/"WoWForeverSetup-LAPTOP.zip").is_file(), "both PCs save their setup to the sync folder")
    m1 = (sync/"WoWForeverSetup-DESKTOP.zip").stat().st_mtime; time.sleep(3)
    check((sync/"WoWForeverSetup-DESKTOP.zip").stat().st_mtime == m1, "nothing changed → not saved again")
    # desktop plays more (settings change) → saves again after the 10-minute gap; fake it by clearing the time
    st_file = tmp/"DESKTOP"/"data"/"state.json"
    time.sleep(1)
    (acc/"SavedVariables"/"Questie.lua").write_text('QuestieConfig = { ["mine"] = "newer" }\n')
    (acc/"SavedVariables"/"New.lua").write_text("x = 1\n")
    # newer setup from DESKTOP is offered on the laptop (after its own save) — write it straight away for the test
    post(A, "/api/setup_export"); idle(A)
    time.sleep(31)    # the offer is looked at every 30 s
    o = st(Bb)["sync_offer"]
    check(o and o["computer"] == "DESKTOP", f"laptop is offered the newer desktop setup: {o}")
    check(st(A)["sync_offer"] is None, "the desktop isn't offered its own (or an older) one")
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-proxy-server"])
        pg = b.new_page(viewport={"width": 1400, "height": 900}); errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(Bb + "/"); time.sleep(1.5)
        check(not pg.locator(".modal.show").count() and pg.locator(".tab[data-v='backups'] .newdot").count() == 1, "only a small dot on Backups, no popup")
        pg.click(".tab[data-v='backups']"); time.sleep(0.6); pg.set_viewport_size({'width': 1180, 'height': 830}); time.sleep(0.3); pg.screenshot(path='/home/claude/shots/v57-backups.png'); pg.set_viewport_size({'width': 1400, 'height': 900})
        check("DESKTOP saved a newer setup" in pg.inner_text("#syncOffer") and "last saved its setup there" in pg.inner_text("#syncStatus"), "Backups shows the offer and the sync status")
        check(pg.input_value("#fSync") == str(sync) and pg.inner_text("#bSetupExport") == "Save to sync folder now", "sync folder shown")
        pg.click("#syncOffer button:has-text('Restore')"); time.sleep(0.3); pg.click("#askYes"); time.sleep(1); idle(Bb)
        check((gB/"WTF"/"Account"/"ME"/"SavedVariables"/"New.lua").is_file() and "newer" in (gB/"WTF"/"Account"/"ME"/"SavedVariables"/"Questie.lua").read_text(),
              "Restore from the offer copies the newer settings")
        time.sleep(31.5)
        check(st(Bb)["sync_offer"] is None, "…and it isn't offered again")
        pg.goto(Bb + "/"); time.sleep(1.2)
        check(pg.locator(".tab[data-v='backups'] .newdot").count() == 0, "dot gone")
        check(not errs, f"no page errors {errs}")
        # Report ↗ in the errors dialog (GitHub issue with the errors filled in)
        sv = gB/"WTF"/"Account"/"ME"/"SavedVariables"
        t = time.strftime("%Y/%m/%d %H:%M:%S", time.localtime(time.time() - 1))
        (sv/"!BugGrabber.lua").write_text('BugGrabberDB = { ["errors"] = { { ["message"] = "Interface/AddOns/Questie/Questie.lua:5: boom", ["time"] = "%s", ["counter"] = 2 } } }' % t)
        time.sleep(21); pg.goto(Bb + "/"); time.sleep(1.5)
        pg.click(".tab[data-v='installed']"); time.sleep(0.4)
        pg.click("tr.arow:has-text('Questie') .errl"); time.sleep(0.8)
        href = pg.get_attribute("#clAct a:has-text('Report')", "href")
        check(href and href.startswith("https://github.com/Questie/Questie/issues/new?title=") and "boom" in urllib.parse.unquote(href), "Report opens a filled-in GitHub issue: " + (href or "")[:90])
        b.close()
finally:
    pA.terminate(); pB.terminate()
print("FAILURES:", fails); sys.exit(1 if fails else 0)
