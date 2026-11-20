# 3.15.1: an addon without a CurseForge icon borrows Wago's; an icon that never finishes loading shows the letter.
import os, sys, json, time, subprocess, tempfile, urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
sys.path.insert(0, str(Path(__file__).parent)); import fake_cf
fake_cf.start(); fake_cf.seed(); T = fake_cf.toc; tmp = Path(tempfile.mkdtemp()); (tmp/"data").mkdir()
addons = tmp/"World of Warcraft"/"_forever_"/"Interface"/"AddOns"; addons.mkdir(parents=True)
THUMB = fake_cf.BASE + "/static/grabber.png"
# BugGrabber (1003) has no icon on CurseForge (logo None) but one on Wago
fake_cf.wago_addon("BGrab01", "bug-grabber", "BugGrabber", thumb=THUMB)
fake_cf.wago_release("BGrab01", "v12.1.0", {"!BugGrabber/!BugGrabber.toc": T("!BugGrabber", "12.1.0")})
(tmp/"data"/"config.json").write_text(json.dumps({"api_key": fake_cf.KEY, "addons_dir": str(addons), "auto_check": False,
                                                  "wago_key": fake_cf.WAGO_KEY, "use_wago": True}))
APP = os.environ.get("WFAM_APP", "/home/claude/app/WoWForeverAddons.pyw")
env = dict(os.environ, WFAM_API_BASE=fake_cf.BASE, WFAM_DATA_DIR=str(tmp/"data"), WFAM_WAGO_BASE=fake_cf.BASE + "/wago")
B = "http://127.0.0.1:47998"
app = subprocess.Popen([sys.executable, APP, "--no-browser", "--port", "47998"], env=env); time.sleep(1.2)
fails = 0
def check(c, msg):
    global fails; print(("PASS " if c else "FAIL ") + msg); fails += 0 if c else 1
def post(p, b=None):
    try: return json.loads(urllib.request.urlopen(urllib.request.Request(B+p, data=json.dumps(b or {}).encode(), headers={"X-WFAM":"1","Content-Type":"application/json"}, method="POST")).read())
    except urllib.error.HTTPError as e: return json.loads(e.read())
def st(): return json.loads(urllib.request.urlopen(B+"/api/state").read())
def idle():
    time.sleep(0.25)
    while st()["job"]["running"]: time.sleep(0.2)
try:
    r = post("/api/search", {"q": "BugGrabber", "sort": "2", "category": "", "index": 0})
    g = next((x for x in r.get("results", []) if x["name"] == "BugGrabber"), None)
    check(g and g["logo"] == THUMB, f"Browse: no CurseForge icon → Wago's icon ({g and g.get('logo')})")
    post("/api/add", {"text": "bug-grabber"}); idle()
    post("/api/check"); idle()
    a = next((x for x in st()["managed"] if x["name"] == "BugGrabber"), None)
    check(a and a["logo"] == THUMB, f"My addons: Wago's icon after a check ({a and a.get('logo')})")
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-proxy-server"])
        pg = b.new_page(viewport={"width": 1400, "height": 900}); errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(B + "/"); time.sleep(1.5)
        pg.evaluate("""u => { const d = document.createElement('div'); d.id = 'slow'; d.style = 'position:fixed;top:0;left:0;z-index:99';
                         d.append(logoEl(u, 'Slow Addon')); document.body.append(d); }""", fake_cf.BASE + "/slowimg/x.png")
        time.sleep(1)
        check(pg.locator("#slow img").count() == 1, "the icon starts loading")
        time.sleep(13)
        check(pg.locator("#slow img").count() == 0 and pg.inner_text("#slow .logo.fb") == "S", "after 12 s without an image the letter is shown")
        check(not errs, f"no page errors {errs}")
        b.close()
finally:
    app.terminate()
print("FAILURES:", fails); sys.exit(1 if fails else 0)
