# Moving the Collection Database to the Windows PC — Step-by-Step

A plain, do-this-then-that guide for setting up the **one host computer**. Your staff never do any of
this — for them it's just a browser bookmark. Set aside about **30–45 minutes**. Tick each box as you go.

> You only need two things to start: **(1)** this whole `server` folder, and **(2)** your office Excel
> workbook (the one with a `Database` sheet and a `Source` sheet).

---

## Before you start — pick the host PC
- [ ] Choose a PC that **stays on** during office hours (a dedicated desktop or mini-PC is ideal).
- [ ] Make sure it's on the **office Wi-Fi/LAN**, and that the Wi-Fi has a **password (WPA2/WPA3)** —
      not an open/guest network. (This is what keeps logins private, since the app uses plain HTTP.)

## Step 1 — Copy the folder onto the Windows PC
- [ ] Copy this entire **`server`** folder to the Windows PC (USB drive or network copy).
      A good place: `C:\CollectionDB\server`.
- [ ] Copy your **Excel workbook** onto the PC too (e.g. into the same folder).

## Step 2 — Install Python
- [ ] Go to **python.org → Downloads**, get **Python 3** for Windows, run the installer.
- [ ] ⚠️ **Very important:** on the first installer screen, tick **“Add Python to PATH”**, then click
      **Install Now**.

## Step 3 — One-time setup
- [ ] Open the `server` folder and **double-click `Setup (Windows) - run once.bat`**.
- [ ] A black window appears, installs a few components, and builds a starter database. Wait until it
      says it's finished, then close it.

## Step 4 — Load your real data
- [ ] In the `server` folder, click the address bar, type **`cmd`**, and press Enter (this opens a
      command window already in the folder).
- [ ] Type this (use your workbook's real name, keep the quotes) and press Enter:
      ```
      python import_xlsx.py "2026 Collection Database (Final_).xlsx"
      ```
- [ ] When it finishes it prints a box like this — **write the password down**, you'll need it in a second:
      ```
      ==============================================================
        ADMIN LOGIN   username: admin    password: <a random password>
      ==============================================================
      ```

## Step 5 — Start it
- [ ] **Double-click `Start Collection Database (Windows).bat`.**
- [ ] The first time, Windows asks to **allow Python through the firewall → click Allow.**
      (Your staff can't reach the app without this.)
- [ ] A browser opens, and the window shows an address like **`http://192.168.1.50:5059`** — that's the
      address you'll give your team. **Leave this window open** while people are using the app.

## Step 6 — Log in and secure the admin account
- [ ] Log in as **`admin`** with the password from Step 4.
- [ ] Go to **Admin → Reset password** and set a password **you'll remember** (at least 8 characters,
      not a common one). This is your master account — keep it private.

## Step 7 — Create your staff logins
- [ ] Still in the **Admin** tab, create one user per staff member and give each the right role:
  - **encoder** — can add and edit collection entries
  - **certifier** — can issue certifications and fulfil report requests
  - **viewer** — can view reports and request them
  - (**admin** — full control; keep this to yourself)
- [ ] Give each person their **username + password** and the **bookmark address** from Step 5.

## Step 8 — Protect the folder (important)
- [ ] Right-click the `server` folder → **Properties → Security** tab → limit access to **this PC's own
      user account** only. This protects two sensitive files inside it — `collection.db` (holds the
      login password hashes) and `.secret_key` (signs the logins). On Windows, folder permissions are
      what protect these — nothing else does.

## Step 9 — Make it start by itself
- [ ] Right-click **`Install Auto-Start (Windows).bat` → Run as administrator.**
- [ ] After this, the app comes up on its own whenever the PC is on — you won't need to start it manually.

## Step 10 — Give the host a permanent address
- [ ] So the bookmark never breaks, ask whoever manages your office router to set a **“DHCP reservation”**
      for this PC — that locks its `192.168.x.x` address so it never changes.

## Step 11 — Automatic daily backups
- [ ] Open **Task Scheduler** (Windows search → “Task Scheduler”) → **Create Basic Task** →
      name it “Collection DB backup” → **Daily** → Action: **Start a program** →
      Program: `python`, Arguments: `backup.py`, Start in: your `server` folder.
- [ ] Backups land in `server\backups\` (it keeps the last 30). **Also copy some off the machine**
      occasionally (a second drive or USB) — that's your real safety net if the PC ever dies.

---

## You're live 🎉
Staff open the bookmark, log in, and work. To stop the server, close the black `Start…` window;
to start it again, double-click `Start Collection Database (Windows).bat` (or just reboot the PC —
auto-start handles it).

## If something goes wrong
- **Staff can't reach the address** → the `Start…` window must be open (or the PC on, if auto-start is
  set), they must be on the **same office Wi-Fi**, and the firewall must have been **Allowed** (Step 5).
- **Forgot the admin password** → in the `server` folder, open `cmd` and run `python set_password.py admin`.
- **Loaded the wrong workbook** → just re-run Step 4 with the correct file (it rebuilds and prints a new
  admin password; you'll re-create staff logins).
- **Need to move the data you entered on the Mac trial** → copy the Mac's `collection.db` and `backups`
  folder into the Windows `server` folder *before* Step 4, and **skip Step 4** (your data is already in
  `collection.db`).

## Optional — run without installing Python
On the Windows PC you can package everything into a single `.exe` so the host needs no Python:
`pip install pyinstaller`, then
`pyinstaller --onefile --add-data "static;static" --add-data "schema.sql;." --add-data "defaults.json;." run_server.py`.
(Build it on Windows.) This is optional — the steps above are all you need.
