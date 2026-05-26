# dryophoenix mailing list — setup guide

## What this is

A self-hosted subscriber management app sitting behind your existing nginx server.
Flask handles the logic, SQLite stores the data, gunicorn keeps it running.

---

## 1. Get the files onto your server

```bash
# From your local machine — copy the whole folder up
scp -r dryophoenix-mailer/ ubuntu@dryophoenix.net:~/
```

Or clone/pull from a repo if you put it in one.

---

## 2. On the server — create a virtualenv and install deps

```bash
cd ~/dryophoenix-mailer
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

---

## 3. Edit the three secrets in app.py

Open `app.py` and change these three lines near the top:

```python
app.secret_key   = "CHANGE_ME_TO_SOMETHING_RANDOM"   # any long random string
ADMIN_PASSWORD   = "CHANGE_ME_TOO"                    # your login password
WEBHOOK_SECRET   = "CHANGE_ME_POWER_AUTOMATE_SECRET"  # shared secret with Power Automate
```

Generate a good secret key with:
```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

---

## 4. Initialise the database

```bash
source venv/bin/activate
python3 app.py   # starts the app and creates subscribers.db, then Ctrl+C
```

Or run it properly once to create the db:
```bash
python3 -c "from app import init_db; init_db()"
```

---

## 5. Install the systemd service

```bash
sudo cp mailer.service /etc/systemd/system/
# Edit the file to make sure User= and WorkingDirectory= match your setup
sudo systemctl daemon-reload
sudo systemctl enable mailer
sudo systemctl start mailer
sudo systemctl status mailer   # should say "active (running)"
```

---

## 6. Update nginx

```bash
sudo cp nginx-mailer.conf /etc/nginx/sites-available/mailer
sudo ln -s /etc/nginx/sites-available/mailer /etc/nginx/sites-enabled/
sudo nginx -t          # test config — must say "syntax is ok"
sudo systemctl reload nginx
```

> **If nginx already has a config for dryophoenix.net**, don't create a new
> server block — just add the `location /mailer/` and `location /webhook`
> blocks into your existing config file.

---

## 7. (Recommended) Enable HTTPS with certbot

```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d dryophoenix.net
```

Certbot will patch your nginx config automatically and set up auto-renewal.

---

## 8. Configure Power Automate

In your existing Power Automate flow, after the "Get response details" step, add:

- **Action**: HTTP
- **Method**: POST
- **URI**: `https://dryophoenix.net/webhook`
- **Headers**:
  - `Content-Type`: `application/json`
  - `X-Webhook-Secret`: *(your WEBHOOK_SECRET from app.py)*
- **Body**:
```json
{
  "email": "@{outputs('Get_response_details')?['body/rb_email_field']}",
  "name":  "@{outputs('Get_response_details')?['body/rb_name_field']}"
}
```
*(replace the field names with your actual Microsoft Forms field names)*

---

## Admin UI

Visit `https://dryophoenix.net/mailer/` — you'll see the login page.

From there you can:
- View all subscribers with live search
- Filter active / inactive / all
- Add subscribers manually
- Import from a CSV (needs an `email` column)
- Edit notes per subscriber
- Deactivate or delete subscribers
- Export active subscribers as CSV

---

## File layout

```
dryophoenix-mailer/
├── app.py              ← Flask app (routes, db logic)
├── requirements.txt    ← Flask + gunicorn
├── subscribers.db      ← SQLite database (created on first run)
├── mailer.service      ← systemd service file
├── nginx-mailer.conf   ← nginx location blocks
├── README.md           ← this file
└── templates/
    ├── base.html       ← shared layout + styles
    ├── login.html      ← login page
    └── index.html      ← subscriber dashboard
```

---

## Useful commands once running

```bash
sudo systemctl status mailer        # is it running?
sudo systemctl restart mailer       # restart after editing app.py
sudo journalctl -u mailer -f        # live logs
tail -f /var/log/mailer-error.log   # gunicorn errors
```
