# 3.13: WeakAuras in the sidebar, ElvUI/Tukui in the normal addon search, My addons sortable (menu + column
# headings), the app remembers where you were, coloured letter icons, real logo, shortcuts, updated texts.
import os, sys, json, time, subprocess, tempfile, urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
sys.path.insert(0, str(Path(__file__).parent)); import fake_cf
fake_cf.start(); fake_cf.seed(); T = fake_cf.toc; tmp = Path(tempfile.mkdtemp()); (tmp/"data").mkdir()
addons = tmp/"World of Warcraft"/"_forever_"/"Interface"/"AddOns"; addons.mkdir(parents=True)
fake_cf.tukui_addon("elvui", "ElvUI", "16.05", {"ElvUI/ElvUI_Mainline.toc": T("ElvUI", "16.05"),
                                                "ElvUI_Options/ElvUI_Options_Mainline.toc": T("ElvUI_Options", "16.05")})
fake_cf.aura("SidebarA1", "sidebar-aura", "Sidebar Aura", installs=50)
(tmp/"data"/"config.json").write_text(json.dumps({"api_key": fake_cf.KEY, "addons_dir": str(addons), "auto_check": False}))
APP = os.environ.get("WFAM_APP", "/home/claude/app/WoWForeverAddons.pyw")
env = dict(os.environ, WFAM_API_BASE=fake_cf.BASE, WFAM_DATA_DIR=str(tmp/"data"), WFAM_TUKUI_API=fake_cf.BASE + "/tukui",
           WFAM_WAGO_DATA=fake_cf.BASE + "/wagodata")
