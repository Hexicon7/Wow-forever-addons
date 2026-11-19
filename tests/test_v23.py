import os, sys, json, time, subprocess, tempfile, urllib.request
from pathlib import Path
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
sys.path.insert(0, str(Path(__file__).parent)); import fake_cf
srv = fake_cf.start(); T = fake_cf.toc
fake_cf.add_mod(1040, "needs-lib", "Needs Lib")
fake_cf.add_file(1040, "1.0", {"NeedsLib/NeedsLib.toc": T("Needs Lib", "1") + "## Dependencies: LibThing, Blizzard_Collections\n"})
fake_cf.add_mod(1041, "libthing", "LibThing")
fake_cf.add_file(1041, "2.0", {"LibThing/LibThing.toc": T("LibThing", "2")})
tmp = Path(tempfile.mkdtemp())
a = tmp/"WoW"/"_forever_"/"Interface"/"AddOns"; a.mkdir(parents=True); (tmp/"d").mkdir()
(a/"HandMade").mkdir(); (a/"HandMade"/"HandMade.toc").write_text(T("Hand Made", "1") + "## RequiredDeps: NotThere\n")
(tmp/"d"/"config.json").write_text(json.dumps({"api_key": fake_cf.KEY, "addons_dir": str(a)}))
env = dict(os.environ, WFAM_API_BASE=fake_cf.BASE, WFAM_DATA_DIR=str(tmp/"d"), BROWSER="true")
B = "http://127.0.0.1:47985"; fails = 0
def check(c, m):
    global fails; print(("PASS " if c else "FAIL ") + m); fails += 0 if c else 1
def run(): p = subprocess.Popen([sys.executable, "/home/claude/app/WoWForeverAddons.pyw", "--no-browser", "--port", "47985"], env=env, stdout=subprocess.DEVNULL); time.sleep(1.2); return p
def post(p, b=None): return json.loads(urllib.request.urlopen(urllib.request.Request(B+p, data=json.dumps(b or {}).encode(), headers={"X-WFAM":"1","Content-Type":"application/json"}, method="POST")).read())
def st(): return json.loads(urllib.request.urlopen(B+"/api/state").read())
def idle():
    time.sleep(0.3)
    while st()["job"]["running"]: time.sleep(0.2)
def rec(k): return next((x for x in st()["managed"] if x["key"] == str(k)), None)
def state_file(): return json.loads(next((tmp/"d").glob("addons-*.json")).read_text())
p = run()
try:
    post("/api/add", {"text": "questie needs-lib leatrix-plus"}); idle()
    # dependency check
    n = rec(1040); check(n["missing_deps"] == ["LibThing"], f"missing dependency found (Blizzard_ ignored): {n['missing_deps']}")
    un = st()["unmanaged"]; check(any(g.get("missing_deps") == ["NotThere"] for g in un), "unmanaged folder's missing dependency shown")
    post("/api/fix_deps", {"key": "1040"}); idle()
    check(rec(1040)["missing_deps"] == [] and (a/"LibThing").is_dir() and rec(1041), "Install missing found LibThing by name and installed it")
    # update + previous so a CF version label would be stored
    urllib.request.urlopen(urllib.request.Request(fake_cf.BASE+"/admin/publish", data=json.dumps({"mod":1001,"version":"v12.7.0","entries":{"Questie/Questie.toc": T("Questie","12.7.0")}}).encode(), method="POST"))
    post("/api/update_all"); idle()
    # what is on disk?
    sf = json.dumps(state_file())
    for word in ('"name"', '"summary"', '"authors"', '"logo"', '"latest_version"', '"latest_file_id"', '"installed_version"', '"recognized"', '"v12.6.0"', '"v12.7.0"', "Questie summary", "Author100"):
        check(word not in sf, f"not stored on disk: {word}")
    q = json.loads(sf)["addons"]["1001"]
    check(q["installed_file_id"] and q["folders"] == ["Questie"] and q["previous"][0]["installed_file_id"], "identifiers ARE stored")
    check("version_type_name" not in json.loads((tmp/"d"/"config.json").read_text()), "CurseForge's version name not in config")
finally:
    p.kill()
time.sleep(0.5)
# restart: everything is fetched again
p = run()
try:
    time.sleep(1.5); idle()
    q = rec(1001)
    check(q["name"] == "Questie" and q["installed_version"] == "v12.7.0" and q["previous_version"] == "v12.6.0" and q["status"] == "ok", f"after restart names/versions fetched again: {q['name']}, {q['installed_version']}, prev {q['previous_version']}, {q['status']}")
    check(rec(1005)["status"] in ("manual", "awaiting") and rec(1005)["distribution_disabled"], "site-only addon still known as site-only")
finally:
    p.kill()
time.sleep(0.5)
# offline (CurseForge unreachable): names from the addons' own .toc
srv.shutdown(); srv.server_close()
p = run()
try:
    time.sleep(2.5)
    q0 = rec(1001)
    time.sleep(7)
    q = rec(1001); check(q["name"] == "Questie" and q["installed_version"] in ("12.7.0", "v12.7.0"), f"offline: name/version from .toc ({q['name']}, {q['installed_version']})")
    check(any("Couldn't reach CurseForge" in l["msg"] for l in st()["logs"]), "offline explained")
finally:
    p.kill()
print("FAILURES:", fails)
