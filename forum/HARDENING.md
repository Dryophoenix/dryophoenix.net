# Server hardening guide
## forum.dryophoenix.net — and the main server generally

Opening a server to a school network means a large pool of users who are bored,
curious, and probably running Kali Linux tutorials in their spare time. This guide
addresses that threat model systematically. Work through it in order.

---

## 1. Firewall (ufw)

The firewall should be the first line of defence. Only three ports need to be
publicly reachable: SSH, HTTP (for Let's Encrypt redirects), and HTTPS.

```bash
sudo apt install ufw -y

sudo ufw default deny incoming
sudo ufw default allow outgoing

sudo ufw allow 22/tcp      # SSH — harden this port next (see §2)
sudo ufw allow 80/tcp      # HTTP (Let's Encrypt ACME + redirects)
sudo ufw allow 443/tcp     # HTTPS

sudo ufw enable
sudo ufw status verbose
```

If you later change your SSH port (recommended), update the rule:
```bash
sudo ufw delete allow 22/tcp
sudo ufw allow <new-port>/tcp
```

Never expose port 3001 (blog webhook) or 3002 (forum handler) publicly — they
only need to be reachable by nginx on localhost, which the firewall already
enforces since those services bind to 127.0.0.1.

---

## 2. SSH hardening

Default SSH configuration is the most common entry point for attacks. Edit
`/etc/ssh/sshd_config`:

```
# Disable password login — keys only
PasswordAuthentication no
ChallengeResponseAuthentication no
UsePAM no

# Disable root login entirely
PermitRootLogin no

# Change the port (makes automated scanners miss you; not security through
# obscurity alone, but meaningfully reduces log noise)
Port 2222          # pick any unused port above 1024

# Only allow your specific user
AllowUsers leah    # replace with your actual username

# Limit authentication attempts
MaxAuthTries 3
MaxSessions  3

# Disable X11 and agent forwarding (attack surface you don't need)
X11Forwarding no
AllowAgentForwarding no
AllowTcpForwarding no

# Disconnect idle sessions after 5 minutes
ClientAliveInterval 300
ClientAliveCountMax 1
```

After saving, test before disconnecting:
```bash
sudo sshd -t          # validate config — must show no errors
sudo systemctl reload sshd
# Open a second SSH session to confirm login still works BEFORE closing the first
```

---

## 3. fail2ban

fail2ban watches log files for repeated failures and bans the offending IP via
the firewall. Install and configure it for SSH and nginx.

```bash
sudo apt install fail2ban -y
```

Create `/etc/fail2ban/jail.local` (overrides the default; survives package updates):

```ini
[DEFAULT]
bantime  = 1h
findtime = 10m
maxretry = 5
backend  = systemd

[sshd]
enabled  = true
port     = 2222          # match your SSH port from §2
logpath  = %(sshd_log)s

[nginx-http-auth]
enabled  = true

[nginx-botsearch]
enabled  = true
port     = http,https
logpath  = %(nginx_error_log)s
maxretry = 2

# Catch repeated 429s from the /submit rate limiter
[nginx-req-limit]
enabled  = true
filter   = nginx-req-limit
port     = http,https
logpath  = /var/log/nginx/forum.error.log
maxretry = 10
findtime = 1m
bantime  = 1h
```

Create the filter `/etc/fail2ban/filter.d/nginx-req-limit.conf`:

```ini
[Definition]
failregex = limiting requests, excess:.* by zone.*client: <HOST>
ignoreregex =
```

Enable and start:
```bash
sudo systemctl enable --now fail2ban
sudo fail2ban-client status          # confirm jails are active
sudo fail2ban-client status sshd     # see current bans
```

---

## 4. Nginx hardening (global settings)

These go in `/etc/nginx/nginx.conf` inside the `http { }` block — they apply to
every site on the server, including the main dryophoenix.net vhost.

```nginx
http {
    # Hide nginx version from error pages and headers
    server_tokens off;

    # Rate limit zones (referenced by the forum vhost config)
    limit_req_zone $binary_remote_addr zone=forum_submit:10m rate=5r/m;
    limit_req_zone $binary_remote_addr zone=forum_global:10m  rate=30r/m;

    # Also add a zone for the main site if you want it
    limit_req_zone $binary_remote_addr zone=main_global:10m   rate=60r/m;

    # Limit body size globally (individual vhosts can override lower)
    client_max_body_size 16k;

    # Timeouts — disconnect slow/stalled connections
    client_body_timeout   10s;
    client_header_timeout 10s;
    send_timeout          30s;
    keepalive_timeout     65s;

    # Don't send buffered data back to attackers doing slowloris
    reset_timedout_connection on;

    # ...rest of your existing http block...
}
```

After any change: `sudo nginx -t && sudo systemctl reload nginx`

---

## 5. TLS / HTTPS

Use certbot to get Let's Encrypt certificates.

```bash
sudo apt install certbot python3-certbot-nginx -y

# Get cert for the forum subdomain
sudo certbot certonly --nginx -d forum.dryophoenix.net

# Also renew the main domain cert if not done yet
sudo certbot certonly --nginx -d dryophoenix.net -d www.dryophoenix.net
```

Certbot installs a systemd timer that auto-renews. Verify it's active:
```bash
sudo systemctl status certbot.timer
```

Certbot creates `/etc/letsencrypt/options-ssl-nginx.conf` which disables
TLS 1.0/1.1 and configures secure cipher suites. The nginx vhost config
already includes it.

To check your TLS rating after go-live: https://www.ssllabs.com/ssltest/

---

## 6. Automatic security updates

Servers that aren't patched get owned. `unattended-upgrades` handles this.

```bash
sudo apt install unattended-upgrades -y
sudo dpkg-reconfigure --priority=low unattended-upgrades
# Select "Yes" when prompted
```

Edit `/etc/apt/apt.conf.d/50unattended-upgrades` to enable auto-reboot for
kernel updates (optional but recommended for a personal server):

```
Unattended-Upgrade::Automatic-Reboot "true";
Unattended-Upgrade::Automatic-Reboot-Time "04:00";
```

---

## 7. Deploy the forum site

```bash
# Create the web root
sudo mkdir -p /var/www/forum.dryophoenix.net
sudo chown www-data:www-data /var/www/forum.dryophoenix.net

# Copy the forum files (run from your local machine)
rsync -av --exclude='*.md' --exclude='*.service' --exclude='nginx-forum.conf' \
  forum/ user@yourserver:/var/www/forum.dryophoenix.net/

# Copy the submit handler and service separately
scp forum/submit-handler.py  user@yourserver:/var/www/forum.dryophoenix.net/
scp forum/forum-submit.service user@yourserver:/tmp/

# On the server:
sudo cp /tmp/forum-submit.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now forum-submit
sudo systemctl status forum-submit   # should show "active (running)"
```

Install the nginx vhost:
```bash
scp forum/nginx-forum.conf user@yourserver:/tmp/

# On the server:
sudo cp /tmp/nginx-forum.conf /etc/nginx/sites-available/forum.dryophoenix.net
sudo ln -s /etc/nginx/sites-available/forum.dryophoenix.net \
           /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

---

## 8. DNS

Add an A record (and AAAA if you have IPv6) for `forum.dryophoenix.net` pointing
to the same server IP as `dryophoenix.net`. Depending on your registrar this
takes a few minutes to an hour to propagate.

```
forum   A     <your server IPv4>
forum   AAAA  <your server IPv6>   # if applicable
```

---

## 9. Read your submissions

Submissions are stored as newline-delimited JSON in:
```
/var/www/forum.dryophoenix.net/submissions.jsonl
```

To read them on the server:
```bash
sudo -u www-data cat /var/www/forum.dryophoenix.net/submissions.jsonl | python3 -m json.tool
```

Or pretty-print each line:
```bash
while IFS= read -r line; do echo "$line" | python3 -m json.tool; echo "---"; done \
  < /var/www/forum.dryophoenix.net/submissions.jsonl
```

---

## 10. Ongoing: what to watch

```bash
# Live nginx access log
sudo tail -f /var/log/nginx/forum.access.log

# Submit handler log
sudo tail -f /var/log/forum-submit.log

# Active fail2ban bans
sudo fail2ban-client status sshd
sudo fail2ban-client status nginx-req-limit

# Manually ban an IP (e.g. a persistent scanner)
sudo fail2ban-client set sshd banip <IP>

# Check for failed SSH attempts
sudo journalctl -u sshd --since "1 hour ago" | grep "Failed"
```

---

## Threat summary for the school scenario

| Threat                        | Mitigation                                              |
|-------------------------------|---------------------------------------------------------|
| SSH brute-force               | Keys only + fail2ban + port change + AllowUsers         |
| Port scanning                 | ufw (only 3 ports open) + changed SSH port              |
| Form spam / mass submission   | Rate limiting (5/min per IP) + honeypot + validation    |
| Cross-site form submission    | Origin/Referer check in handler + CSP form-action       |
| Clickjacking                  | X-Frame-Options DENY + CSP frame-ancestors none         |
| MIME sniffing attacks         | X-Content-Type-Options nosniff                          |
| Accessing internal data file  | nginx location deny for *.jsonl                         |
| Slow-connection DoS           | nginx timeout settings + body size limits               |
| Unpatched CVEs                | unattended-upgrades                                     |
| Directory traversal           | try_files + no directory listing                        |
| Exposing server software ver. | server_tokens off + more_clear_headers Server           |