B = "http://127.0.0.1:47996"
def start(): return subprocess.Popen([sys.executable, APP, "--no-browser", "--port", "47996"], env=env)
app = start(); time.sleep(1.2)
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
    post("/api/add", {"text": "questie bugsack deadly-boss-mods"}); idle()
    names = sorted(a["name"] for a in st()["managed"])
    check(len(names) >= 3, f"three addons (plus deps) installed: {names}")
    # the UI-state endpoint keeps only small, clean values and logs nothing
    n_logs = len(st()["logs"])
    post("/api/ui_state", {"state": {"view": "chars", "msort": "name", "evil key!": "x", "bcat": "<script>" + "a" * 90}})
    u = st()["config"]["ui_state"]
    check(u.get("view") == "chars" and "evil key!" not in u and "<" not in u.get("bcat", "") and len(u.get("bcat", "")) <= 40,
          f"ui_state cleaned: {u}")
    check(len(st()["logs"]) == n_logs, "saving where you were writes nothing to the activity log")
    post("/api/ui_state", {"state": {}})
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-proxy-server"])
        pg = b.new_page(viewport={"width": 1400, "height": 900}); errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(B + "/"); time.sleep(1.5)
        tabs = pg.locator(".side nav button.tab .lbl").all_inner_texts()
        check(tabs[:5] == ["My addons", "Browse", "WeakAuras", "Characters", "Backups"], f"sidebar: {tabs}")
        check(pg.locator(".brand .mark img").count() == 1 and pg.evaluate("document.querySelector('.brand .mark img').naturalWidth") > 0,
              "the sidebar shows the program's real icon")
        # sorting: default status, then name via heading, reverse with a second click, menu works too
        rows = lambda: pg.locator("#managedCard tbody tr.arow .nm .t").all_inner_texts()
        pg.click("#managedCard th:has-text('Addon')"); time.sleep(0.4)
        r1 = rows()
        check(r1 == sorted(r1, key=str.lower), f"click 'Addon' → A–Z: {r1}")
        check("▲" in pg.inner_text("#managedCard th.on"), "the sorted column is marked with an arrow")
        pg.click("#managedCard th:has-text('Addon')"); time.sleep(0.4)
        r2 = rows()
        check(r2 == sorted(r1, key=str.lower, reverse=True), f"click again → Z–A: {r2}")
        pg.click("#managedCard th:has-text('Status')"); time.sleep(0.4)
        on_th = pg.locator("#managedCard th.on").all_inner_texts()
        check(len(on_th) == 1 and on_th[0].lower().startswith("status"), f"Status heading sorts: {on_th}")
        pg.click("#managedCard th:has-text('Addon')"); time.sleep(0.4)
        check(rows() == r1 and pg.locator("#msort").count() == 0, "back to A–Z; no sort menu repeating the headings")
        # fallback letters get their own colour
        pg.evaluate("""() => { const a = document.createElement('div'); a.id = 'fbtest';
             a.append(logoEl('', 'Alpha'), logoEl('', 'Zeta Thing'), logoEl('', '!BugGrabber')); document.body.append(a); }""")
        fbs = pg.locator("#fbtest .logo.fb")
        hues = [fbs.nth(i).evaluate("e => e.style.getPropertyValue('--hue')") for i in range(3)]
        check(fbs.count() == 3 and hues[0] != hues[1] and fbs.nth(2).inner_text() == "B", f"letter icons coloured by name ({hues}), '!BugGrabber' → B")
        # Browse: ElvUI comes up in the normal addon search
        pg.click("button.tab[data-v='browse']"); time.sleep(1.2)
        check(pg.inner_text("#vTitle") == "Browse addons" and pg.locator(".srcsw").count() == 0, "Browse is one addon search")
        pg.fill("#bq", "elvui"); time.sleep(2)
        t = pg.inner_text("#bres")
        check("ElvUI" in t and "tukui.org" in t and "from tukui.org" in pg.inner_text("#bmeta"), "ElvUI in the addon search: " + t.replace("\n", " | ")[:160])
        pg.fill("#bq", "questie"); time.sleep(2)
        check("ElvUI" not in pg.inner_text("#bres") and "Questie" in pg.inner_text("#bres"), "…and not when the search is about something else")
        # WeakAuras: own tab, own search text
        pg.click("button.tab[data-v='auras']"); time.sleep(1.5)
        check(pg.inner_text("#vTitle") == "WeakAuras for WoW Forever" and "Sidebar Aura" in pg.inner_text("#bres") and pg.input_value("#bq") == "",
              "WeakAuras tab with its own (empty) search")
        check(pg.locator("button.tab[data-v='auras'].on").count() == 1, "WeakAuras is marked in the sidebar")
        pg.select_option("#asort", "date"); time.sleep(0.8)
        pg.keyboard.press("Control+2"); time.sleep(1)
        check(pg.input_value("#bq") == "questie" and "Questie" in pg.inner_text("#bres"), "Ctrl+2 back to Browse keeps its search")
        pg.keyboard.press("Control+1"); time.sleep(0.5)
        check(pg.inner_text("#vTitle") == "My addons", "Ctrl+1 → My addons")
        # F5 = check for updates (not a page reload)
        pg.evaluate("window.__alive = 1"); pg.keyboard.press("F5"); time.sleep(1.5); idle()
        check(pg.evaluate("window.__alive") == 1 and st()["managed"][0].get("checked_at"), "F5 checks for updates instead of reloading")
        check("Look up online" in pg.inner_text("#unH") or pg.locator("#bScan").count() == 1, "button renamed")
        check(pg.locator("#bScan").inner_text() == "Look up online", "'Look up online' (it uses Wago too)")
        pg.click("button.tab[data-v='auras']"); time.sleep(1.2)
        time.sleep(1)   # the save is debounced
        check(not errs, f"no page errors {errs}")
        b.close()
    u = st()["config"]["ui_state"]
    check(u.get("view") == "auras" and u.get("msort") == "name" and u.get("asort") == "date", f"where you were is saved: {u}")
    app.terminate(); app.wait(); app = start(); time.sleep(1.5)
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-proxy-server"])
        pg = b.new_page(viewport={"width": 1400, "height": 900}); errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(B + "/"); time.sleep(2)
        check(pg.inner_text("#vTitle") == "WeakAuras for WoW Forever" and pg.input_value("#asort") == "date", "next start opens where you left off")
        pg.click("button.tab[data-v='installed']"); time.sleep(0.5)
        check("addon" in pg.inner_text("#managedCard th.on").lower(), "…with your sorting in My addons")
        pg.click("#bDiag") if pg.locator("#bDiag").is_visible() else None
        check("CurseForge, Wago, GitHub and tukui.org" in pg.content(), "Diagnostics text names every source")
        check(not errs, f"no page errors {errs}")
        b.close()
    r = post("/api/diagnose"); idle(); time.sleep(0.5)
    titles = [x["title"] for x in (st().get("diag") or {}).get("results", [])]
    check("tukui.org reachable" in titles and "wago.io WeakAuras reachable" in titles, f"diagnostics checks tukui.org and wago.io: {titles[-6:]}")
finally:
    app.terminate()
print("FAILURES:", fails); sys.exit(1 if fails else 0)
