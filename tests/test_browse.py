import os, sys, json, time, subprocess, tempfile
from pathlib import Path
from playwright.sync_api import sync_playwright
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
sys.path.insert(0, str(Path(__file__).parent)); import fake_cf
fake_cf.start(); tmp = Path(tempfile.mkdtemp())
addons = tmp / "World of Warcraft" / "_forever_" / "Interface" / "AddOns"; addons.mkdir(parents=True)
(tmp/"data").mkdir()
(tmp/"data"/"config.json").write_text(json.dumps({"api_key": fake_cf.KEY, "addons_dir": str(addons)}))
env = dict(os.environ, WFAM_API_BASE=fake_cf.BASE, WFAM_DATA_DIR=str(tmp/"data"))
app = subprocess.Popen([sys.executable, "/home/claude/app/WoWForeverAddons.pyw", "--no-browser", "--port", "47996"], env=env)
time.sleep(1.2); fails = 0
def check(c, msg):
    global fails; print(("PASS " if c else "FAIL ") + msg); fails += 0 if c else 1
idle = "() => !document.querySelector('#bAdd').disabled"
try:
  with sync_playwright() as p:
    b = p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-proxy-server"])
    pg = b.new_page(viewport={"width": 1280, "height": 900}); errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto("http://127.0.0.1:47996/"); time.sleep(1)
    pg.click(".tab[data-v=browse]"); pg.wait_for_selector(".res"); time.sleep(0.4)
    n = pg.locator(".res").count(); meta = pg.inner_text("#bmeta")
    check(n == 50 and "WoW Forever addons" in meta, f"popular list first page: {n} rows, '{meta}'")  # 6 forever mods + 45 filler; classic-only & beta... 
    check("Classic Only" not in pg.inner_text("#bres"), "addons without a Forever file are hidden")
    pg.click("#bMoreBtn"); time.sleep(0.8)
    check(pg.locator(".res").count() == 53 and pg.locator("#bmore").is_hidden(), f"show more loads the rest ({pg.locator('.res').count()})")
    pg.fill("#bq", "ques"); time.sleep(1.0)
    check(pg.locator(".res").count() == 1 and "Questie" in pg.inner_text("#bres"), "search 'ques' finds Questie")
    pg.screenshot(path="/home/claude/shots/b1-search.png")
    pg.locator(".res", has_text="Questie").locator("text=Install").click()
    time.sleep(0.4); pg.wait_for_function(idle, timeout=20000); time.sleep(1.2)
    check("Installed ✓" in pg.inner_text("#bres") and (addons/"Questie").is_dir(), "install from search")
    # queue several quickly
    pg.fill("#bq", "bag helper 1"); time.sleep(1.0)
    rows = pg.locator(".res"); cnt = rows.count()
    for i in range(3): rows.nth(i).locator("button:has-text('Install')").click(); time.sleep(0.05)
    pg.screenshot(path="/home/claude/shots/b2-queue.png")
    for _ in range(60):
        time.sleep(0.5)
        if pg.inner_text("#bres").count("Installed ✓") >= 3 and pg.evaluate("queue.length===0 && sending===null"): break
    check(pg.inner_text("#bres").count("Installed ✓") == 3, f"3 queued installs all done ({cnt} results)")
    pg.fill("#bq", "leatrix"); time.sleep(1.0)
    check("tries GitHub first" in pg.inner_text("#bres"), "site-only addon with GitHub is labelled")
    pg.fill("#bq", ""); time.sleep(1.0)
    pg.select_option("#bcat", label="Quests & Leveling"); time.sleep(0.8)
    names = pg.eval_on_selector_all(".res .t", "e=>e.map(x=>x.textContent)")
    check(len(names) > 0 and len(names) < 51, f"category filter narrows ({len(names)})")
    pg.select_option("#bcat", value=""); pg.select_option("#bsort", value="4"); time.sleep(0.8)
    names = pg.eval_on_selector_all(".res .t", "e=>e.map(x=>x.textContent)")
    check(names == sorted(names, key=str.lower), "sort by name")
    pg.select_option("#bsort", value="2"); time.sleep(0.8)
    pg.screenshot(path="/home/claude/shots/b3-browse.png")
    pg.click(".tab[data-v=installed]"); time.sleep(0.4)
    check(pg.inner_text("#tabN") == "4" and pg.locator("#managedCard tbody tr").count() == 4, "Installed tab shows the 4 addons")
    check(not errs, f"no JS errors {errs}")
    b.close()
finally:
    app.kill()
print("FAILURES:", fails)
