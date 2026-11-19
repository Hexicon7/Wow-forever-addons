import os, sys, json, shutil, tempfile, importlib.machinery, importlib.util, urllib.request
from pathlib import Path

os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
sys.path.insert(0, str(Path(__file__).parent))
import fake_cf

srv = fake_cf.start()
os.environ["WFAM_API_BASE"] = fake_cf.BASE
tmp = Path(tempfile.mkdtemp())
os.environ["WFAM_DATA_DIR"] = str(tmp / "data")
wow = tmp / "World of Warcraft"
addons = wow / "_forever_" / "Interface" / "AddOns"
addons.mkdir(parents=True)
(wow / "_classic_" / "Interface" / "AddOns").mkdir(parents=True)
os.environ["WFAM_EXTRA_WOW_ROOT"] = str(wow)
# a pre-existing hand-installed addon that carries its CurseForge id, and one without
(addons / "Questie").mkdir(); (addons / "Questie" / "Questie.toc").write_text(fake_cf.toc("Questie", "12.4.0", "11507", pid=1001))
(addons / "OldThing").mkdir(); (addons / "OldThing" / "OldThing.toc").write_text(fake_cf.toc("Old Thing", "0.9", "11507"))

loader = importlib.machinery.SourceFileLoader("wfam", "/home/claude/app/WoWForeverAddons.pyw")
spec = importlib.util.spec_from_loader("wfam", loader)
W = importlib.util.module_from_spec(spec); loader.exec_module(W)

fails = 0
def check(cond, msg):
    global fails
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: fails += 1

store = W.Store(); m = W.Manager(store, echo=True)
run = lambda fn, *a: m.run_job("t", fn, *a, background=False)

# --- parse refs
check(W.parse_ref("https://www.curseforge.com/wow/addons/questie")["slug"] == "questie", "parse plain url")
r = W.parse_ref("https://www.curseforge.com/wow/addons/questie/files/7123456"); check(r["file"] == 7123456, "parse file url")
check(W.parse_ref("123")["kind"] == "id", "parse id")
check(W.parse_ref("https://www.wowace.com/projects/ace3")["slug"] == "ace3", "parse wowace")
try: W.parse_ref("https://www.curseforge.com/minecraft/mc-mods/jei"); check(False, "reject minecraft")
except W.UserError: check(True, "reject minecraft")

# --- detection & normalize
d = W.detect_addon_dirs(); check(d and d[0]["flavor"] == "_forever_", f"detect finds _forever_ first: {[x['flavor'] for x in d]}")
check(W.normalize_addons_dir(str(wow)) == addons, "normalize WoW root -> _forever_ AddOns")
check(W.normalize_addons_dir(str(wow / "_forever_")) == addons, "normalize flavor dir")

# --- no key yet
check(run(m.op_add, "questie") is False, "add without key fails cleanly")
store.update_config(api_key="wrong", addons_dir=str(addons))
ok = run(m.op_add, "questie"); check(not ok and "rejected the API key" in m.logs[-1]["msg"], "bad key message: " + m.logs[-1]["msg"])
store.update_config(api_key=fake_cf.KEY)

# --- install, auto type detection, deps, multi-folder, spaces in filename
ok = run(m.op_add, "https://www.curseforge.com/wow/addons/questie\nhttps://www.curseforge.com/wow/addons/bugsack, deadly-boss-mods")
check(ok, "install three addons")
check(store.config["version_type_id"] == fake_cf.FOREVER, "picked live Forever type, not beta")
check((addons / "Questie" / "Modules" / "a.lua").exists(), "Questie replaced with Forever build")
check("12.6.0" in (addons / "Questie" / "Questie.toc").read_text(), "Questie toc is new version")
check((addons / "!BugGrabber").is_dir() and "1003" in store.addons, "required dependency installed")
check((addons / "DBM-Core").is_dir() and (addons / "DBM-StatusBarTimers").is_dir() and not (addons / "README.txt").exists(), "multi-folder zip, top-level file skipped")
check(store.addons["1004"]["primary"] == "DBM-Core", "primary folder pick: " + str(store.addons["1004"]["primary"]))
check(not any(p.name.startswith(".wfam") for p in addons.parent.iterdir()), "no staging/backup leftovers")

# --- already installed
run(m.op_add, "questie"); check("already installed" in m.logs[-1]["msg"], "re-add is a no-op")

# --- errors that should be friendly
run(m.op_add, "classic-only"); check("no WoW Forever file" in m.logs[-1]["msg"], "classic-only message")
run(m.op_add, "beta-only"); check("beta builds" in m.logs[-1]["msg"], "beta-only message: " + m.logs[-1]["msg"])
run(m.op_add, "nope-not-real"); check("No WoW addon called" in m.logs[-1]["msg"], "unknown slug message")
run(m.op_add, "evil-addon"); check("unsafe path" in m.logs[-1]["msg"] and not (tmp / "escape.txt").exists() and not (addons / "Evil").exists(), "zip-slip rejected, nothing installed")
check("1007" in store.addons and not store.addons["1007"].get("folders"), "evil addon not marked installed")
del store.addons["1007"]; store.save_state()

# --- nested zip
run(m.op_add, "nested-addon"); check((addons / "Nested" / "Nested.toc").exists() and not (addons / "Nested-1.0").exists(), "nested zip root stripped")

# --- distribution disabled
run(m.op_add, "leatrix-plus")
snap = m.snapshot(); lp = next(a for a in snap["managed"] if a["key"] == "1005")
check(lp["status"] == "manual" and "/files/" in lp["latest_url"], f"leatrix manual status ({lp['status']})")
# manual zip install that matches the CurseForge file
fid = store.addons["1005"]["latest_file_id"]
run(m.op_install_zip, fake_cf.ZIPS[fid], "Leatrix_Plus-12.0.10.zip", "1005")
lp = next(a for a in m.snapshot()["managed"] if a["key"] == "1005")
check(lp["status"] == "ok" and lp["installed_version"] == "12.0.10-forever", f"manual zip recognised as latest ({lp['status']}, {lp['installed_version']})")

