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

```bash
# Edit paths and secret in the file first:
nano tools/dryoblog-sync.service

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
    proxy_pass         http://127.0.0.1:3001/webhook;
    proxy_set_header   X-Real-IP        $remote_addr;
    proxy_set_header   X-Hub-Signature-256  $http_x_hub_signature_256;
    proxy_set_header   X-GitHub-Event   $http_x_github_event;
    proxy_read_timeout 10s;
}
```

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
| `CLONE_DIR`      | `/tmp/dryoblog`                                | Where the repo is cached           |
| `PORT`           | `3001`                                         | Webhook server port                |
| `WEBHOOK_SECRET` | *(empty — verification disabled)*              | GitHub webhook secret              |
| `LOG_FILE`       | *(stderr)*                                     | Optional path to a log file        |
