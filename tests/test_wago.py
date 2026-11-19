import os, sys, json, time, subprocess, tempfile, urllib.request
from pathlib import Path
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
sys.path.insert(0, str(Path(__file__).parent)); import fake_cf
fake_cf.start(); T = fake_cf.toc
tmp = Path(tempfile.mkdtemp())
past = "2026-10-01T10:00:00Z"
# Wago catalogue (game version "forever")
fake_cf.wago_addon("LPwago01", "leatrix-plus", "Leatrix Plus")
fake_cf.wago_release("LPwago01", "12.0.10", {"Leatrix_Plus/Leatrix_Plus.toc": T("LP", "12.0.10")}, created=past)
fake_cf.wago_addon("QSwago01", "questie", "Questie")
fake_cf.wago_release("QSwago01", "v12.6.0", {"Questie/Questie.toc": T("Questie", "12.6.0")}, created=past)
fake_cf.wago_addon("BSwago01", "bugsack-wago", "BugSack")      # different slug: linked through the .toc
fake_cf.wago_release("BSwago01", "v12.1.7", {"BugSack/BugSack.toc": T("BugSack", "12.1.7")}, created=past)
fake_cf.wago_addon("XXwago01", "nested-addon", "Totally Different Addon")   # same slug as a CF addon, other addon
fake_cf.wago_release("XXwago01", "9.9", {"Other/Other.toc": T("x", "9.9")})
# a CF site-only addon whose Wago build is Classic-only -> must fall back
fake_cf.add_mod(1030, "classic-on-wago", "Classic On Wago", allow=False)
fake_cf.add_file(1030, "1.0", {"COW/COW.toc": T("x", "1")})
fake_cf.wago_addon("COwago01", "classic-on-wago", "Classic On Wago")
fake_cf.wago_release("COwago01", "1.0", {"COW/COW.toc": T("x", "1", "11508")})
# BugSack's CurseForge zip carries an X-Wago-ID
bs = next(f for f, v in fake_cf.FILES.items() if v["modId"] == 1002)
fake_cf.ZIPS[bs] = fake_cf.make_zip({"BugSack/BugSack.toc": T("BugSack", "12.1.7") + "## X-Wago-ID: BSwago01\n"})
import hashlib; fake_cf.FILES[bs]["hashes"] = [{"value": hashlib.sha1(fake_cf.ZIPS[bs]).hexdigest(), "algo": 1}]

