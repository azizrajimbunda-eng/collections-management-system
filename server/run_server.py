#!/usr/bin/env python3
"""Point-and-click launcher: starts the Collection Database server and opens it in a browser.

Double-click one of the 'Start Collection Database' files (they call this), or run: python run_server.py
The other staff reach it from their own browsers at the address printed below.
"""
import json
import os
import pathlib
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser

HERE = pathlib.Path(__file__).resolve().parent
PORT = 5059

# 1) Dependencies present?
try:
    import flask  # noqa: F401
except ImportError:
    print("\n  Flask is not installed. Please run this once, then try again:\n")
    print(f"      {os.path.basename(sys.executable)} -m pip install -r requirements.txt\n")
    input("  Press Enter to close...")
    sys.exit(1)

# 2) Database built? If not, build it from the seed data (first run).
if not (HERE / "collection.db").exists():
    print("First run — building the database from the seed data...")
    subprocess.run([sys.executable, str(HERE / "import_data.py")], cwd=str(HERE))

import app  # the Flask application (does not auto-run on import)  # noqa: E402


def lan_ip():
    """Best-effort local network address to share with other staff."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def open_browser():
    time.sleep(1.3)
    webbrowser.open(f"http://localhost:{PORT}")


def already_running():
    """True if OUR app is already answering on this port (e.g. double-clicked twice)."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/health", timeout=1) as r:
            return json.loads(r.read()).get("ok") is True
    except Exception:
        return False


if __name__ == "__main__":
    if already_running():
        print("\n  The Collection Database is already running on this computer.")
        print(f"  Opening it in your browser: http://localhost:{PORT}\n")
        webbrowser.open(f"http://localhost:{PORT}")
        input("  Press Enter to close this window (the app keeps running).")
        sys.exit(0)
    ip = lan_ip()
    print("=" * 60)
    print("  Collection Database is starting...")
    print(f"  On THIS computer:      http://localhost:{PORT}")
    print(f"  For other staff (LAN): http://{ip}:{PORT}")
    print("  Keep this window open while people are using it.")
    print("  Close this window to stop the server.")
    print("=" * 60)
    threading.Thread(target=open_browser, daemon=True).start()
    try:
        # host 0.0.0.0 so other office computers on the same Wi-Fi can reach it
        app.app.run(host="0.0.0.0", port=PORT)
    except OSError:
        print(f"\n  Could not start: port {PORT} is being used by another program.")
        print("  Close that program (or restart the computer) and try again.\n")
        input("  Press Enter to close...")
        sys.exit(1)
