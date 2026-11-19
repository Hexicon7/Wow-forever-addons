# WeakAuras tab: ForeverAuras from wago.io, "Add to game" via the ForeverAurasCompanion addon, copy string.
import os, sys, json, time, subprocess, tempfile, urllib.request, re
from pathlib import Path
from playwright.sync_api import sync_playwright
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
sys.path.insert(0, str(Path(__file__).parent)); import fake_cf
fake_cf.start(); tmp = Path(tempfile.mkdtemp()); (tmp/"data").mkdir()
forever = tmp/"World of Warcraft"/"_forever_"; addons = forever/"Interface"/"AddOns"; addons.mkdir(parents=True)
tricky = '!WA:2!ab"c\\\\d\nE\x01' + "é" * 3
fake_cf.aura("MageAur1", "mage-cooldowns", "Mage Cooldowns", categories=["cl8"], installs=5000, stars=40, encoded=tricky, note="Fixed blizzard")
fake_cf.aura("RogueAu1", "rogue-energy", "Rogue Energy Bar", categories=["cl4"], installs=9000, stars=10)
fake_cf.aura("HealAur1", "heal-helper", "Healing Helper", categories=["role2"], installs=100, stars=90)
fake_cf.aura("RetailA1", "retail-thing", "Retail Thing", game="tww", installs=99999)
for i in range(25): fake_cf.aura(f"Filler{i:03d}", f"filler-{i}", f"Filler {i}", installs=i)
(tmp/"data"/"config.json").write_text(json.dumps({"api_key": fake_cf.KEY, "addons_dir": str(addons), "auto_check": False}))
env = dict(os.environ, WFAM_API_BASE=fake_cf.BASE, WFAM_DATA_DIR=str(tmp/"data"), WFAM_WAGO_DATA=fake_cf.BASE + "/wagodata")
app = subprocess.Popen([sys.executable, os.environ.get("WFAM_APP", "/home/claude/app/WoWForeverAddons.pyw"), "--no-browser", "--port", "47987"], env=env)
time.sleep(1.2); fails = 0; B = "http://127.0.0.1:47987"
def check(c, msg):
    global fails; print(("PASS " if c else "FAIL ") + msg); fails += 0 if c else 1
def post(p, b=None):
    try: return json.loads(urllib.request.urlopen(urllib.request.Request(B+p, data=json.dumps(b or {}).encode(), headers={"X-WFAM":"1","Content-Type":"application/json"}, method="POST")).read())
    except urllib.error.HTTPError as e: return json.loads(e.read())
def st(): return json.loads(urllib.request.urlopen(B+"/api/state").read())
def idle():
    time.sleep(0.25)
    while st()["job"]["running"]: time.sleep(0.2)
def logs(): return " | ".join(l["msg"] for l in st()["logs"])
def parse_lua_strings(text):
    """Pull [slug] = { key = "value", ... } out of the generated data.lua, undoing Lua escapes."""
    def un(sq):
        out, i = [], 0
        while i < len(sq):
            c = sq[i]
            if c == "\\":
                n = sq[i+1]
                if n.isdigit():
                    j = i + 1
                    while j < len(sq) and j < i + 4 and sq[j].isdigit(): j += 1
                    out.append(chr(int(sq[i+1:j]))); i = j; continue
                out.append({"n": "\n", '"': '"', "\\": "\\"}[n]); i += 2; continue
            out.append(c); i += 1
        return "".join(out)
    res = {}
    for m in re.finditer(r'\[("(?:[^"\\]|\\.)*")\] = \{(.*?)\},\n', text, re.S):
        slug = un(m.group(1)[1:-1])
        res[slug] = {k: un(v[1:-1]) for k, v in re.findall(r'(\w+) = ("(?:[^"\\]|\\.)*")', m.group(2))}
    return res
