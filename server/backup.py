#!/usr/bin/env python3
"""Safe backup of collection.db.

Uses SQLite's online backup API, so it is safe to run even while the server is
running and people are using it. Keeps the most recent KEEP backups.

Run manually any time, or schedule it (cron on Mac/Linux, Task Scheduler on Windows):
    python3 backup.py
"""
import datetime
import pathlib
import sqlite3
import sys

HERE = pathlib.Path(__file__).resolve().parent
DB = HERE / "collection.db"
BK = HERE / "backups"
KEEP = 30   # how many timestamped backups to retain


def main():
    if not DB.exists():
        sys.exit("No collection.db to back up (build it first with import_data.py).")
    BK.mkdir(exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = BK / f"collection-{ts}.db"
    src = sqlite3.connect(str(DB))
    dst = sqlite3.connect(str(dest))
    with dst:
        src.backup(dst)          # consistent snapshot even if the DB is in use
    src.close()
    dst.close()
    print(f"Backup written: {dest}")

    old = sorted(BK.glob("collection-*.db"))[:-KEEP]
    for f in old:
        f.unlink()
        print(f"Pruned old backup: {f.name}")


if __name__ == "__main__":
    main()
