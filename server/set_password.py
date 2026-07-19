#!/usr/bin/env python3
"""Set (or reset) a user's password from the command line.

Use this to change the default admin password before go-live, or to reset a
password if someone is locked out.

    python3 set_password.py admin
"""
import getpass
import pathlib
import sqlite3
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from pwutil import hash_password   # noqa: E402

DB = pathlib.Path(__file__).resolve().parent / "collection.db"


def main():
    if not DB.exists():
        sys.exit("No collection.db found (build it first with import_data.py).")
    user = sys.argv[1] if len(sys.argv) > 1 else input("Username: ").strip()
    con = sqlite3.connect(str(DB))
    if not con.execute("SELECT 1 FROM users WHERE username=?", [user]).fetchone():
        sys.exit(f"No such user: {user}")
    p1 = getpass.getpass("New password: ")
    p2 = getpass.getpass("Confirm new password: ")
    if p1 != p2:
        sys.exit("Passwords did not match.")
    if len(p1) < 8:
        sys.exit("Password too short (minimum 8 characters).")
    if p1.strip().lower() in {"admin123", "password", "12345678", "changeme", "admin1234", "adminadmin"}:
        sys.exit("That password is too common — please choose a stronger one.")
    con.execute("UPDATE users SET password_hash=? WHERE username=?", [hash_password(p1), user])
    con.execute("INSERT INTO audit(action,detail,by_user) VALUES('Change Password',?,'cli')", [user])
    con.commit()
    con.close()
    print(f"Password updated for '{user}'.")


if __name__ == "__main__":
    main()