try:
    r = post("/api/aura_search", {"q": "", "sort": "installs"})
    names = [x["name"] for x in r["results"]]
    check("Retail Thing" not in names and names[:3] == ["Rogue Energy Bar", "Mage Cooldowns", "Healing Helper"] and len(names) == 20 and r["next"] == 1,
          f"ForeverAuras only, most installed first, 20 per page: {names[:3]} next={r['next']}")
    r2 = post("/api/aura_search", {"q": "", "sort": "installs", "page": 1})
    check(len(r2["results"]) == 8 and r2["next"] is None, f"second page has the rest ({len(r2['results'])})")
    r = post("/api/aura_search", {"q": "", "category": "cl8"})
    check([x["name"] for x in r["results"]] == ["Mage Cooldowns"] and r["results"][0]["categories"] == ["Mage"], "category filter (Mage)")
    r = post("/api/aura_search", {"q": "heal", "sort": "stars"})
    check([x["name"] for x in r["results"]] == ["Healing Helper"], "text search")
    # add to game
    post("/api/aura_add", {"id": "MageAur1"}); idle()
    comp = addons/"ForeverAurasCompanion"
    toc = (comp/"ForeverAurasCompanion.toc").read_text("utf-8") if comp.is_dir() else ""
    data = (comp/"data.lua").read_text("utf-8") if comp.is_dir() else ""
    check("## Interface: 16001" in toc and "## Dependencies: ForeverAuras" in toc and "data.lua" in toc, "companion addon written (toc)")
    parsed = parse_lua_strings(data)
    check(data.startswith("-- Written by") and "ForeverAurasCompanion = {" in data and "stash = {" in data, "data.lua has ForeverAuras' companion table")
    e = parsed.get("mage-cooldowns", {})
    check(e.get("encoded") == tricky and e.get("name") == "Mage Cooldowns" and e.get("author") == "AuraMaker" and e.get("wagoVersion") == "1" and e.get("versionNote") == "Fixed blizzard",
          f"stash entry round-trips exactly, even quotes/backslashes/newlines: {sorted(e)}")
    s = st()
    check([p["name"] for p in s["auras_pending"]] == ["Mage Cooldowns"] and not s["foreverauras"], "pending list shown; ForeverAuras missing is reported")
    check("ForeverAurasCompanion" not in json.dumps(s["unmanaged"]), "the companion folder isn't listed as an unknown addon")
    check("ForeverAuras itself isn't in your AddOns" in logs(), "tells you to install ForeverAuras")
    post("/api/aura_add", {"id": "RetailA1"}); idle()
    check("isn't made for WoW Forever" in logs() and len(st()["auras_pending"]) == 1, "a retail aura is refused")
    post("/api/aura_add", {"id": "RogueAu1"}); idle()
    check(len(parse_lua_strings((comp/"data.lua").read_text("utf-8"))) == 2, "two auras waiting")
    r = post("/api/aura_code", {"id": "HealAur1"}); check(r["code"].startswith("!WA:2!HealAur1"), "copy import string works")
    r = post("/api/aura_code", {"id": "RetailA1"}); check("error" in r and "isn't made for WoW Forever" in r["error"], "no import string for retail auras")
    # imported in game -> dropped on the next check
    sv = forever/"WTF"/"Account"/"ACC1"/"SavedVariables"; sv.mkdir(parents=True)
    (sv/"ForeverAurasSaved.lua").write_text('ForeverAurasSaved = {\n["displays"] = {\n["Mage Cooldowns"] = {\n["url"] = "https://wago.io/mage-cooldowns/1",\n},\n},\n}\n')
    post("/api/check"); idle()
    s = st()
    check([p["name"] for p in s["auras_pending"]] == ["Rogue Energy Bar"] and "mage-cooldowns" not in (comp/"data.lua").read_text("utf-8"), "an aura imported in game leaves the list")
    post("/api/aura_add", {"id": "MageAur1"}); idle()
    check("already in ForeverAuras" in logs() and len(st()["auras_pending"]) == 1, "an aura you already have in game isn't added again")
    post("/api/aura_remove", {"slug": "rogue-energy"}); time.sleep(0.3)
    check(not st()["auras_pending"] and not comp.exists(), "removing the last one removes the companion folder")
    (addons/"ForeverAuras").mkdir()
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-proxy-server"])
        pg = b.new_page(viewport={"width": 1400, "height": 900}); errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(B + "/"); time.sleep(1.2)
        pg.click("button.tab[data-v='browse']"); time.sleep(0.8)
        check(pg.locator(".srcsw button[data-src='auras']").is_visible() and not pg.locator("#srcWago").is_visible(), "WeakAuras tab is there even without a Wago key (Wago tab hidden)")
        pg.click(".srcsw button[data-src='auras']"); time.sleep(1.5)
        t = pg.inner_text("#bres")
        check("Rogue Energy Bar" in t and "Retail Thing" not in t and pg.inner_text("#vTitle") == "WeakAuras for WoW Forever", "aura list appears straight away")
        check(pg.locator("#acat").is_visible() and pg.locator("#asort").is_visible() and not pg.locator("#bcat").is_visible(), "own category and sort menus")
        pg.select_option("#acat", "cl8"); time.sleep(1.2)
        check(pg.locator("#bres .res").count() == 1 and "Mage" in pg.inner_text("#bmeta"), "category menu filters: " + pg.inner_text("#bmeta"))
        pg.select_option("#acat", "role2"); time.sleep(1.2)
        card = pg.locator("#bres .res", has_text="Healing Helper")
        check(card.locator("text=Add to game").count() == 0 and card.locator("text=Copy string").count() == 1, "cards only have Copy string")
        pg.context.grant_permissions(["clipboard-read", "clipboard-write"])
        card.locator("text=Copy string").click(); time.sleep(1.2)
        clip = pg.evaluate("navigator.clipboard.readText()")
        check(clip.startswith("!WA:2!HealAur1"), "Copy string puts the import string on the clipboard")
        check("waiting in game" not in pg.inner_text("#bres"), "no 'waiting in game' box any more")
        pg.screenshot(path="/home/claude/shots/v47-auras.png")
        pg.select_option("#acat", ""); time.sleep(1.2)
        check(pg.locator("#bres .res").count() == 20 and pg.locator("#bMoreBtn").is_visible(), "20 at a time with Show more")
        pg.click("#bMoreBtn"); time.sleep(1.2)
        check(pg.locator("#bres .res").count() == 28, "Show more loads the next page")
        pg.click(".srcsw button[data-src='cf']"); time.sleep(1)
        check(pg.locator("#bcat").is_visible() and not pg.locator("#acat").is_visible(), "back on CurseForge, its menus return")
        check(not errs, f"no page errors {errs}")
        b.close()
finally:
    app.terminate()
print("FAILURES:", fails); sys.exit(1 if fails else 0)
