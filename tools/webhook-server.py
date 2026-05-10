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
  WEBHOOK_SECRET    GitHub webhook secret    (recommended; leave blank to skip verification)
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
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────

PORT    = int(os.environ.get("PORT", 3001))
SECRET  = os.environ.get("WEBHOOK_SECRET", "").encode()
SCRIPT  = Path(__file__).resolve().parent / "sync-blog.py"
HUGO_DIR = os.environ.get("HUGO_DIR", str(Path(__file__).resolve().parent.parent))

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
        env = {**os.environ, "HUGO_DIR": HUGO_DIR}
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

        length = int(self.headers.get("Content-Length", 0))
        body   = self.rfile.read(length)

        # ── Signature verification ────────────────────────────────────────────
        if SECRET:
            sig_header = self.headers.get("X-Hub-Signature-256", "")
            expected   = "sha256=" + hmac.new(SECRET, body, hashlib.sha256).hexdigest()
            if not hmac.compare_digest(sig_header, expected):
                log.warning("Bad HMAC signature from %s", self.client_address[0])
                self._respond(401, "Invalid signature")
                return
        # ─────────────────────────────────────────────────────────────────────

        event = self.headers.get("X-GitHub-Event", "")
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


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    server = HTTPServer(("", PORT), WebhookHandler)
    log.info("Webhook server listening on :%d", PORT)
    log.info("Sync script : %s", SCRIPT)
    log.info("Hugo dir    : %s", HUGO_DIR)
    if not SECRET:
        log.warning("WEBHOOK_SECRET is not set — signature verification disabled!")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Shutting down.")
