# 3.12: icons stay in Browse, a missing Forever file is mentioned once, other folders found on Wago /
# shown as libraries, keys pasted in one box (each tested only with its own site), compiled start-up copy.
import os, sys, json, time, subprocess, tempfile, urllib.request, shutil
from pathlib import Path
from playwright.sync_api import sync_playwright
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
sys.path.insert(0, str(Path(__file__).parent)); import fake_cf
CFKEY = "$2a$10$" + "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0"
fake_cf.KEY = CFKEY
fake_cf.start(); T = fake_cf.toc; tmp = Path(tempfile.mkdtemp()); (tmp/"data").mkdir()
addons = tmp/"World of Warcraft"/"_forever_"/"Interface"/"AddOns"; addons.mkdir(parents=True)
# other folders: one on Wago by name, one with a Wago id in its .toc, one library used by Questie
(addons/"ShinyThing").mkdir(); (addons/"ShinyThing"/"ShinyThing.toc").write_text(T("Shiny Thing", "0.9"))
(addons/"TocTagged").mkdir(); (addons/"TocTagged"/"TocTagged.toc").write_text(T("Toc Tagged", "1.0") + "## X-Wago-ID: TTagged1\n")
(addons/"LibFoo-1.0").mkdir(); (addons/"LibFoo-1.0"/"LibFoo-1.0.toc").write_text(T("LibFoo-1.0", "3"))
fake_cf.wago_addon("ShinyW01", "shiny-thing", "Shiny Thing"); fake_cf.wago_release("ShinyW01", "1.2.0", {"ShinyThing/ShinyThing.toc": T("Shiny Thing", "1.2.0")})
fake_cf.wago_addon("TTagged1", "toc-tagged", "Toc Tagged"); fake_cf.wago_release("TTagged1", "2.0", {"TocTagged/TocTagged.toc": T("Toc Tagged", "2.0")})
fake_cf.add_file(1001, "v12.6.1", {"Questie/Questie.toc": T("Questie", "12.6.1") + "## OptionalDeps: LibFoo-1.0\n"})
fake_cf.add_mod(3050, "nofor", "No Forever Lib")
fake_cf.add_file(3050, "nf-1.0", {"NoFor/NoFor.toc": T("NoFor", "1.0")})
(tmp/"data"/"config.json").write_text(json.dumps({"addons_dir": str(addons), "auto_check": False}))
APP = os.environ.get("WFAM_APP", "/home/claude/app/WoWForeverAddons.pyw")
env = dict(os.environ, WFAM_API_BASE=fake_cf.BASE, WFAM_DATA_DIR=str(tmp/"data"))
app = subprocess.Popen([sys.executable, APP, "--no-browser", "--port", "47995"], env=env); time.sleep(1.2)
fails = 0; B = "http://127.0.0.1:47995"
def check(c, msg):
    global fails; print(("PASS " if c else "FAIL ") + msg); fails += 0 if c else 1
def post(p, b=None):
    try: return json.loads(urllib.request.urlopen(urllib.request.Request(B+p, data=json.dumps(b or {}).encode(), headers={"X-WFAM":"1","Content-Type":"application/json"}, method="POST")).read())
    except urllib.error.HTTPError as e: return json.loads(e.read())
def st(): return json.loads(urllib.request.urlopen(B+"/api/state").read())
def idle():
    time.sleep(0.25)
    while st()["job"]["running"]: time.sleep(0.2)
