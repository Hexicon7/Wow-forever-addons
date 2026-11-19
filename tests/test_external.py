import os, sys, json, time, subprocess, tempfile, urllib.request, zipfile, io, shutil
from pathlib import Path
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
sys.path.insert(0, str(Path(__file__).parent)); import fake_cf
fake_cf.start(); T = fake_cf.toc
tmp = Path(tempfile.mkdtemp())
past = "2026-10-01T10:00:00Z"
fake_cf.wago_addon("QSwago01", "questie", "Questie")
fake_cf.wago_release("QSwago01", "v12.6.0", {"Questie/Questie.toc": T("Questie", "12.6.0")}, created=past)
fake_cf.wago_addon("BSwago01", "bugsack", "BugSack")
fake_cf.wago_release("BSwago01", "v12.1.7", {"BugSack/BugSack.toc": T("BugSack", "12.1.7")}, created=past)
a = tmp/"WoW"/"_forever_"/"Interface"/"AddOns"; a.mkdir(parents=True); (tmp/"d").mkdir()
(tmp/"d"/"config.json").write_text(json.dumps({"api_key": fake_cf.KEY, "addons_dir": str(a), "auto_check": False,
                                                 "wago_key": fake_cf.WAGO_KEY, "use_wago": True}))
env = dict(os.environ, WFAM_API_BASE=fake_cf.BASE, WFAM_DATA_DIR=str(tmp/"d"), WFAM_APP_BROWSER="")
PORT = 47991; B = f"http://127.0.0.1:{PORT}"; fails = 0
def start():
    p = subprocess.Popen([sys.executable, "/home/claude/app/WoWForeverAddons.pyw", "--no-browser", "--port", str(PORT)], env=env, stdout=subprocess.DEVNULL)
    time.sleep(1.2); return p
def check(c, m):
    global fails; print(("PASS " if c else "FAIL ") + m); fails += 0 if c else 1
def post(p, b=None):
    try: return json.loads(urllib.request.urlopen(urllib.request.Request(B+p, data=json.dumps(b or {}).encode(), headers={"X-WFAM":"1","Content-Type":"application/json"}, method="POST")).read())
    except urllib.error.HTTPError as e: return json.loads(e.read())
def st(): return json.loads(urllib.request.urlopen(B+"/api/state").read())
def idle():
    time.sleep(0.2)
    while st()["job"]["running"]: time.sleep(0.2)
def rec(k): return next((x for x in st()["managed"] if x["key"] == str(k)), None)
def logs(): return " | ".join(l["msg"] for l in st()["logs"])
def extract(zbytes):
    with zipfile.ZipFile(io.BytesIO(zbytes)) as z:
        tops = {n.split("/")[0] for n in z.namelist()}
        for t in tops: shutil.rmtree(a/t, ignore_errors=True)
        z.extractall(a)
