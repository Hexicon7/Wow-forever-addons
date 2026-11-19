import os, sys, json, time, subprocess, tempfile, urllib.request
from pathlib import Path
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
sys.path.insert(0, str(Path(__file__).parent)); import fake_cf
fake_cf.start(); T = fake_cf.toc
tmp = Path(tempfile.mkdtemp())
a = tmp/"WoW"/"_forever_"/"Interface"/"AddOns"; a.mkdir(parents=True); (tmp/"d").mkdir()
(tmp/"d"/"config.json").write_text(json.dumps({"api_key": fake_cf.KEY, "addons_dir": str(a), "auto_check": False}))
env = dict(os.environ, WFAM_API_BASE=fake_cf.BASE, WFAM_DATA_DIR=str(tmp/"d"), WFAM_APP_BROWSER="")
PORT = 47992; B = f"http://127.0.0.1:{PORT}"; fails = 0
host = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
def start():
    rf = tmp/"ready.txt"
    if rf.exists(): rf.unlink()
    p = subprocess.Popen([sys.executable, "/home/claude/app/WoWForeverAddons.pyw", "--no-browser", "--port", str(PORT),
                          "--ready-file", str(rf), "--host-pid", str(host.pid)], env=env, stdout=subprocess.DEVNULL)
    for _ in range(50):
        if rf.exists(): break
        time.sleep(0.1)
    return p
def check(c, m):
    global fails; print(("PASS " if c else "FAIL ") + m); fails += 0 if c else 1
def post(p, b=None):
    try: return json.loads(urllib.request.urlopen(urllib.request.Request(B+p, data=json.dumps(b or {}).encode(), headers={"X-WFAM":"1","Content-Type":"application/json"}, method="POST")).read())
    except urllib.error.HTTPError as e: return json.loads(e.read())
def get(p): return json.loads(urllib.request.urlopen(B+p).read())
def idle():
    time.sleep(0.3)
    while get("/api/state")["job"]["running"]: time.sleep(0.2)
def rec(k): return next((x for x in get("/api/state")["managed"] if x["key"] == str(k)), None)
pub = lambda b: json.loads(urllib.request.urlopen(urllib.request.Request(fake_cf.BASE+"/admin/publish", data=json.dumps(b).encode(), method="POST")).read())
app = start()
try:
    c = get("/api/state")["config"]
    check(c["hosted"] and c["tray"] and c["bg_check_hours"] == 3 and c["bg_auto_update"] and c["autostart"] is False, f"desktop settings exposed: {c['hosted']} {c['tray']} {c['bg_check_hours']} {c['bg_auto_update']}")
    post("/api/add", {"text": "questie bugsack"}); idle()
    n = get("/api/notices?after=0"); check(n["tray"] is True and isinstance(n["notices"], list), f"notices endpoint: last={n['last']}")
    post("/api/tray_check"); idle()
    n = get("/api/notices?after=0"); check(any(x["title"] == "Everything is up to date" for x in n["notices"]), "tray check reports 'up to date': " + str([x["title"] for x in n["notices"]]))
    last = n["last"]
    pub({"mod": 1001, "version": "v12.8.0", "entries": {"Questie/Questie.toc": T("Questie", "12.8.0")}})
    post("/api/settings", {"bg_auto_update": False, "bg_check_hours": 6, "tray": False}); 
    c = get("/api/state")["config"]; check(c["bg_auto_update"] is False and c["bg_check_hours"] == 6 and c["tray"] is False, "settings saved")
    check(get("/api/notices?after=0")["tray"] is False, "launcher sees tray turned off")
    post("/api/tray_check"); idle()
    n = get(f"/api/notices?after={last}"); t = [x["title"] for x in n["notices"]]
    check(t == ["1 update available"] and "Questie" in n["notices"][0]["text"], f"notify-only mode: {t}")
    check(rec(1001)["installed_version"] == "v12.6.0", "nothing installed in notify-only mode")
    last = n["last"]
    post("/api/tray_check"); idle()
    check(get(f"/api/notices?after={last}")["notices"][0]["title"] == "1 update available", "manual tray check repeats it")
    post("/api/settings", {"bg_auto_update": True, "tray": True})
    last = get("/api/notices?after=0")["last"]
    # background run (what the scheduler and the startup check call)
    post("/api/tray_check"); idle()
    n = get(f"/api/notices?after={last}"); t = [x["title"] for x in n["notices"]]
    check(t == ["1 addon updated"] and rec(1001)["installed_version"] == "v12.8.0", f"auto-update installs and reports: {t} {rec(1001)['installed_version']}")
    last = n["last"]
    pub({"mod": 1002, "version": "v12.2.0", "entries": {"BugSack/BugSack.toc": T("BugSack", "12.2.0")}})
    post("/api/tray_update"); idle()
    n = get(f"/api/notices?after={last}"); check([x["title"] for x in n["notices"]] == ["1 addon updated"] and "BugSack" in n["notices"][0]["text"], "tray 'Update all' reports")
    r = post("/api/settings", {"bg_check_hours": 5}); check(get("/api/state")["config"]["bg_check_hours"] == 3, "odd interval falls back to 3 hours")
    r = post("/api/settings", {"autostart": True}); check("error" in r and "Windows" in r["error"], "autostart refused off Windows: " + str(r.get("error"))[:60])
    # the desktop program goes away -> the manager closes by itself
    host.kill(); host.wait()
    t0 = time.time()
    try: app.wait(10); gone = True
    except subprocess.TimeoutExpired: gone = False
    check(gone and time.time() - t0 < 6, f"manager stops when the desktop program is gone ({time.time()-t0:.1f}s)")
    # bye from the window -> stops
    host = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"]); app = start()
    req = urllib.request.Request(B+"/api/bye", data=b"", headers={"Origin": B}, method="POST"); urllib.request.urlopen(req).read()
    t0 = time.time()
    try: app.wait(15); gone = True
    except subprocess.TimeoutExpired: gone = False
    check(gone, f"manager stops after the window says bye ({time.time()-t0:.1f}s)")
    # the scheduler: 1 "hour" = 4 seconds here
    host.kill(); host.wait(); app.wait(10)
    env["WFAM_TEST_HOUR_SECONDS"] = "4"
    host = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"]); app = start(); idle()
    post("/api/settings", {"bg_check_hours": 1, "bg_auto_update": True}); idle()
    last = get("/api/notices?after=0")["last"]
    pub({"mod": 1001, "version": "v12.9.0", "entries": {"Questie/Questie.toc": T("Questie", "12.9.0")}})
    for _ in range(40):
        time.sleep(0.5)
        if rec(1001)["installed_version"] == "v12.9.0" and not get("/api/state")["job"]["running"]: break
    n = get(f"/api/notices?after={last}")
    check(rec(1001)["installed_version"] == "v12.9.0" and not any(x["title"] == "1 addon updated" for x in n["notices"]), "scheduled background check installs the update by itself, without a pop-up")
    post("/api/settings", {"bg_check_hours": 0}); time.sleep(0.5); idle()
    pub({"mod": 1001, "version": "v13.0.0", "entries": {"Questie/Questie.toc": T("Questie", "13.0.0")}})
    time.sleep(10)
    check(rec(1001)["installed_version"] == "v12.9.0", "'never' turns the background checks off")
    # without bye it does NOT idle-exit while hosted (simulate by checking still alive after idle period is irrelevant) 
finally:
    try: post("/api/quit")
    except Exception: pass
    try: app.wait(5)
    except Exception: app.kill()
    host.kill()
print("FAILURES:", fails)
