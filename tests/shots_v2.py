import os, sys, json, time, subprocess, tempfile, urllib.request, zipfile, io
from pathlib import Path
from playwright.sync_api import sync_playwright
os.environ["NO_PROXY"] = "127.0.0.1,localhost"
sys.path.insert(0, str(Path(__file__).parent)); import fake_cf
fake_cf.start(); tmp = Path(tempfile.mkdtemp())
fo = tmp/"WoW"/"_forever_"; addons = fo/"Interface"/"AddOns"; addons.mkdir(parents=True)
(fo/"WTF"/"Account").mkdir(parents=True); (fo/"WTF"/"Config.wtf").write_text("x"*5000)
(tmp/"data").mkdir(); (tmp/"data"/"config.json").write_text(json.dumps({"api_key": fake_cf.KEY, "addons_dir": str(addons), "downloads_dir": str(tmp)}))
env = dict(os.environ, WFAM_API_BASE=fake_cf.BASE, WFAM_DATA_DIR=str(tmp/"data"), BROWSER="true", WFAM_FAKE_GAME_RUNNING="1")
app = subprocess.Popen([sys.executable, "/home/claude/app/WoWForeverAddons.pyw", "--no-browser", "--port", "47991"], env=env); time.sleep(1.2)
B="http://127.0.0.1:47991"
def post(p, b=None): return urllib.request.urlopen(urllib.request.Request(B+p, data=json.dumps(b or {}).encode(), headers={"X-WFAM":"1","Content-Type":"application/json"}, method="POST")).read()
def idle():
    time.sleep(0.3)
    while json.loads(urllib.request.urlopen(B+"/api/state").read())["job"]["running"]: time.sleep(0.3)
try:
    post("/api/add", {"text": "questie bugsack"}); idle()
    urllib.request.urlopen(urllib.request.Request(fake_cf.BASE+"/admin/publish", data=json.dumps({"mod":1001,"version":"v12.7.0","entries":{"Questie/Questie.toc": fake_cf.toc("Questie","12.7.0")}}).encode(), method="POST"))
    post("/api/update_all"); idle()
    urllib.request.urlopen(urllib.request.Request(fake_cf.BASE+"/admin/publish", data=json.dumps({"mod":1002,"version":"v12.2.0","entries":{"BugSack/BugSack.toc": fake_cf.toc("BugSack","12.2")}}).encode(), method="POST"))
    dbm = fake_cf.ZIPS[next(f for f, v in fake_cf.FILES.items() if v["modId"]==1004)]
    with zipfile.ZipFile(io.BytesIO(dbm)) as z: z.extractall(addons)
    (addons/"README.txt").unlink(missing_ok=True)
    post("/api/check"); idle()
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-proxy-server"])
        pg = b.new_page(viewport={"width":1280,"height":980}); errs=[]
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(B); time.sleep(1.2)
        pg.screenshot(path="/home/claude/shots/v2-installed.png", full_page=True)
        pg.click("text=What's new >> nth=0"); time.sleep(0.8); pg.screenshot(path="/home/claude/shots/v2-changelog.png"); pg.click("#clClose")
        pg.click(".tab[data-v=backups]"); time.sleep(0.5); pg.screenshot(path="/home/claude/shots/v2-backups.png")
        pg.click(".tab[data-v=installed]"); pg.click("#bSettings"); time.sleep(0.3); pg.click("#bDiag")
        for _ in range(40):
            time.sleep(0.5)
            if "Managed addons" in pg.inner_text("#dgBody"): break
        time.sleep(0.5); pg.screenshot(path="/home/claude/shots/v2-diag.png")
        print("errors:", errs)
        b.close()
finally:
    app.kill()
