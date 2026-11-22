"""Packs app/wfam into wfam.zip for the Setup: the sources plus bytecode compiled ahead of time.

    python3 tools/build_zip.py [OUT]        (default: setup/payload/wfam.zip)

Python imports straight from the zip. Each module has its compiled copy next to it (wfam/x.pyc, the
layout zipimport looks for) built as "unchecked hash" bytecode, so nothing is compiled when the program
starts. It must be built with the same Python minor version the Setup installs (3.13); with any other
version Python would ignore the .pyc files and compile the sources instead (slower, still correct).
The zip is reproducible: fixed timestamps and order, so the same code gives the same file.
"""
from __future__ import annotations

import io
import py_compile
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "app" / "wfam"
TARGET_PY = (3, 13)          # setup/main_windows.go: pyVersion
STAMP = (2026, 1, 1, 0, 0, 0)


def build(out: Path) -> int:
    if sys.version_info[:2] != TARGET_PY:
        raise SystemExit(f"build with Python {TARGET_PY[0]}.{TARGET_PY[1]} (this is {sys.version.split()[0]})")
    files = sorted(f for f in PKG.rglob("*") if f.is_file() and "__pycache__" not in f.parts and f.suffix != ".pyc")
    buf = io.BytesIO()
    with tempfile.TemporaryDirectory() as td, zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        def put(arc: str, data: bytes):
            zi = zipfile.ZipInfo(arc, STAMP)
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = 0o644 << 16
            z.writestr(zi, data)
        for f in files:
            arc = "wfam/" + f.relative_to(PKG).as_posix()
            put(arc, f.read_bytes())
            if f.suffix == ".py":
                cfile = Path(td) / (arc.replace("/", "_") + "c")
                py_compile.compile(str(f), cfile=str(cfile), dfile=arc, doraise=True,
                                   invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH)
                put(arc[:-3] + ".pyc", cfile.read_bytes())
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(buf.getvalue())
    return len(files)


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "setup" / "payload" / "wfam.zip"
    n = build(out)
    print(f"{out}: {n} files, {out.stat().st_size // 1024} KB")