def logs(): return [l["msg"] for l in st()["logs"]]
try:
    # keys: one box, recognised, each only tested with its own site
    r = post("/api/save_key", {"key": "  " + fake_cf.WAGO_KEY + "\n"})
    check(r["ok"] and r["kind"] == "wago" and st()["config"]["has_wago_key"], "a Wago key pasted in the box is saved as the Wago key")
    r = post("/api/save_key", {"key": CFKEY[:30]})
    check(not r["ok"] and "cut off" in r["message"], "a half-copied CurseForge key is caught: " + r["message"][:60])
    r = post("/api/save_key", {"key": "$2a$10$" + "x" * 53})
    check(not r["ok"] and "didn't accept" in r["message"], "a wrong CurseForge key isn't saved")
    r = post("/api/save_key", {"key": "not-a-real-key-123"})
    check(not r["ok"] and "isn't a CurseForge key" in r["message"], "a key that's neither says so")
    r = post("/api/save_key", {"key": CFKEY})
    check(r["ok"] and r["kind"] == "curseforge" and st()["config"]["has_key"], "CurseForge key saved: " + r["message"][:50])
    post("/api/add", {"text": "questie 3050"}); idle()
    fake_cf.FILES.get(3050, None)
    # remove the Forever file of 3050 so CurseForge has none for it
    for f in fake_cf.MODS[3050]["latestFilesIndexes"]: f["gameVersionTypeId"] = 999
    for fid, fobj in list(getattr(fake_cf, "FILES", {}).items()):
        if fobj.get("modId") == 3050: fobj["sortableGameVersions"] = [{"gameVersionTypeId": 999, "gameVersion": "x"}]
    for _ in range(3):
        post("/api/check"); idle()
    n = sum(1 for m in logs() if "No Forever Lib" in m and "no WoW Forever file" in m)
    check(n == 1, f"'no Forever file' is said once, not at every check ({n}x)")
    # other folders
    post("/api/scan"); idle()
    un = {g["title"]: g for g in st()["unmanaged"]}
    check(un.get("Shiny Thing", {}).get("wago_id") == "ShinyW01", "a folder CurseForge doesn't know is found on Wago by name")
    check(un.get("Toc Tagged", {}).get("wago_id") == "TTagged1", "a folder whose .toc names its Wago id is recognised")
    check(un.get("LibFoo-1.0", {}).get("used_by") == ["Questie"], f"library folder shows who uses it: {un.get('LibFoo-1.0', {}).get('used_by')}")
    post("/api/add_wago", {"id": "ShinyW01"}); idle()
    s = st()
    check(any(a["key"] == "wago-ShinyW01" and a["installed_version"] == "1.2.0" for a in s["managed"]) and "Shiny Thing" not in [g["title"] for g in s["unmanaged"]],
          "Manage with updates installs it from Wago and it leaves Other folders")
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-proxy-server"])
        pg = b.new_page(viewport={"width": 1300, "height": 860}); errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(B + "/"); time.sleep(1.5)
        pg.click("button.tab[data-v='browse']"); time.sleep(0.6); pg.fill("#bq", "questie"); time.sleep(2)
        card = pg.locator("#bres .res", has_text="Questie").first
        check(card.locator(".logo").count() == 1, "Browse card keeps its icon while My addons shows the same one")
        pg.evaluate("refresh()"); time.sleep(1)
        check(card.locator(".logo").count() == 1, "…also after the list redraws")
        pg.click("button.tab[data-v='installed']"); time.sleep(0.6)
        check(pg.locator("tr.arow:has-text('Questie') .logo").count() == 1, "My addons row has its icon too")
        un_txt = pg.inner_text("#unCard")
        check("Library used by Questie" in un_txt and "found on Wago" in un_txt, "Other folders explains what they are")
        check(not errs, f"no page errors {errs}")
        b.close()
    app.terminate(); app.wait()
    # first start without a key: the paste box is right on the page
    (tmp/"data2").mkdir(); (tmp/"data2"/"config.json").write_text(json.dumps({"addons_dir": str(addons), "auto_check": False}))
    env2 = dict(env, WFAM_DATA_DIR=str(tmp/"data2"))
    app = subprocess.Popen([sys.executable, APP, "--no-browser", "--port", "47995"], env=env2); time.sleep(1.2)
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-proxy-server"])
        pg = b.new_page(viewport={"width": 1300, "height": 860}); pg.goto(B + "/"); time.sleep(1.2)
        box = pg.locator("#managedCard input[type=password]")
        check(box.count() == 1 and "CurseForge" in pg.inner_text("#managedCard"), "first start: paste box for the key on the main page")
        pg.screenshot(path="/home/claude/shots/v52-firststart.png")
        box.fill(CFKEY); box.press("Enter"); time.sleep(2)
        check(st()["config"]["has_key"], "pasting the key there saves it — no Settings page needed")
        pg.screenshot(path="/home/claude/shots/v52-setup.png")
        b.close()
    app.terminate(); app.wait()
    # compiled copy for a faster start (desktop program only)
    inst = tmp/"inst"; inst.mkdir(); shutil.copy(APP, inst/"WoWForeverAddons.pyw")
    env3 = dict(env, WFAM_BYTECODE_DELAY="0.5")
    app = subprocess.Popen([sys.executable, str(inst/"WoWForeverAddons.pyw"), "--no-browser", "--port", "47995", "--host-pid", str(os.getpid())], env=env3); time.sleep(3)
    pyc = inst/"WoWForeverAddons.pyc"
    check(pyc.is_file() and pyc.stat().st_mtime >= (inst/"WoWForeverAddons.pyw").stat().st_mtime, "the desktop program saves a compiled copy of itself")
    app.terminate(); app.wait()
    r = subprocess.run([sys.executable, str(pyc), "--version"], capture_output=True, text=True, env=env)
    check("usage" in (r.stdout + r.stderr) or r.returncode == 0, "the compiled copy runs")
finally:
    app.terminate()
print("FAILURES:", fails); sys.exit(1 if fails else 0)
