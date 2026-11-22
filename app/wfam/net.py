"""HTTPS with kept-alive connections, host rules for API keys, and running calls in parallel."""
from __future__ import annotations

import concurrent.futures
import gzip
import http.client
import ssl
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .config import MAX_DOWNLOAD_BYTES, WAGO_BASE
from .errors import NetError, UserError
from .util import key_allowed_for, quote_url

_tls = threading.local()
_ssl_ctx = None


def _proxied(url: str) -> bool:
    parts = urllib.parse.urlsplit(url)
    proxies = urllib.request.getproxies()
    return bool(proxies.get(parts.scheme)) and not urllib.request.proxy_bypass(parts.hostname or "")


def _conn_for(scheme: str, netloc: str, timeout: float):
    global _ssl_ctx
    pool = getattr(_tls, "pool", None)
    if pool is None:
        pool = _tls.pool = {}
    conn = pool.get((scheme, netloc))
    if conn is None:
        if scheme == "https":
            if _ssl_ctx is None:
                _ssl_ctx = ssl.create_default_context()
            conn = http.client.HTTPSConnection(netloc, timeout=timeout, context=_ssl_ctx)
        else:
            conn = http.client.HTTPConnection(netloc, timeout=timeout)
        pool[(scheme, netloc)] = conn
    conn.timeout = timeout
    return conn


def _drop_conn(scheme: str, netloc: str):
    pool = getattr(_tls, "pool", None) or {}
    conn = pool.pop((scheme, netloc), None)
    if conn:
        conn.close()


def wago_allowed_for(url: str) -> bool:
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    base = (urllib.parse.urlsplit(WAGO_BASE).hostname or "").lower()
    return host == base or host == "wago.io" or host.endswith(".wago.io")


def http_call(method: str, url: str, headers: dict, body: bytes | None = None, key: str | None = None,
              dest: Path | None = None, hashers=(), max_bytes: int = MAX_DOWNLOAD_BYTES, timeout: float = 30,
              wago_key: str | None = None):
    """One HTTP request, following redirects. Returns (status, headers, body_bytes).
    With `dest`, a 200 response is streamed to that file (body_bytes is b"").
    `key` (CurseForge API key) is only sent to CurseForge's own hosts, on every hop."""
    for _hop in range(6):
        h = dict(headers)
        if key and key_allowed_for(url):
            h["x-api-key"] = key
        if wago_key and wago_allowed_for(url):
            h["Authorization"] = f"Bearer {wago_key}"
        url = quote_url(url)
        if _proxied(url):
            return _urllib_call(method, url, h, body, dest, hashers, max_bytes, timeout)
        parts = urllib.parse.urlsplit(url)
        path = (parts.path or "/") + ("?" + parts.query if parts.query else "")
        for attempt in (0, 1):
            conn = _conn_for(parts.scheme, parts.netloc, timeout)
            try:
                conn.request(method, path, body=body, headers=h)
                r = conn.getresponse()
                break
            except (http.client.HTTPException, OSError) as e:
                _drop_conn(parts.scheme, parts.netloc)   # stale keep-alive connection: retry once on a new one
                if attempt:
                    raise NetError(getattr(e, "strerror", None) or str(e) or type(e).__name__)
        if r.status in (301, 302, 303, 307, 308) and r.getheader("Location"):
            r.read()
            if r.will_close:
                _drop_conn(parts.scheme, parts.netloc)
            url = urllib.parse.urljoin(url, r.getheader("Location"))
            if r.status == 303:
                method, body = "GET", None
            continue
        try:
            if dest is not None and r.status == 200:
                total = 0
                with open(dest, "wb") as out:
                    while True:
                        chunk = r.read(1024 * 256)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > max_bytes:
                            raise UserError("The download is larger than 300 MB - stopped.")
                        for hs in hashers:
                            hs.update(chunk)
                        out.write(chunk)
                data = b""
            else:
                data = r.read()
                if r.getheader("Content-Encoding") == "gzip":
                    data = gzip.decompress(data)
        except UserError:
            _drop_conn(parts.scheme, parts.netloc)
            raise
        except (http.client.HTTPException, OSError) as e:
            _drop_conn(parts.scheme, parts.netloc)
            raise NetError(str(e) or type(e).__name__)
        if r.will_close:
            _drop_conn(parts.scheme, parts.netloc)
        return r.status, r.headers, data
    raise NetError("too many redirects")


def _urllib_call(method, url, headers, body, dest, hashers, max_bytes, timeout):
    """Fallback for PCs that go through a proxy (urllib knows how to use it)."""
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if dest is None:
                data = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    data = gzip.decompress(data)
                return r.status, r.headers, data
            total = 0
            with open(dest, "wb") as out:
                while True:
                    chunk = r.read(1024 * 256)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        raise UserError("The download is larger than 300 MB - stopped.")
                    for hs in hashers:
                        hs.update(chunk)
                    out.write(chunk)
            return r.status, r.headers, b""
    except urllib.error.HTTPError as e:
        try:
            data = e.read()
        except Exception:
            data = b""
        return e.code, e.headers, data
    except urllib.error.URLError as e:
        raise NetError(str(e.reason))


def run_parallel(fn, items, workers: int = 6):
    """fn(item) for each item on a few threads; returns {item: result or exception}."""
    out = {}
    items = list(items)
    if not items:
        return out
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(workers, len(items))) as ex:
        futs = {ex.submit(fn, it): it for it in items}
        for fut in concurrent.futures.as_completed(futs):
            try:
                out[futs[fut]] = fut.result()
            except Exception as e:  # noqa: BLE001 - reported per item by the caller
                out[futs[fut]] = e
    return out
