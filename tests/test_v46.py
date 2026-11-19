# Program self-update: never automatic — a notification and an "Update now / Later" choice, plus a check button.
import os, sys, json, time, subprocess, tempfile, urllib.request, hashlib, threading
from pathlib import Path
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
sys.path.insert(0, str(Path(__file__).parent)); import fake_cf
fake_cf.start(); T = fake_cf.toc
APP = os.environ.get("WFAM_APP", "/home/claude/app/WoWForeverAddons.pyw")
exe = b"MZ" + os.urandom(500)
fake_cf.RAW["fake/selfup/main/updates/latest.json"] = json.dumps({"version": "9.9.10", "file": "WoWForeverAddons-Setup-9.9.10.exe",
    "sha256": hashlib.sha256(exe).hexdigest(), "size": len(exe), "notes": "n"}).encode()
fake_cf.RAW["fake/selfup/main/updates/WoWForeverAddons-Setup-9.9.10.exe"] = exe
fails = 0
def check(c, m):
    global fails; print(("PASS " if c else "FAIL ") + m); fails += 0 if c else 1
def run(port, auto, ui, hosted=True, wait=6):
    tmp = Path(tempfile.mkdtemp()); (tmp/"data").mkdir()
    a = tmp/"WoW"/"_forever_"/"Interface"/"AddOns"; a.mkdir(parents=True)
    (tmp/"data"/"config.json").write_text(json.dumps({"api_key": fake_cf.KEY, "addons_dir": str(a), "auto_check": False,
        "update_repo": "fake/selfup", "auto_self_update": auto}))
    env = dict(os.environ, WFAM_API_BASE=fake_cf.BASE, WFAM_DATA_DIR=str(tmp/"data"), WFAM_GITHUB_API=fake_cf.BASE + "/gh",
               WFAM_GITHUB_RAW=fake_cf.BASE + "/raw", WFAM_SELF_UPDATE_EVERY="2")
    args = [sys.executable, APP, "--no-browser", "--port", str(port)] + (["--host-pid", str(os.getpid())] if hosted else [])
    p = subprocess.Popen(args, env=env, stdout=subprocess.DEVNULL); B = f"http://127.0.0.1:{port}"
    time.sleep(1.2); stop = [False]
    def poller():
        while not stop[0]:
            try: urllib.request.urlopen(B + "/api/state").read()
            except Exception: pass
            time.sleep(0.5)
    if ui: threading.Thread(target=poller, daemon=True).start()
    time.sleep(wait); stop[0] = True
    st = json.loads(urllib.request.urlopen(B + "/api/state").read())
    no = json.loads(urllib.request.urlopen(B + "/api/notices?after=0").read())
    def post(path):
        return json.loads(urllib.request.urlopen(urllib.request.Request(B+path, data=b"{}", headers={"X-WFAM":"1","Content-Type":"application/json"}, method="POST")).read())
    out = {"logs": " | ".join(l["msg"] for l in st["logs"]), "notices": " | ".join(n["title"] for n in no["notices"]), "st": st, "post": post}
    return p, out
p, o = run(47971, auto=True, ui=False)
check("9.9.10 is available" in o["notices"] and "installed program" not in o["logs"] and "Updating" not in o["notices"],
      f"window closed: it never updates by itself, it only tells you ({o['notices']})")
r = o["post"]("/api/check_self_update"); check(r["update"] and r["version"] == "9.9.10" and r["current"], f"the Check button answers with versions: {r}")
p.terminate(); p.wait()
from playwright.sync_api import sync_playwright
p, o = run(47974, auto=False, ui=False, hosted=False, wait=0.5)
try:
    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-proxy-server"])
        pg = b.new_page(viewport={"width": 1400, "height": 900}); errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto("http://127.0.0.1:47974/"); time.sleep(1.2)
        check(pg.locator("#mAsk.show").count() == 0, "nothing pops up before a new version is known")
        check(pg.locator("#bSelfChk").is_visible(), "a 'Check for program update' button sits under the version number")
        pg.click("#bSelfChk"); time.sleep(1.5)
        check(pg.locator("#mAsk.show").count() == 1 and "9.9.10" in pg.inner_text("#askT"), "clicking it offers the new version: " + pg.inner_text("#askT"))
        pg.screenshot(path="/home/claude/shots/v46-selfcheck.png")
        pg.click("#askNo"); time.sleep(0.5)
        check(pg.locator("#mAsk.show").count() == 0 and pg.locator("#selfUpd").is_visible(), "Cancel: nothing installed, the update stays offered in the menu")
        pg.reload(); time.sleep(2)
        check(pg.locator("#mAsk.show").count() == 1 and "is available" in pg.inner_text("#askT") and pg.inner_text("#askNo") == "Later", "when the program opens and knows of a new version it asks once: Update now / Later")
        pg.click("#askNo"); time.sleep(0.8); pg.reload(); time.sleep(2)
        check(pg.locator("#mAsk.show").count() == 0 and pg.locator("#selfUpd").is_visible(), "after Later it doesn't ask again for that version, but the menu still offers it")
        logs = " | ".join(l["msg"] for l in json.loads(urllib.request.urlopen("http://127.0.0.1:47974/api/state").read())["logs"])
        check("installed program" not in logs and "Settings saved" not in logs, "nothing was installed and no noise in the activity log")
        check(not errs, f"no page errors {errs}")
        b.close()
finally:
    p.terminate(); p.wait()
print("FAILURES:", fails); sys.exit(1 if fails else 0)
