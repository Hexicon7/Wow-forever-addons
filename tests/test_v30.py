import os, sys, json, time, subprocess, tempfile, urllib.request, io, base64
from pathlib import Path
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
sys.path.insert(0, str(Path(__file__).parent)); import fake_cf
fake_cf.start(); T = fake_cf.toc
import importlib.machinery, importlib.util
from wfam_load import W, mod
S_ = mod("system")
os.environ["WFAM_SCRIPT"] = "/home/claude/app/WoWForeverAddons.pyw"
fails = 0
def check(c, m):
    global fails; print(("PASS " if c else "FAIL ") + m); fails += 0 if c else 1
from PIL import Image
im = Image.open(io.BytesIO(W.make_icon_png(256))); im.load()
check(im.size == (256, 256) and im.mode == "RGBA", "icon PNG decodes (256x256 RGBA)")
check(im.getpixel((0, 0))[3] == 0 and im.getpixel((128, 128))[3] == 255, "rounded transparent corners")
check(im.getpixel((int(256*0.40), int(256*0.5)))[:3] == (0x1B, 0x14, 0x06), "dark F drawn")
im.save("/home/claude/shots/icon-256.png")
ico = Image.open(io.BytesIO(W.make_ico())); check(set(ico.info.get("sizes", [])) >= {(16, 16), (32, 32), (48, 48), (256, 256)}, f"ICO has all sizes: {sorted(ico.info.get('sizes', []))}")
args = W.app_window_args("C:/Edge/msedge.exe", "http://127.0.0.1:47811/")
check(args[1] == "--app=http://127.0.0.1:47811/" and any(a.startswith("--user-data-dir=") for a in args), "window launched in app mode with its own profile")
# shortcut script (simulate Windows)
captured = {}

class FakeRun:
    def __call__(self, cmd, **kw):
        captured["cmd"] = cmd
        class R: returncode = 0; stdout = ""; stderr = ""
        return R()
real_run = S_.subprocess.run; S_.subprocess.run = FakeRun()
real_os = S_.os
class FakeOs:
    name = "nt"
    def __getattr__(self, k): return getattr(real_os, k)
S_.os = FakeOs()
try:
    W.create_shortcuts()
finally:
    S_.os = real_os; S_.subprocess.run = real_run
ps = base64.b64decode(captured["cmd"][-1]).decode("utf-16-le")
check("GetFolderPath('Desktop')" in ps and "GetFolderPath('Programs')" in ps and "WoW Forever Addons.lnk" in ps and "IconLocation" in ps and "WoWForeverAddons.pyw" in ps, "shortcut script targets desktop + Start menu with icon")
print(ps)
# characters + quit on window close
tmp = Path(tempfile.mkdtemp())
fo = tmp/"WoW"/"_forever_"; a = fo/"Interface"/"AddOns"; a.mkdir(parents=True)
for name, extra in (("Questie", ""), ("Leatrix_Plus", ""), ("OffByDefault", "## DefaultState: disabled\n"), ("Bagnon", ""), ("Bagnon_Config", "")):
    (a/name).mkdir(); (a/name/f"{name}.toc").write_text(T(name, "1") + extra)
acc = fo/"WTF"/"Account"/"ACCOUNT1"; (acc/"SavedVariables").mkdir(parents=True)
for realm, ch, txt in (("Firemaw", "Magechar", "Questie: enabled\nLeatrix_Plus: disabled\n"), ("Firemaw", "Palachar", "Questie: disabled\nOffByDefault: enabled\n"), ("Golemagg", "Roguechar", None)):
    d = acc/realm/ch; (d/"SavedVariables").mkdir(parents=True)
    if txt: (d/"AddOns.txt").write_text(txt)
(tmp/"d").mkdir(); (tmp/"d"/"config.json").write_text(json.dumps({"addons_dir": str(a)}))
env = dict(os.environ, WFAM_API_BASE=fake_cf.BASE, WFAM_DATA_DIR=str(tmp/"d"), WFAM_APP_BROWSER="")
p = subprocess.Popen([sys.executable, "/home/claude/app/WoWForeverAddons.pyw", "--no-browser", "--port", "47984"], env=env, stdout=subprocess.DEVNULL)
time.sleep(1.2); B = "http://127.0.0.1:47984"
try:
    ch = json.loads(urllib.request.urlopen(B+"/api/characters").read())
    names = [c["name"] for c in ch["characters"]]
    check(names == ["Magechar", "Palachar", "Roguechar"], f"characters found across realms: {names}")
    rows = {r["folder"]: r for r in ch["addons"]}
    check(rows["Questie"]["states"] == ["enabled", "disabled", None] and rows["Questie"]["on"] == [True, False, True], "Questie on/off/default per character")
    check(rows["OffByDefault"]["on"] == [False, True, False], "DefaultState: disabled respected")
    check(not ch["characters"][2]["has_file"], "character without AddOns.txt noted")
    fav = urllib.request.urlopen(B+"/favicon.png").read(); check(fav[:8] == b"\x89PNG\r\n\x1a\n", "favicon served")
    # bye without our header but with matching Origin is accepted; foreign Origin refused
    r = urllib.request.Request(B+"/api/bye", data=b"", headers={"Origin": "https://evil.example"}, method="POST")
    try: urllib.request.urlopen(r); check(False, "foreign bye refused")
    except urllib.error.HTTPError as e: check(e.code == 403, "foreign bye refused")
    urllib.request.urlopen(urllib.request.Request(B+"/api/bye", data=b"", headers={"Origin": B}, method="POST")).read()
    time.sleep(2); urllib.request.urlopen(B+"/api/state").read()     # a reload reconnects -> keeps running
    time.sleep(7.5); check(p.poll() is None, "reload after bye keeps the app running")
    urllib.request.urlopen(urllib.request.Request(B+"/api/bye", data=b"", headers={"Origin": B}, method="POST")).read()
    time.sleep(9); check(p.poll() is not None, "closing the window quits the app within seconds")
finally:
    if p.poll() is None: p.kill()
print("FAILURES:", fails)
