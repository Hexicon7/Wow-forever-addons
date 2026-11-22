"""Put dist/WoWForeverAddons-Setup-<version>.exe into updates/ with updates/latest.json.
Installed programs read updates/latest.json on the main branch and offer "Update now".
Usage: python3 tools/publish_update.py "What changed"   (then commit and push)"""
import hashlib, json, re, sys
from pathlib import Path
root = Path(__file__).resolve().parent.parent
ver = re.search(r'APP_VERSION = "([^"]+)"', (root / "app/wfam/config.py").read_text(encoding="utf-8")).group(1)
exe = root / "dist" / f"WoWForeverAddons-Setup-{ver}.exe"
data = exe.read_bytes()
assert data[:2] == b"MZ", "not a Windows program"
out = root / "updates"; out.mkdir(exist_ok=True)
for old in out.glob("WoWForeverAddons-Setup-*.exe"):
    old.unlink()                      # keep only the newest setup in the tree
(out / exe.name).write_bytes(data)
(out / "latest.json").write_text(json.dumps({"version": ver, "file": exe.name, "sha256": hashlib.sha256(data).hexdigest(),
                                             "size": len(data), "notes": sys.argv[1] if len(sys.argv) > 1 else ""},
                                            indent=2) + "\n", encoding="utf-8")
print("published", exe.name)
