# Deploying the Collection Database (Phase 2)

Plain-language setup. **Part A** gets it running on this Mac for a trial. **Part B** moves it to the
Windows PC that will host it for the office. Everything lives in the `server/` folder.

Your team's daily experience is just a **bookmark** in their browser — they never do any of this.
Only the **one host computer** needs the steps below.

---

## Part A — Try it on this Mac (now)

**1. One-time setup (Terminal, in the `server` folder):**
```bash
cd "server"
pip3 install -r requirements.txt
```
(Python 3 already comes with macOS. If `pip3` isn't found, install Python 3 from python.org.)

**2. Start it — double-click** `server/Start Collection Database (Mac).command`.
- First time only: macOS may block it — **right-click the file → Open → Open**. After that, double-click works.
- A browser opens automatically, and the Terminal window shows two addresses:
  - `http://localhost:5059` — this Mac.
  - `http://<this-mac-ip>:5059` — the address to give your team on the same Wi-Fi.
- **Keep that window open** while people are using it. Close it to stop the server.

**3. Log in as `admin`.** There is **no fixed default password** — the database builder generates a
strong random one and prints it once (look for the `ADMIN LOGIN … password:` banner in the Terminal).
*(For this Mac trial the admin password was set during the go-live security review.)*

**4. Change it to something you'll remember:** in the app, **Admin tab → Reset password**, or:
```bash
python3 set_password.py admin
```
Passwords must be at least 8 characters and can't be a common one.

**5. Add your people:** Admin tab → create a user for each staff member with the right role
(encoder / certifier / viewer). Give each their login + the `http://<mac-ip>:5059` bookmark.

**6. Load your real data** (replaces the sample data):
- **From the office Excel workbook** (it must have a `Database` sheet and a `Source` sheet):
```bash
python3 import_xlsx.py "/path/to/2026 Collection Database (Final).xlsx"
python3 set_password.py admin        # re-set the admin password after any rebuild
```
- Or from a Phase-1 JSON backup: `python3 import_data.py --seed backup.json --force`.
- **Export back to Excel** any time — in the app: **Admin → Export all data to Excel**, or on the
  command line: `python3 export_xlsx.py`. The exported file re-imports with `import_xlsx.py`.

**7. Backups** — run any time; safe even while people are using it:
```bash
python3 backup.py         # writes a timestamped copy into server/backups/
```
Automate it daily (Terminal): `crontab -e`, then add a line like:
```
0 18 * * *  cd "/path/to/collection-db/server" && /usr/bin/python3 backup.py
```

---

## Part B — Move it to the Windows PC (the office host)

**1. Copy the `server` folder** onto the Windows PC (USB drive or network copy).
- To keep the data you entered during the Mac trial, copy `collection.db` and the `backups` folder too.
  (If you leave `collection.db` behind, it rebuilds fresh from the sample data on first start.)

**2. Install Python 3** on Windows from python.org — **tick "Add Python to PATH"** during install.

**3. One-time setup — double-click** `Setup (Windows) - run once.bat`. It installs the components and
builds the database. Then load your real data (Command Prompt in the folder):
```
python import_xlsx.py "your-workbook.xlsx"
```
This prints a **random admin password** — write it down. Set your own memorable one anytime with
`python set_password.py admin` (or in the app: **Admin → Reset password**).

**4. Start it — double-click** `Start Collection Database (Windows).bat`.
- It prints the address to share (e.g. `http://192.168.1.50:5059`) and opens the browser.
- **First time only:** Windows may ask to allow Python through the firewall — click **Allow** (your staff
  need this to reach the app).

**5. Make it start by itself — double-click** `Install Auto-Start (Windows).bat`
(right-click → **Run as administrator**). After that the app is up automatically whenever the PC logs in.

**6. Give the host a fixed address:** on the router, set a **DHCP reservation** for the host PC (ask
whoever manages the office network) so its `192.168.x.x` address never changes and bookmarks keep working.

**7. Automate backups on Windows:** Task Scheduler → daily → run `python backup.py` in the server folder.

**8. (Optional) No-Python packaging:** on the Windows PC, `pip install pyinstaller` then
`pyinstaller --onefile --add-data "static;static" --add-data "schema.sql;." --add-data "defaults.json;." run_server.py`
produces a single `.exe` the host can run with no Python installed. (Must be built on Windows.)

---

## Good-to-know
- **Access is staff-only by the network:** only computers on your office Wi-Fi can reach it; on top of
  that, everyone logs in with a role. There is no exposure to the internet.
- **The host PC must be on** for others to use it — a dedicated always-on PC or mini-PC is ideal.
- **The database is one file** (`collection.db`). Your backups are its safety net — keep some copies
  off the machine (a second drive / USB / occasional encrypted cloud copy).
- **Roles:** admin (everything) · encoder (add/edit entries) · certifier (issue certifications, fulfil
  requests) · viewer (view + request reports).

## Security hardening (from the go-live review, 2026-07-08)
The code was reviewed and hardened before go-live. A few items are **operational** — they depend on how
you run the host, so please do them:
- **Wi-Fi password:** the app runs over plain HTTP on the LAN, so the office Wi-Fi must use
  **WPA2/WPA3 with a staff-only password** — that's what keeps logins private on the wire. Don't put this
  on an open/guest network.
- **Protect the host folder (Windows):** right-click the `server` folder → Properties → Security and
  limit it to the host's own user account. `collection.db` (holds password hashes) and `.secret_key`
  (signs logins) must not be readable by other accounts on that PC. (On Windows, folder permissions —
  not the app — are what protect these files.)
- **Never enable debug mode on the network:** always use the normal launcher. `--debug` is now refused
  unless bound to `127.0.0.1`, so the launchers are safe as shipped.
- **Admin password:** no fixed default anymore; every build prints a random one — change it to your own
  after first login.
- Everything else (input validation, unique certificate numbers, login lockout, etc.) is built into the
  code, no action needed. Full details: the `PRE-GOLIVE-REVIEW/` folder in the project.
