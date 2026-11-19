"""Excel -> SQLite loader. Combines the on-hand and BOM workbooks into one database.

The workbooks are COPIED to a temp folder before reading, so a file somebody has
open in Excel (or that OneDrive is syncing) does not block or corrupt the refresh.
If a refresh fails, the previous data stays in place and the failure is recorded.
"""
import json
import os
import re
import shutil
import tempfile
import threading
from datetime import datetime

import pandas as pd

import config
from portal import db

_lock = threading.Lock()


# ---------- helpers ----------

def project_number(filename):
    m = re.search(r"#([^ ]+)", str(filename))
    return m.group(1) if m else str(filename)


def file_key(filename):
    """Forgiving key: case, spacing, punctuation and extension are ignored."""
    s = re.sub(r"\.(xlsx|xlsm|xls)$", "", str(filename).strip(), flags=re.I)
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def supplier_key(name):
    return re.sub(r"\s+", " ", str(name or "").strip()).upper()


def _clean(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    s = str(v).strip()
    return s or None


def _num(series):
    return pd.to_numeric(series, errors="coerce")


def _read(path, **kw):
    if not os.path.exists(path):
        raise FileNotFoundError(f"Source file not found: {path}")
    with tempfile.TemporaryDirectory() as tmp:
        copy = os.path.join(tmp, os.path.basename(path))
        shutil.copy2(path, copy)
        df = pd.read_excel(copy, **kw)
    df, merged = dedupe_columns(df)
    df.attrs["merged_columns"] = merged
    return df


def dedupe_columns(df):
    """Trim header names and MERGE columns that end up with the same name
    (e.g. 'Material' and 'Material '), taking the first non-blank value per row.
    Without this, df['Material'] returns a whole DataFrame and the load crashes."""
    names = [str(c).strip() for c in df.columns]
    if len(set(names)) == len(names):
        df.columns = names
        return df, []
    out, merged = {}, []
    for name in dict.fromkeys(names):
        idx = [i for i, n in enumerate(names) if n == name]
        block = df.iloc[:, idx]
        if len(idx) > 1:
            merged.append(name)
            block = block.replace(r"^\s*$", None, regex=True)
            out[name] = block.bfill(axis=1).iloc[:, 0]
        else:
            out[name] = block.iloc[:, 0]
    return pd.DataFrame(out, index=df.index), merged


def _require(df, columns, source):
    missing = [c for c in columns if c not in df.columns]
    if missing:
        raise ValueError(f"{source} is missing column(s) {missing}. Found: {list(df.columns)}")


# ---------- refresh ----------

def refresh(trigger="manual"):
    """Reload both workbooks into SQLite. Returns a result dict; never raises."""
    if not _lock.acquire(blocking=False):
        return {"ok": False, "message": "A refresh is already running."}
    conn = db.connect()
    try:
        started = datetime.now()
        try:
            onhand = _read(config.ONHAND_FILE, sheet_name=config.ONHAND_SHEET)
            bom = _read(config.BOM_FILE, sheet_name=config.BOM_SHEET)
            _require(onhand, ["Excel File", "REQ#", "Completion"], "On-hand workbook")
            _require(bom, ["Source File", "Part No.", "Qty", "Rec'd"], "BOM workbook")
            stats = _load(conn, onhand, bom)
            stats["merged_columns"] = onhand.attrs.get("merged_columns", []) + bom.attrs.get("merged_columns", [])
        except Exception as exc:  # keep old data; remember why
            with conn:
                db.set_meta(conn, "refresh_status", "error")
                db.set_meta(conn, "refresh_message", f"{type(exc).__name__}: {exc}")
                db.set_meta(conn, "refresh_attempt", started.strftime("%Y-%m-%d %H:%M"))
            return {"ok": False, "message": f"{type(exc).__name__}: {exc}"}

        secs = (datetime.now() - started).total_seconds()
        with conn:
            db.audit(conn, "refresh", f"{trigger}: {stats['projects']} projects, {stats['bom_lines']} lines")
            db.set_meta(conn, "last_refresh", started.strftime("%Y-%m-%d %H:%M"))
            db.set_meta(conn, "refresh_attempt", started.strftime("%Y-%m-%d %H:%M"))
            db.set_meta(conn, "refresh_status", "ok")
            db.set_meta(conn, "refresh_message", f"{trigger} refresh in {secs:.1f}s")
            db.set_meta(conn, "refresh_stats", json.dumps(stats))
        return {"ok": True, "message": f"Loaded {stats['projects']} projects and {stats['bom_lines']} BOM lines in {secs:.1f}s", "stats": stats}
    finally:
        conn.close()
        _lock.release()


def _load(conn, onhand, bom):
    onhand = onhand.drop_duplicates(subset="Excel File").copy()
    onhand["Excel File"] = onhand["Excel File"].astype(str)

    with conn:
        # ---- projects: upsert so ids (and kit requests pointing at them) stay stable
        conn.execute("UPDATE projects SET active=0")
        for r in onhand.to_dict(orient="records"):
            ef = r["Excel File"]
            conn.execute(
                """INSERT INTO projects(excel_file,file_key,project_number,req,completion,me_ee,
                                        remaining_items,bom_length,sharepoint_url,active)
                   VALUES(?,?,?,?,?,?,?,?,?,1)
                   ON CONFLICT(excel_file) DO UPDATE SET
                     file_key=excluded.file_key, project_number=excluded.project_number,
                     req=excluded.req, completion=excluded.completion, me_ee=excluded.me_ee,
                     remaining_items=excluded.remaining_items, bom_length=excluded.bom_length,
                     sharepoint_url=excluded.sharepoint_url, active=1""",
                (
                    ef, file_key(ef), project_number(ef), _clean(r.get("REQ#")),
                    float(pd.to_numeric(r.get("Completion"), errors="coerce") or 0),
                    _clean(r.get("ME/EE")),
                    int(pd.to_numeric(r.get("Remaining Items"), errors="coerce") or 0),
                    int(pd.to_numeric(r.get("BOM Length"), errors="coerce") or 0),
                    _clean(r.get("SharePoint URL")),
                ),
            )

        # ---- matching BOM "Source File" -> project (exact, then forgiving, then #number)
        rows = conn.execute("SELECT id, excel_file, file_key, project_number FROM projects WHERE active=1").fetchall()
        exact = {r["excel_file"]: r["id"] for r in rows}
        by_key, by_num = {}, {}
        for r in rows:
            by_key.setdefault(r["file_key"], []).append(r["id"])
            by_num.setdefault(r["project_number"], []).append(r["id"])

        match_counts = {"exact": 0, "forgiving": 0, "project_number": 0}
        unmatched, source_map = [], {}
        for src in bom["Source File"].dropna().astype(str).unique():
            if src in exact:
                source_map[src] = exact[src]; match_counts["exact"] += 1
            elif len(by_key.get(file_key(src), [])) == 1:
                source_map[src] = by_key[file_key(src)][0]; match_counts["forgiving"] += 1
            elif len(by_num.get(project_number(src), [])) == 1:
                source_map[src] = by_num[project_number(src)][0]; match_counts["project_number"] += 1
            else:
                unmatched.append(src)

        # ---- BOM lines
        bom = bom.copy()
        qty, rec = _num(bom["Qty"]).fillna(0), _num(bom["Rec'd"]).fillna(0)
        bom["_qty"], bom["_rec"], bom["_open"] = qty, rec, qty - rec
        bom["_due"] = pd.to_datetime(bom["Due Date"], errors="coerce") if "Due Date" in bom.columns else pd.NaT
        bom["_price"] = _num(bom["Price"]) if "Price" in bom.columns else None

        def col(name):
            return bom[name] if name in bom.columns else pd.Series([None] * len(bom), index=bom.index)

        aliases = dict(conn.execute("SELECT alias_key, canonical_key FROM supplier_aliases").fetchall())

        # data quality: values in Qty / Rec'd that are not numbers (treated as 0)
        bad_numbers, bad_total = [], 0
        for cname in ("Qty", "Rec'd"):
            raw = bom[cname]
            mask = raw.notna() & _num(raw).isna() & (raw.astype(str).str.strip() != "")
            bad_total += int(mask.sum())
            for i in bom.index[mask][:100]:
                bad_numbers.append({"source": str(bom.at[i, "Source File"]), "part": _clean(bom.at[i, "Part No."]) or "",
                                    "column": cname, "value": str(raw.at[i])})
        db.set_meta(conn, "dq_bad_numbers", json.dumps(bad_numbers))

        conn.execute("DELETE FROM bom_lines")
        batch = []
        for i, src in enumerate(bom["Source File"].astype(str)):
            part = _clean(col("Part No.").iat[i])
            sup = _clean(col("Supplier").iat[i])
            due = bom["_due"].iat[i]
            price = bom["_price"].iat[i] if "Price" in bom.columns else None
            batch.append((
                source_map.get(src), src, part, (part or "").upper(),
                _clean(col("Description").iat[i]), _clean(col("Material").iat[i]),
                _clean(col("MATERIAL").iat[i]), sup, aliases.get(supplier_key(sup), supplier_key(sup)),
                _clean(col("Vendor ID").iat[i]),
                None if price is None or pd.isna(price) else float(price),
                float(bom["_qty"].iat[i]), float(bom["_rec"].iat[i]), float(bom["_open"].iat[i]),
                None if pd.isna(due) else due.strftime("%Y-%m-%d"),
                _clean(col("PO").iat[i]),
            ))
        conn.executemany(
            """INSERT INTO bom_lines(project_id,source_file,part_no,part_key,description,material,
                   material_type,supplier,supplier_key,vendor_id,price,qty,received,open_qty,due_date,po)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            batch,
        )

        no_bom = conn.execute(
            """SELECT COUNT(*) FROM projects p WHERE p.active=1 AND p.remaining_items>0
               AND NOT EXISTS (SELECT 1 FROM bom_lines b WHERE b.project_id=p.id)"""
        ).fetchone()[0]
        db.set_meta(conn, "unmatched_sources", json.dumps(unmatched))

        # daily snapshot for "what changed" (latest refresh of the day wins)
        conn.execute(
            """INSERT INTO project_history(snap_date,project_id,completion,open_lines,overdue_lines)
               SELECT date('now','localtime'), p.id, p.completion,
                 (SELECT COUNT(*) FROM bom_lines b WHERE b.project_id=p.id AND b.open_qty>0),
                 (SELECT COUNT(*) FROM bom_lines b WHERE b.project_id=p.id AND b.open_qty>0
                    AND b.due_date < date('now','localtime'))
               FROM projects p WHERE p.active=1
               ON CONFLICT(snap_date,project_id) DO UPDATE SET completion=excluded.completion,
                 open_lines=excluded.open_lines, overdue_lines=excluded.overdue_lines"""
        )

    return {
        "projects": len(onhand), "bom_lines": len(batch), "matches": match_counts,
        "unmatched_sources": len(unmatched), "missing_items_but_no_bom_rows": no_bom,
        "non_numeric_values": bad_total,
    }


# ---------- twice-a-day scheduler ----------

_scheduler_started = False


def start_scheduler(log=print):
    """Background thread: refresh at each time in config.REFRESH_TIMES, plus once at
    startup if the data is missing or older than 12 hours."""
    global _scheduler_started
    if _scheduler_started or not config.SCHEDULER_ENABLED:
        return
    _scheduler_started = True

    def loop():
        done = set()                      # (date, "HH:MM") already run
        conn = db.connect()
        last = db.get_meta(conn, "last_refresh")
        conn.close()
        stale = True
        if last:
            age = datetime.now() - datetime.strptime(last, "%Y-%m-%d %H:%M")
            stale = age.total_seconds() > 12 * 3600
        if stale:
            res = refresh("startup")
            log(f"[refresh] startup: {res['message']}")
        while True:
            now = datetime.now()
            stamp = (now.date(), now.strftime("%H:%M"))
            if stamp[1] in config.REFRESH_TIMES and stamp not in done:
                done.add(stamp)
                res = refresh("scheduled")
                log(f"[refresh] scheduled {stamp[1]}: {res['message']}")
            done = {d for d in done if d[0] == now.date()}
            threading.Event().wait(20)

    threading.Thread(target=loop, name="portal-refresh", daemon=True).start()
