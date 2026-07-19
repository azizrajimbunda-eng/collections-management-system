# Applying an Update to the Live System

You update the office PC by **replacing a few program files and restarting**. Your data is never touched —
the entries, logins, and settings all live in `collection.db`, which you do **not** replace.

> ⏱ About 10 minutes. Do it when no one is mid-entry (or just after hours).

## Update history
- **2026-07-09 feature batch + 2026-07-13 certification groups + org codes** — 8 files, deployed to the
  live PC 2026-07-13 (see HANDOFF.md). No longer pending.
- **2026-07-14 keyboard autocomplete for Data-Entry search boxes** — 1 file (`index.html`), deployed to the
  live PC 2026-07-14. No longer pending.
- **2026-07-14 "Collection Report (Official Format)"** — 3 files (`db.py`, `app.py`, `index.html`),
  deployed to the live PC 2026-07-14. No longer pending.

## What changes in THIS update (2026-07-14 — Excel export + subtitle cleanup)
**3 files.** No `fix_orgmap.py` step, no new database columns — just new code.

| File | Copy it into |
|---|---|
| `export_xlsx.py` | `server\` ← the main server folder |
| `app.py` | `server\` ← the main server folder |
| `index.html` | `server\static\` ← **the static sub-folder, not the main one** |

What it does:
1. Adds an **"Export to Excel"** button next to Print / Issue & Log, but only when the **"Collection Report
   (Official Format)"** report is selected. Downloads a real `.xlsx` file with the same I/II/III/IV
   structure — indented rows, bold section/subtotal headers — so it can be emailed, archived, or worked on
   further in Excel.
2. Removes the small gray subtitle line ("Per Official Report Format, by Month...") that used to appear
   under the "SUMMARY OF COLLECTION" title on that report — purely cosmetic, no data change.

## The steps

**1. Get all 3 files onto the PC** (USB drive, email to yourself, or a shared folder).

**2. Back up first (safety net).** Open `cmd` in the `server` folder and run:
```
python backup.py
```
You'll see a new file appear in `server\backups`. (Or just copy `collection.db` onto a USB.)

**3. Stop the app.** Close the black `Start Collection Database…` window. (If it auto-starts: open Task
Manager → find `python` → End task.)

**4. Replace the files.**
- Copy `export_xlsx.py` and `app.py` into the **`server`** folder → choose **"Replace the file in the
  destination"** for each.
- Copy `index.html` into the **`server\static`** folder → choose **"Replace the file in the destination."**
- ⚠️ **Do NOT copy `collection.db`, `.secret_key`, or the `backups` folder.** Those hold your live
  entries, logins, and history.

**5. Start the app again.** Double-click `Start Collection Database (Windows).bat` (or reboot if auto-start
is on).

**6. Confirm it worked.** Log in as a certifier or admin, go to **Print Reports**, pick **"Collection Report
(Official Format)"** — the subtitle line under "SUMMARY OF COLLECTION" should be gone, and a new **"Export
to Excel"** button should appear next to Print / Issue & Log. Click it and confirm a `.xlsx` file downloads
and opens correctly in Excel with the same I/II/III/IV layout.

## Optional — record interest income as a normal entry
In **Data Entry**, search the org code for **RTO**, pick type **Interest Income**, enter the amount and the
bank credit date, and Save — one entry per bank interest credit. After that the dashboard and reports include
it automatically, and your official report ties without a hand-added line.

## Rolling back (if ever needed)
Keep the previous copies of `export_xlsx.py`, `app.py`, and `index.html`. If the update misbehaves, put the
old files back and restart — your data is unaffected either way (this update adds an export button and
removes a subtitle line only, it doesn't change the database schema or touch any existing report).
