# Wago tab: popular addons without searching, categories and sorting.
import os, sys, json, time, subprocess, tempfile, urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
sys.path.insert(0, str(Path(__file__).parent)); import fake_cf
fake_cf.start(); T = fake_cf.toc; tmp = Path(tempfile.mkdtemp()); (tmp/"data").mkdir()
addons = tmp/"World of Warcraft"/"_forever_"/"Interface"/"AddOns"; addons.mkdir(parents=True)
fake_cf.LEAKY_SEARCH = True
for wid, slug, name, cats, dl, rel in [("WBag0001", "bagnon-w", "Bagnon W", ["Bags & Inventory"], 9000, "1.0.0"),
                                         ("WQst0001", "questie-w", "Questie W", ["Quests & Leveling"], 50000, "2.0.0"),
                                         ("WBag0002", "adibags-w", "AdiBags W", ["Bags & Inventory"], 3000, "3.0.0"),
                                         ("WRet0001", "retail-w", "Retail W", ["Bags & Inventory"], 99999, None)]:
    fake_cf.wago_addon(wid, slug, name, cats, dl)
    if rel: fake_cf.wago_release(wid, rel, {f"{slug}/{slug}.toc": T(name, rel)}, created=f"2026-11-0{len(rel)}T10:00:00Z")
(tmp/"data"/"config.json").write_text(json.dumps({"api_key": fake_cf.KEY, "addons_dir": str(addons), "auto_check": False,
    "wago_key": fake_cf.WAGO_KEY, "use_wago": True}))
env = dict(os.environ, WFAM_API_BASE=fake_cf.BASE, WFAM_DATA_DIR=str(tmp/"data"))
app = subprocess.Popen([sys.executable, os.environ.get("WFAM_APP", "/home/claude/app/WoWForeverAddons.pyw"), "--no-browser", "--port", "47986"], env=env)
time.sleep(1.2); fails = 0; B = "http://127.0.0.1:47986"
def check(c, msg):
    global fails; print(("PASS " if c else "FAIL ") + msg); fails += 0 if c else 1
def post(p, b=None):
    try: return json.loads(urllib.request.urlopen(urllib.request.Request(B+p, data=json.dumps(b or {}).encode(), headers={"X-WFAM":"1","Content-Type":"application/json"}, method="POST")).read())
    except urllib.error.HTTPError as e: return json.loads(e.read())
try:
    r = post("/api/wago_search", {"q": ""})
    names = [x["name"] for x in r["results"]]
    check(names == ["Questie W", "Bagnon W", "AdiBags W"] and r["hidden"] == 1, f"popular list without searching, Forever only, Wago's order: {names}")
    check(r["results"][1]["categories"] == ["Bags & Inventory"] and r["results"][0]["updated"], "categories and last release date come along")
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-proxy-server"])
        pg = b.new_page(viewport={"width": 1400, "height": 900}); errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(B + "/"); time.sleep(1.2)
        pg.click("button.tab[data-v='browse']"); time.sleep(0.8)
        pg.click(".srcsw button[data-src='wago']"); time.sleep(1.5)
        t = pg.inner_text("#bres")
        check("Questie W" in t and "Bagnon W" in t and "Retail W" not in t, "Wago tab shows addons straight away")
        check(pg.locator("#wcat").is_visible() and pg.locator("#wsort").is_visible() and not pg.locator("#bcat").is_visible(), "Wago has its own category and sort menus")
        pg.select_option("#wcat", "Bags & Inventory"); time.sleep(0.4)
        t = pg.inner_text("#bres")
        check("Bagnon W" in t and "AdiBags W" in t and "Questie W" not in t, "category filter works")
        pg.select_option("#wsort", "name"); time.sleep(0.3)
        order = pg.locator("#bres .res .t").all_inner_texts()
        check(order == ["AdiBags W", "Bagnon W"], f"sort by name: {order}")
        check("in Bags & Inventory" in pg.inner_text("#bmeta"), "meta line: " + pg.inner_text("#bmeta"))
        pg.screenshot(path="/home/claude/shots/v45-wago.png")
        pg.select_option("#wcat", ""); pg.fill("#bq", "questie"); time.sleep(1.5)
        check(pg.locator("#bres .res").count() == 1, "typing still searches")
        pg.fill("#bq", ""); time.sleep(1.5)
        check(pg.locator("#bres .res").count() == 3, "clearing the box brings the popular list back")
        pg.click(".srcsw button[data-src='cf']"); time.sleep(1.2)
        check(pg.locator("#bcat").is_visible() and not pg.locator("#wcat").is_visible(), "CurseForge menus come back on the CurseForge tab")
        check(not errs, f"no page errors {errs}")
        b.close()
    fake_cf.WAGO_NO_POPULAR = True; import importlib
    r = post("/api/wago_search", {"q": "x"})   # one letter: nothing yet
    check(r.get("results") == [], "one letter waits for more")
finally:
    app.terminate()
print("FAILURES:", fails); sys.exit(1 if fails else 0)
