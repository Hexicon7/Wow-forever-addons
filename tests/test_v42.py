# Self-update published in the repository itself (updates/latest.json), next to or instead of GitHub releases.
import os, sys, json, time, hashlib, subprocess, tempfile, urllib.request
from pathlib import Path
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
sys.path.insert(0, str(Path(__file__).parent)); import fake_cf
fake_cf.start(); tmp = Path(tempfile.mkdtemp()); (tmp/"data").mkdir()
addons = tmp/"World of Warcraft"/"_forever_"/"Interface"/"AddOns"; addons.mkdir(parents=True)
APP = os.environ.get("WFAM_APP", "/home/claude/app/WoWForeverAddons.pyw")
fails = 0
def check(c, msg):
    global fails; print(("PASS " if c else "FAIL ") + msg); fails += 0 if c else 1
def publish(repo, ver, **over):
    exe = b"MZ" + os.urandom(2000)
    m = {"version": ver, "file": f"WoWForeverAddons-Setup-{ver}.exe", "sha256": hashlib.sha256(exe).hexdigest(),
         "size": len(exe), "notes": f"notes {ver}"}; m.update(over)
    fake_cf.RAW[f"{repo}/main/updates/latest.json"] = json.dumps(m).encode()
    fake_cf.RAW[f"{repo}/main/updates/{m['file']}"] = exe
def run(repo, port):
    (tmp/"data"/"config.json").write_text(json.dumps({"api_key": fake_cf.KEY, "addons_dir": str(addons), "auto_check": False, "update_repo": repo}))
    env = dict(os.environ, WFAM_API_BASE=fake_cf.BASE, WFAM_DATA_DIR=str(tmp/"data"), WFAM_GITHUB_API=fake_cf.BASE + "/gh",
               WFAM_GITHUB_RAW=fake_cf.BASE + "/raw")
    app = subprocess.Popen([sys.executable, APP, "--no-browser", "--port", str(port)], env=env); time.sleep(1.2)
    B = f"http://127.0.0.1:{port}"
    try:
        r = json.loads(urllib.request.urlopen(urllib.request.Request(B + "/api/check_self_update", data=b"{}",
                       headers={"X-WFAM": "1", "Content-Type": "application/json"}, method="POST")).read())
        time.sleep(4)
        st = json.loads(urllib.request.urlopen(B + "/api/state").read())
        return r, st.get("self_update")
    finally:
        app.terminate(); app.wait()

publish("fake/pushonly", "9.9.10")
r, su = run("fake/pushonly", 47991)
check(r.get("update") is True and su and su.get("version") == "9.9.10", f"update found from updates/latest.json without any release: {su}")
url = (su or {}).get("asset", {}).get("browser_download_url", "") if isinstance((su or {}).get("asset"), dict) else ""
check(url.endswith("/raw/fake/pushonly/main/updates/WoWForeverAddons-Setup-9.9.10.exe") or "asset" not in (su or {}),
      "points at the setup in the repo: " + url)
# the download is checked against the manifest's sha256
import importlib.machinery, importlib.util
os.environ.update(WFAM_GITHUB_RAW=fake_cf.BASE + "/raw", WFAM_DATA_DIR=str(tmp/"data"))
ld = importlib.machinery.SourceFileLoader("wfam", APP); W = importlib.util.module_from_spec(importlib.util.spec_from_loader("wfam", ld)); ld.exec_module(W)
exe = fake_cf.RAW["fake/pushonly/main/updates/WoWForeverAddons-Setup-9.9.10.exe"]
asset = {"browser_download_url": fake_cf.BASE + "/raw/fake/pushonly/main/updates/WoWForeverAddons-Setup-9.9.10.exe",
         "digest": "sha256:" + hashlib.sha256(exe).hexdigest()}
W.GitHub().download(asset, tmp/"ok.exe"); check((tmp/"ok.exe").read_bytes() == exe, "setup downloaded from the repo")
try:
    W.GitHub().download(dict(asset, digest="sha256:" + "0" * 64), tmp/"bad.exe"); check(False, "tampered download refused")
except W.UserError:
    check(True, "tampered download refused")

fake_cf.gh_release("fake/both", "v9.9.9", {"WoWForeverAddons-Setup-9.9.9.exe": {"x.txt": "y"}})
publish("fake/both", "9.9.10")
r, su = run("fake/both", 47992)
check(su and su.get("version") == "9.9.10", f"newest of release and repo wins: {su and su.get('version')}")

publish("fake/old", "1.0.0")
r, su = run("fake/old", 47993)
check(not su, f"older version in the repo is ignored: {su}")

publish("fake/bad", "9.9.10", file="evil.exe")
r, su = run("fake/bad", 47994)
check(not su, "manifest with an unexpected file name is ignored")
publish("fake/bad2", "9.9.10", sha256="nothex")
r, su = run("fake/bad2", 47995)
check(not su, "manifest without a valid checksum is ignored")
r, su = run("fake/nothing", 47996)
check(not su and r.get("update") is not True, "no releases and no manifest: nothing offered, no crash")
print("FAILURES:", fails); sys.exit(1 if fails else 0)
