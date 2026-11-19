import csv
import io
import re

from flask import Blueprint, Response, abort, g, jsonify, request

from portal import queries
import re as _re

from portal.db import audit, get_db, get_meta, set_meta
from portal.auth import admin_required
from portal.notify import notify_admins

api_bp = Blueprint("api", __name__, url_prefix="/api")


def _project_or_404(pid):
    p = queries.get_project(get_db(), pid)
    if not p:
        abort(404)
    return p


def _fmt_date(iso):
    return f"{iso[5:7]}/{iso[8:10]}/{iso[:4]}" if iso else ""


def _kit_json(row):
    if not row:
        return None
    return {"id": row["id"], "status": row["status"], "note": row["note"],
            "requested_by": row["requester"], "created_at": row["created_at"],
            "can_cancel": bool(g.user and (g.user["role"] == "admin" or row["requested_by"] == g.user["id"]))}


@api_bp.route("/projects/<int:pid>/shortages")
def shortages(pid):
    _project_or_404(pid)
    db = get_db()
    rows = queries.shortages(db, pid, overdue_only=request.args.get("overdue") == "1",
                             sort=request.args.get("sort", "due"))
    lines = [{
        "part_no": r["part_no"] or "", "part_key": r["part_key"], "description": r["description"] or "",
        "material": r["material"] or "", "material_type": r["material_type"] or "",
        "supplier": r["supplier"] or "", "po": r["po"] or "", "open_qty": r["open_qty"],
        "due_date": _fmt_date(r["due_date"]), "overdue": bool(r["overdue"]), "days_late": r["days_late"],
        "received_elsewhere": r["received_elsewhere"],
    } for r in rows]
    return jsonify({"lines": lines, "kit": _kit_json(queries.kit_request_for(db, pid)),
                    "last_refresh": get_meta(db, "last_refresh")})


