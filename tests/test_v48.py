# ElvUI & Tukui from tukui.org (kept up to date like everything else), and the update summary only opens on click.
import os, sys, json, time, subprocess, tempfile, urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
sys.path.insert(0, str(Path(__file__).parent)); import fake_cf
fake_cf.start(); T = fake_cf.toc; tmp = Path(tempfile.mkdtemp()); (tmp/"data").mkdir()
addons = tmp/"World of Warcraft"/"_forever_"/"Interface"/"AddOns"; addons.mkdir(parents=True)
def elv(v):
    return {"ElvUI/ElvUI.toc": T("ElvUI", v, "11509, 16001, 20506, 120100"), "ElvUI/core.lua": "-- " + v,
            "ElvUI_Options/ElvUI_Options.toc": T("ElvUI Options", v, "11509, 16001, 120100"),
            "ElvUI_Libraries/ElvUI_Libraries.toc": T("ElvUI Libraries", v, "11509, 16001, 120100")}
fake_cf.tukui_addon("elvui", "ElvUI", "16.05", elv("16.05"))
fake_cf.tukui_addon("tukui", "Tukui", "11.2.7", {"Tukui/Tukui-Mainline.toc": T("Tukui", "11.2.7", "110207"),
                                                   "Tukui/Tukui-Classic.toc": T("Tukui", "11.2.7", "11507")})
(tmp/"data"/"config.json").write_text(json.dumps({"api_key": fake_cf.KEY, "addons_dir": str(addons), "auto_check": False}))
env = dict(os.environ, WFAM_API_BASE=fake_cf.BASE, WFAM_DATA_DIR=str(tmp/"data"), WFAM_TUKUI_API=fake_cf.BASE + "/tukui")
app = subprocess.Popen([sys.executable, os.environ.get("WFAM_APP", "/home/claude/app/WoWForeverAddons.pyw"), "--no-browser", "--port", "47988"], env=env)
time.sleep(1.2); fails = 0; B = "http://127.0.0.1:47988"
def check(c, msg):
    global fails; print(("PASS " if c else "FAIL ") + msg); fails += 0 if c else 1
def post(p, b=None):
    try: return json.loads(urllib.request.urlopen(urllib.request.Request(B+p, data=json.dumps(b or {}).encode(), headers={"X-WFAM":"1","Content-Type":"application/json"}, method="POST")).read())
    except urllib.error.HTTPError as e: return json.loads(e.read())
def st(): return json.loads(urllib.request.urlopen(B+"/api/state").read())
def idle():
    time.sleep(0.25)
    while st()["job"]["running"]: time.sleep(0.2)
