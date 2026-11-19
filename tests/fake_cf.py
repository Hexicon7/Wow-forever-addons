"""Minimal fake of the CurseForge Core API v1 for offline testing."""
import hashlib, io, json, sys, threading, time, zipfile, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

KEY = "test-key-1234"
FOREVER, FOREVER_BETA, CLASSIC, RETAIL = 90002, 90001, 67408, 517
BASE = None
MODS, FILES, ZIPS = {}, {}, {}
LOG = []
_next_fid = [7000000]


def make_zip(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in entries.items():
            z.writestr(name, data)
    return buf.getvalue()


def toc(title, version, iface="16001", pid=None):
    s = f"## Interface: {iface}\n## Title: {title}\n## Version: {version}\n"
    if pid:
        s += f"## X-Curse-Project-ID: {pid}\n"
    return s + "core.lua\n"


CATS = [{"id": 1001 + i, "name": n, "isClass": False} for i, n in
        enumerate(["Bags & Inventory", "Boss Encounters", "Quests & Leveling", "Auction & Economy", "Unit Frames"])]


def add_mod(mid, slug, name, allow=True):
    MODS[mid] = {"id": mid, "gameId": 1, "name": name, "slug": slug, "summary": f"{name} summary",
                 "links": {"websiteUrl": f"https://www.curseforge.com/wow/addons/{slug}"},
                 "authors": [{"name": "Author" + str(mid)}], "logo": None,
                 "allowModDistribution": allow, "latestFilesIndexes": [],
                 "downloadCount": 1000 * (mid % 997) + 7, "dateModified": f"2026-11-{mid % 28 + 1:02d}T08:00:00Z",
                 "categories": [CATS[mid % len(CATS)]]}


_APP = []


def app_module():
    if not _APP:
        import importlib.machinery, importlib.util
        l = importlib.machinery.SourceFileLoader("wfam_for_fake", "/home/claude/app/WoWForeverAddons.pyw")
        sp = importlib.util.spec_from_loader("wfam_for_fake", l); m = importlib.util.module_from_spec(sp); l.exec_module(m)
        _APP.append(m)
    return _APP[0]


def modules_of(data):
    import tempfile, pathlib
    W = app_module()
    d = pathlib.Path(tempfile.mkdtemp())
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        z.extractall(d)
    mods = []
    for folder in sorted(x for x in d.iterdir() if x.is_dir()):
        fps = sorted(W.folder_fingerprints(folder))
        mods.append({"name": folder.name, "fingerprint": fps[0] if fps else 0})
    return mods


def add_file(mid, version, entries, type_id=FOREVER, release=1, deps=(), fname=None, date=None):
    _next_fid[0] += 1
    fid = _next_fid[0]
    data = make_zip(entries)
    ZIPS[fid] = data
    fname = fname or f"{MODS[mid]['slug']}-{version}.zip"
    allow = MODS[mid]["allowModDistribution"]
    f = {"id": fid, "modId": mid, "gameId": 1, "isAvailable": True, "displayName": version, "fileName": fname,
         "releaseType": release, "fileDate": date or (__import__("datetime").datetime(2026, 11, 1) + __import__("datetime").timedelta(minutes=fid - 7000000)).strftime("%Y-%m-%dT%H:%M:%SZ"),
         "fileLength": len(data),
         "downloadUrl": f"{BASE}/cdn/{fid}/{fname}" if allow else None,
         "hashes": [{"value": hashlib.sha1(data).hexdigest(), "algo": 1}],
         "dependencies": [{"modId": d, "relationType": 3} for d in deps],
         "sortableGameVersions": [{"gameVersionName": "1.60.1", "gameVersion": "1.60.1", "gameVersionTypeId": type_id}],
         "gameVersions": ["1.60.1"], "modules": modules_of(data)}
    FILES[fid] = f
    MODS[mid]["latestFilesIndexes"].append({"gameVersion": "1.60.1", "fileId": fid, "filename": fname,
                                            "releaseType": release, "gameVersionTypeId": type_id})
    return fid


def seed():
    add_mod(1001, "questie", "Questie")
    add_file(1001, "v12.5.0-classic", {"Questie/Questie.toc": toc("Questie", "12.5.0", "11507")}, type_id=CLASSIC)
    add_file(1001, "v12.6.0", {"Questie/Questie.toc": toc("|cFFFFFFFFQuestie|r", "12.6.0", pid=1001),
                               "Questie/Modules/a.lua": "x"})
    add_mod(1002, "bugsack", "BugSack")
    add_file(1002, "v12.1.7", {"BugSack/BugSack.toc": toc("BugSack", "12.1.7")}, deps=[1003])
    add_mod(1003, "bug-grabber", "BugGrabber")
    add_file(1003, "v12.1.0", {"!BugGrabber/!BugGrabber.toc": toc("!BugGrabber", "12.1.0")})
    add_mod(1004, "deadly-boss-mods", "Deadly Boss Mods (DBM)")
    add_file(1004, "12.0.1", {"DBM-Core/DBM-Core.toc": toc("DBM Core", "12.0.1"),
                              "DBM-StatusBarTimers/DBM-StatusBarTimers.toc": toc("DBM Timers", "12.0.1"),
                              "README.txt": "top level file is ignored"},
             fname="DBM 12.0.1 forever.zip")
    add_mod(1005, "leatrix-plus", "Leatrix Plus", allow=False)
    add_file(1005, "12.0.10-forever", {"Leatrix_Plus/Leatrix_Plus.toc": toc("Leatrix Plus", "12.0.10")})
    MODS[1005]["links"]["sourceUrl"] = "https://github.com/fake/leatrix-plus"
    add_mod(1006, "nested-addon", "Nested Addon")
    add_file(1006, "1.0", {"Nested-1.0/Nested/Nested.toc": toc("Nested", "1.0"),
                           "Nested-1.0/Nested/x.lua": "y"})
    add_mod(1007, "evil-addon", "Evil Addon")
    add_file(1007, "6.6.6", {"Evil/Evil.toc": toc("Evil", "1"), "../../escape.txt": "pwned"})
    add_mod(1008, "classic-only", "Classic Only")
    add_file(1008, "1.0-classic", {"CO/CO.toc": toc("CO", "1")}, type_id=CLASSIC)
    add_mod(1009, "beta-only", "Beta Only")
    add_file(1009, "2.0-beta", {"BO/BO.toc": toc("BO", "2")}, release=2)
    for i in range(45):
        mid = 2000 + i
        add_mod(mid, f"bag-helper-{i}", f"Bag Helper {i}")
        add_file(mid, f"1.{i}", {f"BagHelper{i}/BagHelper{i}.toc": toc(f"Bag Helper {i}", f"1.{i}")})


GH = {}      # repo -> list of releases (newest first)
LEAKY_SEARCH = False
AURAS = {}   # wago.io WeakAuras: id -> dict
WAGO_NO_POPULAR = False   # True: behave like a search that ignores the game-version filter
RAW = {}     # "owner/repo/main/path" -> bytes (raw.githubusercontent.com)
GH_ASSETS = {}  # asset id -> bytes
_gh_id = [500]


def gh_release(repo, tag, files, release_json=None, prerelease=False, bad_digest=False):
    _gh_id[0] += 1
    rid = _gh_id[0]
    assets = []
    for name, entries in files.items():
        _gh_id[0] += 1
        data = make_zip(entries)
        GH_ASSETS[_gh_id[0]] = data
        dg = hashlib.sha256(data).hexdigest() if not bad_digest else "0" * 64
        assets.append({"id": _gh_id[0], "name": name, "size": len(data), "digest": "sha256:" + dg,
                       "browser_download_url": f"{BASE}/ghdl/{_gh_id[0]}/{name}"})
    if release_json is not None:
        _gh_id[0] += 1
        GH_ASSETS[_gh_id[0]] = json.dumps(release_json).encode()
        assets.append({"id": _gh_id[0], "name": "release.json", "browser_download_url": f"{BASE}/ghdl/{_gh_id[0]}/release.json"})
    rel = {"id": rid, "tag_name": tag, "name": tag, "draft": False, "prerelease": prerelease,
           "published_at": f"2026-11-1{rid % 9}T12:00:00Z", "html_url": f"https://github.com/{repo}/releases/tag/{tag}",
           "assets": assets}
    GH.setdefault(repo, []).insert(0, rel)
    return rel


WAGO_KEY = "test-wago-key-0000"
WAGO = {}        # id -> addon
WAGO_FILES = {}  # n -> bytes
_wago_n = [0]
WBASE = None


def aura(aid, slug, name, game="forever", categories=(), installs=0, stars=0, version=1, semver="1.0.0", encoded=None, note="", user="AuraMaker", ts=1762000000):
    AURAS[aid] = {"id": aid, "slug": slug, "name": name, "game": game, "categories": list(categories), "installs": installs,
                  "stars": stars, "version": version, "semver": semver, "encoded": encoded or ("!WA:2!" + aid + "Zz"),
                  "note": note, "user": user, "timestamp": ts, "desc": f"{name} description"}


def wago_addon(wid, slug, name, categories=(), downloads=0):
    WAGO[wid] = {"id": wid, "slug": slug, "name": name, "display_name": name, "authors": ["WagoAuthor"],
                 "website_url": f"https://addons.wago.io/addons/{slug}", "rr": {},
                 "categories": [{"id": i, "display_name": c} for i, c in enumerate(categories)], "download_count": downloads}


def wago_release(wid, label, entries, channel="stable", created=None):
    _wago_n[0] += 1
    n = _wago_n[0]
    WAGO_FILES[n] = make_zip(entries)
    WAGO[wid]["rr"][channel] = {"label": label, "created_at": created or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                "download_link": f"{WBASE}/dl/{n}/{WAGO[wid]['slug']}-{label}.zip",
                                "supported_forever_patches": ["1.60.1"]}


LATENCY = [0.0, 0.0]   # [per new connection (TLS handshake), per request]
STATS = {"conns": 0, "reqs": 0}


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    disable_nagle_algorithm = True

    def log_message(self, *a):
        pass

    def setup(self):
        super().setup()
        STATS["conns"] += 1
        if LATENCY[0]:
            time.sleep(LATENCY[0])

    def parse_request(self):
        ok = super().parse_request()
        STATS["reqs"] += 1
        if ok and LATENCY[1]:
            time.sleep(LATENCY[1])
        return ok

    def send_json(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def authed(self):
        if self.headers.get("x-api-key") == "cf-block-key-xxxxxxxxxxxxxx":
            b = b"<!DOCTYPE html><html><head><title>Attention Required! | Cloudflare</title></head><body>Sorry, you have been blocked</body></html>"
            self.send_response(403); self.send_header("Server", "cloudflare"); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
            return False
        if self.headers.get("x-api-key") != KEY:
            self.send_json(403, {"error": "bad key"})
            return False
        return True

    def do_GET(self):
        u = urllib.parse.urlsplit(self.path)
        q = urllib.parse.parse_qs(u.query)
        p = urllib.parse.unquote(u.path)
        LOG.append(("GET", p))
        if p.startswith("/wago/"):
            return self.wago_route("GET", p, q, None)
        if p == "/gh/rate_limit":
            return self.send_json(200, {"resources": {"core": {"limit": 60, "remaining": 57}}})
        if p.startswith("/gh/repos/"):
            repo = "/".join(p.split("/")[3:5])
            parts_ = p.split("/")
            if len(parts_) > 6 and parts_[5] == "releases" and parts_[6].isdigit():
                rel = next((r for r in GH.get(repo, []) if r["id"] == int(parts_[6])), None)
                return self.send_json(200 if rel else 404, rel or {})
            if repo == "fake/ratelimited":
                b = b'{"message":"API rate limit exceeded"}'
                self.send_response(403); self.send_header("X-RateLimit-Remaining", "0"); self.send_header("Content-Length", str(len(b))); self.end_headers()
                return self.wfile.write(b)
            if repo not in GH:
                return self.send_json(404, {"message": "Not Found"})
            return self.send_json(200, GH[repo])
        if p.startswith("/wagodata/"):
            if self.headers.get("Authorization") or self.headers.get("api-key") or self.headers.get("x-api-key"):
                return self.send_json(400, {"error": "a key was sent to wago.io"})
            qq = {k: v[0] for k, v in q.items()}
            if p == "/wagodata/search/es":
                if qq.get("expansion") != "forever" or qq.get("type") != "weakaura":
                    return self.send_json(400, {"error": "expected expansion=forever&type=weakaura"})
                text = qq.get("q", "")
                cat = None
                m_ = __import__("re").search(r"category:(\S+)", text)
                if m_:
                    cat = m_.group(1); text = text.replace(m_.group(0), "").strip()
                hits = [a for a in AURAS.values() if a["game"] == "forever" and (not cat or cat in a["categories"])
                        and text.lower() in a["name"].lower()]
                key = {"installs": "installs", "date": "timestamp", "stars": "stars"}.get(qq.get("sort"), "installs")
                hits.sort(key=lambda a: -a[key])
                page = int(qq.get("page", "0"))
                out = [{"id": a["id"], "slug": a["slug"], "name": a["name"], "description": a["desc"], "descriptionSanitized": a["desc"],
                        "categories": a["categories"], "installs": a["installs"], "stars": a["stars"], "userName": a["user"],
                        "versionString": a["semver"], "thumbnail": None, "timestamp": a["timestamp"], "hidden": False}
                       for a in hits[page * 20:(page + 1) * 20]]
                return self.send_json(200, {"hits": out, "total": len(hits), "index": "x", "query": text})
            if p == "/wagodata/lookup/wago":
                a = AURAS.get(qq.get("id")) or next((x for x in AURAS.values() if x["slug"] == qq.get("id")), None)
                if not a:
                    return self.send_json(404, {"error": "page_not_found"})
                return self.send_json(200, {"_id": a["id"], "name": a["name"], "slug": a["slug"], "game": a["game"],
                                            "type": "FOREVERAURAS" if a["game"] == "forever" else "WEAKAURA",
                                            "user": {"name": a["user"]},
                                            "versions": {"total": 1, "versions": [{"version": a["version"], "versionString": a["semver"],
                                                                                   "date": "2026-11-01T00:00:00Z", "changelog": {"text": a["note"], "format": "markdown"}}]}})
            if p == "/wagodata/api/raw/encoded":
                a = AURAS.get(qq.get("id")) or next((x for x in AURAS.values() if x["slug"] == qq.get("id")), None)
                if not a:
                    return self.send_json(404, {"error": "page_not_found"})
                b = a["encoded"].encode()
                self.send_response(200); self.send_header("Content-Type", "text/plain"); self.send_header("Content-Length", str(len(b))); self.end_headers()
                return self.wfile.write(b)
            return self.send_json(404, {})
        if p.startswith("/raw/"):
            data = RAW.get(p[5:])
            if data is None:
                return self.send_json(404, {"message": "404: Not Found"})
            self.send_response(200); self.send_header("Content-Length", str(len(data))); self.end_headers()
            return self.wfile.write(data)
        if p.startswith("/ghdl/"):
            if self.headers.get("x-api-key"):
                return self.send_json(400, {"error": "curseforge key leaked to github"})
            data = GH_ASSETS[int(p.split("/")[2])]
            self.send_response(200); self.send_header("Content-Length", str(len(data))); self.end_headers()
            return self.wfile.write(data)
        if not self.authed():
            return
        parts = p.strip("/").split("/")
        if p == "/v1/games/1/version-types":
            return self.send_json(200, {"data": [
                {"id": RETAIL, "gameId": 1, "name": "WoW Retail", "slug": "wow_retail"},
                {"id": CLASSIC, "gameId": 1, "name": "WoW Classic", "slug": "wow_classic"},
                {"id": FOREVER_BETA, "gameId": 1, "name": "WoW Forever Beta", "slug": "wow-forever-beta"},
                {"id": FOREVER, "gameId": 1, "name": "WoW Forever", "slug": "wow-forever"}]})
        if p == "/v1/categories":
            return self.send_json(200, {"data": [{"id": 1, "name": "Addons", "isClass": True}] + CATS})
        if p == "/v1/mods/search":
            slug = q.get("slug", [""])[0]
            if slug:
                return self.send_json(200, {"data": [m for m in MODS.values() if m["slug"] == slug], "pagination": {}})
            res = list(MODS.values())
            t = q.get("gameVersionTypeId", [None])[0]
            if t and not LEAKY_SEARCH:
                res = [m for m in res if any(x["gameVersionTypeId"] == int(t) for x in m["latestFilesIndexes"])]
            f = q.get("searchFilter", [""])[0].lower()
            if f:
                res = [m for m in res if f in m["name"].lower() or f in m["summary"].lower()]
            c = q.get("categoryId", [None])[0]
            if c:
                res = [m for m in res if any(x["id"] == int(c) for x in m["categories"])]
            sf = int(q.get("sortField", ["2"])[0])
            key = {4: lambda m: m["name"].lower(), 3: lambda m: m["dateModified"]}.get(sf, lambda m: m["downloadCount"])
            res.sort(key=key, reverse=q.get("sortOrder", ["desc"])[0] == "desc")
            i, n = int(q.get("index", ["0"])[0]), int(q.get("pageSize", ["50"])[0])
            LOG.append(("SEARCH", dict((k, v[0]) for k, v in q.items())))
            return self.send_json(200, {"data": res[i:i + n], "pagination": {"index": i, "pageSize": n,
                                        "resultCount": len(res[i:i + n]), "totalCount": len(res)}})
        if parts[0] == "cdn":
            fid = int(parts[1])
            data = ZIPS[fid]
            if parts[2] != FILES[fid]["fileName"]:
                return self.send_json(404, {})
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            return self.wfile.write(data)
        if parts[:2] == ["v1", "mods"]:
            mid = int(parts[2])
            if mid not in MODS:
                return self.send_json(404, {})
            if len(parts) == 3:
                return self.send_json(200, {"data": MODS[mid]})
            if len(parts) == 4 and parts[3] == "description":
                return self.send_json(200, {"data": f"<h2>About {MODS[mid]['name']}</h2><p>Great <b>addon</b>. <a href='https://example.com/x' onclick='alert(1)'>docs</a> <a href='javascript:alert(2)'>bad</a></p><img src='https://media.forgecdn.net/x.png' onerror='alert(3)'><script>window.PWNED=1</script><iframe src='https://evil.example'></iframe><p style='color:red' class='x'>Styled</p>"})
            if len(parts) == 4 and parts[3] == "files":
                t = q.get("gameVersionTypeId", [None])[0]
                fs = [f for f in FILES.values() if f["modId"] == mid and
                      (t is None or any(sv["gameVersionTypeId"] == int(t) for sv in f["sortableGameVersions"]))]
                fs.sort(key=lambda f: -f["id"])
                return self.send_json(200, {"data": fs, "pagination": {}})
            fid = int(parts[4])
            if fid not in FILES or FILES[fid]["modId"] != mid:
                return self.send_json(404, {})
            if len(parts) == 5:
                return self.send_json(200, {"data": FILES[fid]})
            if parts[5] == "changelog":
                return self.send_json(200, {"data": f"<p>Fixed <b>stuff</b> in {FILES[fid]['displayName']}</p><ul><li>One</li><li>Two &amp; three</li></ul><script>alert(1)</script>"})
            if parts[5] == "download-url":
                if not MODS[mid]["allowModDistribution"]:
                    return self.send_json(403, {})
                return self.send_json(200, {"data": FILES[fid]["downloadUrl"]})
        self.send_json(404, {})

    def wago_route(self, method, p, q, body):
        LOG.append(("WAGO", p, self.headers.get("x-api-key")))
        if self.headers.get("x-api-key"):
            return self.send_json(400, {"error": "curseforge key leaked to wago"})
        gv = (q.get("game_version") or [""])[0]
        rr_of = lambda a: a["rr"] if gv == "forever" else {}
        if p == "/wago/api/data/slugs":
            return self.send_json(200, {"addons": {a["slug"]: {"id": a["id"]} for a in WAGO.values() if rr_of(a)}})
        auth = self.headers.get("Authorization") or ""
        if auth == "Bearer expired-key-xxxxxxxx":
            return self.send_json(403, {"error": "expired"})
        if auth != f"Bearer {WAGO_KEY}":
            return self.send_json(401, {"error": "unauthorized"})
        if p.startswith("/wago/dl/"):
            data = WAGO_FILES[int(p.split("/")[3])]
            self.send_response(200); self.send_header("Content-Length", str(len(data))); self.end_headers()
            return self.wfile.write(data)
        if p == "/wago/api/external/addons/popular":
            if WAGO_NO_POPULAR:
                return self.send_json(404, {"message": "not found"})
            res = [a for a in WAGO.values() if rr_of(a) or LEAKY_SEARCH]
            res.sort(key=lambda a: -a["download_count"])
            return self.send_json(200, {"data": [{k: a[k] for k in ("id", "slug", "name", "display_name", "website_url", "categories", "download_count")} for a in res]})
        if p == "/wago/api/external/addons/_search":
            qq = (q.get("query") or [""])[0].lower()
            return self.send_json(200, {"data": [{k: a[k] for k in ("id", "slug", "name", "display_name", "website_url", "categories", "download_count")}
                                                 for a in WAGO.values() if qq in a["name"].lower() and (rr_of(a) or LEAKY_SEARCH)]})
        if p == "/wago/api/external/addons/_recents" and method == "POST":
            return self.send_json(200, {"addons": {i: {"name": WAGO[i]["name"], "website_url": WAGO[i]["website_url"],
                                                       "recent_releases": rr_of(WAGO[i])} for i in body.get("addons", []) if i in WAGO}})
        ident = urllib.parse.unquote(p.split("/")[-1])
        a = WAGO.get(ident) or next((x for x in WAGO.values() if x["slug"] == ident), None)
        if not a:
            return self.send_json(404, {})
        out = {k: a[k] for k in ("id", "slug", "name", "display_name", "authors", "website_url")}
        out["recent_release"] = rr_of(a)
        return self.send_json(200, out)

    def do_POST(self):
        p = urllib.parse.urlsplit(self.path).path
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        LOG.append(("POST", p))
        if p.startswith("/wago/"):
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            return self.wago_route("POST", p, q, body)
        if p == "/admin/publish":
            fid = add_file(body["mod"], body["version"], {k: v for k, v in body["entries"].items()},
                           type_id=body.get("type", FOREVER), release=body.get("release", 1))
            return self.send_json(200, {"id": fid})
        if not self.authed():
            return
        if p == "/v1/mods":
            return self.send_json(200, {"data": [MODS[i] for i in body["modIds"] if i in MODS]})
        if p in ("/v1/fingerprints/1", "/v1/fingerprints"):
            want = set(body.get("fingerprints") or [])
            matches, hit = [], set()
            for f in FILES.values():
                fps = {m_["fingerprint"] for m_ in f["modules"]}
                if fps and fps <= want:
                    matches.append({"id": f["modId"], "file": f, "latestFiles": []}); hit |= fps
            return self.send_json(200, {"data": {"isCacheBuilt": True, "exactMatches": matches, "exactFingerprints": sorted(hit)}})
        if p == "/v1/mods/files":
            return self.send_json(200, {"data": [FILES[i] for i in body["fileIds"] if i in FILES]})
        self.send_json(404, {})


def start(port=0):
    global BASE
    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    BASE = f"http://127.0.0.1:{srv.server_address[1]}"
    import os; os.environ["WFAM_GITHUB_API"] = BASE + "/gh"
    global WBASE
    WBASE = f"http://localhost:{srv.server_address[1]}/wago"
    os.environ["WFAM_WAGO_BASE"] = WBASE
    seed()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


if __name__ == "__main__":
    s = start(int(sys.argv[1]) if len(sys.argv) > 1 else 0)
    print(BASE, flush=True)
    threading.Event().wait()
