"""Command-line helper.

    python manage.py create-user <username> [--admin]   add a login (prompts for password)
    python manage.py refresh                            reload the Excel files into the database
    python manage.py info                               show paths and last refresh
"""
import getpass
import sqlite3
import sys

import config
from portal import auth, db, etl


def main(argv):
    db.init_db()
    cmd = argv[1] if len(argv) > 1 else "info"
    conn = db.connect()

    if cmd == "create-user":
        if len(argv) < 3:
            raise SystemExit("usage: python manage.py create-user <username> [--admin]")
        pw = getpass.getpass("Password (min 8 chars): ")
        if len(pw) < 8 or pw != getpass.getpass("Repeat password: "):
            raise SystemExit("Passwords must match and be at least 8 characters.")
        try:
            with conn:
                auth.create_user(conn, argv[2], pw, "admin" if "--admin" in argv else "user")
        except sqlite3.IntegrityError:
            raise SystemExit("That username already exists.")
        print(f"Created {'admin' if '--admin' in argv else 'user'} '{argv[2]}'.")
    elif cmd == "refresh":
        print(etl.refresh("cli")["message"])
    else:
        print("Database      :", config.DB_FILE)
        print("Excel folder  :", config.SOURCE_DIR)
        print("Last refresh  :", db.get_meta(conn, "last_refresh", "never"))
        print("Users         :", conn.execute("SELECT COUNT(*) FROM users").fetchone()[0])


if __name__ == "__main__":
    main(sys.argv)