def rec(k): return next((x for x in st()["managed"] if x["key"] == k), None)
def logs(): return " | ".join(l["msg"] for l in st()["logs"])
try:
    r = post("/api/tukui_list")
    check([x["name"] for x in r["results"]] == ["ElvUI", "Tukui"] and r["results"][0]["version"] == "16.05", "tukui.org list: ElvUI and Tukui")
    post("/api/add_tukui", {"slug": "elvui"}); idle()
    e = rec("tukui-elvui")
    check(e and e["installed_version"] == "16.05" and e["source"] == "tukui" and e["tukui"] and sorted(e["folders"]) == ["ElvUI", "ElvUI_Libraries", "ElvUI_Options"],
          f"ElvUI installed with its three folders: {e and (e['installed_version'], e['folders'])}")
    check((addons/"ElvUI"/"core.lua").read_text() == "-- 16.05" and e["compat"] != "mismatch", "files in place, made for Forever")
    post("/api/add_tukui", {"slug": "tukui"}); idle()
    check(rec("tukui-tukui") is None and not (addons/"Tukui").exists() and "isn't built for WoW Forever" in logs(), "Tukui without a Forever version is refused, nothing changed")
    check(next(x for x in post("/api/tukui_list")["results"] if x["slug"] == "tukui")["no_forever"], "…and the list marks it 'no Forever version yet'")
    post("/api/check"); idle()
    check(rec("tukui-elvui")["status"] == "ok", "check: ElvUI up to date")
    fake_cf.TUKUI.clear()
    fake_cf.tukui_addon("elvui", "ElvUI", "16.10", elv("16.10"))
    fake_cf.tukui_addon("tukui", "Tukui", "11.2.7", {"Tukui/Tukui-Mainline.toc": T("Tukui", "11.2.7", "110207")})
    import importlib
    time.sleep(0.1)
    # the app caches tukui.org's list for 10 minutes; a check after that sees 16.10 — simulate by restarting
    app.terminate(); app.wait()
    app = subprocess.Popen([sys.executable, os.environ.get("WFAM_APP", "/home/claude/app/WoWForeverAddons.pyw"), "--no-browser", "--port", "47988"], env=env); time.sleep(1.2)
    post("/api/check"); idle()
    e = rec("tukui-elvui")
    check(e["status"] == "update" and e["latest_version"] == "16.10" and e["update_from"] == "tukui", f"new ElvUI found ({e['status']}, {e['latest_version']})")
    post("/api/update_all"); idle()
    e = rec("tukui-elvui")
    check(e["installed_version"] == "16.10" and (addons/"ElvUI"/"core.lua").read_text() == "-- 16.10" and e["status"] == "ok", "Update all updates ElvUI")
    check(e.get("previous_version") == "16.05", "the old ElvUI is kept for revert")
    code = post("/api/share_code")["code"]
    check(code.startswith("WFA1."), "share code includes it")
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-proxy-server"])
        pg = b.new_page(viewport={"width": 1400, "height": 900}); errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(B + "/"); time.sleep(1.5)
        check(pg.locator("#mBatch.show").count() == 0 and pg.locator("#bLast").is_visible() and "1 updated" in pg.inner_text("#bLast"), "no popup; a '✓ 1 updated' button instead")
        pg.click("#bLast"); time.sleep(0.4)
        check(pg.locator("#mBatch.show").count() == 1 and "16.05" in pg.inner_text("#btL") and "16.10" in pg.inner_text("#btL"), "clicking it shows what changed")
        pg.click("#btClose"); time.sleep(0.2)
        row = pg.locator("tr.arow", has_text="ElvUI"); row.locator("button.more").click(); time.sleep(0.3)
        menu = pg.inner_text("#pop")
        check("Open on tukui.org" in menu and "Details" not in menu, "row menu for ElvUI: " + menu.replace("\n", " | "))
        pg.keyboard.press("Escape"); pg.mouse.click(5, 5); time.sleep(0.2)
        # an update while the window is open doesn't pop anything up either
        fake_cf.TUKUI.clear(); fake_cf.tukui_addon("elvui", "ElvUI", "16.11", elv("16.11"))
        app.terminate(); app.wait()
        app = subprocess.Popen([sys.executable, os.environ.get("WFAM_APP", "/home/claude/app/WoWForeverAddons.pyw"), "--no-browser", "--port", "47988"], env=env); time.sleep(1.5)
        pg.reload(); time.sleep(1.2)
        pg.click("#bCheck"); time.sleep(2); pg.click("#bUpdAll"); time.sleep(0.5)
        if pg.locator("#mAsk.show").count(): pg.click("#askYes")
        time.sleep(3)
        check(rec("tukui-elvui")["installed_version"] == "16.11", "Update all from the window")
        check(pg.locator("#mBatch.show").count() == 0 and pg.locator(".modal.show").count() == 0, "no summary pops up after Update all")
        pg.click("button.tab[data-v='browse']"); time.sleep(0.6); pg.click(".srcsw button[data-src='tukui']"); time.sleep(1.5)
        t = pg.inner_text("#bres")
        check("ElvUI" in t and "Installed 16.11" in t and pg.inner_text("#vTitle") == "ElvUI & Tukui", "ElvUI & Tukui tab")
        pg.screenshot(path="/home/claude/shots/v48-tukui.png")
        check(not errs, f"no page errors {errs}")
        b.close()
finally:
    app.terminate()
print("FAILURES:", fails); sys.exit(1 if fails else 0)
