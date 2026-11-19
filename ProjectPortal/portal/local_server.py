"""Runs the portal on this PC only (127.0.0.1) without waitress. Used by launcher.py."""
import json
import os
import socket
import threading

from werkzeug.serving import make_server

import config

SETTINGS_FILE = os.path.join(config.STATE_DIR, "launcher.json")


def load_settings():
    try:
        with open(SETTINGS_FILE) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def save_settings(data):
    os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
    with open(SETTINGS_FILE, "w") as fh:
        json.dump(data, fh, indent=2)


def set_data_folder(folder):
    """Point the loader at a folder at runtime (no environment variables needed)."""
    config.SOURCE_DIR = folder
    config.ONHAND_FILE = os.path.join(folder, "Sharepoint_OnHand.xlsx")
    config.BOM_FILE = os.path.join(folder, "Combined_BOMs.xlsx")


def missing_source_files():
    return [p for p in (config.ONHAND_FILE, config.BOM_FILE) if not os.path.exists(p)]


def find_free_port(start=8080, tries=20):
    for port in range(start, start + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise OSError(f"No free port between {start} and {start + tries - 1}")


class LocalServer:
    def __init__(self, app, port):
        self.port = port
        self.url = f"http://127.0.0.1:{port}"
        self._srv = make_server("127.0.0.1", port, app, threaded=True)
        self._thread = threading.Thread(target=self._srv.serve_forever, name="portal-web", daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self._srv.shutdown()
