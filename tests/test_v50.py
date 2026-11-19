# 3.11.2: size setting reaches the desktop window; the activity panel doesn't open by itself; full status text.
import os, sys, json, time, subprocess, tempfile, urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
sys.path.insert(0, str(Path(__file__).parent)); import fake_cf
fake_cf.start(); T = fake_cf.toc; tmp = Path(tempfile.mkdtemp()); (tmp/"data").mkdir()
addons = tmp/"World of Warcraft"/"_forever_"/"Interface"/"AddOns"; addons.mkdir(parents=True)
(tmp/"data"/"config.json").write_text(json.dumps({"api_key": fake_cf.KEY, "addons_dir": str(addons), "auto_check": False}))
env = dict(os.environ, WFAM_API_BASE=fake_cf.BASE, WFAM_DATA_DIR=str(tmp/"data"))
app = subprocess.Popen([sys.executable, os.environ.get("WFAM_APP", "/home/claude/app/WoWForeverAddons.pyw"), "--no-browser", "--port", "47992"], env=env)
time.sleep(1.2); fails = 0; B = "http://127.0.0.1:47992"
def check(c, msg):
    global fails; print(("PASS " if c else "FAIL ") + msg); fails += 0 if c else 1
def post(p, b=None):
    try: return json.loads(urllib.request.urlopen(urllib.request.Request(B+p, data=json.dumps(b or {}).encode(), headers={"X-WFAM":"1","Content-Type":"application/json"}, method="POST")).read())
    except urllib.error.HTTPError as e: return json.loads(e.read())
def get(p): return json.loads(urllib.request.urlopen(B+p).read())
def idle():
    time.sleep(0.25)
    while get("/api/state")["job"]["running"]: time.sleep(0.2)
try:
    check(get("/api/notices?after=0")["ui_scale"] == "100", "default size is real 100 %")
    post("/api/settings", {"ui_scale": "110"})
    check(get("/api/notices?after=0")["ui_scale"] == "110" and get("/api/state")["config"]["ui_scale"] == "110", "Settings → 110 % reaches the window")
    check("error" in post("/api/settings", {"ui_scale": "400"}), "unknown sizes refused")
    post("/api/settings", {"ui_scale": "100"})
    post("/api/add", {"text": "questie"}); idle()
    fake_cf.add_file(1001, "v12.11.1+v1.1.3", {"Questie/Questie.toc": T("Questie", "12.11.1")})
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-proxy-server"])
        pg = b.new_page(viewport={"width": 1180, "height": 760}); errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(B + "/"); time.sleep(1.2)
        pg.click("#bCheck"); time.sleep(2.5)
        check(not pg.locator("#drawer.open").count(), "checking doesn't pop the activity panel open")
        row = pg.locator("tr.arow", has_text="Questie")
        pill = row.locator(".pill")
        check(pill.inner_text().strip() == "Update → v12.11.1+v1.1.3", "status shows the whole version: " + pill.inner_text())
        check(pg.evaluate("(()=>{const e=document.querySelector('tr.arow .pill .tx');return e.scrollWidth<=e.clientWidth+1})()"), "nothing cut off in the status")
        pg.click("#bUpdAll"); time.sleep(0.5)
        if pg.locator("#mAsk.show").count(): pg.click("#askYes")
        time.sleep(3)
        check(not pg.locator("#drawer.open").count() and not pg.locator(".modal.show").count(), "updating doesn't pop anything open either")
        pg.click("#dbar"); time.sleep(0.3)
        check(pg.locator("#drawer.open").count() == 1 and "Installed Questie" in pg.inner_text("#logbox"), "clicking the bottom bar opens the activity")
        pg.click("text=Settings"); time.sleep(0.6)
        check(pg.input_value("#fScale") == "100", "Settings shows the size (100 %)")
        check(not errs, f"no page errors {errs}")
        b.close()
    # start-up: names and icons arrive right away (not after the first full check)
    app.terminate(); app.wait()
    t0 = time.time()
    app = subprocess.Popen([sys.executable, os.environ.get("WFAM_APP", "/home/claude/app/WoWForeverAddons.pyw"), "--no-browser", "--port", "47992"], env=env)
    while True:
        try: s = get("/api/state"); break
        except Exception: time.sleep(0.02)
    while s.get("warming") and time.time() - t0 < 5:
        time.sleep(0.05); s = get("/api/state")
    q = next(a for a in s["managed"] if a["key"] == "1001")
    check(not s.get("warming") and q["summary"] == "Questie summary" and time.time() - t0 < 3, f"names/icons ready {time.time()-t0:.2f}s after start, before any update check")
finally:
    app.terminate()
print("FAILURES:", fails); sys.exit(1 if fails else 0)
