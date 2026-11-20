# WoW Forever Addons

A desktop program for Windows that installs and updates World of Warcraft: Forever addons from
CurseForge (and Wago / GitHub), without the CurseForge app.

**Download:** get `WoWForeverAddons-Setup-<version>.exe` from [Releases](../../releases/latest) and run it.
No administrator rights needed. When a new version is published here, the program tells you and you choose
when to update (it never updates itself without asking).

Also: a WeakAuras tab with ForeverAuras from wago.io — "Copy string", then import it in game.
ElvUI and Tukui (tukui.org) come up in the normal addon search and are kept up to date too.

Each new version is put in `updates/` (`tools/publish_update.py`) and published as a release automatically
by `.github/workflows/release.yml`.

## What's in this repository

| Folder | What |
|---|---|
| `app/` | The manager itself (`WoWForeverAddons.pyw`, Python standard library only) and its interface (`ui/page.html`, built into the .pyw). |
| `desktop/` | `WoW Forever Addons.exe`: the program window (Microsoft WebView2), tray icon and background updates. Go. |
| `setup/` | `WoWForeverAddons-Setup.exe`: installer, updater and uninstaller. Go, no dependencies. |
| `tests/` | Tests against fake CurseForge / Wago / GitHub servers (`python3 tests/test_*.py`, needs Playwright for the UI ones). |
| `tools/` | Build helpers. |

## Building

On Linux: Go 1.24+, `llvm-windres` and Python 3, then `./build.sh` → `dist/WoWForeverAddons-Setup-<version>.exe`.

## Third-party code

* `desktop/third_party/go-webview2` — [jchv/go-webview2](https://github.com/jchv/go-webview2), MIT licence (see its LICENSE). Modified: loads `WebView2Loader.dll` only from the program's folder, plus hooks for the tray and window.
* `desktop/third_party/x-sys` — the `windows` part of [golang.org/x/sys](https://github.com/golang/sys) v0.30.0, BSD licence.
* `desktop/payload/WebView2Loader.dll` — Microsoft's redistributable WebView2 loader, signed by Microsoft.
* The installer downloads the official Python embeddable package from python.org and checks its Python Software Foundation signature.
