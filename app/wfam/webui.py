"""The window's page: index.html, app.css and app.js, read from the package (also from inside wfam.zip)."""
from __future__ import annotations

import os

from .config import APP_VERSION

TYPES = {"index.html": "text/html; charset=utf-8", "app.css": "text/css; charset=utf-8",
         "app.js": "text/javascript; charset=utf-8"}
_cache: dict[str, bytes] = {}


def asset(name: str) -> bytes:
    """The file's bytes, read once. The page asks for app.css / app.js with ?v=<version>, so the window
    may keep them cached between starts and only fetches them again after an update."""
    b = _cache.get(name)
    if b is None:
        # the package's own loader reads from the folder or from inside wfam.zip alike
        b = __loader__.get_data(os.path.join(os.path.dirname(__file__), "ui", name))
        if name == "index.html":
            b = b.replace(b"{{VERSION}}", APP_VERSION.encode())
        _cache[name] = b
    return b
