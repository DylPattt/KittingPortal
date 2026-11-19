"""All SQL lives here so views stay thin."""
from datetime import date

SORTS = {
    "due":  "(b.due_date IS NULL), b.due_date ASC, b.open_qty DESC",
    "late": "(b.due_date IS NULL OR b.due_date >= :today), b.due_date ASC",
    "qty":  "b.open_qty DESC",
    "part": "b.part_key ASC",
}


def today():
    return date.today().isoformat()


def list_projects(conn):
    return conn.execute(
        """
        SELECT p.*,
          (SELECT COUNT(*) FROM bom_lines b WHERE b.project_id=p.id AND b.open_qty>0
              AND b.due_date IS NOT NULL AND b.due_date < :today) AS overdue_lines,
          (SELECT k.status FROM kit_requests k WHERE k.project_id=p.id AND k.status IN ('requested','sent')
              ORDER BY k.id DESC LIMIT 1) AS kit_status,
          (SELECT MAX(b.due_date) FROM bom_lines b WHERE b.project_id=p.id AND b.open_qty>0) AS expected_complete,
          cp.cart_id, c.name AS cart_name
        FROM projects p
        LEFT JOIN cart_projects cp ON cp.project_id = p.id
        LEFT JOIN carts c ON c.id = cp.cart_id
        WHERE p.active=1
        ORDER BY p.completion ASC, p.project_number
        """,
        {"today": today()},
    ).fetchall()


def get_project(conn, pid):
    return conn.execute("SELECT * FROM projects WHERE id=? AND active=1", (pid,)).fetchone()


def shortages(conn, pid, overdue_only=False, sort="due"):
    sql = f"""
        SELECT b.id, b.part_no, b.part_key, b.description, b.material, b.material_type,
               b.supplier, b.po, b.qty, b.received, b.open_qty, b.due_date,
               CASE WHEN b.due_date IS NOT NULL AND b.due_date < :today THEN 1 ELSE 0 END AS overdue,
               CASE WHEN b.due_date IS NOT NULL AND b.due_date < :today
                    THEN CAST(julianday(:today) - julianday(b.due_date) AS INTEGER) END AS days_late,
               (SELECT COUNT(DISTINCT o.project_id) FROM bom_lines o
                  WHERE o.part_key = b.part_key AND b.part_key <> '' AND o.received > 0
                    AND o.project_id IS NOT NULL AND o.project_id <> b.project_id) AS received_elsewhere
        FROM bom_lines b
        WHERE b.project_id = :pid AND b.open_qty > 0
          {"AND b.due_date IS NOT NULL AND b.due_date < :today" if overdue_only else ""}
        ORDER BY {SORTS.get(sort, SORTS['due'])}
    """
    return conn.execute(sql, {"pid": pid, "today": today()}).fetchall()


def replacements(conn, pid, part_key):
    """Other projects where this part has already been RECEIVED (possible stock to borrow)."""
    return conn.execute(
        """
        SELECT p.id AS project_id, p.project_number, p.req, p.completion,
               o.received, o.qty, o.open_qty, o.po, o.supplier, o.due_date
        FROM bom_lines o JOIN projects p ON p.id = o.project_id AND p.active = 1
        WHERE o.part_key = ? AND o.project_id <> ? AND o.received > 0
        ORDER BY p.completion DESC, o.received DESC
        """,
        (part_key.upper(), pid),
    ).fetchall()


def critical_parts(conn, min_days=0, q="", limit=100):
    like = f"%{q.strip().upper()}%"
    return conn.execute(
        """
        SELECT b.part_no, b.description, b.supplier, b.po, b.open_qty, b.due_date,
               CAST(julianday(:today) - julianday(b.due_date) AS INTEGER) AS days_late,
               p.id AS project_id, p.project_number, p.req, p.completion
        FROM bom_lines b JOIN projects p ON p.id = b.project_id AND p.active = 1
        WHERE b.open_qty > 0 AND b.due_date IS NOT NULL AND b.due_date < :today
          AND CAST(julianday(:today) - julianday(b.due_date) AS INTEGER) >= :min_days
          AND (:q = '%%' OR b.part_key LIKE :q OR UPPER(b.description) LIKE :q
               OR UPPER(p.project_number) LIKE :q OR UPPER(IFNULL(p.req,'')) LIKE :q)
        ORDER BY b.due_date ASC, b.open_qty DESC
        LIMIT :limit
        """,
        {"today": today(), "min_days": min_days, "q": like, "limit": limit},
    ).fetchall()


