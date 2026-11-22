import os, sys, json, time, subprocess, tempfile, urllib.request
from pathlib import Path
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
tmp = Path(tempfile.mkdtemp()); (tmp/"d").mkdir(); opened = tmp/"opened.txt"
(tmp/"b.sh").write_text(f'#!/bin/sh\necho "$1" >> {opened}\n'); os.chmod(tmp/"b.sh", 0o755)
env = dict(os.environ, WFAM_DATA_DIR=str(tmp/"d"), BROWSER=str(tmp/"b.sh"), WFAM_APP_BROWSER="")
p = subprocess.Popen([sys.executable, "/home/claude/app/WoWForeverAddons.pyw", "--no-browser", "--port", "47993"], env=env, stdout=subprocess.DEVNULL)
time.sleep(1.2); B = "http://127.0.0.1:47993"; fails = 0
def check(c, m):
    global fails; print(("PASS " if c else "FAIL ") + m); fails += 0 if c else 1
def post(path, b, hdr=True):
    h = {"Content-Type": "application/json"}
    if hdr: h["X-WFAM"] = "1"
    try: return json.loads(urllib.request.urlopen(urllib.request.Request(B+path, data=json.dumps(b).encode(), headers=h, method="POST")).read())
    except urllib.error.HTTPError as e: return {"status": e.code, **json.loads(e.read() or b"{}")}
try:
    r = post("/api/open_url", {"url": "https://www.curseforge.com/wow/addons/questie"}); time.sleep(0.5)
    check(r.get("ok") and opened.exists() and "curseforge.com/wow/addons/questie" in opened.read_text(), "https link opened in the web browser")
    for bad in ("file:///C:/Windows/System32/calc.exe", "javascript:alert(1)", "ms-settings:", "https://"):
        r = post("/api/open_url", {"url": bad}); check(not r.get("ok"), f"refused: {bad}")
    r = post("/api/open_url", {"url": "https://example.com"}, hdr=False); check(not r.get("ok"), "refused without the app's header (other websites can't use it)")
    html = urllib.request.urlopen(B+"/app.js").read().decode()
    check("window.chrome.webview" in html and "/api/open_url" in html, "page sends outside links to the browser inside the app window")
    pg = urllib.request.urlopen(B+"/api/ping").read().decode(); check('"version"' in pg and 'WoW Forever Addon Manager' in pg, "ping reports the version: " + pg)
finally:
    post("/api/quit", {}); p.wait(5)
print("FAILURES:", fails)
