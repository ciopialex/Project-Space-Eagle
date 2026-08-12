#!/usr/bin/env python3
"""A local site that exists to be operated, and to prove it was.

Every live test so far has been against someone else's website, which means
every failure came with a question attached: was that the eagle, or was that
Cloudflare, a layout change, a slow CDN, an A/B test? This removes all of it.
We own the pages, so a failure is ours.

It serves the smallest complete workflow that exercises the whole machine:

    open a page  ->  download a file  ->  read local data off the disk
                 ->  write a filled copy  ->  navigate  ->  upload it
                 ->  submit  ->  and the SERVER records what it received

That last part is the point. The test does not ask the eagle whether it
succeeded — the eagle is the thing under test, and this codebase's entire
history is tools reporting success they never had. The server keeps what was
actually submitted, so the verdict comes from the other side of the wire.

Deliberately ugly and dependency-free: stdlib only, no CSS worth the name.
It is a rig, not a product.

    python tools/testsite/server.py            # serves on 127.0.0.1:8971
    python tools/testsite/server.py --port 8971
"""
from __future__ import annotations

import argparse
import json
import re
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

PORT = 8971

#: What the eagle is expected to fetch, fill and send back. Plain text on
#: purpose: a PDF would test a PDF library, not the eagle. The chain being
#: measured — fetch a file, read local data, write a filled copy, upload it —
#: is identical either way, and swapping the format later changes one string.
FORM_TEMPLATE = """\
REGISTRATION FORM  (fill in every line after the colon)

FULL NAME:
EMAIL:
PHONE:
CITY:
REFERENCE:
"""

#: Dropped on the Desktop by `prepare()`. The eagle has to go and read it —
#: it is never given these values in the goal.
DESKTOP_DATA = """\
My details for forms
--------------------
Full name: Shenny Cioponea
Email: shennyonthebeat@gmail.com
Phone: +40 700 111 222
City: Bucharest
Reference: EAGLE-2026-A
"""

#: The values a correct submission must contain. Checked against what the
#: SERVER received, never against what the eagle claims.
EXPECTED = {
    "name": "Shenny Cioponea",
    "email": "shennyonthebeat@gmail.com",
    "phone": "+40 700 111 222",
    "city": "Bucharest",
    "reference": "EAGLE-2026-A",
}

_LOCK = threading.Lock()
SUBMISSIONS: list[dict] = []


_PAGE = """<!doctype html><html><head><title>{title}</title></head><body>
<h1>{title}</h1>{body}</body></html>"""


def _dash() -> str:
    return _PAGE.format(title="Aethelark Test Dashboard", body="""
<p>A rig for testing a complete workflow.</p>
<p><a id="download-form" href="/form.txt" download>Download the registration form</a></p>
<p><a id="go-submit" href="/submit">Go to Submit page</a></p>
""")


def _submit_page(msg: str = "") -> str:
    return _PAGE.format(title="Submit your form", body=f"""
{f'<p id="msg">{msg}</p>' if msg else ''}
<form method="POST" action="/submit" enctype="multipart/form-data">
  <p><input type="file" name="document"></p>
  <p><button type="submit" id="submit-button">Submit form</button></p>
</form>
<p><a href="/">Back to dashboard</a></p>
""")


def _done_page(ok: bool, missing) -> str:
    if ok:
        body = "<p id='result'>ACCEPTED — every required field was present.</p>"
    else:
        body = (f"<p id='result'>REJECTED — missing or wrong: "
                f"{', '.join(missing)}</p>")
    return _PAGE.format(title="Submission received",
                        body=body + "<p><a href='/'>Back to dashboard</a></p>")


def check(text: str) -> tuple[bool, list[str]]:
    """Which expected values are absent from a submitted document.

    Substring matching, case-insensitive: the eagle may lay the form out
    differently and that is not a failure. What matters is that the values it
    had to go and READ ended up in the file it sent back.
    """
    low = (text or "").lower()
    missing = [k for k, v in EXPECTED.items() if v.lower() not in low]
    return (not missing), missing


def _first_file(body: bytes, content_type: str) -> tuple[str, str]:
    """The first uploaded file's text and name, from a multipart body.

    Only what this rig needs: one file field. Returns ("", "") for anything
    it cannot parse, which the verifier then rejects — an unreadable upload
    must never pass.
    """
    m = re.search(r"boundary=([^;]+)", content_type or "")
    if not m:
        return "", ""
    boundary = b"--" + m.group(1).strip('"').encode()
    for part in body.split(boundary):
        head, _, data = part.partition(b"\r\n\r\n")
        if b"filename=" not in head:
            continue
        name = re.search(rb'filename="([^"]*)"', head)
        return (data.rstrip(b"\r\n--").decode("utf-8", "replace"),
                name.group(1).decode() if name else "")
    return "", ""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):        # quiet: the test prints its own story
        pass

    def _send(self, body: str, code: int = 200, ctype="text/html; charset=utf-8"):
        raw = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/":
            return self._send(_dash())
        if path == "/form.txt":
            raw = FORM_TEMPLATE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Disposition",
                             'attachment; filename="registration-form.txt"')
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            return self.wfile.write(raw)
        if path == "/submit":
            return self._send(_submit_page())
        if path == "/submissions":          # for the test, not the eagle
            with _LOCK:
                return self._send(json.dumps(SUBMISSIONS, indent=2), 200,
                                  "application/json")
        if path == "/reset":
            with _LOCK:
                SUBMISSIONS.clear()
            return self._send("reset", 200, "text/plain")
        return self._send("<h1>404</h1>", 404)

    def do_POST(self):
        if self.path.split("?")[0] != "/submit":
            return self._send("<h1>404</h1>", 404)
        # Hand-rolled rather than `cgi.parse_multipart`: `cgi` is removed in
        # Python 3.13, and a rig that only runs on one interpreter is a rig
        # that stops working the week someone upgrades.
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b""
        text, filename = _first_file(raw, self.headers.get("Content-Type", ""))
        ok, missing = check(text)
        with _LOCK:
            SUBMISSIONS.append({
                "at": datetime.now().isoformat(timespec="seconds"),
                "filename": filename, "accepted": ok, "missing": missing,
                "bytes": len(text), "text": text[:2000],
            })
        return self._send(_done_page(ok, missing))


def prepare(desktop: Path | None = None) -> Path:
    """Put the data file where the eagle has to go and find it."""
    desktop = desktop or (Path.home() / "Desktop")
    desktop.mkdir(parents=True, exist_ok=True)
    target = desktop / "my-details.txt"
    target.write_text(DESKTOP_DATA, encoding="utf-8")
    return target


def serve(port: int = PORT) -> HTTPServer:
    httpd = HTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=PORT)
    args = ap.parse_args()
    data = prepare()
    serve(args.port)
    print(f"  dashboard : http://127.0.0.1:{args.port}/")
    print(f"  submit    : http://127.0.0.1:{args.port}/submit")
    print(f"  data file : {data}")
    print("  ctrl-c to stop")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
