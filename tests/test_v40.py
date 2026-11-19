# New interface: detail panel (safe description, versions), libraries group, hidden folders, characters editing, settings page.
import os, sys, json, time, subprocess, tempfile
from pathlib import Path
from playwright.sync_api import sync_playwright
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
sys.path.insert(0, str(Path(__file__).parent)); import fake_cf
fake_cf.start(); T = fake_cf.toc; tmp = Path(tempfile.mkdtemp())
fake_cf.MODS[1003]["categories"] = [{"id": 99, "name": "Libraries"}]          # BugGrabber counts as a library
forever = tmp / "World of Warcraft" / "_forever_"; addons = forever / "Interface" / "AddOns"; addons.mkdir(parents=True)
(tmp/"data").mkdir()
(tmp/"data"/"config.json").write_text(json.dumps({"api_key": fake_cf.KEY, "addons_dir": str(addons), "auto_check": False}))
for n in ("Zygor", "ZygorData"):
    (addons/n).mkdir(); (addons/n/f"{n}.toc").write_text(T("Zygor Guides", "9.1"))
chars = [("ACC", "Realm", "Alva"), ("ACC", "Realm", "Bella")]
for a, r, c in chars:
    d = forever/"WTF"/"Account"/a/r/c; d.mkdir(parents=True)
(forever/"WTF"/"Account"/"ACC"/"Realm"/"Alva"/"AddOns.txt").write_text("Zygor: enabled\nSomethingOld: disabled\n")
env = dict(os.environ, WFAM_API_BASE=fake_cf.BASE, WFAM_DATA_DIR=str(tmp/"data"))
app = subprocess.Popen([sys.executable, "/home/claude/app/WoWForeverAddons.pyw", "--no-browser", "--port", "47997"], env=env)
time.sleep(1.2); fails = 0
def check(c, msg):
    global fails; print(("PASS " if c else "FAIL ") + msg); fails += 0 if c else 1
