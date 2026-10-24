"""Sending exported JPEGs to a phone, over the tablet's own hotspot.

The phone joins the hotspot (the first QR code), then opens a small download page
(the second QR code). The page:

- listens only on the hotspot's address, never on any other network
- lists only the photos chosen for it, and can open nothing else
- needs a random code in its address, made fresh each time
- is read-only, and stops by itself after 30 minutes, or when Stop is tapped

The screens themselves are never reachable from the phone.
"""

from __future__ import annotations

import html
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote

from . import photos

PORT = 8780
MINUTES = 30


def qr_svg(text: str, scale: int = 8) -> str:
    """A QR code as SVG, or '' when the QR library is not installed."""
    try:
        import segno
    except ImportError:
        return ""
    import io

    out = io.BytesIO()
    segno.make(text, error="m").save(out, kind="svg", scale=scale, border=2, dark="#000", light="#fff",
                                     xmldecl=False, svgns=True)
    return out.getvalue().decode("utf-8")


def wifi_text(ssid: str, password: str) -> str:
    """What phones read from a 'join this Wi-Fi' QR code."""
    def esc(value: str) -> str:
        return "".join("\\" + c if c in '\\;,:"' else c for c in value)
    return f"WIFI:T:WPA;S:{esc(ssid)};P:{esc(password)};;"


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>DoubleTake photos</title>
<style>
body{{margin:0;font:16px/1.4 -apple-system,system-ui,sans-serif;background:#0b0f12;color:#e8eaec}}
header{{padding:18px 16px 8px}} h1{{margin:0;font-size:22px}} p{{margin:6px 0 0;color:#aab1b8;font-size:14px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px;padding:12px 16px 32px}}
.ph{{background:#1b2025;border-radius:8px;overflow:hidden}} .ph img{{width:100%;aspect-ratio:1;object-fit:contain;display:block;background:#000}}
.ph div{{display:flex;justify-content:space-between;align-items:center;padding:6px 8px;font-size:13px}}
.ph a.dl{{color:#0b0f12;background:#ff6a13;border-radius:5px;padding:4px 10px;text-decoration:none;font-weight:600}}
</style></head><body>
<header><h1>{title}</h1><p>{count}. iPhone: tap a photo, then press and hold it and choose Save to Photos.
Android: tap Save.</p></header>
<div class="grid">{items}</div></body></html>"""


class PhoneShare:
    """One sharing session: a list of files, a code, and a small web server on the hotspot."""

    def __init__(self, files: list, host: str, port: int = PORT, minutes: int = MINUTES, on_stop=None):
        self.files = [(shoot, name, Path(path)) for shoot, name, path in files]
        self.host, self.port = host, port
        self.token = secrets.token_urlsafe(9)
        self.until = time.time() + minutes * 60
        self.served: set = set()
        self.visitors: set = set()
        self._server = None
        self._timer = None
        self._on_stop = on_stop

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/{self.token}/"

    def start(self) -> None:
        share = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "DoubleTake"

            def log_message(self, fmt, *args):
                pass

            def _send(self, status, body: bytes, kind: str, extra=None):
                self.send_response(status)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                for k, v in (extra or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                parts = self.path.split("?")[0].strip("/").split("/")
                if time.time() > share.until or not parts or not secrets.compare_digest(parts[0], share.token):
                    return self._send(404, b"Not found", "text/plain")
                share.visitors.add(self.client_address[0])
                if len(parts) == 1:
                    return self._send(200, share.page().encode("utf-8"), "text/html; charset=utf-8")
                number = parts[2][:-4] if len(parts) >= 3 and parts[2].endswith(".jpg") else (parts[2] if len(parts) >= 3 else "")
                if len(parts) >= 3 and parts[1] in ("t", "f") and number.isdigit():
                    index = int(number)
                    if index >= len(share.files):
                        return self._send(404, b"Not found", "text/plain")
                    shoot, name, path = share.files[index]
                    if parts[1] == "t":
                        body = photos.small(path, 600)
                        if body is not None:
                            return self._send(200, body, "image/jpeg")
                    try:
                        body = path.read_bytes()
                    except OSError:
                        return self._send(404, b"Not found", "text/plain")
                    share.served.add(index)
                    how = "attachment" if "dl=1" in self.path else "inline"
                    return self._send(200, body, "image/jpeg",
                                      {"Content-Disposition": f'{how}; filename="{quote(name)}"'})
                return self._send(404, b"Not found", "text/plain")

        self._server = ThreadingHTTPServer((self.host, self.port), Handler)
        self._server.daemon_threads = True
        threading.Thread(target=self._server.serve_forever, name="phone", daemon=True).start()
        self._timer = threading.Timer(max(1.0, self.until - time.time()), self.stop)
        self._timer.daemon = True
        self._timer.start()

    def page(self) -> str:
        items = []
        for i, (shoot, name, _) in enumerate(self.files):
            safe = html.escape(name)
            items.append(f'<div class="ph"><a href="f/{i}/{quote(name)}"><img src="t/{i}.jpg" alt="{safe}" loading="lazy"></a>'
                         f'<div><span>{safe}</span><a class="dl" href="f/{i}/{quote(name)}?dl=1" download="{safe}">Save</a></div></div>')
        count = f"{len(self.files)} photo{'s' if len(self.files) != 1 else ''}"
        return PAGE.format(title="Photos from DoubleTake", count=count, items="".join(items))

    def running(self) -> bool:
        return self._server is not None and time.time() < self.until

    def stop(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
        server, self._server = self._server, None
        if server is not None:
            threading.Thread(target=lambda: (server.shutdown(), server.server_close()), daemon=True).start()
            if self._on_stop is not None:
                self._on_stop()

    def status(self) -> dict:
        return {"on": self.running(), "url": self.url, "count": len(self.files), "served": len(self.served),
                "phones": len(self.visitors), "minutes_left": max(0, round((self.until - time.time()) / 60))}
