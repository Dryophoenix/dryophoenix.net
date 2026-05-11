#!/usr/bin/env python3
"""
submit-handler.py
=================
Minimal stdlib HTTP server that accepts POST /submit from the forum
registration form on forum.dryophoenix.net.

No external dependencies — stdlib only.

What it does
------------
  • Validates name + email fields (length, basic regex).
  • Rejects the request if the honeypot field is non-empty.
  • Checks the Origin / Referer header to block simple cross-site posts.
  • Appends valid submissions to a newline-delimited JSON file.
  • Returns a plain HTML success or error page.

Environment variables
---------------------
  PORT          Listening port              (default: 3002)
  DATA_FILE     Path to submissions file    (default: /var/www/forum.dryophoenix.net/submissions.jsonl)
  ALLOWED_HOST  Accepted Origin hostname    (default: forum.dryophoenix.net)
  LOG_FILE      Optional path for log file  (default: stderr)

Running
-------
  python3 submit-handler.py

  Or via systemd — see forum-submit.service in this folder.
"""

import os
import re
import json
import hmac
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse
from datetime import datetime, timezone
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────

PORT         = int(os.environ.get("PORT", 3002))
DATA_FILE    = Path(os.environ.get(
    "DATA_FILE",
    "/var/www/forum.dryophoenix.net/submissions.jsonl"
))
ALLOWED_HOST = os.environ.get("ALLOWED_HOST", "forum.dryophoenix.net")

# ── Logging ───────────────────────────────────────────────────────────────────

log_file = os.environ.get("LOG_FILE")
handlers = [logging.StreamHandler()]
if log_file:
    handlers.append(logging.FileHandler(log_file))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=handlers,
)
log = logging.getLogger("submit")

# ── Validation helpers ────────────────────────────────────────────────────────

# RFC-5322 simplified — good enough for a registration form.
_EMAIL_RE = re.compile(
    r"^[a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+"
    r"@[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?"
    r"(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*"
    r"\.[a-zA-Z]{2,}$"
)

_file_lock = threading.Lock()


def valid_email(s: str) -> bool:
    return bool(_EMAIL_RE.match(s)) and len(s) <= 254


def valid_name(s: str) -> bool:
    return 1 <= len(s.strip()) <= 100


# ── Response helpers ──────────────────────────────────────────────────────────

def _html_page(title: str, heading: str, body: str, status: int = 200) -> bytes:
    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title} — forum.dryophoenix.net</title>
  <link rel="stylesheet"
        href="https://cdn.jsdelivr.net/npm/@picocss/pico@2/css/pico.min.css">
  <style>
    main.container {{ max-width:480px; margin-top:4rem; }}
  </style>
</head>
<body>
  <main class="container">
    <h1>{heading}</h1>
    {body}
    <p><a href="/">← back</a></p>
  </main>
</body>
</html>"""
    return page.encode()


SUCCESS_HTML = _html_page(
    "Registered",
    "You're on the list.",
    "<p>Thanks — you'll be notified when the forum opens.</p>"
)

ERROR_HTML = {
    "honeypot": _html_page("Error", "Submission rejected.", "<p>Bot detected.</p>", 400),
    "origin":   _html_page("Error", "Bad request.", "<p>Invalid request origin.</p>", 403),
    "fields":   _html_page("Error", "Invalid input.", "<p>Please check your name and email and try again.</p>", 400),
    "toolarge": _html_page("Error", "Bad request.", "<p>Request body too large.</p>", 413),
    "method":   _html_page("Error", "Method not allowed.", "<p>Use POST.</p>", 405),
}


# ── Request handler ───────────────────────────────────────────────────────────

class SubmitHandler(BaseHTTPRequestHandler):

    # Max body size: 4 KB — a name + email needs ~50 bytes
    MAX_BODY = 4096

    def do_POST(self):
        if self.path != "/submit":
            self._respond(404, _html_page("Not found", "Not found.", ""))
            return

        # ── Size guard ────────────────────────────────────────────────────────
        length = int(self.headers.get("Content-Length", 0))
        if length > self.MAX_BODY:
            log.warning("Body too large (%d bytes) from %s", length, self._ip())
            self._respond(413, ERROR_HTML["toolarge"])
            return

        # ── Origin / Referer check (basic CSRF guard) ─────────────────────────
        origin  = self.headers.get("Origin",  "")
        referer = self.headers.get("Referer", "")
        source  = origin or referer
        if source:
            host = urlparse(source).netloc.split(":")[0]
            if host != ALLOWED_HOST:
                log.warning("Bad origin %r from %s", source, self._ip())
                self._respond(403, ERROR_HTML["origin"])
                return

        # ── Parse body ────────────────────────────────────────────────────────
        raw  = self.rfile.read(length).decode("utf-8", errors="replace")
        data = parse_qs(raw, keep_blank_values=True)

        def field(key):
            vals = data.get(key, [""])
            return vals[0] if vals else ""

        honeypot = field("website")
        name     = field("name").strip()
        email    = field("email").strip().lower()

        # ── Honeypot ──────────────────────────────────────────────────────────
        if honeypot:
            log.warning("Honeypot triggered from %s", self._ip())
            # Respond 200 to bots so they think it worked
            self._respond(200, ERROR_HTML["honeypot"])
            return

        # ── Field validation ──────────────────────────────────────────────────
        if not valid_name(name) or not valid_email(email):
            log.info("Invalid fields from %s: name=%r email=%r", self._ip(), name, email)
            self._respond(400, ERROR_HTML["fields"])
            return

        # ── Persist ───────────────────────────────────────────────────────────
        record = {
            "ts":    datetime.now(timezone.utc).isoformat(),
            "ip":    self._ip(),
            "name":  name,
            "email": email,
        }

        try:
            DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
            with _file_lock:
                with DATA_FILE.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(record) + "\n")
            log.info("Saved submission from %s (%s)", self._ip(), email)
        except OSError as exc:
            log.error("Could not write to %s: %s", DATA_FILE, exc)
            # Don't expose internal errors to the user
            self._respond(200, SUCCESS_HTML)
            return

        self._respond(200, SUCCESS_HTML)

    def do_GET(self):
        """Reject GET on /submit; everything else is handled by nginx."""
        if self.path == "/submit":
            self._respond(405, ERROR_HTML["method"])
        else:
            self._respond(404, _html_page("Not found", "Not found.", ""))

    def _respond(self, code: int, body: bytes):
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        # Prevent this internal response from being cached or framed
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _ip(self) -> str:
        # nginx forwards the real IP in X-Real-IP
        return self.headers.get("X-Real-IP", self.client_address[0])

    def log_message(self, fmt, *args):
        log.debug(fmt, *args)   # suppress per-request CLF noise


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    server = HTTPServer(("127.0.0.1", PORT), SubmitHandler)
    log.info("Submit handler listening on 127.0.0.1:%d", PORT)
    log.info("Data file : %s", DATA_FILE)
    log.info("Allowed host: %s", ALLOWED_HOST)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Shutting down.")
