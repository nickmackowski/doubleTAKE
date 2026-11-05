"""Sending exported JPEGs to a phone, over the tablet's own hotspot.

The phone joins the hotspot (the first QR code), then opens a small download page
(the second QR code). The page:

- listens only on the hotspot's address, never on any other network
- lists only the photos chosen for it, and can open nothing else
- follows the choice as it changes: photos ticked, or exported, while it is open show
  on the phone the next time the page looks (it looks every few seconds)
- needs a random code in its address, made fresh each time
- is read-only, and stops by itself after 30 minutes, or when Stop is tapped

The screens themselves are never reachable from the phone.
"""

from __future__ import annotations

import html
import json
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote

from . import photos

PORT = 8780
DOUBLETAKE_LOGO = Path(__file__).resolve().parent / "web" / "phone-doubletake.png"
DOUBLETAKE_RING = DOUBLETAKE_LOGO.with_name("phone-ring.png")  # the boot screen's ring
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


def picture_size(path: Path) -> tuple:
    """(width, height) as the photo is seen, or (3, 2) when it cannot be read."""
    try:
        from PIL import Image

        with Image.open(path) as img:
            w, h = img.size
            try:
                if img.getexif().get(0x0112) in (5, 6, 7, 8):
                    w, h = h, w
            except Exception:
                pass
            return (w, h) if w > 0 and h > 0 else (3, 2)
    except Exception:
        return (3, 2)


# The page is one file: the list of photos goes in as data, and a little script draws the
# rows, the one-photo view, and looks again every few seconds for photos added.
PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#16181b">
<title>__TITLE__</title>
<style>
*{box-sizing:border-box}
html,body{margin:0;background:#16181b;color:#fff}
body{font:16px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,system-ui,sans-serif;-webkit-text-size-adjust:100%}
a{color:inherit}
header{padding:28px 18px 6px;text-align:center}
.logo{display:block;margin:0 auto 18px;max-width:min(82%,520px);max-height:96px;width:auto;height:auto}
h1{margin:0;font-size:24px;font-weight:650;letter-spacing:.01em}
.site{display:inline-block;margin-top:6px;color:#4ea1ff;font-size:16px;text-decoration:underline;text-underline-offset:3px}
.links{display:flex;flex-wrap:wrap;justify-content:center;gap:8px;margin:16px 0 4px}
.links a{border:1.5px solid rgba(255,255,255,.55);border-radius:999px;padding:7px 16px;font-size:15px;
  font-weight:600;text-decoration:none}
.how{margin:14px auto 0;max-width:34em;color:rgba(255,255,255,.62);font-size:14px}
.count{margin-top:6px;color:rgba(255,255,255,.62);font-size:14px}
.rows{display:flex;flex-wrap:wrap;align-items:flex-start;gap:6px;padding:14px 12px 8px}
.rows::after{content:"";flex-grow:999999}
.ph{position:relative;display:block;background:#000;border-radius:4px;overflow:hidden}
.ph img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;display:block}
.empty{padding:40px 18px;text-align:center;color:rgba(255,255,255,.62)}
footer{padding:26px 18px 38px;text-align:center;color:rgba(255,255,255,.42);font-size:13px}
.mark{position:relative;width:88px;height:88px;margin:0 auto 10px}
.mark img{position:absolute;display:block}
.mark .ring{inset:0;width:88px;height:88px;animation:turn 2s linear infinite}
.mark .icon{left:15px;top:15px;width:58px;height:58px}
@keyframes turn{to{transform:rotate(360deg)}}
@media (prefers-reduced-motion:reduce){.mark .ring{animation:none}}
#one{position:fixed;inset:0;background:#000;display:none;flex-direction:column;z-index:5}
#one.open{display:flex}
#one .pic{flex:1;min-height:0;display:flex;align-items:center;justify-content:center;padding:8px}
#one img{max-width:100%;max-height:100%;-webkit-touch-callout:default}
#one .bar{display:flex;align-items:center;gap:8px;padding:10px 12px calc(12px + env(safe-area-inset-bottom));
  background:#16181b}
#one .bar .grow{flex:1;text-align:center;color:rgba(255,255,255,.7);font-size:14px;white-space:nowrap;overflow:hidden;
  text-overflow:ellipsis}
#one button,#one a.save{font:inherit;font-weight:600;font-size:15px;color:#fff;background:none;
  border:1.5px solid rgba(255,255,255,.55);border-radius:999px;padding:8px 14px;text-decoration:none;cursor:pointer}