def dashboard(conn):
    t = today()
    one = lambda sql, *a: conn.execute(sql, a).fetchone()[0] or 0
    return {
        "projects": one("SELECT COUNT(*) FROM projects WHERE active=1"),
        "open_lines": one("SELECT COUNT(*) FROM bom_lines b JOIN projects p ON p.id=b.project_id AND p.active=1 WHERE b.open_qty>0"),
        "overdue_lines": one(
            "SELECT COUNT(*) FROM bom_lines b JOIN projects p ON p.id=b.project_id AND p.active=1 "
            "WHERE b.open_qty>0 AND b.due_date<?", t),
        "pending_kits": one("SELECT COUNT(*) FROM kit_requests WHERE status='requested'"),
    }


def vendor_directory(conn):
    return conn.execute(
        """
        SELECT s.supplier_key, s.seen_as, s.parts, s.lines, s.open_lines, s.last_due, s.last_price,
               v.display_name, v.contact, v.phone, v.email, v.website, v.notes
        FROM (
          SELECT supplier_key, MIN(supplier) AS seen_as, COUNT(DISTINCT part_key) AS parts,
                 COUNT(*) AS lines, SUM(open_qty > 0) AS open_lines, MAX(due_date) AS last_due,
                 (SELECT price FROM bom_lines x WHERE x.supplier_key = b.supplier_key AND x.price IS NOT NULL
                    ORDER BY (x.due_date IS NULL), x.due_date DESC LIMIT 1) AS last_price
          FROM bom_lines b WHERE supplier_key <> '' GROUP BY supplier_key
        ) s LEFT JOIN vendors v ON v.supplier_key = s.supplier_key
        ORDER BY s.open_lines DESC, s.supplier_key
        """
    ).fetchall()


def kit_request_for(conn, pid):
    return conn.execute(
        """SELECT k.*, u.username AS requester FROM kit_requests k
           LEFT JOIN users u ON u.id = k.requested_by
           WHERE k.project_id = ? AND k.status IN ('requested','sent') ORDER BY k.id DESC LIMIT 1""",
        (pid,),
    ).fetchone()


# ---------------- materials (browse + narrow) ----------------

MATERIAL_SORTS = {
    "open": "r.open_total DESC, r.part_key",
    "recent": "(r.due_date IS NULL), r.due_date DESC, r.part_key",
    "part": "r.part_key",
    "uses": "r.uses DESC, r.part_key",
}


