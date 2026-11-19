import os, sys, json, time, subprocess, tempfile, urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright

os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
sys.path.insert(0, str(Path(__file__).parent))
import fake_cf

srv = fake_cf.start()
tmp = Path(tempfile.mkdtemp())
wow = tmp / "World of Warcraft"
addons = wow / "_forever_" / "Interface" / "AddOns"
addons.mkdir(parents=True)
(wow / "_classic_era_" / "Interface" / "AddOns").mkdir(parents=True)
(addons / "Questie").mkdir(); (addons / "Questie" / "Questie.toc").write_text(fake_cf.toc("Questie", "12.4.0", "11507", pid=1001))
(addons / "OldThing").mkdir(); (addons / "OldThing" / "OldThing.toc").write_text(fake_cf.toc("Old Thing", "0.9", "11507"))
env = dict(os.environ, WFAM_API_BASE=fake_cf.BASE, WFAM_DATA_DIR=str(tmp / "data"), WFAM_EXTRA_WOW_ROOT=str(wow))
PORT = 47999
app = subprocess.Popen([sys.executable, "/home/claude/app/WoWForeverAddons.pyw", "--no-browser", "--port", str(PORT)], env=env)
time.sleep(1.2)
URL = f"http://127.0.0.1:{PORT}/"
shots = Path("/home/claude/shots"); shots.mkdir(exist_ok=True)
fails = 0
def check(c, msg):
    global fails; print(("PASS " if c else "FAIL ") + msg); fails += 0 if c else 1

# security: cross-site style requests are refused
def raw(path, headers, data=b"{}"):
    try:
        return urllib.request.urlopen(urllib.request.Request(URL.rstrip("/") + path, data=data, headers=headers, method="POST")).status
    except urllib.error.HTTPError as e:
        return e.code
check(raw("/api/check", {"Content-Type": "application/json"}) == 403, "POST without X-WFAM header refused")
check(raw("/api/check", {"X-WFAM": "1", "Origin": "https://evil.example"}) == 403, "foreign Origin refused")
check(raw("/api/check", {"X-WFAM": "1", "Host": "evil.example"}) == 403, "DNS-rebinding Host refused")

