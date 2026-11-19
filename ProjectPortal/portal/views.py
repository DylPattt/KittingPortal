import sqlite3

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from portal import etl, queries
from portal.auth import admin_required, create_user
from portal.db import audit, get_db, get_meta
from portal.notify import notify_user
from werkzeug.security import generate_password_hash

import json

main_bp = Blueprint("main", __name__)


@main_bp.route("/")
def home():
    db = get_db()
    return render_template("home.html", stats=queries.dashboard(db), changes=queries.changes_since_previous(db))


@main_bp.route("/projects")
def projects():
    rows = queries.list_projects(get_db())
    total = len(rows)
    avg = (sum(r["completion"] for r in rows) / total) if total else 0
    missing = sum(r["remaining_items"] for r in rows)
    overdue = sum(r["overdue_lines"] for r in rows)
    return render_template("projects.html", projects=rows, total=total, avg=avg,
                           missing=missing, overdue=overdue)


@main_bp.route("/critical")
def critical():
    try:
        min_days = max(0, int(request.args.get("min_days", 0)))
    except ValueError:
        min_days = 0
    q = request.args.get("q", "")
    rows = queries.critical_parts(get_db(), min_days=min_days, q=q, limit=200)
    return render_template("critical.html", rows=rows, min_days=min_days, q=q)


@main_bp.route("/materials")
def materials():
    return render_template("materials.html", suppliers=queries.supplier_keys(get_db()))


@main_bp.route("/vendors")
def vendors():
    return render_template("vendors.html", vendors=queries.vendor_directory(get_db()))


@main_bp.route("/vendors/save", methods=["POST"])
@admin_required
def vendor_save():
    f = request.form
    key = f.get("supplier_key", "").strip()
    if not key:
        abort(400)
    db = get_db()
    with db:
        db.execute(
            """INSERT INTO vendors(supplier_key,display_name,contact,phone,email,website,notes,updated_by,updated_at)
               VALUES(?,?,?,?,?,?,?,?,datetime('now','localtime'))
               ON CONFLICT(supplier_key) DO UPDATE SET display_name=excluded.display_name,
                 contact=excluded.contact, phone=excluded.phone, email=excluded.email,
                 website=excluded.website, notes=excluded.notes,
                 updated_by=excluded.updated_by, updated_at=excluded.updated_at""",
            (key, f.get("display_name"), f.get("contact"), f.get("phone"), f.get("email"),
             f.get("website"), f.get("notes"), g.user["username"]),
        )
        audit(db, "vendor_edit", key)
    flash(f"Saved vendor {key}.")
    return redirect(url_for("main.vendors") + "#v-" + key.replace(" ", "-"))


@main_bp.route("/notifications", methods=["GET", "POST"])
def notifications():
    db = get_db()
    if request.method == "POST":
        with db:
            db.execute("UPDATE notifications SET is_read=1 WHERE user_id=?", (g.user["id"],))
        return redirect(url_for("main.notifications"))
    rows = db.execute(
        "SELECT * FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT 100", (g.user["id"],)
    ).fetchall()
    return render_template("notifications.html", rows=rows)


# ---------------- admin ----------------

@main_bp.route("/admin")
@admin_required
def admin():
    db = get_db()
    requests_ = db.execute(
        """SELECT k.*, p.project_number, p.req, u.username AS requester
           FROM kit_requests k JOIN projects p ON p.id = k.project_id
           LEFT JOIN users u ON u.id = k.requested_by
           ORDER BY (k.status='requested') DESC, k.id DESC LIMIT 100"""
    ).fetchall()
    users = db.execute("SELECT * FROM users ORDER BY username").fetchall()
    stats = json.loads(get_meta(db, "refresh_stats", "{}"))
    unmatched = json.loads(get_meta(db, "unmatched_sources", "[]"))
    audit_rows = db.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT 60").fetchall()
    return render_template("admin.html", requests=requests_, users=users, stats=stats, audit_rows=audit_rows,
                           unmatched=unmatched,
                           refresh_message=get_meta(db, "refresh_message", ""),
                           refresh_status=get_meta(db, "refresh_status", ""))


@main_bp.route("/admin/refresh", methods=["POST"])
@admin_required
def admin_refresh():
    res = etl.refresh("manual")
    flash(res["message"], "ok" if res["ok"] else "error")
    return redirect(url_for("main.admin"))


@main_bp.route("/admin/requests/<int:kid>", methods=["POST"])
@admin_required
def admin_request_status(kid):
    status = request.form.get("status")
    if status not in ("requested", "sent", "cancelled"):
        abort(400)
    db = get_db()
    row = db.execute(
        "SELECT k.*, p.project_number FROM kit_requests k JOIN projects p ON p.id=k.project_id WHERE k.id=?",
        (kid,),
    ).fetchone()
    if not row:
        abort(404)
    with db:
        db.execute(
            "UPDATE kit_requests SET status=?, updated_at=datetime('now','localtime'), updated_by=? WHERE id=?",
            (status, g.user["username"], kid),
        )
        audit(db, "kit_status", f"#{kid} {row['project_number']}: {row['status']} -> {status}")
        if status != row["status"]:
            notify_user(db, row["requested_by"],
                        f"Kit for {row['project_number']} marked {status} by {g.user['username']}.",
                        link=url_for("main.projects", select=row["project_id"]),
                        subject=f"Kit {status}: {row['project_number']}")
    flash(f"Kit request #{kid} set to {status}.")
    return redirect(url_for("main.admin"))