def materials_page(conn, q="", supplier="", open_only=False, sort="open", page=1, per=50):
    params = {"q": f"%{q.strip().upper()}%", "s": supplier.strip().upper()}
    inner = "part_key <> ''" + (" AND (part_key LIKE :q OR UPPER(IFNULL(description,'')) LIKE :q)" if q.strip() else "")
    outer = "r.rn = 1" + (" AND r.supplier_key = :s" if supplier.strip() else "") + (" AND r.open_total > 0" if open_only else "")
    cte = f"""
        WITH ranked AS (
          SELECT b.*,
            ROW_NUMBER() OVER (PARTITION BY part_key ORDER BY (due_date IS NULL), due_date DESC, id DESC) AS rn,
            COUNT(*) OVER (PARTITION BY part_key) AS uses,
            SUM(CASE WHEN open_qty > 0 THEN open_qty ELSE 0 END) OVER (PARTITION BY part_key) AS open_total
          FROM bom_lines b WHERE {inner}
        )"""
    total = conn.execute(f"{cte} SELECT COUNT(*) FROM ranked r WHERE {outer}", params).fetchone()[0]
    pages = max(1, -(-total // per))
    page = min(max(1, page), pages)
    rows = conn.execute(
        f"""{cte}
        SELECT r.part_no, r.part_key, r.description, r.supplier, r.supplier_key, r.vendor_id, r.price, r.po,
               r.due_date, r.uses, r.open_total, v.display_name, v.contact, v.phone, v.email, v.website
        FROM ranked r LEFT JOIN vendors v ON v.supplier_key = r.supplier_key
        WHERE {outer} ORDER BY {MATERIAL_SORTS.get(sort, MATERIAL_SORTS['open'])}
        LIMIT :per OFFSET :off""",
        {**params, "per": per, "off": (page - 1) * per},
    ).fetchall()
    return rows, total, page, pages


def part_detail(conn, part_key):
    return conn.execute(
        """SELECT b.qty, b.received, b.open_qty, b.due_date, b.po, b.supplier, b.price,
                  p.id AS project_id, p.project_number, p.req, p.completion
           FROM bom_lines b LEFT JOIN projects p ON p.id = b.project_id
           WHERE b.part_key = ? ORDER BY (b.due_date IS NULL), b.due_date DESC""",
        (part_key.upper(),),
    ).fetchall()


def supplier_keys(conn):
    return [r[0] for r in conn.execute(
        "SELECT supplier_key FROM bom_lines WHERE supplier_key <> '' GROUP BY supplier_key ORDER BY supplier_key")]


# ---------------- ready to kit ----------------

def ready_to_kit(conn, min_completion=95):
    return conn.execute(
        """SELECT p.*,
             (SELECT COUNT(*) FROM bom_lines b WHERE b.project_id=p.id AND b.open_qty>0) AS open_lines,
             (SELECT COUNT(*) FROM bom_lines b WHERE b.project_id=p.id AND b.open_qty>0 AND b.due_date IS NULL) AS open_no_due,
             (SELECT MAX(b.due_date) FROM bom_lines b WHERE b.project_id=p.id AND b.open_qty>0) AS expected_complete,
             (SELECT COUNT(*) FROM bom_lines b WHERE b.project_id=p.id) AS bom_rows,
             (SELECT k.status FROM kit_requests k WHERE k.project_id=p.id AND k.status IN ('requested','sent')
                ORDER BY k.id DESC LIMIT 1) AS kit_status
           FROM projects p WHERE p.active=1 AND p.completion >= ?
           ORDER BY p.completion DESC, open_lines ASC, p.project_number""",
        (min_completion,),
    ).fetchall()


# ---------------- purchase orders ----------------

def po_search(conn, q=""):
    return conn.execute(
        """SELECT MIN(po) AS po, MIN(supplier) AS supplier, COUNT(*) AS lines, SUM(open_qty > 0) AS open_lines,
                  MAX(due_date) AS last_due, COUNT(DISTINCT project_id) AS projects
           FROM bom_lines WHERE po IS NOT NULL AND (:q = '' OR UPPER(po) LIKE :like)
           GROUP BY UPPER(po) ORDER BY open_lines DESC, last_due DESC LIMIT 150""",
        {"q": q.strip(), "like": f"%{q.strip().upper()}%"},
    ).fetchall()


def po_lines(conn, po):
    return conn.execute(
        """SELECT b.*, p.id AS project_id, p.project_number, p.req,
                  CASE WHEN b.open_qty > 0 AND b.due_date < :today THEN 1 ELSE 0 END AS overdue
           FROM bom_lines b LEFT JOIN projects p ON p.id = b.project_id
           WHERE UPPER(b.po) = UPPER(:po) ORDER BY (b.due_date IS NULL), b.due_date, p.project_number""",
        {"po": po.strip(), "today": today()},
    ).fetchall()


# ---------------- data quality ----------------

def data_quality(conn, stale_days=180):
    import re as _re
    t = today()
    out = {
        "no_bom": conn.execute(
            """SELECT p.id, p.project_number, p.req, p.remaining_items, p.excel_file FROM projects p
               WHERE p.active=1 AND p.remaining_items>0
                 AND NOT EXISTS (SELECT 1 FROM bom_lines b WHERE b.project_id=p.id)
               ORDER BY p.remaining_items DESC""").fetchall(),
        "stale": conn.execute(
            """SELECT b.part_no, b.description, b.open_qty, b.due_date, b.po, b.supplier,
                      CAST(julianday(:t) - julianday(b.due_date) AS INTEGER) AS days_late,
                      p.id AS project_id, p.project_number
               FROM bom_lines b JOIN projects p ON p.id=b.project_id AND p.active=1
               WHERE b.open_qty>0 AND b.due_date < date(:t, :offset)
               ORDER BY b.due_date LIMIT 150""", {"t": t, "offset": f"-{stale_days} days"}).fetchall(),
        "stale_total": conn.execute(
            """SELECT COUNT(*) FROM bom_lines b JOIN projects p ON p.id=b.project_id AND p.active=1
               WHERE b.open_qty>0 AND b.due_date < date(?, ?)""", (t, f"-{stale_days} days")).fetchone()[0],
        "dup_reqs": conn.execute(
            """SELECT req, COUNT(*) AS n, GROUP_CONCAT(project_number, ', ') AS projects FROM projects
               WHERE active=1 GROUP BY req HAVING COUNT(*) > 1 ORDER BY n DESC""").fetchall(),
        "open_no_part": conn.execute(
            "SELECT COUNT(*) FROM bom_lines WHERE open_qty>0 AND part_key=''").fetchone()[0],
        "open_no_due": conn.execute(
            "SELECT COUNT(*) FROM bom_lines WHERE open_qty>0 AND due_date IS NULL").fetchone()[0],
        "open_no_supplier": conn.execute(
            "SELECT COUNT(*) FROM bom_lines WHERE open_qty>0 AND supplier_key=''").fetchone()[0],
        "stale_days": stale_days,
    }
    groups = {}
    for key, n in conn.execute(
            "SELECT supplier_key, COUNT(*) FROM bom_lines WHERE supplier_key<>'' GROUP BY supplier_key"):
        sig = _re.sub(r"[^A-Z]", "", key)
        if sig:
            groups.setdefault(sig, []).append((key, n))
    out["supplier_variants"] = sorted(
        (sorted(v, key=lambda x: -x[1]) for v in groups.values() if len(v) > 1),
        key=lambda v: -sum(n for _, n in v))
    return out


def aliases(conn):
    return conn.execute("SELECT * FROM supplier_aliases ORDER BY canonical_key, alias_key").fetchall()


# ---------------- what changed ----------------

def changes_since_previous(conn, limit=12):
    dates = [r[0] for r in conn.execute(
        "SELECT DISTINCT snap_date FROM project_history ORDER BY snap_date DESC LIMIT 2")]
    if len(dates) < 2:
        return None
    cur, prev = dates
    rows = conn.execute(
        """SELECT p.id, p.project_number, p.req, a.completion AS now_pct, b.completion AS before_pct,
                  a.open_lines - b.open_lines AS open_delta, a.overdue_lines - b.overdue_lines AS overdue_delta
           FROM project_history a
           JOIN project_history b ON b.project_id = a.project_id AND b.snap_date = :prev
           JOIN projects p ON p.id = a.project_id AND p.active = 1
           WHERE a.snap_date = :cur AND (a.completion <> b.completion OR a.open_lines <> b.open_lines)
           ORDER BY ABS(a.completion - b.completion) DESC, ABS(a.open_lines - b.open_lines) DESC""",
        {"cur": cur, "prev": prev},
    ).fetchall()
    return {
        "since": prev, "current": cur, "rows": rows[:limit], "changed": len(rows),
        "improved": sum(1 for r in rows if r["now_pct"] > r["before_pct"]),
        "slipped": sum(1 for r in rows if r["now_pct"] < r["before_pct"]),
    }


# ---------------- cart map ----------------

MAP_DEFAULT_W, MAP_DEFAULT_H = 3000, 1800      # map units (1 unit = 1px at 100% zoom)
MAP_MIN_W, MAP_MIN_H, MAP_MAX_W, MAP_MAX_H = 800, 600, 8000, 6000


def map_size(conn):
    from portal.db import get_meta
    try:
        return int(get_meta(conn, "map_width", MAP_DEFAULT_W)), int(get_meta(conn, "map_height", MAP_DEFAULT_H))
    except ValueError:
        return MAP_DEFAULT_W, MAP_DEFAULT_H


def status_of(completion):
    return "ok" if completion >= 95 else ("warn" if completion >= 80 else "bad")


def project_card(r):
    """The same fields the Projects tab puts on each card, so both pages share one detail panel."""
    return {
        "id": r["id"], "project": r["project_number"], "req": r["req"] or "",
        "completion": r["completion"], "remaining": r["remaining_items"], "bom": r["bom_length"],
        "me": r["me_ee"] or "", "url": r["sharepoint_url"] or "", "expected": r["expected_complete"] or "",
        "overdue": r["overdue_lines"], "kit": r["kit_status"] or "", "status": status_of(r["completion"]),
        "cart_id": r["cart_id"], "cart": r["cart_name"] or "",
    }


def map_data(conn):
    projects = [project_card(r) for r in list_projects(conn)]
    carts = []
    for c in conn.execute("SELECT * FROM carts ORDER BY name COLLATE NOCASE, id"):
        on_cart = sorted((p for p in projects if p["cart_id"] == c["id"]), key=lambda p: p["completion"])
        carts.append({
            "id": c["id"], "name": c["name"], "notes": c["notes"] or "",
            "x": c["x"], "y": c["y"], "w": c["w"], "h": c["h"], "projects": on_cart,
            "avg": round(sum(p["completion"] for p in on_cart) / len(on_cart), 1) if on_cart else None,
            "overdue": sum(p["overdue"] for p in on_cart),
        })
    w, h = map_size(conn)
    return {"width": w, "height": h, "carts": carts, "projects": projects}