try:
  with sync_playwright() as p:
      b = p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-proxy-server"])
      pg = b.new_page(viewport={"width": 1280, "height": 860})
      errors = []
      pg.on("pageerror", lambda e: errors.append(str(e)))
      pg.on("dialog", lambda d: d.accept())
      pg.goto(URL)
      pg.wait_for_selector("#mSettings.show")
      pg.wait_for_selector(".chip")
      time.sleep(0.3)
      pg.screenshot(path=str(shots / "1-setup.png"))
      check(pg.input_value("#fDir").endswith("_forever_/Interface/AddOns"), "Detect pre-filled _forever_ folder")
      pg.fill("#fKey", fake_cf.KEY)
      pg.click("#bTypes"); time.sleep(0.6)
      opts = pg.eval_on_selector_all("#fType option", "os => os.map(o => o.textContent)")
      check(any("WoW Forever" in o for o in opts), f"version list loaded: {opts[:3]}")
      pg.click("#bSave"); pg.wait_for_selector("#mSettings", state="hidden")
      time.sleep(0.5)
      pg.screenshot(path=str(shots / "2-empty.png"))

      pg.click("#bAddOpen")
      pg.fill("#links", "https://www.curseforge.com/wow/addons/bugsack\nhttps://www.curseforge.com/wow/addons/deadly-boss-mods\nhttps://www.curseforge.com/wow/addons/leatrix-plus\nnested-addon\nclassic-only")
      pg.click("#bAdd")
      time.sleep(0.5)
      pg.wait_for_function("() => !document.querySelector('#bAdd').disabled", timeout=20000)
      time.sleep(1.2)
      pg.screenshot(path=str(shots / "3-installed.png"), full_page=True)
      rows = pg.eval_on_selector_all("#managedCard tbody tr", "rs => rs.map(r => r.innerText.replace(/\\s+/g,' '))")
      print("\n".join(rows))
      check(len(rows) == 5, f"5 managed rows (incl. dependency): {len(rows)}")
      check(pg.locator("#unCard").inner_text().count("Manage with updates") == 1, "unmanaged Questie offers Manage")

      # adopt Questie
      pg.click("text=Manage with updates"); time.sleep(0.5)
      pg.wait_for_function("() => !document.querySelector('#bAdd').disabled", timeout=20000); time.sleep(1)
      check("12.6.0" in (addons / "Questie" / "Questie.toc").read_text(), "Manage replaced Questie with Forever build")

      # publish updates then check
      def pub(body): urllib.request.urlopen(urllib.request.Request(fake_cf.BASE + "/admin/publish", data=json.dumps(body).encode(), method="POST"))
      pub({"mod": 1001, "version": "v12.7.0", "entries": {"Questie/Questie.toc": fake_cf.toc("Questie", "12.7.0")}})
      pub({"mod": 1004, "version": "12.0.2", "entries": {"DBM-Core/DBM-Core.toc": fake_cf.toc("DBM", "12.0.2")}})
      pub({"mod": 1005, "version": "12.0.11-forever", "entries": {"Leatrix_Plus/Leatrix_Plus.toc": fake_cf.toc("LP", "12.0.11")}})
      pg.click("#bCheck"); time.sleep(0.5)
      pg.wait_for_function("() => !document.querySelector('#bCheck').disabled", timeout=20000); time.sleep(1)
      pg.screenshot(path=str(shots / "4-updates.png"), full_page=True)
      check(pg.inner_text("#bUpdAll") == "Update all (2)", "Update all shows count: " + pg.inner_text("#bUpdAll"))
      check(pg.inner_text("#sUpd") == "2" and "Waiting for download" in pg.inner_text("#managedCard"), "2 updates + Leatrix waiting for its download")

      # filter by updates
      pg.click(".stat.upd"); time.sleep(0.3)
      check(pg.locator("#managedCard tbody tr").count() == 2, "updates filter")
      pg.click(".stat.upd")

      pg.click("#bUpdAll"); time.sleep(0.5)
      pg.wait_for_function("() => !document.querySelector('#bUpdAll').disabled", timeout=20000); time.sleep(1)
      check(pg.inner_text("#sUpd") == "0", "no updates left after Update all")
      check(pg.locator("#mBatch.show").count() == 1, "update summary shown"); pg.click("#btClose")

      # manual zip for Leatrix via the row's file input
      zpath = tmp / "Leatrix_Plus-12.0.11.zip"
      fid = max(f for f, v in fake_cf.FILES.items() if v["modId"] == 1005)
      zpath.write_bytes(fake_cf.ZIPS[fid])
      row = pg.locator("#managedCard tbody tr", has_text="Leatrix Plus")
      with pg.expect_file_chooser() as fc:
          row.locator("text=Install zip…").click()
      fc.value.set_files(str(zpath))
      time.sleep(0.6); pg.wait_for_function("() => !document.querySelector('#bAdd').disabled", timeout=20000); time.sleep(1)
      check(pg.inner_text("#sBad") == "0" and "Up to date" in row.inner_text(), "manual zip install -> Leatrix up to date")

      # remove DBM
      row = pg.locator("#managedCard tbody tr", has_text="Deadly Boss Mods")
      row.locator("button.more").click(); pg.click("#pop >> text=Remove"); pg.click("#askYes"); time.sleep(0.5)
      pg.wait_for_function("() => !document.querySelector('#bAdd').disabled", timeout=20000); time.sleep(1)
      check(not (addons / "DBM-Core").exists(), "Remove deleted DBM folders")

      pg.click("#dbar"); time.sleep(0.3)
      pg.screenshot(path=str(shots / "5-final.png"), full_page=True)
      pg.set_viewport_size({"width": 760, "height": 900}); time.sleep(0.3)
      pg.screenshot(path=str(shots / "6-narrow.png"), full_page=True)
      check(not errors, f"no JS errors: {errors}")

      pg.click("#bQuit"); pg.click("#askYes"); time.sleep(1.2)
      check(app.poll() is not None, "Quit stops the server")
      b.close()

finally:
    if app.poll() is None: app.kill()
print("FAILURES:", fails)