a = tmp/"WoW"/"_forever_"/"Interface"/"AddOns"; a.mkdir(parents=True); (tmp/"d").mkdir(); dl = tmp/"dl"; dl.mkdir()
opened = tmp/"opened.txt"; (tmp/"b.sh").write_text(f'#!/bin/sh\necho "$1" >> {opened}\n'); os.chmod(tmp/"b.sh", 0o755)
(tmp/"d"/"config.json").write_text(json.dumps({"api_key": fake_cf.KEY, "addons_dir": str(a), "downloads_dir": str(dl), "auto_check": False}))
env = dict(os.environ, WFAM_API_BASE=fake_cf.BASE, WFAM_DATA_DIR=str(tmp/"d"), BROWSER=str(tmp/"b.sh"))
app = subprocess.Popen([sys.executable, "/home/claude/app/WoWForeverAddons.pyw", "--no-browser", "--port", "47987"], env=env, stdout=subprocess.DEVNULL)
time.sleep(1.2); B = "http://127.0.0.1:47987"; fails = 0
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
pub = lambda b: urllib.request.urlopen(urllib.request.Request(fake_cf.BASE+"/admin/publish", data=json.dumps(b).encode(), method="POST")).read()
try:
    # key test
    r = post("/api/test_wago", {"wago_key": "wrong-key-123456"}); check(not r["ok"] and "rejected" in r["message"], "bad Wago key explained: " + r["message"])
    r = post("/api/test_wago", {"wago_key": "expired-key-xxxxxxxx"}); check(not r["ok"] and "expired" in r["message"], "expired key explained")
    r = post("/api/test_wago", {"wago_key": fake_cf.WAGO_KEY}); check(r["ok"], "good key: " + r["message"])
    post("/api/settings", {"wago_key": "  " + fake_cf.WAGO_KEY + "\n"})
    s = st()["config"]; check(s["has_wago_key"] and s["use_wago"], "key saved")
    # site-only Leatrix now comes straight from Wago
    post("/api/add", {"text": "leatrix-plus questie bugsack nested-addon"}); idle()
    lp = rec(1005)
    check(lp["status"] in ("ok", "unchecked") and lp["source"] == "wago" and lp["installed_version"] == "12.0.10" and (a/"Leatrix_Plus").is_dir(), f"site-only addon installed from Wago ({lp['source']}, {lp['status']})")
    check(not opened.exists(), "no browser page needed")
    # check: link addons, compare
    post("/api/check"); idle()
    s = {x["key"]: x for x in st()["managed"]}
    check(s["1002"]["wago_url"] and "bugsack-wago" in s["1002"]["wago_url"], "BugSack linked via X-Wago-ID in its .toc")
    check(s["1001"]["wago_url"] and s["1001"]["status"] == "ok", "Questie linked via slug; same version on both -> up to date")
    check(not s["1006"]["wago_url"], "same slug but different addon on Wago is NOT linked")
    # Wago gets a newer Questie than CurseForge
    fake_cf.wago_release("QSwago01", "v12.7.0", {"Questie/Questie.toc": T("Questie", "12.7.0-wago")}, created="2026-12-01T10:00:00Z")
    post("/api/check"); idle()
    q = rec(1001); check(q["status"] == "update" and q["update_from"] == "wago", f"newer on Wago detected ({q['status']}, {q['update_from']})")
    check("Questie (Wago)" in logs(), "summary mentions Wago")
    post("/api/update_all"); idle()
    q = rec(1001); check(q["source"] == "wago" and q["installed_version"] == "v12.7.0" and "12.7.0-wago" in (a/"Questie"/"Questie.toc").read_text(), "updated from Wago")
    check(q["previous_version"] == "v12.6.0", "previous version kept for Revert")
    # later CurseForge publishes an even newer one -> back to CurseForge
    time.sleep(1)
    fake_cf.FILES  # publish with a date after Wago's
    fid = fake_cf.add_file(1001, "v12.8.0", {"Questie/Questie.toc": T("Questie", "12.8.0")}, date="2026-12-05T10:00:00Z")
    post("/api/check"); idle()
    q = rec(1001); check(q["status"] == "update" and q["update_from"] == "curseforge", f"newer on CurseForge again ({q['update_from']})")
    post("/api/update_all"); idle()
    q = rec(1001); check(q["source"] == "curseforge" and q["installed_version"] == "v12.8.0", "updated from CurseForge")
    post("/api/check"); idle()
    check(rec(1001)["status"] == "ok", "no ping-pong between sources")
    # Wago release that isn't a Forever build -> falls back to the download page
    post("/api/add", {"text": "classic-on-wago"}); idle()
    c = rec(1030); check(c["status"] == "awaiting" and "isn't built for WoW Forever" in logs() and not (a/"COW").exists(), f"non-Forever Wago build rejected, fallback ({c['status']})")
    # keys never leak
    leaks = [x for x in fake_cf.LOG if x[0] == "WAGO" and x[2]]
    check(not leaks, "CurseForge key never sent to Wago")
    # diagnostics
    post("/api/diagnose"); idle()
    dg = {r["title"]: r for r in st()["diag"]["results"]}
    check(dg.get("Wago key", {}).get("status") == "ok", "diagnostics: " + str(dg.get("Wago key")))
    check(fake_cf.WAGO_KEY not in json.dumps(st()), "Wago key never shown in the page data")
    # turning Wago off
    post("/api/settings", {"use_wago": False}); fake_cf.wago_release("BSwago01", "v13", {"BugSack/BugSack.toc": T("BugSack", "13")}, created="2026-12-20T10:00:00Z")
    post("/api/check"); idle()
    check(rec(1002)["status"] == "ok", "Wago ignored when turned off")
finally:
    app.kill()
print("FAILURES:", fails)
