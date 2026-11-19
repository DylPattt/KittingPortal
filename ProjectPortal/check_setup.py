"""Run `python check_setup.py` to diagnose the portal in one pass.

Checks the folder layout, Python syntax, packages, Excel files, the data load, and
every page/API through Flask's test client. It uses a THROWAWAY database, so your
real portal data is never touched. Add --live to use the real database instead.
Exit code is 1 if anything fails.
"""
import os
import py_compile
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
sys.path.insert(0, HERE)

LIVE = "--live" in sys.argv
if not LIVE:
    os.environ["PORTAL_STATE_DIR"] = tempfile.mkdtemp(prefix="portal_check_")
    os.environ.pop("PORTAL_DB", None)

REQUIRED = [
    "app.py", "config.py", "serve.py", "launcher.py", "portal/local_server.py", "manage.py", "requirements.txt",
    "portal/__init__.py", "portal/db.py", "portal/etl.py", "portal/queries.py", "portal/auth.py",
    "portal/notify.py", "portal/views.py", "portal/api.py",
    "templates/layout.html", "templates/login.html", "templates/error.html", "templates/home.html",
    "templates/projects.html", "templates/critical.html", "templates/materials.html",
    "templates/vendors.html", "templates/notifications.html", "templates/admin.html",
    "templates/map.html", "templates/_project_detail.html", "static/js/map.js", "static/js/project_detail.js",
    "templates/ready.html", "templates/po.html", "templates/po_detail.html", "templates/data_quality.html",
    "static/css/site.css", "static/js/projects.js", "static/js/materials.js",
]
failures = 0


def check(name, fn):
    global failures
    try:
        detail = fn()
        print(f"  OK    {name}" + (f"  ({detail})" if detail else ""))
        return True
    except Exception as exc:                          # noqa: BLE001
        failures += 1
        print(f"  FAIL  {name}\n        {type(exc).__name__}: {exc}")
        return False


def layout():
    if ".zip" in HERE.lower():
        raise RuntimeError("You are running from inside a .zip. Right-click the zip > Extract All, then run from the extracted folder.")
    missing = [f for f in REQUIRED if not os.path.exists(f)]
    if not missing:
        return f"{len(REQUIRED)} files present"
    hints = []
    for m in missing:
        stem = os.path.splitext(os.path.basename(m))[0]
        for root, _dirs, files in os.walk(HERE):
            for f in files:
                if re.match(rf"^{re.escape(stem)}(\s*\(\d+\)|_\d+|_[0-9a-f]{{6}})\.", f):
                    hints.append(f"'{os.path.relpath(os.path.join(root, f), HERE)}' looks like a renamed copy of '{m}'")
    msg = "Missing: " + ", ".join(missing)
    if hints:
        msg += "\n        Likely renamed duplicates (rename them): " + "; ".join(hints[:6])
    raise FileNotFoundError(msg)


def syntax():
    bad = []
    for root, _d, files in os.walk(HERE):
        if any(skip in root for skip in ("__pycache__", ".venv", "instance")):
            continue
        for f in files:
            if f.endswith(".py"):
                try:
                    py_compile.compile(os.path.join(root, f), doraise=True)
                except py_compile.PyCompileError as e:
                    bad.append(f"{os.path.relpath(os.path.join(root, f), HERE)}: {e.msg.strip().splitlines()[-1]}")
    if bad:
        raise SyntaxError("; ".join(bad))


def deps():
    import importlib
    missing = []
    for mod in ("flask", "pandas", "openpyxl"):
        try:
            importlib.import_module(mod)
        except ImportError:
            missing.append(mod)
    if missing:
        raise ImportError("Run:  pip install " + " ".join(missing))
    notes = []
    try:
        importlib.import_module("waitress")
    except ImportError:
        notes.append("waitress not installed: fine for launcher.py, needed only for serve.py")
    try:
        importlib.import_module("tkinter")
    except ImportError:
        notes.append("tkinter missing: reinstall Python with 'tcl/tk' ticked to use launcher.py")
    return "; ".join(notes)