#one a.save{background:#fff;color:#16181b;border-color:#fff}
#one button:disabled{opacity:.3}
#one .hold{display:none;position:absolute;left:0;right:0;top:calc(64px + env(safe-area-inset-top));text-align:center;
  font-size:14px;color:rgba(255,255,255,.85);pointer-events:none}
.ios #one .hold{display:block} .ios #one a.save{display:none}
#one .top{display:flex;padding:calc(10px + env(safe-area-inset-top)) 12px 6px;background:#16181b}
</style></head><body>
<header>__LOGO__<h1>__TITLE__</h1>__SITE____LINKS__
<p class="how" id="how">Tap a photo to see it large, then Save.</p>
<div class="count" id="count"></div></header>
<div class="rows" id="rows"></div>
<footer><div class="mark"><img class="ring" src="ring.png" alt=""><img class="icon" src="doubletake.png" alt=""></div>Sent with DoubleTake</footer>
<div id="one"><div class="top"><button type="button" id="all">&#8249; All photos</button></div>
<div class="pic"><img id="big" alt=""></div>
<div class="bar"><button type="button" id="prev">&#8249; Previous</button><div class="grow" id="where"></div>
<a class="save" id="save" href="#" download>Save</a><div class="hold" id="hold">Press and hold the photo, then Save to Photos</div><button type="button" id="next">Next &#8250;</button></div></div>
<script>
let list = __LIST__, at = -1, sx = 0, sy = 0;
const $ = (id) => document.getElementById(id);
// iPhone and iPad: a download goes to Files, but press-and-hold puts the photo in Photos.
const ios = /iPhone|iPad|iPod/.test(navigator.userAgent) || (/Macintosh/.test(navigator.userAgent) && navigator.maxTouchPoints > 1);
if (ios) {
  document.documentElement.classList.add("ios");
  $("how").textContent = "Tap a photo to see it large, then press and hold it and choose Save to Photos.";
}
const key = (p) => p.s + "/" + p.n;
const path = (kind, p) => kind + "/" + encodeURIComponent(p.s) + "/" + encodeURIComponent(p.n) + "?v=" + p.v;
function draw() {
  $("count").textContent = list.length + (list.length === 1 ? " photo" : " photos");
  const rows = $("rows");
  const keep = new Map([...rows.children].map((el) => [el.dataset.k, el]));
  const items = list.map((p, i) => {
    let a = keep.get(key(p) + "@" + p.v);
    if (!a) {
      a = document.createElement("a");
      a.className = "ph"; a.href = path("f", p); a.dataset.k = key(p) + "@" + p.v;
      const r = p.w / p.h;
      a.style.flex = r + " 1 " + Math.round(r * 150) + "px";
      a.style.aspectRatio = p.w + " / " + p.h;
      const img = document.createElement("img");
      img.loading = "lazy"; img.alt = p.n; img.src = path("t", p);
      a.append(img);
    }
    a.onclick = (e) => { e.preventDefault(); open(i); };
    return a;
  });
  if (!list.length) {
    const d = document.createElement("div"); d.className = "empty"; d.textContent = "No photos here yet.";
    items.push(d);
  }
  rows.replaceChildren(...items);
}
function open(i) {
  if (i < 0 || i >= list.length) return;
  at = i;
  const p = list[i];
  $("big").src = path("f", p);
  $("save").href = path("f", p) + "&dl=1";
  $("save").setAttribute("download", p.n);
  $("where").textContent = (i + 1) + " of " + list.length;
  $("prev").disabled = i === 0;
  $("next").disabled = i === list.length - 1;
  $("one").classList.add("open");
  document.body.style.overflow = "hidden";
  if (location.hash !== "#photo") history.pushState(null, "", "#photo");
}
function shut() {
  at = -1;
  $("one").classList.remove("open");
  document.body.style.overflow = "";
  $("big").removeAttribute("src");
}
$("all").onclick = () => { if (location.hash === "#photo") history.back(); else shut(); };
window.onpopstate = () => { if (at >= 0) shut(); };
$("prev").onclick = () => open(at - 1);
$("next").onclick = () => open(at + 1);
$("one").addEventListener("touchstart", (e) => { sx = e.touches[0].clientX; sy = e.touches[0].clientY; }, { passive: true });
$("one").addEventListener("touchend", (e) => {
  const dx = e.changedTouches[0].clientX - sx, dy = e.changedTouches[0].clientY - sy;
  if (Math.abs(dx) > 60 && Math.abs(dx) > 2 * Math.abs(dy)) open(at + (dx < 0 ? 1 : -1));
});
document.addEventListener("keydown", (e) => {
  if (at < 0) return;
  if (e.key === "ArrowLeft") open(at - 1);
  if (e.key === "ArrowRight") open(at + 1);
  if (e.key === "Escape") $("all").click();
});
async function look() {
  try {
    const r = await fetch("list.json", { cache: "no-store" });
    if (r.ok) {
      const fresh = await r.json();
      if (JSON.stringify(fresh) !== JSON.stringify(list)) {
        const was = at >= 0 ? key(list[at]) : "";
        list = fresh;
        draw();
        if (was) { const i = list.findIndex((p) => key(p) === was); if (i >= 0) open(i); else $("all").click(); }
      }
    }
  } catch (err) {}
  setTimeout(look, 5000);
}
draw();
setTimeout(look, 5000);
</script></body></html>"""


def render_page(items: list, brand: dict, logo: bool) -> str:
    """The page, with the photographer's name, logo and links when they are set."""
    from .settings import web_address

    name = (brand.get("name") or "").strip()
    title = html.escape(f"Photos from {name}" if name else "Photos from DoubleTake")
    site = web_address(brand.get("website") or "")
    site_html = ""
    if site:
        shown = site.split("://", 1)[1].rstrip("/")
        site_html = f'<a class="site" href="{html.escape(site)}">{html.escape(shown)}</a>'
    links = []
    for link in brand.get("links") or []:
        url = web_address(link.get("url") or "")
        if url:
            links.append(f'<a href="{html.escape(url)}">{html.escape(link.get("label") or url)}</a>')
    links_html = f'<div class="links">{"".join(links)}</div>' if links else ""
    logo_html = f'<img class="logo" src="logo.png" alt="{html.escape(name)}">' if logo else ""
    data = json.dumps(items, separators=(",", ":")).replace("<", "\\u003c")
    return (PAGE.replace("__TITLE__", title).replace("__LOGO__", logo_html).replace("__SITE__", site_html)
            .replace("__LINKS__", links_html).replace("__LIST__", data))


