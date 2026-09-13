"""Streamed HTTP on http.client: a connect timeout only, bodies read in 1 MiB chunks.

Shared by the http_lake adapter and DataGovClient. Never calls .read() on a whole body;
error bodies (small JSON) are read with a bound.
"""

from __future__ import annotations

import http.client
import json
import re
from urllib.parse import urlencode, urlsplit

CHUNK = 1024 * 1024
CONNECT_TIMEOUT = 30
ERROR_BODY_LIMIT = CHUNK
_FILENAME = re.compile(r'filename="([^"]*)"')


def filename_from_disposition(header) -> str | None:
    match = _FILENAME.search(header or "")
    return match.group(1) if match else None


class BufferedResponse:
    """Same surface as StreamResponse over a buffered body (Flask test client responses)."""

    def __init__(self, status, headers, data: bytes):
        self.status = int(status)
        self.headers = {str(k).lower(): str(v) for k, v in dict(headers or {}).items()}
        self._data = data or b""

    def header(self, name, default=None):
        return self.headers.get(name.lower(), default)

    def iter_chunks(self, size=CHUNK):
        for start in range(0, len(self._data), size):
            yield self._data[start : start + size]

    def read_json(self):
        try:
            return json.loads(self._data.decode()) or {}
        except (ValueError, UnicodeDecodeError):
            return {}

    def close(self):
        return None


class StreamResponse:
    def __init__(self, conn, resp):
        self._conn = conn
        self._resp = resp
        self.status = int(resp.status)
        self.headers = {k.lower(): v for k, v in resp.getheaders()}
        self._closed = False

    def header(self, name, default=None):
        return self.headers.get(name.lower(), default)

    def iter_chunks(self, size=CHUNK):
        while True:
            chunk = self._resp.read(size)
            if not chunk:
                return
            yield chunk

    def read_json(self):
        try:
            return json.loads(self._resp.read(ERROR_BODY_LIMIT).decode()) or {}
        except (ValueError, UnicodeDecodeError, OSError):
            return {}

    def close(self):
        if not self._closed:
            self._closed = True
            self._conn.close()


def open_stream(url, headers=None, params=None, method="GET", body=None, timeout=CONNECT_TIMEOUT):
    """Connect with `timeout`, then clear the socket timeout: a slow lake is waited for."""
    parts = urlsplit(url)
    cls = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
    conn = cls(parts.hostname, parts.port, timeout=timeout)
    conn.connect()
    conn.sock.settimeout(None)
    path = parts.path or "/"
    if params:
        query = urlencode({k: v for k, v in params.items() if v is not None})
        if query:
            path += "?" + query
    conn.request(method, path, body=body, headers=headers or {})
    return StreamResponse(conn, conn.getresponse())
