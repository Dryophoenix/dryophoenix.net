# Blog sync — deployment guide

## How it works

```
dryophoenix/dryoblog (GitHub)
        │  push event
        ▼
webhook-server.py  (:3001)
        │  spawns
        ▼
sync-blog.py
  • git clone/pull dryoblog → /tmp/dryoblog
  • reads  monYR/N.md  folders
  • writes content/blog/monYR/N.md  with Hugo front matter
  • runs   hugo  →  public/
```

The `weight = N` front matter field controls post ordering within a month.
If your markdown files already have `+++` / `---` front matter, it is
preserved as-is (weight is injected only when missing).

---

## 1. First-time manual sync

```bash
cd /var/www/dryophoenix.net
HUGO_DIR=$(pwd) python3 tools/sync-blog.py
```

Dry-run (no files written, no Hugo rebuild):

```bash
HUGO_DIR=$(pwd) python3 tools/sync-blog.py --dry-run
```

---

## 2. Systemd service (auto-start on boot)

The secret does **not** go in the unit file — unit files in `/etc/systemd/system`
are world-readable, and `systemctl show dryoblog-sync -p Environment` needs no
privileges, so an inline `Environment="WEBHOOK_SECRET=…"` line is readable by
every local user. It lives in a root-owned 0600 env file instead.

```bash
# Edit paths in the unit file first (the secret lives elsewhere):
nano tools/dryoblog-sync.service

sudo install -o root -g root -m 0600 tools/dryoblog.env /etc/dryoblog.env
sudo cp tools/dryoblog-sync.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now dryoblog-sync

# Check status
sudo systemctl status dryoblog-sync
journalctl -u dryoblog-sync -f
```

---

## 3. Nginx — proxy /webhook to the Python server

Add inside your `server { }` block:

```nginx
location /webhook {
    # Only POST reaches the sync trigger.
    limit_except POST { deny all; }

    # Rate limit: pushes are rare, and every request costs an HMAC over the body.
    # Requires `limit_req_zone $binary_remote_addr zone=webhook:1m rate=10r/m;`
    # in the surrounding http { } block.
    limit_req          zone=webhook burst=5 nodelay;

    # GitHub push payloads are small; cap the body well below the default 1m.
    client_max_body_size 1m;

    proxy_pass         http://127.0.0.1:3001/webhook;
    proxy_set_header   X-Real-IP        $remote_addr;
    proxy_set_header   X-Hub-Signature-256  $http_x_hub_signature_256;
    proxy_set_header   X-GitHub-Event   $http_x_github_event;
    proxy_read_timeout 10s;
}
```

The webhook server binds `127.0.0.1` by default, so port 3001 is never reachable
except through this proxy. Keep it that way — a public bind would skip the limits
above along with TLS.

While you're in the config, `Strict-Transport-Security` is currently
`max-age=63072000` with no `includeSubDomains`. Consider:

```nginx
add_header Strict-Transport-Security "max-age=63072000; includeSubDomains" always;
```

(Add `; preload` only if you're ready to commit — submission to the preload list
is effectively permanent for the apex domain and every subdomain.)

Then reload: `sudo nginx -t && sudo systemctl reload nginx`

---

## 4. GitHub webhook

1. Go to **dryophoenix/dryoblog → Settings → Webhooks → Add webhook**
2. Payload URL:  `https://dryophoenix.net/webhook`
3. Content type: `application/json`
4. Secret:       generate with `python3 -c "import secrets; print(secrets.token_hex(32))"`
                 — paste the same value into `WEBHOOK_SECRET` in the service file
5. Events:       **Just the push event**
6. Click **Add webhook** — GitHub will send a ping; check logs to confirm.

---

## 5. Optional cron backup (sync every hour even without a push)

```cron
# crontab -e
0 * * * * HUGO_DIR=/var/www/dryophoenix.net python3 /var/www/dryophoenix.net/tools/sync-blog.py >> /var/log/dryoblog-cron.log 2>&1
```

---

## Environment variables

| Variable         | Default                                        | Description                        |
|------------------|------------------------------------------------|------------------------------------|
| `HUGO_DIR`       | parent of `tools/`                             | Hugo site root                     |
| `BLOG_REPO`      | `https://github.com/dryophoenix/dryoblog.git` | Git URL to clone/pull              |
| `CLONE_DIR`      | `$STATE_DIRECTORY`, else `HUGO_DIR/.cache/dryoblog` | Where the repo is cached    |
| `PORT`           | `3001`                                         | Webhook server port                |
| `BIND_HOST`      | `127.0.0.1`                                    | Listen address (keep on loopback)  |
| `WEBHOOK_SECRET` | *(none — server refuses to start)*             | GitHub webhook secret              |
| `LOG_FILE`       | *(stderr)*                                     | Optional path to a log file        |

`CLONE_DIR` must not live in `/tmp` or any other world-writable directory:
`sync-blog.py` runs `git pull` inside it, and git executes hooks and honours
`core.*` config found there — so a directory another local user can create first
becomes code execution as the service account. Under systemd it resolves to
`/var/lib/dryoblog` (created 0700 by `StateDirectory=`).