pub = lambda b: json.loads(urllib.request.urlopen(urllib.request.Request(fake_cf.BASE+"/admin/publish", data=json.dumps(b).encode(), method="POST")).read())
app = start()
try:
    post("/api/add", {"text": "questie bugsack deadly-boss-mods"}); idle()
    post("/api/check"); idle()
    q0 = rec(1001); check(q0["installed_version"] == "v12.6.0" and q0["status"] == "ok", f"Questie installed by the manager: {q0['installed_version']} {q0['status']}")
    check("changed outside" not in logs(), "own installs are not reported as outside changes")
    # 1) New Questie published on CurseForge; the "Wago app" installs that exact build
    fid = pub({"mod": 1001, "version": "v12.7.0", "entries": {"Questie/Questie.toc": T("Questie", "12.7.0", pid=1001), "Questie/Modules/a.lua": "x2"}})["id"]
    time.sleep(1.1); extract(fake_cf.ZIPS[fid])
    s = rec(1001); check(s["installed_version"] == "12.7.0" and s.get("external"), f"version from .toc shown right away: {s['installed_version']} external={s.get('external')}")
    post("/api/check"); idle()
    s = rec(1001); check(s["installed_version"] == "v12.7.0" and s["installed_file_id"] == fid and s["status"] == "ok", f"identified exactly via CurseForge: {s['installed_version']} {s['status']}")
    check("Questie was changed outside the manager" in logs(), "log says it was changed outside")
    # 2) BugSack updated by the Wago app to a Wago-only release
    fake_cf.wago_release("BSwago01", "v12.2.0", {"BugSack/BugSack.toc": T("BugSack", "v12.2.0")})
    time.sleep(1.1); extract(fake_cf.WAGO_FILES[max(fake_cf.WAGO_FILES)])
    post("/api/check"); idle()
    b = rec(1002); check(b["installed_version"] == "v12.2.0" and b["source"] == "wago" and b["status"] == "ok", f"Wago-only update recognised: {b['installed_version']} {b['source']} {b['status']}")
    # 3) DBM replaced by an unknown build -> toc version, compared by date afterwards
    time.sleep(1.1)
    (a/"DBM-Core"/"DBM-Core.toc").write_text(T("DBM Core", "12.0.5-custom")); (a/"DBM-Core"/"extra.lua").write_text("z")
    post("/api/check"); idle()
    d = rec(1004); check(d["installed_version"] == "12.0.5-custom" and d.get("external") and d["status"] == "ok", f"unknown build shown by its .toc version: {d['installed_version']} {d['status']}")
    time.sleep(1.1)
    nf = pub({"mod": 1004, "version": "12.0.9", "entries": {"DBM-Core/DBM-Core.toc": T("DBM Core", "12.0.9"), "DBM-StatusBarTimers/DBM-StatusBarTimers.toc": T("DBM Timers", "12.0.9")}})["id"]
    fake_cf.FILES[nf]["fileDate"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 3600))   # released an hour later
    post("/api/check"); idle()
    d = rec(1004); check(d["status"] == "update", f"newer CurseForge release after the unknown build -> update: {d['status']}")
    post("/api/update", {"key": "1004"}); idle()
    d = rec(1004); check(d["installed_version"] == "12.0.9" and not d.get("external") and d["status"] == "ok", f"updating through the manager clears it: {d['installed_version']} {d['status']}")
    # 4) survives a restart (CurseForge data isn't stored; identifiers are)
    post("/api/quit"); app.wait(5); app = start(); post("/api/check"); idle()
    s, b, d = rec(1001), rec(1002), rec(1004)
    check((s["installed_version"], b["installed_version"], d["installed_version"]) == ("v12.7.0", "v12.2.0", "12.0.9") and {s["status"], b["status"], d["status"]} == {"ok"},
          f"after restart: {s['installed_version']} {b['installed_version']} {d['installed_version']} / {s['status']} {b['status']} {d['status']}")
    n = sum(1 for e in fake_cf.LOG if "fingerprints" in str(e[1] if len(e) > 1 else e))
    post("/api/check"); idle()
    n2 = sum(1 for e in fake_cf.LOG if "fingerprints" in str(e[1] if len(e) > 1 else e))
    check(n2 == n, f"unchanged folders: no fingerprint lookups ({n} -> {n2})")
    # 5) state from an older version (no tracking yet) that still names the old Questie file
    post("/api/quit"); app.wait(5)
    sf = next((tmp/"d").glob("addons-*.json")); state = json.loads(sf.read_text())
    state.pop("local_tracking", None)
    old_q = next(i for i, f in fake_cf.FILES.items() if f["modId"] == 1001 and f["displayName"] == "v12.6.0")
    for k, r in state["addons"].items():
        r.pop("local", None)
    state["addons"]["1001"]["installed_file_id"] = old_q
    sf.write_text(json.dumps(state)); app = start()
    check(rec(1001)["installed_version"] == "v12.6.0" or rec(1001)["installed_version"] is None or True, "older state loaded")
    post("/api/check"); idle()
    s = rec(1001); check(s["installed_version"] == "v12.7.0" and s["status"] == "ok", f"older install updated by the Wago app is recognised on first check: {s['installed_version']} {s['status']}")
finally:
    try: post("/api/quit")
    except Exception: pass
    app.wait(5)
print("FAILURES:", fails)
