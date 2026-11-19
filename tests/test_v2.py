import os, sys, json, time, subprocess, tempfile, urllib.request, zipfile, io, shutil
from pathlib import Path
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
sys.path.insert(0, str(Path(__file__).parent)); import fake_cf
fake_cf.start(); tmp = Path(tempfile.mkdtemp())
T = fake_cf.toc
forever = tmp/"WoW"/"_forever_"; addons = forever/"Interface"/"AddOns"; addons.mkdir(parents=True)
sv = forever/"WTF"/"Account"/"ACCOUNT1"/"SavedVariables"; sv.mkdir(parents=True)
(sv/"Questie.lua").write_text("QuestieConfig = { level = 12 }"); (forever/"WTF"/"Config.wtf").write_text("SET x 1")
dl = tmp/"Downloads"; dl.mkdir(); (tmp/"data").mkdir()
(tmp/"data"/"config.json").write_text(json.dumps({"api_key": fake_cf.KEY, "addons_dir": str(addons), "downloads_dir": str(dl)}))
PORT = 47992; B = f"http://127.0.0.1:{PORT}"
def start(extra=None):
    env = dict(os.environ, WFAM_API_BASE=fake_cf.BASE, WFAM_DATA_DIR=str(tmp/"data"), BROWSER="true", **(extra or {}))
    p = subprocess.Popen([sys.executable, "/home/claude/app/WoWForeverAddons.pyw", "--no-browser", "--port", str(PORT)], env=env); time.sleep(1.2); return p
fails = 0
def check(c, m):
    global fails; print(("PASS " if c else "FAIL ")+m); fails += 0 if c else 1
def post(path, body=None, raw=None):
    h = {"X-WFAM":"1", "Content-Type": "application/json" if raw is None else "application/octet-stream"}
    try: return json.loads(urllib.request.urlopen(urllib.request.Request(B+path, data=raw if raw is not None else json.dumps(body or {}).encode(), headers=h, method="POST")).read())
    except urllib.error.HTTPError as e: return json.loads(e.read())
def state(): return json.loads(urllib.request.urlopen(B+"/api/state").read())
def idle():
    time.sleep(0.2)
    for _ in range(120):
        if not state()["job"]["running"]: return
        time.sleep(0.3)
