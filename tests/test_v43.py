# Browse shows only addons with a WoW Forever build, even when the search source lets others through.
import os, sys, json, time, subprocess, tempfile, urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
sys.path.insert(0, str(Path(__file__).parent)); import fake_cf
fake_cf.start(); T = fake_cf.toc; tmp = Path(tempfile.mkdtemp()); (tmp/"data").mkdir()
addons = tmp/"World of Warcraft"/"_forever_"/"Interface"/"AddOns"; addons.mkdir(parents=True)
fake_cf.LEAKY_SEARCH = True
fake_cf.wago_addon("WGforev1", "forever-thing", "Forever Thing"); fake_cf.wago_release("WGforev1", "1.0", {"FT/FT.toc": T("FT", "1.0")})
fake_cf.wago_addon("WGretail", "retail-thing", "Retail Thing")      # no Forever release
(tmp/"data"/"config.json").write_text(json.dumps({"api_key": fake_cf.KEY, "addons_dir": str(addons), "auto_check": False,
    "wago_key": fake_cf.WAGO_KEY, "use_wago": True}))
env = dict(os.environ, WFAM_API_BASE=fake_cf.BASE, WFAM_DATA_DIR=str(tmp/"data"), WFAM_GITHUB_API=fake_cf.BASE + "/gh",
           WFAM_WAGO_BASE=fake_cf.BASE + "/wago")
app = subprocess.Popen([sys.executable, os.environ.get("WFAM_APP", "/home/claude/app/WoWForeverAddons.pyw"), "--no-browser", "--port", "47985"], env=env)
time.sleep(1.2); fails = 0; B = "http://127.0.0.1:47985"
def check(c, msg):
    global fails; print(("PASS " if c else "FAIL ") + msg); fails += 0 if c else 1
def post(p, b=None):
    try: return json.loads(urllib.request.urlopen(urllib.request.Request(B+p, data=json.dumps(b or {}).encode(), headers={"X-WFAM":"1","Content-Type":"application/json"}, method="POST")).read())
    except urllib.error.HTTPError as e: return json.loads(e.read())
try:
    r = post("/api/search", {"q": "only"})
    names = [x["name"] for x in r["results"]]
    check("Classic Only" not in names and "Beta Only" in names and r["hidden"] == 1, f"classic-only addon hidden: {names}, hidden={r.get('hidden')}")
    r = post("/api/search", {"q": "only", "all": True})
    co = next((x for x in r["results"] if x["name"] == "Classic Only"), None)
    check(co and co["forever"] is False and r["hidden"] == 0, "Show them: listed, marked as not for Forever")
    r = post("/api/search", {"q": "questie"})
    check([x["name"] for x in r["results"]] == ["Questie"] and r["results"][0]["forever"], "addon with Classic and Forever files stays")
    r = post("/api/search", {"q": ""})
    check(len(r["results"]) >= 24 and all(x["forever"] for x in r["results"]) and r["hidden"] >= 1, f"popular list filtered ({len(r['results'])} shown, {r['hidden']} hidden)")
    seen, nxt, pages = {x["id"] for x in r["results"]}, r["next"], 0
    while nxt is not None and pages < 5:
        r2 = post("/api/search", {"q": "", "index": nxt}); pages += 1
        dup = seen & {x["id"] for x in r2["results"]}; seen |= {x["id"] for x in r2["results"]}; nxt = r2["next"]
        check(not dup, f"Show more page {pages} has no repeats")
    check(1008 not in seen and 1001 in seen, "paging through everything never shows the classic-only addon")
    w = post("/api/wago_search", {"q": "thing"})
    check([x["name"] for x in w["results"]] == ["Forever Thing"] and w["hidden"] == 1, f"Wago: addon without a Forever release hidden: {w}")
    w = post("/api/wago_search", {"q": "thing", "all": True})
    check(len(w["results"]) == 2 and any(x["forever"] is False for x in w["results"]), "Wago: Show them lists it, marked")
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-proxy-server"])
        pg = b.new_page(viewport={"width": 1400, "height": 900}); errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(B + "/"); time.sleep(1.2)
        pg.click("button.tab[data-v='browse']")
        time.sleep(0.5)
        if pg.locator("#bq").count():
            pg.fill("#bq", "only"); time.sleep(1.8)
        meta = pg.inner_text("#bmeta")
        check("hidden" in meta and "Show them" in meta, "browse says how many were hidden: " + meta)
        pg.click("#bmeta >> text=Show them"); time.sleep(1.5)
        t = pg.inner_text("#bres")
        check("Classic Only" in t and "no Forever version" in t, "after Show them it's listed with a warning tag")
        check("Only WoW Forever addons" in pg.inner_text("#bmeta"), "and the toggle switches back")
        pg.screenshot(path="/home/claude/shots/v43-browse.png")
        check(not errs, f"no page errors {errs}")
        b.close()
finally:
    app.terminate()
print("FAILURES:", fails); sys.exit(1 if fails else 0)
