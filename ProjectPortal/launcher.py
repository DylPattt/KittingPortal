"""Kitting Portal desktop launcher.

Double-click launcher.pyw (no console window) or run:  python launcher.py
Runs the portal on THIS PC only and opens it in your browser.
"""
import os
import queue
import sys
import threading
import webbrowser
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
sys.path.insert(0, HERE)

import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, simpledialog, ttk


def _fatal(title, text):
    """Show an error popup (launcher.pyw has no console, so print() is invisible)."""
    r = tk.Tk(); r.withdraw()
    messagebox.showerror(title, text)
    r.destroy()
    sys.exit(1)


_needed = ["config.py", "app.py", os.path.join("portal", "local_server.py"), "templates", "static"]
_missing = [n for n in _needed if not os.path.exists(os.path.join(HERE, n))]
if _missing:
    _fatal("Kitting Portal: files missing",
           "The launcher must stay inside the extracted ProjectPortal folder.\n\n"
           f"It is running from:\n{HERE}\n\nMissing here: {', '.join(_missing)}\n\n"
           "Extract ProjectPortal.zip (right-click > Extract All), then run launcher.pyw "
           "from inside the extracted ProjectPortal folder.")
if ".zip" in HERE.lower():
    _fatal("Kitting Portal", "You're running from inside the zip. Extract it first (right-click > Extract All).")

try:
    import config
    from portal import local_server
except Exception as exc:
    _fatal("Kitting Portal: could not start",
           f"{type(exc).__name__}: {exc}\n\nIf a package is missing, run:\n  pip install flask pandas openpyxl")

# Apply the saved Excel folder BEFORE anything reads the data.
_saved = local_server.load_settings()
if _saved.get("data_folder"):
    local_server.set_data_folder(_saved["data_folder"])

try:
    from app import app                  # noqa: E402  (import after folder is set)
    from portal import auth, db, etl     # noqa: E402
except Exception as exc:
    _fatal("Kitting Portal: could not start",
           f"{type(exc).__name__}: {exc}\n\nIf a package is missing, run:\n  pip install flask pandas openpyxl")


