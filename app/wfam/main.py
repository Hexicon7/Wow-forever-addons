"""Command line and start-up."""
from __future__ import annotations

import argparse
import os
import sys
import traceback
from pathlib import Path

from .config import APP_NAME, DEFAULT_PORT
from .manager import Manager
from .server import App
from .store import Store
from .system import make_ico
from .util import data_dir


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def cli_list(m: Manager):
    snap = m.snapshot()
    if not snap["managed"]:
        print("No managed addons.")
    for a in snap["managed"]:
        print(f"{a['name']:<32} {a['installed_version'] or '-':<28} {a['status']}")
    if snap["unmanaged"]:
        print(f"\n{len(snap['unmanaged'])} other folder group(s) in AddOns: " +
              ", ".join(g["title"] for g in snap["unmanaged"]))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="WoWForeverAddons", description=APP_NAME)
    ap.add_argument("--list", action="store_true", help="list installed addons")
    ap.add_argument("--check", action="store_true", help="check for updates")
    ap.add_argument("--update-all", action="store_true", help="check and install all updates")
    ap.add_argument("--add", nargs="+", metavar="URL", help="install addons from CurseForge links")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--no-browser", action="store_true", help="don't open the browser")
    ap.add_argument("--no-idle-exit", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--ready-file", metavar="FILE", help=argparse.SUPPRESS)   # used by the desktop app window
    ap.add_argument("--host-pid", type=int, default=0, help=argparse.SUPPRESS)  # the desktop program's process
    ap.add_argument("--write-icon", metavar="FILE", help=argparse.SUPPRESS)
    ap.add_argument("--cleanup", action="store_true", help=argparse.SUPPRESS)   # used by Uninstall.cmd
    args = ap.parse_args(argv)
    if args.write_icon:
        Path(args.write_icon).write_bytes(make_ico())
        return 0
    if args.cleanup:
        for p in Manager(Store(), echo=True).cleanup_leftovers(min_age=0):
            print("removed", p)
        return 0

    cli = args.list or args.check or args.update_all or args.add
    store = Store()
    m = Manager(store, echo=bool(cli))
    if cli:
        ok = True
        if args.add:
            ok &= m.run_job("Installing addons", m.op_add, "\n".join(args.add), background=False)
        if args.update_all:
            ok &= m.run_job("Updating all addons", m.op_check_and_update, background=False)
        elif args.check:
            ok &= m.run_job("Checking for updates", m.op_check, background=False)
        if args.list:
            if store.config.get("api_key") and store.addons and not (args.update_all or args.check or args.add):
                m.run_job("Loading addon info", m.op_check, background=False)
            cli_list(m)
        return 0 if ok else 1
    App(m).serve(args.port, open_browser=not args.no_browser, idle_exit=not args.no_idle_exit,
                 ready_file=args.ready_file, host_pid=args.host_pid)
    return 0


def run():
    """Entry point used by WoWForeverAddons.pyw and `python -m wfam`."""
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:
        msg = traceback.format_exc()
        try:
            (data_dir() / "error.log").write_text(msg, "utf-8")
        except Exception:
            pass
        if os.name == "nt" and (sys.stdout is None or "pythonw" in sys.executable.lower()):
            try:
                import ctypes
                ctypes.windll.user32.MessageBoxW(None, msg[-1500:], APP_NAME + " — error", 0x10)
            except Exception:
                pass
        else:
            print(msg, file=sys.stderr)
        sys.exit(1)
