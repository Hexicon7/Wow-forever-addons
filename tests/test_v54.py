# 3.14: UI-pack chips (ElvUI / Tukui / EllesmereUI) next to the filters, a source menu instead of a sort
# menu that repeated the column headings, shorter version labels, no empty column in a smaller window.
import os, sys, json, time, subprocess, tempfile, urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
sys.path.insert(0, str(Path(__file__).parent)); import fake_cf
fake_cf.start(); fake_cf.seed(); T = fake_cf.toc; tmp = Path(tempfile.mkdtemp()); (tmp/"data").mkdir()
addons = tmp/"World of Warcraft"/"_forever_"/"Interface"/"AddOns"; addons.mkdir(parents=True)
fake_cf.tukui_addon("elvui", "ElvUI", "16.05", {"ElvUI/ElvUI_Mainline.toc": T("ElvUI", "16.05"),
                                                "ElvUI_Options/ElvUI_Options_Mainline.toc": T("ElvUI_Options", "16.05")})
fake_cf.add_mod(4001, "windtools", "WindTools"); fake_cf.add_file(4001, "WindTools-Forever-v4.1.zip", {"ElvUI_WindTools/ElvUI_WindTools.toc": T("WindTools", "4.1")})
fake_cf.add_mod(4002, "shadow-plug", "Shadow Plug"); fake_cf.add_file(4002, "1.0", {"ShadowPlug/ShadowPlug.toc": T("Shadow Plug", "1.0") + "## RequiredDeps: ElvUI\n"})
fake_cf.add_mod(4003, "books-on-map", "Books on Map Forever"); fake_cf.add_file(4003, "Books_on_map_Foreverv2.3.2.zip", {"BooksOnMap/BooksOnMap.toc": T("Books on Map", "2.3.2")})
# a hand-installed ElvUI plugin in "Other folders"
(addons/"ElvUI_Hand").mkdir(); (addons/"ElvUI_Hand"/"ElvUI_Hand.toc").write_text(T("ElvUI Hand Plugin", "0.1"))
(tmp/"data"/"config.json").write_text(json.dumps({"api_key": fake_cf.KEY, "addons_dir": str(addons), "auto_check": False}))
APP = os.environ.get("WFAM_APP", "/home/claude/app/WoWForeverAddons.pyw")
env = dict(os.environ, WFAM_API_BASE=fake_cf.BASE, WFAM_DATA_DIR=str(tmp/"data"), WFAM_TUKUI_API=fake_cf.BASE + "/tukui")
B = "http://127.0.0.1:47997"
app = subprocess.Popen([sys.executable, APP, "--no-browser", "--port", "47997"], env=env); time.sleep(1.2)
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
    post("/api/add", {"text": "questie windtools shadow-plug books-on-map"}); idle()
    post("/api/add_tukui", {"slug": "elvui"}); idle()
    sp = next(a for a in st()["managed"] if a["name"] == "Shadow Plug")
    check(sp["deps"] == ["ElvUI"], f"what an addon needs is in the state: {sp['deps']}")
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-proxy-server"])
        pg = b.new_page(viewport={"width": 1400, "height": 900}); errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(B + "/"); time.sleep(1.5)
        names = lambda: pg.locator("#managedCard tbody tr.arow .nm .t").all_inner_texts()
        chips = pg.locator(".fchips .stat").all_inner_texts()
        check(any(c.startswith("ElvUI") for c in chips) and not any("Tukui" in c or "Ellesmere" in c for c in chips),
              f"only the UI pack you have gets a chip: {chips}")
        el = pg.locator(".stat.suite", has_text="ElvUI")
        check(el.inner_text().replace("\n", " ").endswith("4"), "ElvUI chip counts ElvUI, its two plugins and the hand-installed one: " + el.inner_text())
        el.click(); time.sleep(0.4)
        n = sorted(names())
        check(n == ["ElvUI", "Shadow Plug", "WindTools"], f"chip shows ElvUI and its plugins: {n}")
        check("ElvUI Hand Plugin" in pg.inner_text("#unCard") and pg.is_visible("#unCard"), "…and the hand-installed plugin under Other folders")
        check(pg.locator(".stat.suite.on").count() == 1 and pg.locator(".stat.on").count() == 1, "the chip is marked")
        el.click(); time.sleep(0.4)
        check(len(names()) == 5 and pg.locator('.stat[data-f="all"].on').count() == 1, "click again → All")
        # source menu (replaces the sort menu)
        opts = pg.locator("#msrc option").all_inner_texts()
        check(opts[0] == "All sources" and "tukui.org (1)" in opts and any(o.startswith("CurseForge") for o in opts), f"source menu: {opts}")
        pg.select_option("#msrc", "tukui"); time.sleep(0.4)
        check(names() == ["ElvUI"] and not pg.is_visible("#unCard"), "source menu: only tukui.org addons")
        pg.select_option("#msrc", ""); time.sleep(0.4)
        # version labels
        row = pg.inner_text("tr.arow:has-text('Books on Map Forever')")
        check("v2.3.2" in row and ".zip" not in row, "'Books_on_map_Foreverv2.3.2.zip' shows as v2.3.2: " + row.replace("\n", " | "))
        row = pg.inner_text("tr.arow:has-text('WindTools')")
        check("v4.1" in row and "WindTools-Forever" not in row, "'WindTools-Forever-v4.1.zip' shows as v4.1")
        check(pg.evaluate("shortVer('12.0.1')") == "12.0.1" and pg.evaluate("shortVer('v428')") == "v428" and pg.evaluate("shortVer('Attune-Forever-v16001-44.zip')") == "v16001-44",
              "plain versions stay as they are")
        # the toolbar fits on one line in a ~1180px window, and no empty column on the right
        pg.set_viewport_size({"width": 1180, "height": 830}); time.sleep(0.6)
        tops = pg.evaluate("[...document.querySelectorAll('#vInstalled .bright > *')].filter(e=>e.offsetParent).map(e=>Math.round(e.getBoundingClientRect().top))")
        right = pg.evaluate("Math.round(document.querySelector('#bMoreIns').getBoundingClientRect().right - document.querySelector('#managedCard').getBoundingClientRect().right)")
        check(len(tops) == 4 and max(tops) - min(tops) < 12 and abs(right) < 4, f"search, source, Add addons and ⋯ stay together on one line, at the right: {tops}, {right}")
        pg.evaluate("libsOpen = true"); pg.click('.stat[data-f="all"]'); time.sleep(0.4)
        w = pg.evaluate("""() => { const card = document.querySelector('#managedCard').getBoundingClientRect().width;
             const row = document.querySelector('#managedCard tr.arow').getBoundingClientRect().width; return [card, row]; }""")
        check(w[1] >= w[0] - 4, f"rows fill the whole card (no phantom column): card {w[0]:.0f}, row {w[1]:.0f}")
        check(pg.is_visible("#managedCard th:has-text('Latest for Forever')"), "'Latest for Forever' still shown at this size")
        pg.screenshot(path="/home/claude/shots/v54-1180.png")
        pg.set_viewport_size({"width": 1000, "height": 800}); time.sleep(0.6)
        w = pg.evaluate("[document.querySelector('#managedCard').getBoundingClientRect().width, document.querySelector('#managedCard tr.arow').getBoundingClientRect().width]")
        check(w[1] >= w[0] - 4, f"narrow window: rows still fill the card ({w[0]:.0f}/{w[1]:.0f})")
        check(not errs, f"no page errors {errs}")
        b.close()
finally:
    app.terminate()
print("FAILURES:", fails); sys.exit(1 if fails else 0)