@main_bp.route("/admin/users", methods=["POST"])
@admin_required
def admin_users():
    f, db = request.form, get_db()
    action = f.get("action")
    try:
        with db:
            if action == "create":
                username, pw = f.get("username", "").strip(), f.get("password", "")
                if not username or len(pw) < 8:
                    flash("Username required and password must be at least 8 characters.", "error")
                else:
                    create_user(db, username, pw, f.get("role", "user"), f.get("email") or None)
                    audit(db, "user_create", f"{username} ({f.get('role', 'user')})")
                    flash(f"Created user {username}.")
            else:
                uid = int(f.get("user_id", 0))
                if uid == g.user["id"] and action in ("deactivate", "make_user"):
                    flash("You can't do that to your own account.", "error")
                elif action == "deactivate":
                    db.execute("UPDATE users SET active=0 WHERE id=?", (uid,)); audit(db, "user_disable", uid); flash("User deactivated.")
                elif action == "activate":
                    db.execute("UPDATE users SET active=1 WHERE id=?", (uid,)); audit(db, "user_enable", uid); flash("User activated.")
                elif action == "make_admin":
                    db.execute("UPDATE users SET role='admin' WHERE id=?", (uid,)); audit(db, "user_make_admin", uid); flash("User is now an admin.")
                elif action == "make_user":
                    db.execute("UPDATE users SET role='user' WHERE id=?", (uid,)); audit(db, "user_make_user", uid); flash("User is now a regular user.")
                elif action == "reset":
                    pw = f.get("password", "")
                    if len(pw) < 8:
                        flash("Password must be at least 8 characters.", "error")
                    else:
                        db.execute("UPDATE users SET password_hash=? WHERE id=?", (generate_password_hash(pw), uid))
                        audit(db, "user_password_reset", uid)
                        flash("Password reset.")
    except sqlite3.IntegrityError:
        flash("That username already exists.", "error")
    return redirect(url_for("main.admin"))


# ---------------- ready to kit / POs / data quality ----------------

@main_bp.route("/ready")
def ready():
    try:
        min_c = min(100, max(0, float(request.args.get("min", 95))))
    except ValueError:
        min_c = 95
    return render_template("ready.html", rows=queries.ready_to_kit(get_db(), min_c), min_c=min_c)


@main_bp.route("/po")
def po():
    db = get_db()
    po_no = request.args.get("po", "").strip()
    if po_no:
        lines = queries.po_lines(db, po_no)
        if not lines:
            flash(f"No lines found for {po_no}.", "error")
            return redirect(url_for("main.po"))
        return render_template("po_detail.html", po=lines[0]["po"], lines=lines)
    q = request.args.get("q", "")
    return render_template("po.html", rows=queries.po_search(db, q), q=q)


@main_bp.route("/data-quality")
def data_quality():
    db = get_db()
    return render_template(
        "data_quality.html", dq=queries.data_quality(db),
        bad_numbers=json.loads(get_meta(db, "dq_bad_numbers", "[]")),
        unmatched=json.loads(get_meta(db, "unmatched_sources", "[]")),
        stats=json.loads(get_meta(db, "refresh_stats", "{}")),
        aliases=queries.aliases(db))


# ---------------- supplier aliases (admin) ----------------

@main_bp.route("/vendors/alias", methods=["POST"])
@admin_required
def vendor_alias():
    from portal.etl import supplier_key
    alias = supplier_key(request.form.get("alias_key"))
    canonical = supplier_key(request.form.get("canonical_key"))
    if not alias or not canonical or alias == canonical:
        flash("Pick two different vendor names to merge.", "error")
        return redirect(request.referrer or url_for("main.vendors"))
    db = get_db()
    with db:
        # anything already pointing at the alias now points at the new target (no chains)
        db.execute("UPDATE supplier_aliases SET canonical_key=? WHERE canonical_key=?", (canonical, alias))
        db.execute("""INSERT INTO supplier_aliases(alias_key,canonical_key,created_by) VALUES(?,?,?)
                      ON CONFLICT(alias_key) DO UPDATE SET canonical_key=excluded.canonical_key""",
                   (alias, canonical, g.user["username"]))
        db.execute("DELETE FROM supplier_aliases WHERE alias_key = canonical_key")
        audit(db, "vendor_merge", f"{alias} -> {canonical}")
    res = etl.refresh("vendor merge")
    flash(f"Merged {alias} into {canonical}. " + res["message"], "ok" if res["ok"] else "error")
    return redirect(request.referrer or url_for("main.vendors"))


@main_bp.route("/vendors/alias/delete", methods=["POST"])
@admin_required
def vendor_alias_delete():
    alias = request.form.get("alias_key", "")
    db = get_db()
    with db:
        db.execute("DELETE FROM supplier_aliases WHERE alias_key=?", (alias,))
        audit(db, "vendor_unmerge", alias)
    res = etl.refresh("vendor unmerge")
    flash(f"Removed merge for {alias}. " + res["message"], "ok" if res["ok"] else "error")
    return redirect(request.referrer or url_for("main.data_quality"))


@main_bp.route("/map")
def cart_map():
    return render_template("map.html")
