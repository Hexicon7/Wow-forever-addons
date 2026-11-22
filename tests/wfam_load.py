"""Gives tests the program's functions and classes, like the old single-file module had them.

    from wfam_load import W, mod
    W.parse_ref(...)            # any public name, from whichever wfam module defines it
    mod("net")._tls             # a module itself (to patch something where it's used)
"""
import importlib
import os
import sys

APP_DIR = os.environ.get("WFAM_APP_DIR", "/home/claude/app")
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

MODULES = ["config", "errors", "util", "net", "addonfiles", "sources", "system", "store", "manager",
           "watcher", "server", "webui", "main"]


def mod(name):
    return importlib.import_module("wfam." + name)


class _All:
    def __getattr__(self, name):
        for m in MODULES:
            v = getattr(mod(m), name, _All)
            if v is not _All:
                return v
        raise AttributeError(name)


W = _All()