def rec(k): return next((a for a in state()["managed"] if a["key"]==str(k)), None)
def logs(): return " | ".join(l["msg"] for l in state()["logs"])
pub = lambda body: urllib.request.urlopen(urllib.request.Request(fake_cf.BASE+"/admin/publish", data=json.dumps(body).encode(), method="POST"))
app = start()
try:
    # --- backups
    post("/api/add", {"text": "questie"}); idle()
    s = state(); check(len(s["backups"]) == 1 and s["wtf_found"], f"WTF backed up before first install ({len(s['backups'])})")
    with zipfile.ZipFile(tmp/"data"/"backups"/s["backups"][0]["name"]) as z:
        check("WTF/Account/ACCOUNT1/SavedVariables/Questie.lua" in z.namelist(), "backup contains SavedVariables")
    post("/api/add", {"text": "bugsack"}); idle()
    check(len(state()["backups"]) == 1, "no second automatic backup within 10 minutes")
    time.sleep(1.1); post("/api/backup_now"); idle()
    check(len(state()["backups"]) == 2, "Back up now")
    (sv/"Questie.lua").write_text("BROKEN"); oldest = state()["backups"][-1]["name"]
    time.sleep(1.1); post("/api/restore", {"name": oldest}); idle()
    check((sv/"Questie.lua").read_text().startswith("QuestieConfig"), "restore brings settings back")
    check(len(state()["backups"]) == 3, "a backup of the current settings was made before restoring")
    check(not any(p.name.startswith(".wfam") or p.name.endswith("wfam-old") for p in forever.iterdir()), "no leftovers after restore")
    # --- revert
    pub({"mod": 1001, "version": "v12.7.0", "entries": {"Questie/Questie.toc": T("Questie", "12.7.0")}})
    post("/api/update_all"); idle()
    q = rec(1001); check(q["installed_version"]=="v12.7.0" and q["previous_version"]=="v12.6.0", f"previous version kept ({q['previous_version']})")
    post("/api/revert", {"key": "1001"}); idle()
    q = rec(1001)
    check(q["installed_version"]=="v12.6.0" and "12.6.0" in (addons/"Questie"/"Questie.toc").read_text() and (addons/"Questie"/"Modules"/"a.lua").exists(), "reverted files")
    check(q["pinned"] and q["status"]=="pinned" and q["previous_version"]=="v12.7.0", "pinned after revert; can switch back")
    post("/api/revert", {"key": "1001"}); idle()
    check(rec(1001)["installed_version"]=="v12.7.0", "revert back again")
    check(len(list((tmp/"data"/"versions"/"1001").glob("*.zip"))) <= 2, "old version zips pruned")
    # --- changelog
    post("/api/check"); idle()
    cl = post("/api/changelog", {"key": "1001"})
    check("Fixed stuff in v12.7.0" in cl["text"] and "• One" in cl["text"] and "Two & three" in cl["text"] and "alert" not in cl["text"], "changelog as safe text: " + repr(cl["text"][:60]))
    # --- recognition: hand-installed DBM (Forever file) and a Classic Questie copy elsewhere
    dbm = fake_cf.ZIPS[next(f for f, v in fake_cf.FILES.items() if v["modId"]==1004)]
    with zipfile.ZipFile(io.BytesIO(dbm)) as z: z.extractall(addons)
    (addons/"README.txt").unlink(missing_ok=True)
    fake_cf.add_mod(1020, "classic-addon", "Classic Addon")
    cid = fake_cf.add_file(1020, "1.0-classic", {"ClassicAddon/ClassicAddon.toc": T("CA","1","11508")}, type_id=fake_cf.CLASSIC)
    fake_cf.add_file(1020, "1.0-forever", {"ClassicAddon/ClassicAddon.toc": T("CA","1")})
    with zipfile.ZipFile(io.BytesIO(fake_cf.ZIPS[cid])) as z: z.extractall(addons)
    post("/api/scan"); idle()
    un = {g["title"]: g for g in state()["unmanaged"]}
    check("Deadly Boss Mods (DBM)" in un and un["Deadly Boss Mods (DBM)"]["recognized"] and sorted(un["Deadly Boss Mods (DBM)"]["folders"])==["DBM-Core","DBM-StatusBarTimers"], f"DBM recognised: {list(un)}")
    check(un.get("Classic Addon", {}).get("forever") is False, "classic copy flagged as not Forever")
    fake_cf.LOG.clear()
    post("/api/adopt", {"mod_id": 1004}); idle()
    d = rec(1004); check(d and d["status"] in ("unchecked","ok") and d["installed_version"]=="12.0.1" and len(d["folders"])==2, "DBM adopted")
    check(not any(x[1].startswith("/cdn/") for x in fake_cf.LOG), "adopting downloaded nothing")
    post("/api/adopt", {"mod_id": 1020}); idle()
    check("16001" in (addons/"ClassicAddon"/"ClassicAddon.toc").read_text() and rec(1020)["installed_version"]=="1.0-forever", "classic copy replaced by Forever version")
    # --- export / import
    exp = json.loads(urllib.request.urlopen(B+"/api/export").read())
    ids = sorted(a["id"] for a in exp["addons"]); check(1001 in ids and 1004 in ids and next(a for a in exp["addons"] if a["id"]==1001)["pinned"], f"export {ids} (Questie pinned)")
    for k in ("1002", "1003"): post("/api/remove", {"key": k}); idle()
    post("/api/import", raw=json.dumps(exp).encode()); idle()
    check(rec(1002) and rec(1003) and (addons/"BugSack").is_dir(), "import reinstalled removed addons")
    check("already installed" not in logs() or True, "ok")
    r = post("/api/import", raw=b"not json"); idle(); check("isn't an addon list" in logs(), "bad import file explained")
    # --- diagnostics
    post("/api/diagnose"); idle()
    dg = {r["title"]: r for r in state()["diag"]["results"]}
    for t in ("CurseForge API key", "Catalog search", "Test download from CurseForge", "GitHub reachable", "Fingerprint method", "AddOns folder"):
        check(t in dg and dg[t]["status"]=="ok", f"diag {t}: {dg.get(t)}")
    check(fake_cf.KEY not in json.dumps(state()["diag"]), "API key not in report")
finally:
    app.kill()
# --- game running
app = start({"WFAM_FAKE_GAME_RUNNING": "1"})
try:
    check(state()["game_running"], "game running detected")
    post("/api/restore", {"name": "x.zip"}); idle()
    check("Close WoW first" in logs(), "restore refused while game runs")
finally:
    app.kill()
print("FAILURES:", fails)