idle = "() => !document.querySelector('#bAdd').disabled && !document.querySelector('#dDot').classList.contains('spin')"
try:
  with sync_playwright() as p:
    b = p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-proxy-server"])
    pg = b.new_page(viewport={"width": 1280, "height": 860}); errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.on("dialog", lambda d: (errs.append("dialog: " + d.message), d.dismiss()))
    pg.goto("http://127.0.0.1:47997/"); time.sleep(1)
    pg.click("#bAddOpen"); pg.fill("#links", "questie bugsack buggrabber"); pg.click("#bAdd")
    time.sleep(0.5); pg.wait_for_function(idle, timeout=30000); time.sleep(1.2)
    check(not pg.locator("#mAdd").is_visible(), "add dialog closes after install")
    rows = pg.locator("#managedCard tbody tr")
    txt = pg.inner_text("#managedCard")
    check("Libraries" in txt and "BugGrabber" not in txt, "library collapsed into a 'Libraries' group: " + txt.replace("\n", " ")[:120])
    pg.click("tr.grp"); time.sleep(0.3)
    check("BugGrabber" in pg.inner_text("#managedCard"), "library group expands")
    pg.screenshot(path="/home/claude/shots/v4-installed.png")
    # detail panel
    pg.locator("tr.arow", has_text="Questie").locator("td").first.click(); time.sleep(1.2)
    check(pg.locator("#detail.show").count() == 1 and "About Questie" in pg.inner_text("#dBody"), "detail panel opens with the description")
    html = pg.inner_html("#dBody .desc")
    pwned = pg.evaluate("window.PWNED")
    print("HTML:", html[:900])
    check(pwned is None and "<script" not in html and "<iframe" not in html and "onclick" not in html and "onerror" not in html
          and "javascript:" not in html and "style=" not in html, "description sanitised (no script/iframe/handlers/js links/styles)")
    check('href="https://example.com/x"' in html and 'src="https://media.forgecdn.net/x.png"' in html, "safe links and images kept")
    pg.screenshot(path="/home/claude/shots/v4-detail.png")
    pg.click(".stab[data-t=versions]"); time.sleep(0.4)
    vt = pg.inner_text("#dBody"); check("Installed" in vt and "v12.6.0" in vt, "versions tab lists files with the installed one marked")
    pg.locator(".fr").first.locator("text=What's new").click(); time.sleep(0.8)
    check("Fixed stuff" in pg.inner_text("#dBody"), "per-version changelog")
    pg.click(".stab[data-t=info]"); time.sleep(0.3)
    check("Questie" in pg.inner_text("#dBody") and "Folders" in pg.inner_text("#dBody"), "details tab")
    pg.keyboard.press("Escape"); time.sleep(0.4)
    check(pg.locator("#detail.show").count() == 0, "Esc closes the panel")
    # install an older version from the panel
    fake_cf.add_file(1001, "v12.7.0", {"Questie/Questie.toc": T("Questie", "12.7.0")}) if hasattr(fake_cf, "add_file") else None
    print("LOGS:", pg.evaluate("S.logs.slice(-8).map(l=>l.level+' '+l.msg).join(' | ')"))
    print("bCheck disabled:", pg.evaluate("document.querySelector('#bCheck').disabled"), "shade:", pg.evaluate("getComputedStyle(document.querySelector('#shade')).pointerEvents"), "job:", pg.evaluate("JSON.stringify(S.job)"))
    print("AT:", pg.evaluate("(()=>{const r=document.querySelector('#bCheck').getBoundingClientRect();const e=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);return r.x+','+r.y+' '+(e?e.tagName+'#'+e.id+'.'+e.className:'none')})()"))
    pg.screenshot(path="/home/claude/shots/dbg.png")
    pg.click("#bCheck"); time.sleep(0.5); pg.wait_for_function(idle, timeout=30000); time.sleep(1)
    pg.locator("tr.arow", has_text="Questie").locator("td").first.click(); time.sleep(1.2)
    pg.click(".stab[data-t=versions]"); time.sleep(0.4)
    n_files = pg.locator(".fr").count()
    target = pg.locator(".fr", has_text="v12.6.0") if pg.locator(".fr", has_text="v12.7.0").count() and "Installed" in pg.locator(".fr", has_text="v12.7.0").inner_text() else None
    if target is None:   # 12.6.0 installed, newer exists: install the newest explicitly
        target = pg.locator(".fr").first
    want = target.locator(".n b").inner_text()
    target.locator("button:has-text('Install')").click(); pg.click("#askYes")
    time.sleep(0.5); pg.wait_for_function(idle, timeout=30000); time.sleep(1.2)
    st = json.loads(pg.evaluate("JSON.stringify(S.managed.find(a=>a.key==='1001'))"))
    check(st["installed_version"] == want and st["pinned"], f"specific version installed and pinned: {st['installed_version']} pinned={st['pinned']}")
    pg.keyboard.press("Escape")
    # row menu
    pg.locator("tr.arow", has_text="Questie").locator("button.more").click(); time.sleep(0.2)
    menu = pg.inner_text("#pop"); check("Unpin" in menu and "Remove" in menu and "Open on CurseForge" in menu, "row ⋯ menu: " + menu.replace("\n", " | "))
    pg.keyboard.press("Escape")
    # hidden folders
    check("Zygor Guides" in pg.inner_text("#unCard"), "Zygor listed under other folders")
    for _ in range(2):
        pg.locator("#unCard tr", has_text="Zygor Guides").first.locator("text=Hide").click(); time.sleep(1.2)
    check(pg.inner_text("#sUn") == "0" and "hidden folder" in pg.inner_text("#hidBox"), "Hide moves it to the hidden list")
    pg.click("#hidBox .linkbtn"); pg.locator("#hidBox >> text=Show again").first.click(); time.sleep(1.2)
    check("Zygor Guides" in pg.inner_text("#unCard"), "Show again brings it back")
    # browse -> detail
    pg.click(".tab[data-v=browse]"); pg.wait_for_selector(".res"); time.sleep(0.5)
    pg.screenshot(path="/home/claude/shots/v4-browse.png")
    pg.locator(".res", has_text="Bag Helper 44").locator(".t").click(); time.sleep(1.2)
    check("Bag Helper 44" in pg.inner_text("#dHead") and pg.locator("#dHead button:has-text('Install')").count() == 1, "browse result opens the panel with Install")
    pg.keyboard.press("Escape")
    # characters
    pg.click(".tab[data-v=chars]"); time.sleep(1.2)
    t = pg.inner_text("#chBox"); check("Alva" in t and "Bella" in t and "Questie" in t, "characters matrix")
    pg.locator("#chBox tbody tr", has_text="Questie").locator("td.cell").nth(1).click()   # Bella: Questie off
    pg.locator("#chBox tbody tr", has_text="Zygor").locator("td.cell").nth(0).click()    # Alva: Zygor off
    check(pg.inner_text("#chSave") == "Save changes (2)", "two pending changes")
    pg.screenshot(path="/home/claude/shots/v4-chars.png")
    pg.click("#chSave"); time.sleep(0.5); pg.wait_for_function(idle, timeout=30000); time.sleep(1.5)
    a_txt = (forever/"WTF"/"Account"/"ACC"/"Realm"/"Alva"/"AddOns.txt").read_text()
    l_txt = (forever/"WTF"/"Account"/"ACC"/"Realm"/"Bella"/"AddOns.txt").read_text()
    check("Zygor: disabled" in a_txt and "SomethingOld: disabled" in a_txt, "Alva's AddOns.txt updated, other lines kept: " + a_txt.replace("\n", " | "))
    check(l_txt.strip() == "Questie: disabled", "Bella's AddOns.txt created: " + l_txt.replace("\n", " | "))
    # profile: save Alva's set, apply to Bella
    pg.locator("#chBox th", has_text="Alva").locator("text=options").click(); pg.click("#pop >> text=Save as profile…")
    pg.fill("#askIn", "Raiding"); pg.click("#askYes"); time.sleep(1.2)
    check("Raiding" in pg.inner_text("#profBox"), "profile saved")
    pg.locator("#chBox th", has_text="Bella").locator("text=options").click(); pg.click("#pop >> text=Use profile “Raiding”")
    time.sleep(0.3); n = pg.inner_text("#chSave")
    pg.click("#chSave"); time.sleep(0.5); pg.wait_for_function(idle, timeout=30000); time.sleep(1.5)
    l_txt = (forever/"WTF"/"Account"/"ACC"/"Realm"/"Bella"/"AddOns.txt").read_text()
    check("Questie: enabled" in l_txt and "Zygor: disabled" in l_txt, f"profile applied to Bella ({n}): " + l_txt.replace("\n", " | "))
    # settings is a page; Esc returns
    pg.click("#bSettings"); time.sleep(0.4)
    check(pg.locator("#mSettings.show").count() == 1 and pg.locator(".side .tab.on").inner_text().strip() == "Settings", "settings page")
    pg.screenshot(path="/home/claude/shots/v4-settings.png")
    pg.keyboard.press("Escape"); time.sleep(0.3)
    check(pg.locator("#mSettings.show").count() == 0 and "Characters" in pg.locator(".side .tab.on").inner_text(), "Esc leaves settings")
    check(not errs, f"no JS errors: {errs}")
finally:
    try:
        import urllib.request
        urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:47997/api/quit", data=b"{}", headers={"X-WFAM": "1"}, method="POST"))
    except Exception: pass
    app.wait(5)
print("FAILURES:", fails)