class PhoneShare:
    """One sharing session: a code, and a small web server on the hotspot.

    files() gives the photos to show at that moment, newest first, as (shoot, name, path)
    tuples; brand() gives {name, website, links}; logo is the logo's file, if there is one."""

    def __init__(self, files, host: str, port: int = PORT, minutes: int = MINUTES, on_stop=None,
                 brand=None, logo: Path | None = None):
        if not callable(files):
            fixed = list(files)
            files = lambda: fixed  # noqa: E731
        self._files = files
        self._brand = brand or (lambda: {})
        self._logo = Path(logo) if logo else None
        self.host, self.port = host, port
        self.token = secrets.token_urlsafe(9)
        self.until = time.time() + minutes * 60
        self.served: set = set()
        self.visitors: set = set()
        self._sizes: dict = {}
        self._server = None
        self._timer = None
        self._on_stop = on_stop

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/{self.token}/"

    def files(self) -> list:
        out = []
        for shoot, name, path in self._files():
            out.append((shoot, name, Path(path)))
        return out

    def items(self) -> list:
        """What the page lists: shoot, name, its shape, and a mark that changes when it is exported again."""
        out = []
        for shoot, name, path in self.files():
            try:
                st = path.stat()
            except OSError:
                continue
            mark = f"{int(st.st_mtime)}{int(st.st_ctime)}{st.st_size}"
            known = self._sizes.get(str(path))
            if known is None or known[0] != mark:
                known = (mark, picture_size(path))
                self._sizes[str(path)] = known
            w, h = known[1]
            out.append({"s": shoot, "n": name, "w": w, "h": h, "v": mark[-8:]})
        return out

    def _find(self, shoot: str, name: str):
        for s, n, path in self.files():
            if s == shoot and n == name:
                return path
        return None

    def logo_bytes(self) -> bytes | None:
        if self._logo is None:
            return None
        try:
            return self._logo.read_bytes()
        except OSError:
            return None

    def page(self) -> str:
        return render_page(self.items(), self._brand() or {}, self.logo_bytes() is not None)

    def start(self) -> None:
        share = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "DoubleTake"

            def log_message(self, fmt, *args):
                pass

            def _send(self, status, body: bytes, kind: str, extra=None, cache="no-store"):
                self.send_response(status)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", cache)
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                for k, v in (extra or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                raw, _, query = self.path.partition("?")
                parts = [unquote(p) for p in raw.strip("/").split("/")]
                if time.time() > share.until or not parts or not secrets.compare_digest(parts[0], share.token):
                    return self._send(404, b"Not found", "text/plain")
                share.visitors.add(self.client_address[0])
                if len(parts) == 1:
                    return self._send(200, share.page().encode("utf-8"), "text/html; charset=utf-8",
                                      {"Content-Security-Policy": "default-src 'none'; img-src 'self'; "
                                       "style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'"})
                if parts[1:] == ["list.json"]:
                    return self._send(200, json.dumps(share.items()).encode("utf-8"), "application/json")
                if parts[1:] == ["ring.png"]:
                    return self._send(200, DOUBLETAKE_RING.read_bytes(), "image/png", cache="private, max-age=3600")
                if parts[1:] == ["doubletake.png"]:
                    return self._send(200, DOUBLETAKE_LOGO.read_bytes(), "image/png", cache="private, max-age=3600")
                if parts[1:] == ["logo.png"]:
                    body = share.logo_bytes()
                    if body is None:
                        return self._send(404, b"Not found", "text/plain")
                    return self._send(200, body, "image/png", cache="private, max-age=300")
                if len(parts) == 4 and parts[1] in ("t", "f"):
                    path = share._find(parts[2], parts[3])
                    if path is None:
                        return self._send(404, b"Not found", "text/plain")
                    if parts[1] == "t":
                        body = photos.small(path, 600)
                        if body is not None:
                            return self._send(200, body, "image/jpeg", cache="private, max-age=600")
                    try:
                        body = path.read_bytes()
                    except OSError:
                        return self._send(404, b"Not found", "text/plain")
                    if parts[1] == "f":
                        share.served.add((parts[2], parts[3]))
                    how = "attachment" if "dl=1" in query.split("&") else "inline"
                    return self._send(200, body, "image/jpeg",
                                      {"Content-Disposition": f"{how}; filename=\"{parts[3]}\"; "
                                                              f"filename*=UTF-8''{quote(parts[3])}"},
                                      cache="private, max-age=600")
                return self._send(404, b"Not found", "text/plain")

        self._server = ThreadingHTTPServer((self.host, self.port), Handler)
        self._server.daemon_threads = True
        threading.Thread(target=self._server.serve_forever, name="phone", daemon=True).start()
        self._timer = threading.Timer(max(1.0, self.until - time.time()), self.stop)
        self._timer.daemon = True
        self._timer.start()

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
        return {"on": self.running(), "url": self.url, "count": len(self.files()), "served": len(self.served),
                "phones": len(self.visitors), "minutes_left": max(0, round((self.until - time.time()) / 60))}
