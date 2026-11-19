import os, sys, json, time, subprocess, tempfile, urllib.request
from pathlib import Path
os.environ["NO_PROXY"] = "127.0.0.1,localhost"
sys.path.insert(0, str(Path(__file__).parent)); import fake_cf
fake_cf.start(); tmp = Path(tempfile.mkdtemp())
a = tmp/"WoW"/"_forever_"/"Interface"/"AddOns"; a.mkdir(parents=True); (tmp/"d").mkdir()
(tmp/"d"/"config.json").write_text(json.dumps({"api_key": fake_cf.KEY, "addons_dir": str(a)}))
env = dict(os.environ, WFAM_API_BASE=fake_cf.BASE, WFAM_DATA_DIR=str(tmp/"d"), BROWSER="true")
fails = 0
def check(c, m):
    global fails; print(("PASS " if c else "FAIL ")+m); fails += 0 if c else 1
B = "http://127.0.0.1:47988"
def st(q=""): return json.loads(urllib.request.urlopen(B+"/api/state"+q).read())
def run(): p = subprocess.Popen([sys.executable, "/home/claude/app/WoWForeverAddons.pyw", "--no-browser", "--port", "47988"], env=env, stdout=subprocess.DEVNULL); time.sleep(1.2); return p
p = run()
urllib.request.urlopen(urllib.request.Request(B+"/api/add", data=b'{"text":"questie"}', headers={"X-WFAM":"1","Content-Type":"application/json"}, method="POST")).read()
time.sleep(2.5); p.kill(); time.sleep(0.5)
for f in (tmp/"d").glob("addons-*.json"):      # pretend the last check was long ago
    d = json.loads(f.read_text()); d["last_check"] = time.time() - 7200; f.write_text(json.dumps(d))
p = run(); time.sleep(2.5)
s = st(); check(any("up to date" in l["msg"] or "update" in l["msg"] for l in s["logs"]) and s["managed"][0]["checked_at"], "update check ran by itself at startup")
e = s["etag"]; r = st(f"?since={s['log_seq']}&etag={e}")
check(r == {"same": True, "etag": e}, f"unchanged poll is tiny: {r}")
p.kill(); time.sleep(0.5)
p = run(); time.sleep(2.5)
check(any("Checking for updates" in l["msg"] for l in st()["logs"]), "info is fetched again on every start (nothing from CurseForge is stored)")
p.kill()
# proxy fallback path works (urllib) - call it directly
import importlib.machinery, importlib.util
l = importlib.machinery.SourceFileLoader("w", "/home/claude/app/WoWForeverAddons.pyw"); sp = importlib.util.spec_from_loader("w", l); W = importlib.util.module_from_spec(sp); l.exec_module(W)
code, h, data = W._urllib_call("GET", fake_cf.BASE + "/v1/mods/1001", {"x-api-key": fake_cf.KEY, "User-Agent": "t"}, None, None, (), 10**6, 10)
check(code == 200 and json.loads(data)["data"]["slug"] == "questie", "proxy fallback (urllib) request works")
code, h, data = W._urllib_call("GET", fake_cf.BASE + "/v1/mods/999999", {"x-api-key": fake_cf.KEY}, None, None, (), 10**6, 10)
check(code == 404, "proxy fallback returns HTTP errors as codes")
# keep-alive reuse + stale connection retry
W.API_BASE = fake_cf.BASE
cf = W.CurseForge(fake_cf.KEY); fake_cf.STATS.update(conns=0, reqs=0)
for _ in range(5): cf.mod(1001)
check(fake_cf.STATS["conns"] == 1 and fake_cf.STATS["reqs"] == 5, f"5 requests over 1 connection: {fake_cf.STATS}")
for c in W._tls.pool.values(): c.sock.close()        # simulate the server dropping an idle connection
check(cf.mod(1001)["slug"] == "questie", "stale connection is retried transparently")
print("FAILURES:", fails)
