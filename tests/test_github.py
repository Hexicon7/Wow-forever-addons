import os, sys, json, time, subprocess, tempfile, urllib.request
from pathlib import Path
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
sys.path.insert(0, str(Path(__file__).parent)); import fake_cf
fake_cf.start(); tmp = Path(tempfile.mkdtemp())
T = fake_cf.toc
# Leatrix: GitHub release with release.json; classic + forever zips
fake_cf.gh_release("fake/leatrix-plus", "12.0.9", {"Leatrix_Plus-12.0.9.zip": {"Leatrix_Plus/Leatrix_Plus.toc": T("LP", "12.0.9")}})
fake_cf.gh_release("fake/leatrix-plus", "12.0.10", {
    "Leatrix_Plus-12.0.10.zip": {"Leatrix_Plus/Leatrix_Plus.toc": T("LP", "12.0.10")},
    "Leatrix_Plus-12.0.10-classic.zip": {"Leatrix_Plus/Leatrix_Plus.toc": T("LP", "12.0.10", "11508")}},
    release_json={"releases": [
        {"filename": "Leatrix_Plus-12.0.10-classic.zip", "nolib": False, "metadata": [{"flavor": "vanilla", "interface": 11508}]},
        {"filename": "Leatrix_Plus-12.0.10.zip", "nolib": False, "metadata": [{"flavor": "mainline", "interface": 120007}, {"flavor": "forever", "interface": 16001}]}]})
def site_only(mid, slug, repo):
    fake_cf.add_mod(mid, slug, slug.title(), allow=False)
    fake_cf.MODS[mid]["links"]["sourceUrl"] = f"https://github.com/{repo}/tree/main"
    fake_cf.add_file(mid, "1.0", {f"A{mid}/A{mid}.toc": T("x", "1")})
site_only(1011, "classic-gh", "fake/classic-gh")
fake_cf.gh_release("fake/classic-gh", "v1", {"ClassicGh.zip": {"A1011/A1011.toc": T("x", "1", "11508")}})
site_only(1012, "limited", "fake/ratelimited")
site_only(1013, "baddigest", "fake/baddigest")
fake_cf.gh_release("fake/baddigest", "v1", {"A1013.zip": {"A1013/A1013.toc": T("x", "1")}}, bad_digest=True)
site_only(1014, "multi-toc", "fake/multi-toc")   # one zip for all clients, Forever via _Camelot.toc
fake_cf.gh_release("fake/multi-toc", "v3", {"MultiToc-v3.zip": {"A1014/A1014.toc": T("x","3","20506"), "A1014/A1014_Camelot.toc": T("x","3")}})

addons = tmp/"WoW"/"_forever_"/"Interface"/"AddOns"; addons.mkdir(parents=True)
dl = tmp/"Downloads"; dl.mkdir(); (tmp/"data").mkdir(); opened = tmp/"opened.txt"
(tmp/"b.sh").write_text(f'#!/bin/sh\necho "$1" >> {opened}\n'); os.chmod(tmp/"b.sh", 0o755)
(tmp/"data"/"config.json").write_text(json.dumps({"api_key": fake_cf.KEY, "addons_dir": str(addons), "downloads_dir": str(dl)}))
env = dict(os.environ, WFAM_API_BASE=fake_cf.BASE, WFAM_GITHUB_API=fake_cf.BASE+"/gh", WFAM_DATA_DIR=str(tmp/"data"), BROWSER=str(tmp/"b.sh"))
app = subprocess.Popen([sys.executable, "/home/claude/app/WoWForeverAddons.pyw", "--no-browser", "--port", "47993"], env=env)
time.sleep(1.2); fails = 0; B = "http://127.0.0.1:47993"
def check(c, m):
    global fails; print(("PASS " if c else "FAIL ")+m); fails += 0 if c else 1
def post(path, body): return json.loads(urllib.request.urlopen(urllib.request.Request(B+path, data=json.dumps(body).encode(), headers={"X-WFAM":"1","Content-Type":"application/json"}, method="POST")).read())
def state(): return json.loads(urllib.request.urlopen(B+"/api/state").read())
def idle():
    for _ in range(80):
        time.sleep(0.3)
        if not state()["job"]["running"]: return
def rec(k): return next(a for a in state()["managed"] if a["key"]==str(k))
def opened_n(): return len(opened.read_text().split()) if opened.exists() else 0
try:
    post("/api/add", {"text": "leatrix-plus"}); idle()
    a = rec(1005)
    check(a["status"]=="ok" and a["source"]=="github" and a["installed_version"]=="12.0.10", f"Leatrix from GitHub: {a['status']} {a['source']} {a['installed_version']}")
    check("16001" in (addons/"Leatrix_Plus"/"Leatrix_Plus.toc").read_text(), "Forever zip chosen via release.json (not classic)")
    check(opened_n()==0, "no browser page opened")
    # new prerelease (ignored) and new release (update)
    fake_cf.gh_release("fake/leatrix-plus", "12.0.11", {"Leatrix_Plus-12.0.11.zip": {"Leatrix_Plus/Leatrix_Plus.toc": T("LP","12.0.11")}})
    fake_cf.gh_release("fake/leatrix-plus", "12.1.0-beta", {"Leatrix_Plus-12.1.0-beta.zip": {"Leatrix_Plus/Leatrix_Plus.toc": T("LP","12.1.0b")}}, prerelease=True)
    post("/api/check", {}); idle()
    a = rec(1005); check(a["status"]=="update" and a["latest_version"]=="12.0.11", f"update found, prerelease skipped: {a['status']} {a['latest_version']}")
    post("/api/update_all", {}); idle()
    a = rec(1005); check(a["status"]=="ok" and a["installed_version"]=="12.0.11" and "12.0.11" in (addons/"Leatrix_Plus"/"Leatrix_Plus.toc").read_text(), "Update all updated from GitHub")
    check(opened_n()==0, "still no browser page")
    # one zip for all clients with a _Camelot.toc
    post("/api/add", {"text": "multi-toc"}); idle()
    check(rec(1014)["source"]=="github" and rec(1014)["status"]=="ok", "multi-client zip accepted (via _Camelot.toc)")
    # fallbacks
    post("/api/add", {"text": "classic-gh\nlimited\nbaddigest"}); idle()
    st = {k: rec(k)["status"] for k in (1011, 1012, 1013)}
    check(all(v=="awaiting" for v in st.values()) and opened_n()==3, f"fallback to download page: {st}, opened {opened_n()}")
    logs = " | ".join(l["msg"] for l in state()["logs"])
    check("isn't built for WoW Forever" in logs, "classic-only GitHub build rejected")
    check("hourly limit" in logs, "rate limit explained")
    check("corrupted" in logs, "bad checksum rejected")
    check(not (addons/"A1011").exists() and not (addons/"A1013").exists(), "nothing installed from bad GitHub builds")
    # setting off -> no GitHub
    post("/api/remove", {"key": "1014"}); idle()
    post("/api/settings", {"use_github": False}); post("/api/add", {"text": "multi-toc"}); idle()
    check(rec(1014)["status"]=="awaiting", "GitHub skipped when turned off")
finally:
    app.kill()
print("FAILURES:", fails)
