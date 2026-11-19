"""Benchmark: python bench.py <app.pyw>"""
import os, sys, json, time, subprocess, tempfile, urllib.request, zipfile, io, statistics
from pathlib import Path
os.environ["NO_PROXY"] = "127.0.0.1,localhost"
sys.path.insert(0, str(Path(__file__).parent)); import fake_cf
APP = sys.argv[1]
import faulthandler; faulthandler.dump_traceback_later(int(os.environ.get("BENCH_TIMEOUT","400")), exit=True)
fake_cf.start()
T = fake_cf.toc
# 20 GitHub-only addons to make GitHub checks matter
for i in range(8):
    mid = 3000 + i
    fake_cf.add_mod(mid, f"gh-addon-{i}", f"GH Addon {i}", allow=False)
    fake_cf.MODS[mid]["links"]["sourceUrl"] = f"https://github.com/fake/gh{i}"
    fake_cf.add_file(mid, "1.0", {f"GH{i}/GH{i}.toc": T("x", "1")})
    fake_cf.gh_release(f"fake/gh{i}", "v1", {f"GH{i}-v1.zip": {f"GH{i}/GH{i}.toc": T("x", "1"), f"GH{i}/c.lua": "x"*20000}})
tmp = Path(tempfile.mkdtemp())
fo = tmp/"WoW"/"_forever_"; addons = fo/"Interface"/"AddOns"; addons.mkdir(parents=True)
(fo/"WTF"/"Account").mkdir(parents=True); (fo/"WTF"/"Account"/"sv.lua").write_text("x"*200000)
# 25 hand-installed folders with ~1.5 MB of lua each (fingerprinting cost)
import random; random.seed(1)
for i in range(25):
    d = addons/f"Manual{i}"; d.mkdir()
    (d/f"Manual{i}.toc").write_text(T(f"Manual {i}", "1") + "data.lua\n")
    (d/"core.lua").write_text("x=1\n"); (d/"data.lua").write_text("".join(random.choice("abc def\n") for _ in range(1_500_000)))
dl = tmp/"Downloads"; dl.mkdir()
for i in range(300): (dl/f"file{i}.pdf").write_bytes(b"x")
(tmp/"data").mkdir(); (tmp/"data"/"config.json").write_text(json.dumps({"api_key": fake_cf.KEY, "addons_dir": str(addons), "downloads_dir": str(dl)}))
env = dict(os.environ, WFAM_API_BASE=fake_cf.BASE, WFAM_DATA_DIR=str(tmp/"data"), BROWSER="true")
B = "http://127.0.0.1:47990"
app = subprocess.Popen([sys.executable, APP, "--no-browser", "--port", "47990", "--no-idle-exit"], env=env, stdout=subprocess.DEVNULL)
time.sleep(1.5)
def post(p, b=None): urllib.request.urlopen(urllib.request.Request(B+p, data=json.dumps(b or {}).encode(), headers={"X-WFAM":"1","Content-Type":"application/json"}, method="POST")).read()
def state(): return json.loads(urllib.request.urlopen(B+"/api/state").read())
def timed_job(p, b=None):
    t = time.time(); post(p, b); time.sleep(0.05)
    while state()["job"]["running"]: time.sleep(0.05)
    return time.time() - t
ids = [str(i) for i in list(range(2000, 2045)) + [1001, 1002, 1003, 1004, 1006] + list(range(3000, 3008))]
print("setup install:", round(timed_job("/api/add", {"text": "\n".join(ids)}), 1), "s for", len(ids), "addons")
fake_cf.LATENCY[:] = [0.15, 0.06]   # realistic: TLS handshake 150 ms, 60 ms per request
for mid in list(range(2000, 2020)):
    urllib.request.urlopen(urllib.request.Request(fake_cf.BASE+"/admin/publish", data=json.dumps({"mod": mid, "version": "2.0", "entries": {f"BagHelper{mid-2000}/BagHelper{mid-2000}.toc": T("x", "2"), f"BagHelper{mid-2000}/big.lua": "y"*300000}}).encode(), method="POST")).read()
for i in range(4): fake_cf.gh_release(f"fake/gh{i}", "v2", {f"GH{i}-v2.zip": {f"GH{i}/GH{i}.toc": T("x", "2")}})
fake_cf.STATS.update(conns=0, reqs=0)
t1 = timed_job("/api/check"); c1 = dict(fake_cf.STATS)
fake_cf.STATS.update(conns=0, reqs=0)
t1b = timed_job("/api/check"); c1b = dict(fake_cf.STATS)
fake_cf.STATS.update(conns=0, reqs=0)
t2 = timed_job("/api/update_all"); c2 = dict(fake_cf.STATS)
fake_cf.LATENCY[:] = [0, 0]
times = []
for _ in range(15):
    t = time.time(); state(); times.append(time.time() - t)
# idle CPU of the app process over 20 s while a page polls every 5 s
def cpu():
    with open(f"/proc/{app.pid}/stat") as f: v = f.read().split(); return (int(v[13]) + int(v[14])) / os.sysconf("SC_CLK_TCK")
c0 = cpu(); t0 = time.time()
while time.time() - t0 < 20: state(); time.sleep(5)
idle = cpu() - c0
e = state()["etag"]; small = len(urllib.request.urlopen(B+"/api/state?since=999999&etag="+e).read())
st = state(); up = sum(1 for a in st["managed"] if a["status"] == "update")
print(f"check #1: {t1:.1f}s ({c1['reqs']} requests, {c1['conns']} connections)")
print(f"check #2: {t1b:.1f}s ({c1b['reqs']} requests, {c1b['conns']} connections)")
print(f"update all (20 CF + 4 GitHub): {t2:.1f}s ({c2['reqs']} requests, {c2['conns']} connections); updates left: {up}")
print(f"page poll: median {statistics.median(times)*1000:.0f} ms, max {max(times)*1000:.0f} ms, payload {len(json.dumps(st))//1024} KB when something changed, {small} bytes when not")
print(f"idle CPU over 20 s: {idle:.2f} s")
app.kill()
