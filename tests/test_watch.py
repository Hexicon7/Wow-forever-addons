import os, sys, json, time, subprocess, tempfile, urllib.request
from pathlib import Path
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
sys.path.insert(0, str(Path(__file__).parent)); import fake_cf
fake_cf.start(); tmp = Path(tempfile.mkdtemp())
addons = tmp / "WoW" / "_forever_" / "Interface" / "AddOns"; addons.mkdir(parents=True)
dl = tmp / "Downloads"; dl.mkdir(); (tmp/"data").mkdir()
opened = tmp / "opened.txt"
(tmp/"fakebrowser.sh").write_text(f'#!/bin/sh\necho "$1" >> {opened}\n'); os.chmod(tmp/"fakebrowser.sh", 0o755)
(tmp/"data"/"config.json").write_text(json.dumps({"api_key": fake_cf.KEY, "addons_dir": str(addons), "downloads_dir": str(dl)}))
env = dict(os.environ, WFAM_API_BASE=fake_cf.BASE, WFAM_DATA_DIR=str(tmp/"data"), BROWSER=str(tmp/"fakebrowser.sh"))
app = subprocess.Popen([sys.executable, "/home/claude/app/WoWForeverAddons.pyw", "--no-browser", "--port", "47995"], env=env)
time.sleep(1.2); fails = 0
B = "http://127.0.0.1:47995"
def check(c, msg):
    global fails; print(("PASS " if c else "FAIL ") + msg); fails += 0 if c else 1
def post(path, body):
    return json.loads(urllib.request.urlopen(urllib.request.Request(B+path, data=json.dumps(body).encode(), headers={"X-WFAM":"1","Content-Type":"application/json"}, method="POST")).read())
def state(): return json.loads(urllib.request.urlopen(B+"/api/state").read())
def wait_idle():
    for _ in range(60):
        time.sleep(0.3)
        if not state()["job"]["running"]: return
def lp(): return next(a for a in state()["managed"] if a["key"]=="1005")
def wait_for(cond, secs=20):
    for _ in range(int(secs/0.5)):
        if cond(): return True
        time.sleep(0.5)
    return False
try:
    post("/api/add", {"text": "leatrix-plus"}); wait_idle()
    a = lp()
    check(a["status"] == "awaiting", "status awaiting after Install: " + a["status"])
    time.sleep(0.5)
    urls = opened.read_text().split() if opened.exists() else []
    check(len(urls) == 1 and urls[0].endswith("/leatrix-plus/download/%d" % a["latest_file_id"]), f"download page opened: {urls}")
    # unrelated zip + an in-progress browser download that then completes
    (dl/"holiday-photos.zip").write_bytes(b"PK\x05\x06" + b"\0"*18)
    data = fake_cf.ZIPS[a["latest_file_id"]]
    part = dl/"Leatrix_Plus-12.0.10-forever.zip.crdownload"; part.write_bytes(data[:100]); time.sleep(2.5)
    part.write_bytes(data); part.rename(dl/"Leatrix_Plus-12.0.10-forever.zip")
    check(wait_for(lambda: lp()["status"] == "ok"), "auto-installed from Downloads: " + lp()["status"])
    check((addons/"Leatrix_Plus").is_dir(), "folder present")
    check(not (dl/"Leatrix_Plus-12.0.10-forever.zip").exists(), "zip deleted from Downloads")
    check((dl/"holiday-photos.zip").exists(), "unrelated zip untouched")
    check(lp()["installed_version"] == "12.0.10-forever", "version recorded: " + str(lp()["installed_version"]))
    # update flow
    urllib.request.urlopen(urllib.request.Request(fake_cf.BASE+"/admin/publish", data=json.dumps({"mod":1005,"version":"12.0.11-forever","entries":{"Leatrix_Plus/Leatrix_Plus.toc": fake_cf.toc("LP","12.0.11")}}).encode(), method="POST"))
    post("/api/update_all", {}); wait_idle()
    check(lp()["status"] == "awaiting", "Update all -> awaiting download: " + lp()["status"])
    fid = max(f for f, v in fake_cf.FILES.items() if v["modId"] == 1005)
    (dl/"Leatrix_Plus-12.0.11-forever (1).zip").write_bytes(fake_cf.ZIPS[fid])
    check(wait_for(lambda: lp()["status"] == "ok" and lp()["installed_version"] == "12.0.11-forever"), "update auto-installed (renamed file)")
    # downloaded BEFORE clicking: already in Downloads, then user clicks install
    post("/api/remove", {"key": "1005"}); wait_idle()
    (dl/"lp.zip").write_bytes(fake_cf.ZIPS[fid]); time.sleep(5)
    check((dl/"lp.zip").exists(), "nothing happens while not wanted")
    post("/api/add", {"text": "leatrix-plus"}); wait_idle()
    ok = wait_for(lambda: lp()["status"] == "ok")
    if not ok:
        print(lp()); print([l["msg"][:200] for l in state()["logs"][-8:]]); print(os.listdir(dl))
    check(ok, "zip already in Downloads is picked up after clicking Install")
    # cancel
    post("/api/remove", {"key": "1005"}); wait_idle()
    post("/api/add", {"text": "leatrix-plus"}); wait_idle()
    post("/api/cancel_wait", {"key": "1005"}); time.sleep(0.5)
    check(lp()["status"] == "manual", "cancel -> manual: " + lp()["status"])
finally:
    app.kill()
print("FAILURES:", fails)