# --- status: everything ok; unmanaged list
snap = m.snapshot()
check(all(a["status"] in ("ok",) for a in snap["managed"]), "all ok: " + str([(a['name'], a['status']) for a in snap['managed']]))
un = snap["unmanaged"]; check([g["title"] for g in un] == ["Old Thing"] and un[0]["compat"] == "mismatch", f"unmanaged: {un}")

# --- publish updates, check, update all
req = lambda body: urllib.request.urlopen(urllib.request.Request(fake_cf.BASE + "/admin/publish", data=json.dumps(body).encode(), method="POST"))
req({"mod": 1001, "version": "v12.7.0", "entries": {"Questie/Questie.toc": fake_cf.toc("Questie", "12.7.0")}})
req({"mod": 1004, "version": "12.0.2", "entries": {"DBM-Core/DBM-Core.toc": fake_cf.toc("DBM", "12.0.2")}})  # drops StatusBarTimers
req({"mod": 1002, "version": "v13-beta", "release": 2, "entries": {"BugSack/BugSack.toc": fake_cf.toc("BugSack", "13")}})
req({"mod": 1005, "version": "12.0.11-forever", "entries": {"Leatrix_Plus/Leatrix_Plus.toc": fake_cf.toc("LP", "12.0.11")}})
run(m.op_check)
st = {a["name"]: a["status"] for a in m.snapshot()["managed"]}
check(st["Questie"] == "update" and st["Deadly Boss Mods (DBM)"] == "update", "updates detected")
check(st["BugSack"] == "ok", "beta release ignored on release channel")
check(st["Leatrix Plus"] == "update_manual", "manual update flagged: " + st["Leatrix Plus"])
fake_cf.LOG.clear(); run(m.op_check)
cf_calls = [x for x in fake_cf.LOG if "/gh/" not in str(x)]
check(len(cf_calls) <= 3, f"check uses batched CurseForge requests ({len(cf_calls)} calls; GitHub looked up separately)")

store.addons["1003"]["pinned"] = True
req({"mod": 1003, "version": "v12.2.0", "entries": {"!BugGrabber/!BugGrabber.toc": fake_cf.toc("BG", "12.2")}})
run(m.op_check_and_update)
check("12.7.0" in (addons / "Questie" / "Questie.toc").read_text(), "Questie updated")
check(not (addons / "DBM-StatusBarTimers").exists() and (addons / "DBM-Core").exists(), "folder dropped by new version is removed")
check("12.1.0" in (addons / "!BugGrabber" / "!BugGrabber.toc").read_text(), "pinned addon skipped")
st = {a["name"]: a["status"] for a in m.snapshot()["managed"]}
check(st["Questie"] == "ok" and st["BugGrabber"] == "pinned", f"statuses after update: {st}")

# --- beta channel
store.update_config(channel="beta"); run(m.op_check)
check({a["name"]: a["status"] for a in m.snapshot()["managed"]}["BugSack"] == "update", "beta channel sees beta")
store.update_config(channel="release")
check({a["name"]: a["status"] for a in m.snapshot()["managed"]}["BugSack"] == "unchecked", "channel change clears stale latest")

# --- missing folder -> reinstall
shutil.rmtree(addons / "Nested")
check({a["name"]: a["status"] for a in m.snapshot()["managed"]}["Nested Addon"] == "missing", "missing detected")
run(m.op_update, None); check((addons / "Nested").is_dir(), "missing reinstalled by update all")
check("12.1.7" in (addons / "BugSack" / "BugSack.toc").read_text(), "beta not installed after returning to release channel")

# --- adopt unmanaged w/ project id & shared folder protection
(addons / "Shared").mkdir()
store.addons["1002"]["folders"].append("!BugGrabber")  # pretend BugSack also ships !BugGrabber
run(m.op_remove, "1003")
check((addons / "!BugGrabber").is_dir(), "shared folder kept when other addon still uses it")
run(m.op_remove, "1002")
check(not (addons / "!BugGrabber").exists() and not (addons / "BugSack").exists(), "folders removed with last owner")

# --- rollback on failure: corrupt zip mid-install keeps old version
before = (addons / "Questie" / "Questie.toc").read_text()
bad = tmp / "bad.zip"; bad.write_bytes(b"not a zip")
try: W.install_zip_into(addons, bad, ["Questie"], set()); check(False, "bad zip raises")
except W.InstallError: check(True, "bad zip raises")
check((addons / "Questie" / "Questie.toc").read_text() == before, "old version untouched after failed install")

# --- generic zip install (untracked) + clash protection
z = fake_cf.make_zip({"MyUI/MyUI.toc": fake_cf.toc("My UI", "1")})
run(m.op_install_zip, z, "MyUI.zip", None); check((addons / "MyUI").is_dir(), "generic zip extracted")
z2 = fake_cf.make_zip({"Questie/Questie.toc": "x"})
run(m.op_install_zip, z2, "q.zip", None); check("belongs to Questie" in m.logs[-1]["msg"], "generic zip cannot clobber managed addon")

# --- CLI
import subprocess
out = subprocess.run([sys.executable, "/home/claude/app/WoWForeverAddons.pyw", "--list"], capture_output=True, text=True, env=os.environ)
print(out.stdout); check(out.returncode == 0 and "Questie" in out.stdout, "CLI --list works")

print("\nFAILURES:", fails)
sys.exit(1 if fails else 0)
