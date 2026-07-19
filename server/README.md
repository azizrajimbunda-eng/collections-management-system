# Collection Database — Phase 2 (multi-user backend)

Turns the single-file Phase-1 app into a shared, multi-user system: one office computer runs this
server and holds a **single** SQLite database; staff reach it in a browser over the office network,
each logging in with a role (**admin / encoder / certifier / viewer**).

Status: **Step 2 done — database + data import.** Server, logins, and the ported UI are next.

## Layout
- `schema.sql` — relational schema (keys, CHECK constraints, indexes, append-only audit).
- `defaults.json` — current officials (26 agency heads), signatories, letterhead, and the
  confirmed certification revenue-class mapping.
- `import_data.py` — builds `collection.db` from a Phase-1 JSON data source + `defaults.json`.
- `import_xlsx.py` — imports the office Excel workbook (Database + Source sheets) into the DB.
- `export_xlsx.py` — exports the DB back to an Excel workbook (also available in-app: Admin → Export).
- `pwutil.py` — PBKDF2-SHA256 password hashing (standard library).
- `collection.db` — the live database (generated; not edited by hand).
- `requirements.txt` — Flask (for the upcoming server).

## Build / re-import the database
```bash
cd server
# From the embedded seed (fresh setup):
python3 import_data.py --force
# Or migrate real data — export a JSON backup from the Phase-1 app, then:
python3 import_data.py --seed /path/to/collection_backup_YYYY-MM-DD.json --force
```
Admin login: the database builder generates and prints a strong random admin password on first build (no shipped default).

## Running it — point-and-click (for a non-technical team)
- **The host computer** (runs the server): double-click **`Start Collection Database (Windows).bat`**
  (or `Start Collection Database (Mac).command`). A window shows the address for staff, and a browser
  opens to the app. Closing the window stops the server. (`run_server.py` is what these call.)
- **Everyone else**: just open the printed LAN address (e.g. `http://192.168.7.215:5059`) as a browser
  bookmark — no commands, ever.
- **Fully hands-off (recommended at deployment):** set the host to **auto-start** the launcher on boot
  (Windows Task Scheduler / Startup folder), so the app is up whenever the PC is on. Optionally
  **package to a single .exe** (PyInstaller) so the host needs no Python install.
- Requires Python 3 + `pip install -r requirements.txt` on the host (until packaged).

## Next steps (core-first)
1. Flask server + read/query API (dashboard, records, reports).
2. Login + role-based permissions; server-side validation; append-only audit.
3. Port the Phase-1 screens to talk to the server instead of browser storage.
4. (Later) end-user report-request portal; deployment on the office network with auto-backups.
