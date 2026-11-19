import os, sys, json, time, subprocess, tempfile, urllib.request
from pathlib import Path
os.environ["NO_PROXY"]="127.0.0.1,localhost"
sys.path.insert(0, str(Path(__file__).parent)); import fake_cf
fake_cf.start(); tmp=Path(tempfile.mkdtemp())
env=dict(os.environ, WFAM_API_BASE=fake_cf.BASE, WFAM_DATA_DIR=str(tmp))
app=subprocess.Popen([sys.executable,"/home/claude/app/WoWForeverAddons.pyw","--no-browser","--port","47998"],env=env); time.sleep(1)
def post(path, body):
    r=urllib.request.Request("http://127.0.0.1:47998"+path,data=json.dumps(body).encode(),headers={"X-WFAM":"1","Content-Type":"application/json"},method="POST")
    try: return json.loads(urllib.request.urlopen(r).read())
    except urllib.error.HTTPError as e: return json.loads(e.read())
try:
    for k in ["wrong-key-but-long-enough-123", "abc", "cf-block-key-xxxxxxxxxxxxxx", ' "test-key-1234"\n', fake_cf.KEY]:
        print(repr(k[:12]), "->", post("/api/test_key",{"api_key":k}))
    post("/api/settings",{"api_key":'  "%s" \n' % fake_cf.KEY})
    print("saved key cleaned:", json.load(open(tmp/"config.json"))["api_key"] == fake_cf.KEY)
finally: app.kill()
