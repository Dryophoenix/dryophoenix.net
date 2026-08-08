#!/usr/bin/env python3
"""
webhook-server.py
=================
Minimal HTTP server that listens for GitHub webhook push events and
triggers sync-blog.py.  No external dependencies — stdlib only.

Setup on GitHub
---------------
  Repository → Settings → Webhooks → Add webhook
    Payload URL:   http://your-server:3001/webhook
    Content type:  application/json
    Secret:        (generate one and set WEBHOOK_SECRET below)
    Events:        Just the push event

Environment variables
---------------------
  PORT              Listening port           (default: 3001)
  BIND_HOST         Listening address        (default: 127.0.0.1 — nginx proxies to it)
  WEBHOOK_SECRET    GitHub webhook secret    (REQUIRED — the server refuses to start without it)
  HUGO_DIR          Hugo site root           (default: parent of this script)
  LOG_FILE          Path to log file         (default: stderr)

Running
-------
  python3 webhook-server.py

  Or as a background service — see dryoblog-sync.service in this folder.
"""

import os
import sys
import hmac
import hashlib
import logging
import subprocess
import threading
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────

PORT      = int(os.environ.get("PORT", 3001))
# Bind to loopback only: nginx proxies to 127.0.0.1:3001, so there is no reason
# to expose this port on a public interface where it would bypass nginx's
# request limits and TLS entirely.
BIND_HOST = os.environ.get("BIND_HOST", "127.0.0.1")
SECRET    = os.environ.get("WEBHOOK_SECRET", "").encode()
SCRIPT    = Path(__file__).resolve().parent / "sync-blog.py"
HUGO_DIR  = os.environ.get("HUGO_DIR", str(Path(__file__).resolve().parent.parent))

# Reject anything larger outright rather than buffering an attacker-chosen
# Content-Length into memory. GitHub push payloads are well under this.
MAX_BODY = 5 * 1024 * 1024

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
log = logging.getLogger("webhook")

# ── Sync runner (runs in background thread so webhook returns fast) ────────────

_sync_lock = threading.Lock()


def run_sync():
    """Acquire lock so concurrent pushes don't spawn duplicate syncs."""
    if not _sync_lock.acquire(blocking=False):
        log.info("Sync already running — skipping duplicate trigger.")
        return
    try:
        log.info("Starting sync-blog.py…")
        # The sync script has no use for the webhook secret — don't hand it a
        # copy that would show up in its /proc/<pid>/environ or any crash dump.
        env = {k: v for k, v in os.environ.items() if k != "WEBHOOK_SECRET"}
        env["HUGO_DIR"] = HUGO_DIR
        result = subprocess.run(
            [sys.executable, str(SCRIPT)],
            env=env,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            log.info("Sync complete.\n%s", result.stdout.strip())
        else:
            log.error("Sync failed (exit %d):\n%s", result.returncode, result.stderr.strip())
    finally:
        _sync_lock.release()


# ── Request handler ───────────────────────────────────────────────────────────

class WebhookHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        """Health-check endpoint."""
        if self.path == "/health":
            self._respond(200, "OK")
        else:
            self._respond(404, "Not found")

    def do_POST(self):
        if self.path != "/webhook":
            self._respond(404, "Not found")
            return

        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            self._respond(400, "Bad Content-Length")
            return
        if length < 0 or length > MAX_BODY:
            self._respond(413, "Payload too large")
            return
        body = self.rfile.read(length)

        # ── Signature verification ────────────────────────────────────────────
        # SECRET is guaranteed non-empty (checked at startup), so this can never
        # be skipped — an unsigned request is always rejected.
        sig_header = self.headers.get("X-Hub-Signature-256", "").encode()
        expected   = b"sha256=" + hmac.new(SECRET, body, hashlib.sha256).hexdigest().encode()
        if not hmac.compare_digest(sig_header, expected):
            log.warning("Bad HMAC signature from %s", self.client_address[0])
            self._respond(401, "Invalid signature")
            return
        # ─────────────────────────────────────────────────────────────────────

        # Header value reaches the log file verbatim — strip newlines so a
        # crafted value can't forge extra log lines.
        event = self.headers.get("X-GitHub-Event", "")[:32]
        event = event.replace("\r", "").replace("\n", "")
        log.info("Received GitHub event: %s from %s", event, self.client_address[0])

        if event == "push":
            threading.Thread(target=run_sync, daemon=True).start()
            self._respond(202, "Sync triggered")
        elif event == "ping":
            self._respond(200, "pong")
        else:
            self._respond(200, "Ignored")

    def _respond(self, code: int, body: str):
        encoded = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, fmt, *args):
        log.debug(fmt, *args)   # suppress per-request CLF noise to debug level


# Drop connections that stall mid-request instead of letting one idle socket
# occupy a handler indefinitely. Left on HTTP/1.0 so each connection closes
# after its response rather than being held open.
WebhookHandler.timeout = 15


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Fail closed: without a secret every unauthenticated POST would trigger a
    # deploy, so refuse to start rather than serving an open endpoint.
    if not SECRET:
        log.error("WEBHOOK_SECRET is not set — refusing to start an unauthenticated webhook.")
        sys.exit(1)

    server = ThreadingHTTPServer((BIND_HOST, PORT), WebhookHandler)
    log.info("Webhook server listening on %s:%d", BIND_HOST, PORT)
    log.info("Sync script : %s", SCRIPT)
    log.info("Hugo dir    : %s", HUGO_DIR)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Shutting down.")
