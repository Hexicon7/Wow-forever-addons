# 3.16: Lua errors from BugGrabber per addon (with "since this update → Revert"), and addons that are
# switched off on every character. Both are quiet: a chip and a red line, never a popup.
import os, sys, json, time, subprocess, tempfile, urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
sys.path.insert(0, str(Path(__file__).parent)); import fake_cf
fake_cf.start(); fake_cf.seed(); T = fake_cf.toc; tmp = Path(tempfile.mkdtemp()); (tmp/"data").mkdir()
game = tmp/"World of Warcraft"/"_forever_"
addons = game/"Interface"/"AddOns"; addons.mkdir(parents=True)
fake_cf.add_mod(5001, "erry", "Erry"); fake_cf.add_file(5001, "1.0", {"Erry/Erry.toc": T("Erry", "1.0"), "Erry/core.lua": "x"})
fake_cf.add_mod(5002, "sleepy", "Sleepy"); fake_cf.add_file(5002, "2.0", {"Sleepy/Sleepy.toc": T("Sleepy", "2.0")})
(tmp/"data"/"config.json").write_text(json.dumps({"api_key": fake_cf.KEY, "addons_dir": str(addons), "auto_check": False}))
APP = os.environ.get("WFAM_APP", "/home/claude/app/WoWForeverAddons.pyw")
env = dict(os.environ, WFAM_API_BASE=fake_cf.BASE, WFAM_DATA_DIR=str(tmp/"data"))
B = "http://127.0.0.1:47999"
app = subprocess.Popen([sys.executable, APP, "--no-browser", "--port", "47999"], env=env); time.sleep(1.2)
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
def lt(offset): return time.strftime("%Y/%m/%d %H:%M:%S", time.localtime(time.time() + offset))
def row(name): return next(a for a in st()["managed"] if a["name"] == name)
try:
    post("/api/add", {"text": "questie erry sleepy"}); idle()
    time.sleep(1.1)
    fake_cf.add_file(5001, "1.1", {"Erry/Erry.toc": T("Erry", "1.1"), "Erry/core.lua": "y"})
    post("/api/check"); idle(); post("/api/update", {"key": "5001"}); idle()
    check(row("Erry")["installed_version"] == "1.1" and row("Erry")["previous_version"] == "1.0", "Erry updated to 1.1, 1.0 kept")
    time.sleep(2.5)
    sv = game/"WTF"/"Account"/"ACC1"/"SavedVariables"; sv.mkdir(parents=True)
    (sv/"!BugGrabber.lua").write_text(f'''
BugGrabberDB = {{
	["session"] = 51,
	["errors"] = {{
		{{
			["message"] = "Interface/AddOns/Erry/core.lua:12: attempt to index field 'db' (a nil value)",
			["stack"] = "[string \\"@Interface/AddOns/Erry/core.lua\\"]:12: in function <Interface/AddOns/Erry/core.lua:10>\\n[C]: ?",
			["locals"] = "self = <table> {{\\n}}",
			["time"] = "{lt(-1)}",
			["session"] = 51,
			["counter"] = 7,
		}}, -- [1]
		{{
			["message"] = "Questie/Modules/QuestieMap.lua:55: bad argument #1 to 'pairs'",
			["time"] = "{lt(-1)}",
			["counter"] = 2,
		}}, -- [2]
		{{
			["message"] = "Interface/AddOns/Questie/Questie.lua:3: an old error from before this version",
			["time"] = "{lt(-3*86400)}",
			["counter"] = 9,
		}}, -- [3]
		{{
			["message"] = "Interface/AddOns/Blizzard_Something/x.lua:1: not one of yours",
			["time"] = "{lt(-1)}",
		}}, -- [4]
	}},
}}
''', encoding="utf-8")
    ch = game/"WTF"/"Account"/"ACC1"/"Realm"/"Hero"; ch.mkdir(parents=True)
    (ch/"AddOns.txt").write_text("Questie: enabled\nErry: enabled\nSleepy: disabled\n")
    time.sleep(21)    # the app looks at these files at most every 20 s
    e, q, s_ = row("Erry")["errors"], row("Questie")["errors"], row("Sleepy")
    check(e and e["n"] == 7 and e["since_update"] and "attempt to index" in e["message"], f"Erry: 7 errors, started with this update: {e}")
    check(q and q["n"] == 2 and not q["since_update"], f"Questie: 2 errors from this version (the old one isn't counted): {q}")
    check(s_["unused"] and not row("Erry")["unused"] and row("Sleepy")["errors"] is None, "Sleepy is off on every character")
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-proxy-server"])
        ctx = b.new_context(viewport={"width": 1400, "height": 900}); ctx.grant_permissions(["clipboard-read", "clipboard-write"])
        pg = ctx.new_page(); errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(B + "/"); time.sleep(1.5)
        check(not pg.locator(".modal.show").count(), "nothing pops up by itself")
        chips = [c.replace("\n", " ") for c in pg.locator(".fchips .stat").all_inner_texts()]
        check("Errors 2" in chips and "Unused 1" in chips, f"quiet chips: {chips}")
        er = pg.locator("tr.arow:has-text('Erry') .errl")
        check(er.inner_text() == "⚠ Errors since this update", "Erry's row: a short red line")
        check(pg.locator("tr.arow:has-text('Questie') .errl").inner_text() == "⚠ 2 Lua errors", "Questie's row: '2 Lua errors'")
        pg.click(".stat[data-f='errors']"); time.sleep(0.4)
        n = sorted(pg.locator("#managedCard tbody tr.arow .nm .t").all_inner_texts())
        check(n == ["Erry", "Questie"], f"Errors chip shows just those: {n}")
        pg.click(".stat[data-f='unused']"); time.sleep(0.4)
        n = pg.locator("#managedCard tbody tr.arow").all_inner_texts()
        check(len(n) == 1 and "Sleepy" in n[0] and "Off on every character" in n[0], f"Unused chip: {n}")
        pg.click(".stat[data-f='all']"); time.sleep(0.3)
        er.click(); time.sleep(0.8)
        body = pg.inner_text("#clB")
        check("7×" in body and "core.lua:12" in body and "started after the update to 1.1" in body, "the dialog shows the errors: " + body[:120].replace("\n", " | "))
        acts = pg.locator("#clAct button").all_inner_texts()
        check(acts[:1] == ["Revert to 1.0"] and "Copy" in acts and "Ignore" in acts, f"actions: {acts}")
        pg.click("#clAct button:has-text('Copy')"); time.sleep(0.3)
        clip = pg.evaluate("navigator.clipboard.readText()")
        check(clip.startswith("Erry 1.1") and "attempt to index" in clip, "Copy puts the errors on the clipboard")
        pg.click("#clAct button:has-text('Revert')"); time.sleep(1); idle(); time.sleep(1.5)
        r = row("Erry")
        check(r["installed_version"] == "1.0" and r["pinned"] and r["errors"] is None, "Revert: back to 1.0, pinned, errors gone")
        check(not pg.locator("tr.arow:has-text('Erry') .errl").count(), "…and the red line is gone")
        # Ignore
        pg.click("tr.arow:has-text('Questie') >> button.more"); time.sleep(0.3)
        pg.click("#pop >> text=Lua errors…"); time.sleep(0.8)
        pg.click("#clAct button:has-text('Ignore')"); time.sleep(1.5)
        check(row("Questie")["errors"] is None and not pg.locator(".stat[data-f='errors']").count(), "Ignore hides them, and the chip goes away")
        check(not errs, f"no page errors {errs}")
        b.close()
    # new errors after Ignore show up again
    t = (sv/"!BugGrabber.lua").read_text(encoding="utf-8").replace("-- [4]", f'''-- [4]
		{{
			["message"] = "Interface/AddOns/Questie/Questie.lua:8: a brand new one",
			["time"] = "{lt(0)}",
		}}, -- [5]''')
    (sv/"!BugGrabber.lua").write_text(t, encoding="utf-8"); time.sleep(21)
    q = row("Questie")["errors"]
    check(q and q["n"] == 1 and "brand new" in q["message"], f"a new error after Ignore shows again: {q}")
finally:
    app.terminate()
print("FAILURES:", fails); sys.exit(1 if fails else 0)