class Launcher:
    def __init__(self, root):
        self.root = root
        self.server = None
        self.logs = queue.Queue()

        root.title("Kitting Portal")
        root.geometry("620x440")
        root.minsize(520, 360)
        root.protocol("WM_DELETE_WINDOW", self.quit)

        pad = {"padx": 12, "pady": 4}
        top = ttk.Frame(root)
        top.pack(fill="x", **pad)
        self.status = ttk.Label(top, text="Starting…", font=("Segoe UI", 12, "bold"))
        self.status.pack(anchor="w")
        self.data_label = ttk.Label(top, text="", foreground="#555")
        self.data_label.pack(anchor="w")
        self.refresh_label = ttk.Label(top, text="", foreground="#555")
        self.refresh_label.pack(anchor="w")

        btns = ttk.Frame(root)
        btns.pack(fill="x", **pad)
        self.open_btn = ttk.Button(btns, text="Open portal", command=self.open_browser, state="disabled")
        self.open_btn.pack(side="left")
        ttk.Button(btns, text="Refresh data now", command=self.refresh_now).pack(side="left", padx=6)
        ttk.Button(btns, text="Choose Excel folder…", command=self.choose_folder).pack(side="left")
        ttk.Button(btns, text="Add user…", command=self.add_user).pack(side="left", padx=6)
        ttk.Button(btns, text="Quit", command=self.quit).pack(side="right")

        self.log = scrolledtext.ScrolledText(root, height=12, state="disabled", font=("Consolas", 9))
        self.log.pack(fill="both", expand=True, padx=12, pady=(4, 12))

        self.root.after(100, self.start)
        self.root.after(200, self.drain_logs)

    # ---------- logging (thread-safe) ----------
    def say(self, text):
        self.logs.put(f"{datetime.now():%H:%M:%S}  {text}")

    def drain_logs(self):
        while not self.logs.empty():
            self.log.configure(state="normal")
            self.log.insert("end", self.logs.get() + "\n")
            self.log.see("end")
            self.log.configure(state="disabled")
        self.root.after(200, self.drain_logs)

    # ---------- startup ----------
    def start(self):
        db.init_db()
        self.update_labels()

        missing = local_server.missing_source_files()
        if missing:
            self.say("Excel files not found: " + ", ".join(missing))
            messagebox.showinfo("Choose your Excel folder",
                                "Pick the folder that contains Sharepoint_OnHand.xlsx and Combined_BOMs.xlsx.")
            self.choose_folder()

        if not self.user_count():
            messagebox.showinfo("First run", "Create your admin login for the portal.")
            self.add_user(admin=True)

        try:
            port = local_server.find_free_port(config.PORT)
            self.server = local_server.LocalServer(app, port)
            self.server.start()
        except OSError as exc:
            self.status.configure(text="Could not start")
            self.say(f"Server failed to start: {exc}")
            return

        etl.start_scheduler(log=self.say)        # startup refresh if stale + 06:00 / 14:00
        self.status.configure(text=f"Running at {self.server.url}  (this PC only)")
        self.open_btn.configure(state="normal")
        self.say(f"Portal running at {self.server.url}")
        self.root.after(1500, self.open_browser)
        self.root.after(30000, self.tick)

    def tick(self):
        self.update_labels()
        self.root.after(30000, self.tick)

    def update_labels(self):
        conn = db.connect()
        last = db.get_meta(conn, "last_refresh", "never")
        status = db.get_meta(conn, "refresh_status", "")
        conn.close()
        self.data_label.configure(text=f"Excel folder: {config.SOURCE_DIR}")
        self.refresh_label.configure(
            text=f"Last refresh: {last}" + ("   (last attempt FAILED, see log)" if status == "error" else "")
            + f"   ·   automatic at {', '.join(config.REFRESH_TIMES)}")

    def user_count(self):
        conn = db.connect()
        n = conn.execute("SELECT COUNT(*) FROM users WHERE active=1").fetchone()[0]
        conn.close()
        return n

    # ---------- actions ----------
    def open_browser(self):
        if self.server:
            webbrowser.open(self.server.url)

    def refresh_now(self):
        self.say("Refreshing data…")

        def work():
            res = etl.refresh("manual")
            self.say(res["message"])
            self.root.after(0, self.update_labels)

        threading.Thread(target=work, daemon=True).start()

    def choose_folder(self):
        folder = filedialog.askdirectory(title="Folder with Sharepoint_OnHand.xlsx and Combined_BOMs.xlsx",
                                         initialdir=config.SOURCE_DIR if os.path.isdir(config.SOURCE_DIR) else HERE)
        if not folder:
            return
        local_server.set_data_folder(os.path.normpath(folder))
        missing = local_server.missing_source_files()
        if missing:
            messagebox.showwarning("Files not found", "Not in that folder:\n" + "\n".join(os.path.basename(m) for m in missing))
            return
        local_server.save_settings({**local_server.load_settings(), "data_folder": config.SOURCE_DIR})
        self.say(f"Excel folder set to {config.SOURCE_DIR}")
        self.update_labels()
        self.refresh_now()

    def add_user(self, admin=None):
        name = simpledialog.askstring("New user", "Username:", parent=self.root)
        if not name:
            return
        pw = simpledialog.askstring("New user", "Password (8+ characters):", show="*", parent=self.root)
        if not pw or len(pw) < 8:
            messagebox.showwarning("Not created", "Password must be at least 8 characters.")
            return
        if pw != simpledialog.askstring("New user", "Repeat password:", show="*", parent=self.root):
            messagebox.showwarning("Not created", "Passwords did not match.")
            return
        if admin is None:
            admin = messagebox.askyesno("New user", f"Make {name} an admin?")
        conn = db.connect()
        try:
            with conn:
                auth.create_user(conn, name, pw, "admin" if admin else "user")
            self.say(f"Created {'admin' if admin else 'user'} '{name}'")
        except Exception as exc:
            messagebox.showerror("Not created", "That username already exists." if "UNIQUE" in str(exc) else str(exc))
        finally:
            conn.close()

    def quit(self):
        if self.server:
            self.server.stop()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista" if os.name == "nt" else "clam")
    except tk.TclError:
        pass
    Launcher(root)
    root.mainloop()
