#!/usr/bin/env python3
"""WoW Forever Addon Manager — starts the program (see wfam/__init__.py for what it does).

The code is the `wfam` package: wfam.zip next to this file in an installed copy (compiled ahead of
time, so nothing is compiled at start), or the wfam folder when run from the source.
"""
import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
_zip = os.path.join(_here, "wfam.zip")
sys.path.insert(0, _zip if os.path.isfile(_zip) else _here)

from wfam.main import run  # noqa: E402

if __name__ == "__main__":
    run()
