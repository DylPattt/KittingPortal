import os
import sqlite3

from flask import g

import config

SCHEMA = """
-- ===== Rebuilt from Excel on every refresh =====
CREATE TABLE IF NOT EXISTS projects (
    id              INTEGER PRIMARY KEY,
    excel_file      TEXT NOT NULL UNIQUE,
    file_key        TEXT NOT NULL,
    project_number  TEXT NOT NULL,
    req             TEXT,
    completion      REAL DEFAULT 0,
    me_ee           TEXT,
    remaining_items INTEGER DEFAULT 0,
    bom_length      INTEGER DEFAULT 0,
    sharepoint_url  TEXT,
    active          INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS bom_lines (
    id            INTEGER PRIMARY KEY,
    project_id    INTEGER REFERENCES projects(id),
    source_file   TEXT,
    part_no       TEXT,
    part_key      TEXT,
    description   TEXT,
    material      TEXT,
    material_type TEXT,
    supplier      TEXT,
    supplier_key  TEXT,
    vendor_id     TEXT,
    price         REAL,
    qty           REAL,
    received      REAL,
    open_qty      REAL,
    due_date      TEXT,
    po            TEXT
);
CREATE INDEX IF NOT EXISTS ix_bom_project  ON bom_lines(project_id, open_qty);
CREATE INDEX IF NOT EXISTS ix_bom_part     ON bom_lines(part_key);
CREATE INDEX IF NOT EXISTS ix_bom_supplier ON bom_lines(supplier_key);
CREATE INDEX IF NOT EXISTS ix_bom_due      ON bom_lines(due_date);

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

-- ===== Edited inside the app; never touched by a refresh =====
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY,
    username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'user',     -- 'user' | 'admin'
    active        INTEGER NOT NULL DEFAULT 1,
    email         TEXT,
    created_at    TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS vendors (
    supplier_key TEXT PRIMARY KEY,
    display_name TEXT,
    contact      TEXT,
    phone        TEXT,
    email        TEXT,
    website      TEXT,
    notes        TEXT,
    updated_by   TEXT,
    updated_at   TEXT
);

CREATE TABLE IF NOT EXISTS kit_requests (
    id           INTEGER PRIMARY KEY,
    project_id   INTEGER NOT NULL REFERENCES projects(id),
    requested_by INTEGER REFERENCES users(id),
    note         TEXT,
    status       TEXT NOT NULL DEFAULT 'requested', -- requested | sent | cancelled
    created_at   TEXT DEFAULT (datetime('now','localtime')),
    updated_at   TEXT DEFAULT (datetime('now','localtime')),
    updated_by   TEXT
);
CREATE TABLE IF NOT EXISTS supplier_aliases (
    alias_key     TEXT PRIMARY KEY,          -- e.g. 'C&O 2'
    canonical_key TEXT NOT NULL,             -- e.g. 'C&O'
    created_by    TEXT,
    created_at    TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS audit_log (
    id       INTEGER PRIMARY KEY,
    at       TEXT DEFAULT (datetime('now','localtime')),
    username TEXT,
    action   TEXT NOT NULL,
    detail   TEXT
);

-- one row per project per day, written by each refresh (latest refresh of the day wins)
CREATE TABLE IF NOT EXISTS project_history (
    snap_date     TEXT NOT NULL,
    project_id    INTEGER NOT NULL,
    completion    REAL,
    open_lines    INTEGER,
    overdue_lines INTEGER,
    PRIMARY KEY (snap_date, project_id)
);

-- floor map: carts drawn on a 1200 x 720 grid; a project sits on at most one cart
CREATE TABLE IF NOT EXISTS carts (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    notes      TEXT,
    x          INTEGER NOT NULL DEFAULT 20,
    y          INTEGER NOT NULL DEFAULT 20,
    w          INTEGER NOT NULL DEFAULT 220,
    h          INTEGER NOT NULL DEFAULT 150,
    created_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS cart_projects (
    project_id  INTEGER PRIMARY KEY REFERENCES projects(id),
    cart_id     INTEGER NOT NULL REFERENCES carts(id) ON DELETE CASCADE,
    assigned_by TEXT,
    assigned_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS ix_cart_projects ON cart_projects(cart_id);

CREATE INDEX IF NOT EXISTS ix_kit_project ON kit_requests(project_id, status);

CREATE TABLE IF NOT EXISTS notifications (
    id         INTEGER PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id),
    message    TEXT NOT NULL,
    link       TEXT,
    is_read    INTEGER NOT NULL DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
"""


def connect():
    os.makedirs(os.path.dirname(config.DB_FILE), exist_ok=True)
    conn = sqlite3.connect(config.DB_FILE, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    conn = connect()
    with conn:
        conn.executescript(SCHEMA)
    conn.close()


def get_db():
    if "db" not in g:
        g.db = connect()
    return g.db


def close_db(_exc=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def get_meta(conn, key, default=None):
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_meta(conn, key, value):
    conn.execute(
        "INSERT INTO meta(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )


def audit(conn, action, detail=""):
    """Record who changed what. Works inside and outside a web request."""
    from flask import has_request_context
    user = "system"
    if has_request_context() and g.get("user"):
        user = g.user["username"]
    conn.execute("INSERT INTO audit_log(username,action,detail) VALUES(?,?,?)", (user, action, str(detail)[:500]))