@api_bp.route("/projects/<int:pid>/shortages.csv")
def shortages_csv(pid):
    p = _project_or_404(pid)
    rows = queries.shortages(get_db(), pid, overdue_only=request.args.get("overdue") == "1",
                             sort=request.args.get("sort", "due"))
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Part No.", "Description", "Material", "Supplier", "PO", "Qty", "Received",
                "Open Qty", "Due Date", "Days Late", "Received on other projects"])
    for r in rows:
        w.writerow([r["part_no"], r["description"], r["material"], r["supplier"], r["po"], r["qty"],
                    r["received"], r["open_qty"], _fmt_date(r["due_date"]), r["days_late"] or "",
                    r["received_elsewhere"]])
    name = re.sub(r"[^A-Za-z0-9_-]+", "_", p["project_number"]) or "project"
    # utf-8-sig so Excel opens accents/symbols correctly
    return Response("\ufeff" + buf.getvalue(), mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="shortages_{name}.csv"'})


@api_bp.route("/projects/<int:pid>/replacements")
def replacements(pid):
    _project_or_404(pid)
    part = request.args.get("part", "").strip()
    if not part:
        abort(400)
    rows = queries.replacements(get_db(), pid, part)
    return jsonify([{
        "project_id": r["project_id"], "project_number": r["project_number"], "req": r["req"] or "",
        "completion": r["completion"], "received": r["received"], "qty": r["qty"],
        "open_qty": r["open_qty"], "po": r["po"] or "", "supplier": r["supplier"] or "",
    } for r in rows])


@api_bp.route("/projects/<int:pid>/kit-request", methods=["POST"])
def kit_request(pid):
    p = _project_or_404(pid)
    db = get_db()
    if queries.kit_request_for(db, pid):
        return jsonify({"error": "This kit already has an active request."}), 409
    note = ((request.get_json(silent=True) or {}).get("note") or "").strip()[:500]
    with db:
        db.execute("INSERT INTO kit_requests(project_id,requested_by,note) VALUES(?,?,?)",
                   (pid, g.user["id"], note))
        audit(db, "kit_request", f"{p['project_number']}" + (f": {note}" if note else ""))
        notify_admins(db, f"Kit requested for {p['project_number']} ({p['req'] or 'no REQ'}) "
                          f"by {g.user['username']}." + (f" Note: {note}" if note else ""),
                      link=f"/projects?select={pid}", subject=f"Kit requested: {p['project_number']}")
    return jsonify({"kit": _kit_json(queries.kit_request_for(db, pid))}), 201


@api_bp.route("/kit-requests/<int:kid>/cancel", methods=["POST"])
def kit_cancel(kid):
    db = get_db()
    row = db.execute("SELECT k.*, p.project_number FROM kit_requests k JOIN projects p ON p.id=k.project_id "
                     "WHERE k.id=?", (kid,)).fetchone()
    if not row:
        abort(404)
    if g.user["role"] != "admin" and row["requested_by"] != g.user["id"]:
        abort(403)
    with db:
        db.execute("UPDATE kit_requests SET status='cancelled', updated_at=datetime('now','localtime'), "
                   "updated_by=? WHERE id=? AND status='requested'", (g.user["username"], kid))
        audit(db, "kit_cancel", f"#{kid} {row['project_number']}")
        notify_admins(db, f"Kit request for {row['project_number']} cancelled by {g.user['username']}.",
                      link=f"/projects?select={row['project_id']}")
    return jsonify({"ok": True})


@api_bp.route("/materials")
def materials():
    try:
        page = int(request.args.get("page", 1))
    except ValueError:
        page = 1
    rows, total, page, pages = queries.materials_page(
        get_db(), q=request.args.get("q", ""), supplier=request.args.get("supplier", ""),
        open_only=request.args.get("open") == "1", sort=request.args.get("sort", "open"), page=page)
    return jsonify({
        "total": total, "page": page, "pages": pages,
        "rows": [{
            "part_no": r["part_no"], "part_key": r["part_key"], "description": r["description"] or "",
            "supplier": r["display_name"] or r["supplier"] or "", "vendor_id": r["vendor_id"] or "",
            "price": r["price"], "po": r["po"] or "", "due_date": _fmt_date(r["due_date"]),
            "uses": r["uses"], "open_total": r["open_total"] or 0,
            "contact": " · ".join(x for x in (r["contact"], r["phone"], r["email"]) if x),
            "website": r["website"] if (r["website"] or "").startswith(("https://", "http://")) else "",
        } for r in rows],
    })


@api_bp.route("/materials/detail")
def material_detail():
    part = request.args.get("part", "").strip()
    if not part:
        abort(400)
    return jsonify([{
        "project_id": r["project_id"], "project_number": r["project_number"] or "(unmatched file)",
        "req": r["req"] or "", "qty": r["qty"], "received": r["received"], "open_qty": r["open_qty"],
        "due_date": _fmt_date(r["due_date"]), "po": r["po"] or "", "supplier": r["supplier"] or "",
        "price": r["price"],
    } for r in queries.part_detail(get_db(), part)])


# ---------------- cart map ----------------

CART_W, CART_H, GAP = 220, 170, 20


def _clamp_box(data, size, current=None):
    W, H = size
    cur = dict(current) if current else {"x": GAP, "y": GAP, "w": CART_W, "h": CART_H}
    out = {}
    for k in ("x", "y", "w", "h"):
        try:
            out[k] = int(round(float(data.get(k, cur[k]))))
        except (TypeError, ValueError):
            out[k] = cur[k]
    out["w"] = min(max(out["w"], 120), 600)
    out["h"] = min(max(out["h"], 80), 500)
    out["x"] = min(max(out["x"], 0), W - out["w"])
    out["y"] = min(max(out["y"], 0), H - out["h"])
    return out


def _set_size(db, w, h):
    set_meta(db, "map_width", int(w))
    set_meta(db, "map_height", int(h))


def _free_slot(db, size, w=CART_W, h=CART_H):
    """First grid spot that doesn't overlap an existing cart. Grows the map downward if it's full."""
    W, H = size
    boxes = [tuple(r) for r in db.execute("SELECT x, y, w, h FROM carts")]
    free = lambda x, y: all(x + w + GAP <= bx or bx + bw + GAP <= x or y + h + GAP <= by or by + bh + GAP <= y
                            for bx, by, bw, bh in boxes)
    y = GAP
    while y + h <= H - GAP:
        x = GAP
        while x + w <= W - GAP:
            if free(x, y):
                return x, y, size
            x += w + GAP
        y += h + GAP
    bottom = max((by + bh for _, by, _, bh in boxes), default=0) + GAP
    new_h = min(queries.MAP_MAX_H, max(H, bottom + h + GAP))
    _set_size(db, W, new_h)
    return GAP, min(bottom, new_h - h), (W, new_h)


def _natural(name):
    return [int(t) if t.isdigit() else t.lower() for t in _re.split(r"(\d+)", name)]


def _cart_or_404(db, cid):
    row = db.execute("SELECT * FROM carts WHERE id=?", (cid,)).fetchone()
    if not row:
        abort(404)
    return row


@api_bp.route("/map")
def map_data():
    data = queries.map_data(get_db())
    data["can_edit"] = bool(g.user and g.user["role"] == "admin")
    return jsonify(data)


@api_bp.route("/map/carts", methods=["POST"])
@admin_required
def cart_create():
    db = get_db()
    body = request.get_json(silent=True) or {}
    n = db.execute("SELECT COUNT(*) FROM carts").fetchone()[0]
    name = (body.get("name") or f"Cart {n + 1}").strip()[:60]
    with db:
        x, y, size = _free_slot(db, queries.map_size(db))
        box = _clamp_box({"x": x, "y": y}, size)
        cur = db.execute("INSERT INTO carts(name,x,y,w,h) VALUES(?,?,?,?,?)", (name, box["x"], box["y"], box["w"], box["h"]))
        audit(db, "cart_create", name)
    return jsonify({"id": cur.lastrowid}), 201


@api_bp.route("/map/carts/bulk", methods=["POST"])
@admin_required
def cart_bulk_create():
    db = get_db()
    body = request.get_json(silent=True) or {}
    try:
        count = int(body.get("count", 0)); start = int(body.get("start", 1))
    except (TypeError, ValueError):
        return jsonify({"error": "Count and starting number must be whole numbers."}), 400
    if not 1 <= count <= 200:
        return jsonify({"error": "Add between 1 and 200 carts at a time."}), 400
    prefix = (body.get("prefix") or "Cart").strip()[:50]
    with db:
        for i in range(count):
            x, y, size = _free_slot(db, queries.map_size(db))
            box = _clamp_box({"x": x, "y": y}, size)
            db.execute("INSERT INTO carts(name,x,y,w,h) VALUES(?,?,?,?,?)",
                       (f"{prefix} {start + i}", box["x"], box["y"], box["w"], box["h"]))
        audit(db, "cart_bulk_create", f"{count} carts: {prefix} {start}–{start + count - 1}")
    return jsonify({"ok": True, "created": count}), 201


@api_bp.route("/map/arrange", methods=["POST"])
@admin_required
def cart_arrange():
    """Lay every cart out in rows, sorted by name (Cart 2 before Cart 10), keeping each cart's size."""
    db = get_db()
    W, H = queries.map_size(db)
    carts = sorted(db.execute("SELECT id, name, w, h FROM carts").fetchall(), key=lambda c: _natural(c["name"]))
    x = y = GAP
    row_h = 0
    with db:
        for c in carts:
            if x + c["w"] > W - GAP and x > GAP:
                x, y, row_h = GAP, y + row_h + GAP, 0
            db.execute("UPDATE carts SET x=?, y=? WHERE id=?", (x, y, c["id"]))
            x += c["w"] + GAP
            row_h = max(row_h, c["h"])
        needed = y + row_h + GAP
        if needed > H:
            _set_size(db, W, min(queries.MAP_MAX_H, needed))
        audit(db, "map_arrange", f"{len(carts)} carts")
    return jsonify({"ok": True})


@api_bp.route("/map/size", methods=["POST"])
@admin_required
def map_resize():
    db = get_db()
    body = request.get_json(silent=True) or {}
    try:
        w, h = int(body.get("width")), int(body.get("height"))
    except (TypeError, ValueError):
        return jsonify({"error": "Width and height must be whole numbers."}), 400
    need_w, need_h = db.execute("SELECT IFNULL(MAX(x+w),0), IFNULL(MAX(y+h),0) FROM carts").fetchone()
    lo_w, lo_h = max(queries.MAP_MIN_W, need_w + GAP), max(queries.MAP_MIN_H, need_h + GAP)
    w = min(max(w, lo_w), queries.MAP_MAX_W)
    h = min(max(h, lo_h), queries.MAP_MAX_H)
    with db:
        _set_size(db, w, h)
        audit(db, "map_resize", f"{w} x {h}")
    return jsonify({"ok": True, "width": w, "height": h,
                    "note": "Grown to fit existing carts." if (w == lo_w or h == lo_h) and (w > int(body.get("width")) or h > int(body.get("height"))) else ""})


@api_bp.route("/map/carts/<int:cid>", methods=["POST"])
@admin_required
def cart_update(cid):
    db = get_db()
    cart = _cart_or_404(db, cid)
    body = request.get_json(silent=True) or {}
    box = _clamp_box(body, queries.map_size(db), cart)
    name = (body.get("name", cart["name"]) or cart["name"]).strip()[:60]
    notes = (body.get("notes", cart["notes"]) or "").strip()[:500]
    with db:
        db.execute("UPDATE carts SET name=?, notes=?, x=?, y=?, w=?, h=? WHERE id=?",
                   (name, notes, box["x"], box["y"], box["w"], box["h"], cid))
        if name != cart["name"] or notes != (cart["notes"] or ""):
            audit(db, "cart_edit", f"{cart['name']} -> {name}")
    return jsonify({"ok": True, **box, "name": name, "notes": notes})


@api_bp.route("/map/carts/<int:cid>/delete", methods=["POST"])
@admin_required
def cart_delete(cid):
    db = get_db()
    cart = _cart_or_404(db, cid)
    with db:
        db.execute("DELETE FROM cart_projects WHERE cart_id=?", (cid,))
        db.execute("DELETE FROM carts WHERE id=?", (cid,))
        audit(db, "cart_delete", cart["name"])
    return jsonify({"ok": True})


@api_bp.route("/map/carts/<int:cid>/assign", methods=["POST"])
@admin_required
def cart_assign(cid):
    db = get_db()
    cart = _cart_or_404(db, cid)
    pid = (request.get_json(silent=True) or {}).get("project_id")
    project = queries.get_project(db, pid) if isinstance(pid, int) else None
    if not project:
        return jsonify({"error": "Unknown project."}), 400
    prev = db.execute("SELECT c.name FROM cart_projects cp JOIN carts c ON c.id=cp.cart_id WHERE cp.project_id=?",
                      (pid,)).fetchone()
    with db:
        db.execute("""INSERT INTO cart_projects(project_id,cart_id,assigned_by) VALUES(?,?,?)
                      ON CONFLICT(project_id) DO UPDATE SET cart_id=excluded.cart_id,
                        assigned_by=excluded.assigned_by, assigned_at=datetime('now','localtime')""",
                   (pid, cid, g.user["username"]))
        audit(db, "cart_assign", f"{project['project_number']} -> {cart['name']}" + (f" (moved from {prev['name']})" if prev else ""))
    return jsonify({"ok": True, "moved_from": prev["name"] if prev else None})


@api_bp.route("/map/unassign", methods=["POST"])
@admin_required
def cart_unassign():
    db = get_db()
    pid = (request.get_json(silent=True) or {}).get("project_id")
    row = db.execute("""SELECT p.project_number, c.name FROM cart_projects cp JOIN projects p ON p.id=cp.project_id
                        JOIN carts c ON c.id=cp.cart_id WHERE cp.project_id=?""", (pid,)).fetchone()
    if row:
        with db:
            db.execute("DELETE FROM cart_projects WHERE project_id=?", (pid,))
            audit(db, "cart_unassign", f"{row['project_number']} from {row['name']}")
    return jsonify({"ok": True})
