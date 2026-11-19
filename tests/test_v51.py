# 3.11.4: only 3 WTF backups (the old default 10 is changed once), one earlier version per addon, leftovers removed.
import os, sys, json, time, subprocess, tempfile, urllib.request, zipfile
from pathlib import Path
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
sys.path.insert(0, str(Path(__file__).parent)); import fake_cf
fake_cf.start(); T = fake_cf.toc; tmp = Path(tempfile.mkdtemp()); data = tmp/"data"; data.mkdir()
forever = tmp/"World of Warcraft"/"_forever_"; addons = forever/"Interface"/"AddOns"; addons.mkdir(parents=True)
(forever/"WTF"/"Account"/"A").mkdir(parents=True); (forever/"WTF"/"Account"/"A"/"x.lua").write_text("x=1")
bk = data/"backups"; bk.mkdir()
for i in range(7):
    with zipfile.ZipFile(bk/f"WTF-2026111{i}-120000.zip", "w") as z: z.writestr("WTF/a.txt", "x" * 1000)
(data/"versions"/"9999").mkdir(parents=True); (data/"versions"/"9999"/"old.zip").write_bytes(b"x" * 2_000_000)   # addon long gone
(data/"config.json").write_text(json.dumps({"api_key": fake_cf.KEY, "addons_dir": str(addons), "auto_check": False, "backup_keep": 10}))
env = dict(os.environ, WFAM_API_BASE=fake_cf.BASE, WFAM_DATA_DIR=str(data))
APP = os.environ.get("WFAM_APP", "/home/claude/app/WoWForeverAddons.pyw")
def start(): 
    p = subprocess.Popen([sys.executable, APP, "--no-browser", "--port", "47994"], env=env, stdout=subprocess.DEVNULL); time.sleep(1.5); return p
fails = 0; B = "http://127.0.0.1:47994"
def check(c, msg):
    global fails; print(("PASS " if c else "FAIL ") + msg); fails += 0 if c else 1
def post(p, b=None):
    return json.loads(urllib.request.urlopen(urllib.request.Request(B+p, data=json.dumps(b or {}).encode(), headers={"X-WFAM":"1","Content-Type":"application/json"}, method="POST")).read())
def st(): return json.loads(urllib.request.urlopen(B+"/api/state").read())
def idle():
    time.sleep(0.3)
    while st()["job"]["running"]: time.sleep(0.2)
app = start()
try:
    left = sorted(x.name for x in bk.glob("WTF-*.zip"))
    check(len(left) == 3 and left[-1] == "WTF-20261116-120000.zip", f"old backups trimmed to the newest 3: {left}")
    check(st()["config"]["backup_keep"] == 3, "the old default (10) became 3")
    check(not (data/"versions"/"9999").exists(), "leftovers from a removed addon are deleted")
    post("/api/add", {"text": "questie"}); idle()
    for v in ("v12.7.0", "v12.8.0", "v12.9.0"):
        fake_cf.add_file(1001, v, {"Questie/Questie.toc": T("Questie", v[1:])})
        post("/api/check"); idle(); post("/api/update_all"); idle(); time.sleep(0.1)
    q = next(a for a in st()["managed"] if a["key"] == "1001")
    zips = list((data/"versions"/"1001").glob("*.zip"))
    check(q["installed_version"] == "v12.9.0" and q["previous_version"] == "v12.8.0" and len(zips) == 1, f"one earlier version kept ({len(zips)} zip)")
    post("/api/revert", {"key": "1001"}); idle()
    q = next(a for a in st()["managed"] if a["key"] == "1001")
    check(q["installed_version"] == "v12.8.0" and q["previous_version"] == "v12.9.0", "Revert still works and can switch back")
    check(len(list(bk.glob("WTF-*.zip"))) <= 3, "never more than 3 backups after updating")
    post("/api/settings", {"backup_keep": 5}); app.terminate(); app.wait(); app = start()
    check(st()["config"]["backup_keep"] == 5, "a number you chose yourself is kept")
finally:
    app.terminate()
print("FAILURES:", fails); sys.exit(1 if fails else 0)
