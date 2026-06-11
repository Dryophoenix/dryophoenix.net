"""
dryophoenix mailing list app
Flask + SQLite backend for managing form subscribers.
"""

import csv
import io
import os
import sqlite3
from dotenv import load_dotenv
from datetime import datetime
from functools import wraps

from flask import (
    Flask,
    Response,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

app = Flask(__name__)

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────
# Change these before deploying!
app.secret_key = os.getenv("SECRET_KEY")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")
WEBHOOK_SECRET = os.getenv(
    "WEBHOOK_SECRET"
)  # send this as X-Webhook-Secret header from Power Automate

DATABASE = os.path.join(os.path.dirname(__file__), "subscribers.db")


# ── Database helpers ──────────────────────────────────────────────────────────
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row  # rows behave like dicts
    return g.db


@app.teardown_appcontext
def close_db(exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(DATABASE)
    db.execute("""
        CREATE TABLE IF NOT EXISTS subscribers (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            email          TEXT    UNIQUE NOT NULL,
            name           TEXT,
            subscribed_at  TEXT    DEFAULT (datetime('now')),
            active         INTEGER DEFAULT 1,
            notes          TEXT
        )
    """)
    db.commit()
    db.close()


# ── Auth helpers ──────────────────────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)

    return decorated


# ── Auth routes ───────────────────────────────────────────────────────────────
@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        if request.form.get("password") == ADMIN_PASSWORD:
            session["logged_in"] = True
            return redirect(url_for("index"))
        error = "Wrong password."
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ── Admin UI ──────────────────────────────────────────────────────────────────
@app.route("/")
@login_required
def index():
    db = get_db()
    search = request.args.get("q", "").strip()
    show = request.args.get("show", "active")  # active | all | inactive

    query = "SELECT * FROM subscribers WHERE 1=1"
    params = []

    if show == "active":
        query += " AND active = 1"
    elif show == "inactive":
        query += " AND active = 0"

    if search:
        query += " AND (email LIKE ? OR name LIKE ?)"
        params += [f"%{search}%", f"%{search}%"]

    query += " ORDER BY subscribed_at DESC"
    subscribers = db.execute(query, params).fetchall()
    total = db.execute("SELECT COUNT(*) FROM subscribers").fetchone()[0]
    active = db.execute("SELECT COUNT(*) FROM subscribers WHERE active=1").fetchone()[0]

    return render_template(
        "index.html",
        subscribers=subscribers,
        total=total,
        active=active,
        search=search,
        show=show,
    )


@app.route("/subscriber/<int:sub_id>/toggle", methods=["POST"])
@login_required
def toggle_active(sub_id):
    db = get_db()
    db.execute("UPDATE subscribers SET active = 1 - active WHERE id = ?", (sub_id,))
    db.commit()
    return redirect(request.referrer or url_for("index"))


@app.route("/subscriber/<int:sub_id>/notes", methods=["POST"])
@login_required
def update_notes(sub_id):
    db = get_db()
    notes = request.form.get("notes", "")
    db.execute("UPDATE subscribers SET notes = ? WHERE id = ?", (notes, sub_id))
    db.commit()
    return redirect(request.referrer or url_for("index"))


@app.route("/subscriber/<int:sub_id>/delete", methods=["POST"])
@login_required
def delete_subscriber(sub_id):
    db = get_db()
    db.execute("DELETE FROM subscribers WHERE id = ?", (sub_id,))
    db.commit()
    return redirect(url_for("index"))


# ── CSV export ────────────────────────────────────────────────────────────────
@app.route("/export.csv")
@login_required
def export_csv():
    db = get_db()
    rows = db.execute(
        "SELECT id, email, name, subscribed_at, active, notes "
        "FROM subscribers WHERE active = 1 ORDER BY subscribed_at DESC"
    ).fetchall()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["id", "email", "name", "subscribed_at", "active", "notes"])
    for row in rows:
        writer.writerow(list(row))

    buf.seek(0)
    filename = f"subscribers_{datetime.now().strftime('%Y%m%d')}.csv"
    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ── Webhook (Power Automate → here) ──────────────────────────────────────────
@app.route("/webhook", methods=["POST"])
def webhook():
    # Verify the shared secret sent by Power Automate
    secret = request.headers.get("X-Webhook-Secret", "")
    if secret != WEBHOOK_SECRET:
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    name = (data.get("name") or "").strip()

    if not email:
        return jsonify({"error": "email required"}), 400

    db = get_db()
    try:
        db.execute("INSERT INTO subscribers (email, name) VALUES (?, ?)", (email, name))
        db.commit()
        return jsonify({"status": "created", "email": email}), 201
    except sqlite3.IntegrityError:
        # Already exists — not an error, just a duplicate form submission
        return jsonify({"status": "already_exists", "email": email}), 200


# ── Manual add (from admin UI) ────────────────────────────────────────────────
@app.route("/add", methods=["POST"])
@login_required
def add_subscriber():
    email = request.form.get("email", "").strip().lower()
    name = request.form.get("name", "").strip()
    if not email:
        return redirect(url_for("index"))
    db = get_db()
    try:
        db.execute("INSERT INTO subscribers (email, name) VALUES (?, ?)", (email, name))
        db.commit()
    except sqlite3.IntegrityError:
        pass  # duplicate — silently ignore
    return redirect(url_for("index"))


# ── CSV import ────────────────────────────────────────────────────────────────
@app.route("/import", methods=["POST"])
@login_required
def import_csv():
    f = request.files.get("csvfile")
    if not f:
        return redirect(url_for("index"))

    stream = io.StringIO(f.stream.read().decode("utf-8-sig"))
    reader = csv.DictReader(stream)
    db = get_db()
    imported = 0
    for row in reader:
        email = (row.get("email") or row.get("Email") or "").strip().lower()
        name = (row.get("name") or row.get("Name") or "").strip()
        if not email:
            continue
        try:
            db.execute(
                "INSERT INTO subscribers (email, name) VALUES (?, ?)", (email, name)
            )
            imported += 1
        except sqlite3.IntegrityError:
            pass
    db.commit()
    return redirect(url_for("index"))


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    init_db()
    app.run(host="127.0.0.1", port=5000, debug=False)
