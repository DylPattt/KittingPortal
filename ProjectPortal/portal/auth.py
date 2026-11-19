"""Login, sessions, CSRF protection and role checks.

Every page requires login except /login. Swap `check_credentials` for Entra ID / AD
later without touching the rest of the app.
"""
import secrets
import time
from functools import wraps
from urllib.parse import urlparse

from flask import (Blueprint, abort, flash, g, jsonify, redirect, render_template,
                   request, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

from portal.db import get_db

auth_bp = Blueprint("auth", __name__)

PUBLIC_ENDPOINTS = {"auth.login", "static"}
_failures = {}          # ip -> (count, first_failure_time)
MAX_FAILS, LOCK_SECONDS = 5, 300


def create_user(conn, username, password, role="user", email=None):
    conn.execute(
        "INSERT INTO users(username,password_hash,role,email) VALUES(?,?,?,?)",
        (username.strip(), generate_password_hash(password), role, email),
    )


def check_credentials(conn, username, password):
    """Replace this function to plug in Entra ID / Active Directory."""
    row = conn.execute("SELECT * FROM users WHERE username=? AND active=1", (username.strip(),)).fetchone()
    if row and check_password_hash(row["password_hash"], password):
        return row
    return None


def csrf_token():
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_urlsafe(32)
    return session["_csrf"]


def _is_api():
    return request.path.startswith("/api/")


def _safe_next(target):
    return target if target and urlparse(target).netloc == "" and target.startswith("/") else url_for("main.home")


def init_auth(app):
    app.register_blueprint(auth_bp)
    app.jinja_env.globals["csrf_token"] = csrf_token

    @app.before_request
    def gate():
        g.user = None
        uid = session.get("uid")
        if uid:
            g.user = get_db().execute("SELECT * FROM users WHERE id=? AND active=1", (uid,)).fetchone()
            if g.user is None:
                session.clear()

        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            sent = request.headers.get("X-CSRF-Token") or request.form.get("_csrf")
            if not sent or not secrets.compare_digest(sent, session.get("_csrf", "")):
                abort(400, "Missing or invalid CSRF token. Reload the page and try again.")

        if request.endpoint in PUBLIC_ENDPOINTS or g.user:
            return None
        if _is_api():
            return jsonify({"error": "Login required"}), 401
        return redirect(url_for("auth.login", next=request.full_path.rstrip("?")))

    @app.context_processor
    def inject_user():
        unread = 0
        if g.get("user"):
            unread = get_db().execute(
                "SELECT COUNT(*) FROM notifications WHERE user_id=? AND is_read=0", (g.user["id"],)
            ).fetchone()[0]
        return {"current_user": g.get("user"), "unread_count": unread}


def admin_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if not g.user or g.user["role"] != "admin":
            if _is_api():
                return jsonify({"error": "Admin access required"}), 403
            abort(403)
        return fn(*a, **kw)
    return wrapper


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("main.home"))

    error = None
    if request.method == "POST":
        ip = request.remote_addr or "?"
        count, first = _failures.get(ip, (0, time.time()))
        if count >= MAX_FAILS and time.time() - first < LOCK_SECONDS:
            error = "Too many failed attempts. Try again in a few minutes."
        else:
            user = check_credentials(get_db(), request.form.get("username", ""), request.form.get("password", ""))
            if user:
                _failures.pop(ip, None)
                session.clear()
                session["uid"] = user["id"]
                session.permanent = True
                return redirect(_safe_next(request.args.get("next")))
            if time.time() - first >= LOCK_SECONDS:
                count, first = 0, time.time()
            _failures[ip] = (count + 1, first)
            error = "Invalid username or password."
    return render_template("login.html", error=error)


@auth_bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("Signed out.")
    return redirect(url_for("auth.login"))
