import os, sys, json, time, subprocess, tempfile, urllib.request, zipfile, io
from pathlib import Path
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
sys.path.insert(0, str(Path(__file__).parent)); import fake_cf
fake_cf.start(); tmp = Path(tempfile.mkdtemp())
addons = tmp/"WoW"/"_forever_"/"Interface"/"AddOns"; addons.mkdir(parents=True)
dl = tmp/"Downloads"; dl.mkdir(); (tmp/"data").mkdir()
(tmp/"data"/"config.json").write_text(json.dumps({"api_key": fake_cf.KEY, "addons_dir": str(addons), "downloads_dir": str(dl)}))
env = dict(os.environ, WFAM_API_BASE=fake_cf.BASE, WFAM_DATA_DIR=str(tmp/"data"), BROWSER="true")
app = subprocess.Popen([sys.executable, "/home/claude/app/WoWForeverAddons.pyw", "--no-browser", "--port", "47994"], env=env)
time.sleep(1.2); fails = 0; B = "http://127.0.0.1:47994"
def check(c, m):
    global fails; print(("PASS " if c else "FAIL ")+m); fails += 0 if c else 1
def post(path, body, raw=None, hdr=None):
    h = {"X-WFAM":"1"}; h.update(hdr or {})
    if raw is None: h["Content-Type"]="application/json"; raw=json.dumps(body).encode()
    return json.loads(urllib.request.urlopen(urllib.request.Request(B+path, data=raw, headers=h, method="POST")).read())
def state(): return json.loads(urllib.request.urlopen(B+"/api/state").read())
def idle():
    for _ in range(80):
        time.sleep(0.3)
        if not state()["job"]["running"]: return
def lp(): return next(a for a in state()["managed"] if a["key"]=="1005")
def wait_for(c, secs=20):
    for _ in range(int(secs/0.5)):
        if c(): return True
        time.sleep(0.5)
    return False
def extract(data, dest):
    with zipfile.ZipFile(io.BytesIO(data)) as z: z.extractall(dest)
try:
    post("/api/add", {"text": "leatrix-plus"}); idle()
    fid = lp()["latest_file_id"]; data = fake_cf.ZIPS[fid]; name = fake_cf.FILES[fid]["fileName"]; stem = name[:-4]
    extract(data, dl/stem)                                        # "Extract all" style
    extract(data, dl/"decoy"); (dl/"decoy").rename(dl/"Leatrix_Plus-other")
    (dl/name).write_bytes(data)
    check(wait_for(lambda: lp()["status"]=="ok"), "auto install")
    time.sleep(0.5)
    check(not (dl/name).exists(), "zip removed")
    check(not (dl/stem).exists(), "extracted copy removed")
    check((dl/"Leatrix_Plus-other").exists(), "unrelated folder kept")
    # manual Install zip... with a copy in Downloads + 'Extract here' folder (top folder name)
    post("/api/remove", {"key":"1005"}); idle()
    post("/api/cancel_wait", {"key":"1005"}) if any(a["key"]=="1005" for a in state()["managed"]) else None
    z = fake_cf.make_zip({"MyUI/MyUI.toc": fake_cf.toc("My UI","1"), "MyUI/a.lua": "x"})
    (dl/"MyUI-1.0.zip").write_bytes(z); extract(z, dl)            # creates Downloads/MyUI
    (dl/"MyUI-copy").mkdir(); extract(z, dl/"MyUI-copy")
    (dl/"MyUI-copy"/"MyUI"/"a.lua").write_text("changed")         # modified -> keep
    post("/api/install_zip", None, raw=z, hdr={"X-Filename":"MyUI-1.0.zip","Content-Type":"application/octet-stream"}); idle()
    check((addons/"MyUI").is_dir(), "manual zip installed")
    check(not (dl/"MyUI-1.0.zip").exists() and not (dl/"MyUI").exists(), "manual: zip and 'extract here' folder removed")
    check((dl/"MyUI-copy").exists(), "modified copy kept")
    # setting off
    post("/api/settings", {"delete_after_install": False})
    z2 = fake_cf.make_zip({"Other/Other.toc": fake_cf.toc("Other","1")})
    (dl/"Other.zip").write_bytes(z2)
    post("/api/install_zip", None, raw=z2, hdr={"X-Filename":"Other.zip","Content-Type":"application/octet-stream"}); idle()
    check((dl/"Other.zip").exists() and (addons/"Other").is_dir(), "kept when setting is off")
    print([l["msg"] for l in state()["logs"] if "Cleaned" in l["msg"]])
finally:
    app.kill()
print("FAILURES:", fails)
