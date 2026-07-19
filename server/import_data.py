#!/usr/bin/env python3
"""Build collection.db from a Phase-1 data source + defaults.json.

The Phase-1 source can be either the embedded `seed.json`, or a full JSON backup
exported from the single-file app (which additionally carries a `settings` object;
when present its agency heads / signatories / letterhead / mapping win over defaults).

Usage:
  python3 import_data.py [--seed ../_build_sources/seed.json] [--defaults defaults.json]
                         [--out collection.db] [--admin admin] [--admin-pass admin123] [--force]
"""
import argparse
import json
import pathlib
import secrets
import sqlite3
import sys

from pwutil import hash_password

HERE = pathlib.Path(__file__).resolve().parent
MONTHS = ["January", "February", "March", "April", "May", "June",
          "July", "August", "September", "October", "November", "December"]


def derive(date_str):
    """'YYYY-MM-DD' -> (year, month_name, day). Blank on bad input."""
    try:
        y, m, d = date_str.split("-")[:3]
        return y, MONTHS[int(m) - 1], str(int(d)).zfill(2)
    except Exception:
        return "", "", ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", default=str(HERE.parent / "_build_sources" / "seed.json"))
    ap.add_argument("--minnames", default=str(HERE.parent / "_build_sources" / "minnames.json"))
    ap.add_argument("--defaults", default=str(HERE / "defaults.json"))
    ap.add_argument("--out", default=str(HERE / "collection.db"))
    ap.add_argument("--admin", default="admin")
    ap.add_argument("--admin-pass", default=None,
                    help="admin password; if omitted a strong random one is generated and printed")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()

    out = pathlib.Path(a.out)
    if out.exists():
        if not a.force:
            sys.exit(f"{out} already exists. Use --force to rebuild.")
        out.unlink()
        for ext in ("-wal", "-shm"):
            p = pathlib.Path(str(out) + ext)
            if p.exists():
                p.unlink()

    src = json.loads(pathlib.Path(a.seed).read_text(encoding="utf-8"))
    dflt = json.loads(pathlib.Path(a.defaults).read_text(encoding="utf-8"))
    # a full backup carries its own settings; let it override the file defaults
    bset = src.get("settings") or {}

    con = sqlite3.connect(str(out))
    con.executescript((HERE / "schema.sql").read_text(encoding="utf-8"))
    cur = con.cursor()

    # orgmap
    for o in src.get("orgmap", []):
        cur.execute("INSERT OR REPLACE INTO orgmap(org_code,clearing,ministry,moa) VALUES(?,?,?,?)",
                    (o.get("org_code"), o.get("clearing"), o.get("ministry"), o.get("moa")))

    # collection types (+ extras) mapped to their certification class
    cmap = dict(dflt.get("cert_class_map", {}))
    # a backup's revConfig standard map (if any) overrides
    cmap.update(((bset.get("revConfig") or {}).get("schemes", {}).get("standard", {}) or {}).get("map", {}))
    schemes = bset.get("revConfig") or dflt.get("rev_schemes", {})
    catch = schemes.get("standard", {}).get("catchAll", "Other Revenue (300)")
    types = list(dict.fromkeys(list(src.get("lists", {}).get("collection_types", []))
                               + dflt.get("extra_collection_types", [])))
    for t in types:
        cur.execute("INSERT OR REPLACE INTO collection_types(name,cert_class) VALUES(?,?)",
                    (t, cmap.get(t, catch)))

    # ministry full names (static reference)
    mn_path = pathlib.Path(a.minnames)
    if mn_path.exists():
        for code, full in json.loads(mn_path.read_text(encoding="utf-8")).items():
            cur.execute("INSERT OR REPLACE INTO ministry_names(code,full_name) VALUES(?,?)", (code, full))

    # lookup lists
    lists = src.get("lists", {})
    for ln in ["payment_methods", "lbp_branches", "ministries", "offices", "months", "org_codes"]:
        for i, v in enumerate(lists.get(ln, [])):
            cur.execute("INSERT OR REPLACE INTO list_items(list_name,value,ord) VALUES(?,?,?)", (ln, v, i))

    # transactions — derive year/month/day from the ISO date (source of truth)
    skipped = 0
    for i, t in enumerate(src.get("transactions", [])):
        date = t.get("date") or ""
        y, m, d = derive(date)
        amt = float(t.get("amount") or 0)
        if amt < 0 or not date or not y:   # skip rows with a missing/unparseable date, not just blank
            skipped += 1
            continue
        cur.execute(
            """INSERT OR REPLACE INTO transactions
               (id,org_code,clearing,ministry,office,payment_method,lbp_branch,amount,
                txn_date,year,month,day,type,tagging,remarks,created_by)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (t.get("id") or f"seed-{i}", t.get("org_code"), t.get("clearing"), t.get("ministry"),
             t.get("office"), t.get("payment_method"), t.get("lbp_branch"), amt,
             date, y, m, d, t.get("type"), t.get("tagging") or "Actual", t.get("remarks"), "import"))

    # targets (year-scope; legacy flat {ministry,target} defaults to 2026)
    for tg in src.get("targets", []):
        cur.execute("INSERT OR REPLACE INTO targets(ministry,year,target) VALUES(?,?,?)",
                    (tg.get("ministry"), str(tg.get("year") or "2026"), float(tg.get("target") or 0)))

    # agency heads (backup settings override file defaults)
    heads = dict(dflt.get("agency_heads", {}))
    for off, h in (bset.get("agencyHeads") or {}).items():
        heads[off] = {"name": h.get("name", ""), "title": h.get("title", ""),
                      "agency_name": h.get("agencyName", "")}
    for off, h in heads.items():
        cur.execute("INSERT OR REPLACE INTO agency_heads(office,name,title,agency_name) VALUES(?,?,?,?)",
                    (off, h.get("name"), h.get("title"), h.get("agency_name")))

    # signatories
    sigs = bset.get("signatories") or dflt.get("signatories", [])
    for i, s in enumerate(sigs):
        cur.execute("INSERT OR REPLACE INTO signatories(ord,role_label,name,position) VALUES(?,?,?,?)",
                    (s.get("ord", i + 1), s.get("role_label"), s.get("name"), s.get("position")))

    # settings
    def setk(k, v):
        cur.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (k, v))
    lh = (bset.get("letterhead") or dflt.get("letterhead", {}))
    for k in ["l1", "l2", "l3", "l4"]:
        setk(f"letterhead_{k}", lh.get(k, ""))
    ct = bset.get("certText") or dflt.get("cert_text", {})
    setk("cert_body", ct.get("body", ""))
    setk("cert_purpose", ct.get("purpose", ""))
    setk("rev_schemes", json.dumps(schemes))
    # a backup that explicitly carries {} means "the admin deleted all groups" — respect it
    grp = bset.get("certGroups")
    setk("cert_groups", json.dumps(grp if grp is not None else dflt.get("cert_groups", {})))

    # admin user — never ship a known default; generate a strong random password if none supplied
    admin_pass = a.admin_pass or secrets.token_urlsafe(9)
    admin_generated = a.admin_pass is None
    cur.execute("INSERT OR REPLACE INTO users(username,name,password_hash,role,active) VALUES(?,?,?,?,1)",
                (a.admin, "Administrator", hash_password(admin_pass), "admin"))

    cur.execute("INSERT INTO audit(action,detail,by_user) VALUES(?,?,?)",
                ("Import", f"Initialized database from {pathlib.Path(a.seed).name}", "import"))
    con.commit()

    def qv(sql):
        return cur.execute(sql).fetchone()[0]
    total = qv("SELECT COALESCE(SUM(amount),0) FROM transactions WHERE tagging!='Target'")
    print(f"Built {out}")
    print(f"  users:            {qv('SELECT COUNT(*) FROM users')}")
    print(f"  transactions:     {qv('SELECT COUNT(*) FROM transactions')}   (skipped {skipped})")
    print(f"  orgmap:           {qv('SELECT COUNT(*) FROM orgmap')}")
    print(f"  collection_types: {qv('SELECT COUNT(*) FROM collection_types')}")
    print(f"  targets:          {qv('SELECT COUNT(*) FROM targets')}")
    print(f"  agency_heads:     {qv('SELECT COUNT(*) FROM agency_heads')}")
    print(f"  signatories:      {qv('SELECT COUNT(*) FROM signatories')}")
    print(f"  actual total:     PHP {total:,.2f}")
    con.close()
    if admin_generated:
        print("\n" + "=" * 62)
        print(f"  ADMIN LOGIN   username: {a.admin}    password: {admin_pass}")
        print("  ^ Write this down now. You can change it anytime in the app")
        print(f"    (Admin -> Reset password) or:  python3 set_password.py {a.admin}")
        print("=" * 62)


if __name__ == "__main__":
    main()