def sources():
    import config
    for path in (config.ONHAND_FILE, config.BOM_FILE):
        if not os.path.exists(path):
            raise FileNotFoundError(f"{path}\n        Put the two Excel files in the data/ folder, or set PORTAL_DATA_DIR.")
    return config.SOURCE_DIR


def load():
    from portal import db, etl
    db.init_db()
    res = etl.refresh("check")
    if not res["ok"]:
        raise RuntimeError(res["message"])
    s = res["stats"]
    if s["unmatched_sources"]:
        print(f"        note: {s['unmatched_sources']} BOM source file(s) matched no project (see Admin page)")
    return f"{s['projects']} projects, {s['bom_lines']} BOM lines"


def dup_columns():
    import pandas as pd
    from portal.etl import dedupe_columns
    df = pd.DataFrame([["a", None, 1], [None, "b", 2]], columns=["Material", "Material ", "Qty"])
    out, merged = dedupe_columns(df)
    assert merged == ["Material"] and list(out["Material"]) == ["a", "b"], "duplicate-column merge broken"


def web():
    import re as _re
    from app import app
    from portal import auth, db
    c = app.test_client()

    assert c.get(r"/projects").status_code == 302, "pages should redirect to login when signed out"
    assert c.get("/api/projects/1/shortages").status_code == 401, "API should return 401 when signed out"

    conn = db.connect()
    with conn:
        conn.execute("DELETE FROM notifications WHERE user_id IN (SELECT id FROM users WHERE username='_check_admin')")
        conn.execute("DELETE FROM kit_requests WHERE requested_by IN (SELECT id FROM users WHERE username='_check_admin')")
        conn.execute("DELETE FROM users WHERE username='_check_admin'")
        auth.create_user(conn, "_check_admin", "check-pass-123", "admin")
    conn.close()

    page = c.get("/login").get_data(as_text=True)
    token = _re.search(r'name="_csrf" value="([^"]+)"', page).group(1)
    r = c.post("/login", data={"username": "_check_admin", "password": "check-pass-123", "_csrf": token})
    assert r.status_code == 302, "login failed"

    for url in ("/", "/projects", "/critical", "/materials", "/vendors", "/notifications", "/admin",
                "/map", "/api/map", "/ready", "/ready?min=80", "/po", "/po?q=PO", "/data-quality",
                "/api/materials", "/api/materials?q=belt&open=1&sort=recent&page=2"):
        code = c.get(url).status_code
        assert code == 200, f"GET {url} returned {code}"

    conn = db.connect()
    pid = conn.execute("""SELECT p.id FROM projects p JOIN bom_lines b ON b.project_id=p.id
                          WHERE b.open_qty>0 AND p.active=1 GROUP BY p.id ORDER BY COUNT(*) DESC LIMIT 1""").fetchone()[0]
    conn.close()
    # login resets the session, so read the fresh token the way the browser does (meta tag)
    token = _re.search(r'name="csrf" content="([^"]+)"', c.get("/").get_data(as_text=True)).group(1)
    hdr = {"X-CSRF-Token": token}

    conn = db.connect()
    po = conn.execute("SELECT po FROM bom_lines WHERE po IS NOT NULL LIMIT 1").fetchone()
    part = conn.execute("SELECT part_key FROM bom_lines WHERE part_key<>'' LIMIT 1").fetchone()
    conn.close()
    if po:
        assert c.get("/po", query_string={"po": po[0]}).status_code == 200, "PO detail failed"
    if part:
        assert c.get("/api/materials/detail", query_string={"part": part[0]}).status_code == 200, "part detail failed"

    r = c.get(f"/api/projects/{pid}/shortages?sort=late")
    assert r.status_code == 200, f"shortages returned {r.status_code}"
    lines = r.get_json()["lines"]
    assert c.get(f"/api/projects/{pid}/shortages?overdue=1").status_code == 200
    csv_r = c.get(f"/api/projects/{pid}/shortages.csv")
    assert csv_r.status_code == 200 and b"Part No." in csv_r.data, "CSV export failed"

    with_part = next((l for l in lines if l["part_key"]), None)
    if with_part:
        r = c.get(f"/api/projects/{pid}/replacements", query_string={"part": with_part["part_key"]})
        assert r.status_code == 200, "replacements failed"

    assert c.post(f"/api/projects/{pid}/kit-request", json={"note": "check"}).status_code == 400, "CSRF should be enforced"
    r = c.post(f"/api/projects/{pid}/kit-request", json={"note": "check"}, headers=hdr)
    assert r.status_code == 201, f"kit request returned {r.status_code}"
    assert c.post(f"/api/projects/{pid}/kit-request", json={}, headers=hdr).status_code == 409, "duplicate request not blocked"
    kid = r.get_json()["kit"]["id"]
    assert c.post(f"/api/kit-requests/{kid}/cancel", headers=hdr).status_code == 200

    # cart map: create, move, assign, move project between carts, delete
    r = c.post("/api/map/carts", json={"name": "_check cart"}, headers=hdr)
    assert r.status_code == 201, f"cart create returned {r.status_code}"
    cid = r.get_json()["id"]
    r = c.post("/api/map/carts", json={"name": "_check cart 2"}, headers=hdr)
    cid2 = r.get_json()["id"]
    map_w = c.get("/api/map").get_json()["width"]
    box = c.post(f"/api/map/carts/{cid}", json={"x": 99999, "y": -50, "w": 10}, headers=hdr).get_json()
    assert box["x"] == map_w - 120 and box["y"] == 0 and box["w"] == 120, f"cart box not clamped: {box}"
    r = c.post("/api/map/carts/bulk", json={"count": 3, "prefix": "_check bulk", "start": 1}, headers=hdr)
    assert r.status_code == 201, f"bulk add returned {r.status_code}"
    assert c.post("/api/map/arrange", headers=hdr).status_code == 200, "auto-arrange failed"
    size = c.post("/api/map/size", json={"width": 100, "height": 100}, headers=hdr).get_json()
    assert size["width"] >= 800 and size["height"] >= 600, "map size floor not enforced"
    assert c.post(f"/api/map/carts/{cid}/assign", json={"project_id": pid}, headers=hdr).status_code == 200
    moved = c.post(f"/api/map/carts/{cid2}/assign", json={"project_id": pid}, headers=hdr).get_json()
    assert moved["moved_from"] == "_check cart", "moving a project between carts failed"
    m = c.get("/api/map").get_json()
    on2 = [p for cart in m["carts"] if cart["id"] == cid2 for p in cart["projects"]]
    assert [p["id"] for p in on2] == [pid] and m["can_edit"], "map data wrong"
    assert c.post(f"/api/map/carts/{cid}/assign", json={"project_id": 999999}, headers=hdr).status_code == 400
    for x in [cart["id"] for cart in c.get("/api/map").get_json()["carts"] if cart["name"].startswith("_check")]:
        assert c.post(f"/api/map/carts/{x}/delete", headers=hdr).status_code == 200

    conn = db.connect()
    with conn:
        conn.execute("DELETE FROM kit_requests WHERE note='check'")
        conn.execute("DELETE FROM notifications WHERE user_id IN (SELECT id FROM users WHERE username='_check_admin')")
        conn.execute("DELETE FROM users WHERE username='_check_admin'")
    conn.close()
    return f"{len(lines)} shortage lines on project #{pid}"


print("Portal self-check" + ("  (LIVE database)" if LIVE else "  (throwaway database)"))
if check("folder layout", layout):
    check("Python syntax in every file", syntax)
    check("packages installed", deps)
    check("duplicate column names are merged", dup_columns)
    if check("Excel source files found", sources):
        if check("Excel -> SQLite load", load):
            check("pages, login, API, CSV, kit request", web)
print("\nAll checks passed." if not failures else f"\n{failures} check(s) failed.")
sys.exit(1 if failures else 0)
