"""Data-access layer for the Collection Database server.

All report/query logic lives here (kept out of the HTTP layer) so it can be tested directly.
Aggregations run in SQL where natural; the nested drill-down and certification build in Python.
"""
import datetime
import json
import math
import pathlib
import re
import sqlite3
import uuid

DB_PATH = pathlib.Path(__file__).resolve().parent / "collection.db"
MONTHS = ["January", "February", "March", "April", "May", "June",
          "July", "August", "September", "October", "November", "December"]
# CR-MO-TAX report buckets (report-layer grouping; distinct from the certification cert_class).
TAX_BUCKETS = ["Natural Wealth Tax", "Contractor's Tax", "Travel Tax", "Fees and Charges", "Other Revenues"]


def connect():
    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    return con


def rows(con, sql, params=()):
    return [dict(r) for r in con.execute(sql, params).fetchall()]


def scalar(con, sql, params=()):
    r = con.execute(sql, params).fetchone()
    return r[0] if r else None


def actual_filter(year):
    """WHERE fragment + params for 'Actual (non-target) rows, optionally scoped to a year'."""
    if year:
        return "tagging!='Target' AND year=?", [year]
    return "tagging!='Target'", []


# ---- reference helpers ----
def _minnames(con):
    return {r["code"]: r["full_name"] for r in con.execute("SELECT code, full_name FROM ministry_names")}


def full_name(con, code, cache=None):
    m = cache if cache is not None else _minnames(con)
    return m.get(code, code)


def bucket_of(typ):
    return typ if typ in TAX_BUCKETS else "Other Revenues"


def _schemes(con):
    v = scalar(con, "SELECT value FROM settings WHERE key='rev_schemes'")
    return json.loads(v) if v else {}


def _cert_class_map(con):
    return {r["name"]: r["cert_class"] for r in con.execute("SELECT name, cert_class FROM collection_types")}


def _cert_groups(con):
    """Certification groups: {group_name: {ministry, org_code, head_office, members[]}}.
    A group merges several sub-offices into ONE certification (e.g. MOED + MOED BE/HE/MA)
    while data entry and internal reports keep the sub-offices separate.
    Parses defensively: a corrupted/foreign-shaped settings value must degrade to "no groups",
    never take down every endpoint that calls this."""
    v = scalar(con, "SELECT value FROM settings WHERE key='cert_groups'")
    try:
        d = json.loads(v) if v else {}
    except ValueError:
        return {}
    if not isinstance(d, dict):
        return {}
    return {name: g for name, g in d.items() if isinstance(name, str) and isinstance(g, dict)}


def years(con):
    return [r["year"] for r in con.execute(
        "SELECT DISTINCT year FROM transactions WHERE year<>'' ORDER BY year")]


# ---- meta (lookup data + settings for the UI) ----
def get_meta(con):
    lists = {}
    for r in con.execute("SELECT list_name, value FROM list_items ORDER BY list_name, ord"):
        lists.setdefault(r["list_name"], []).append(r["value"])
    settings = {r["key"]: r["value"] for r in con.execute("SELECT key, value FROM settings")}
    return {
        "years": years(con),
        "lists": lists,
        "collection_types": rows(con, "SELECT name, cert_class FROM collection_types ORDER BY name"),
        "ministries": [r["ministry"] for r in con.execute(
            "SELECT DISTINCT ministry FROM orgmap WHERE ministry<>'' ORDER BY ministry")],
        "orgmap": rows(con, "SELECT org_code, clearing, ministry, moa FROM orgmap ORDER BY org_code"),
        "settings": settings,
        "rev_schemes": _schemes(con),
        "signatories": rows(con, "SELECT ord, role_label, name, position FROM signatories ORDER BY ord"),
        "agency_head_count": scalar(con, "SELECT COUNT(*) FROM agency_heads WHERE name<>''"),
        "ministry_names": _minnames(con),
        "agency_names": {r["office"]: r["agency_name"] for r in con.execute(
            "SELECT office, agency_name FROM agency_heads WHERE agency_name<>''")},
    }


# ---- dashboard ----
def dashboard(con, year):
    w, p = actual_filter(year)
    total = scalar(con, f"SELECT COALESCE(SUM(amount),0) FROM transactions WHERE {w}", p)
    count = scalar(con, f"SELECT COUNT(*) FROM transactions WHERE {w}", p)
    active = scalar(con, f"SELECT COUNT(DISTINCT ministry) FROM transactions WHERE {w}", p)
    if year:
        target = scalar(con, "SELECT COALESCE(SUM(target),0) FROM targets WHERE year=?", [year])
    else:
        target = scalar(con, "SELECT COALESCE(SUM(target),0) FROM targets")
    monthly = {m: 0 for m in MONTHS}
    for r in con.execute(f"SELECT month, SUM(amount) s FROM transactions WHERE {w} GROUP BY month", p):
        if r["month"] in monthly:
            monthly[r["month"]] = r["s"]
    def grp(col, limit=None):
        lim = f" LIMIT {int(limit)}" if limit else ""
        return rows(con, f"SELECT {col} k, SUM(amount) v FROM transactions WHERE {w} "
                         f"GROUP BY {col} ORDER BY v DESC{lim}", p)
    return {
        "total": total, "target": target,
        "pct": (total / target * 100) if target else 0,
        "count": count, "ministries_active": active,
        "monthly": [monthly[m] for m in MONTHS],
        "by_type": grp("type"), "by_payment": grp("payment_method"),
        "top_offices": grp("office", 8),
    }


# ---- targets vs collections ----
def ministry_summary(con, year):
    w, p = actual_filter(year)
    actual = {r["ministry"]: r["s"] for r in con.execute(
        f"SELECT ministry, SUM(amount) s FROM transactions WHERE {w} GROUP BY ministry", p)}
    if year:
        tg = {r["ministry"]: r["target"] for r in con.execute(
            "SELECT ministry, SUM(target) target FROM targets WHERE year=? GROUP BY ministry", [year])}
    else:
        tg = {r["ministry"]: r["target"] for r in con.execute(
            "SELECT ministry, SUM(target) target FROM targets GROUP BY ministry")}
    names = set(actual) | set(tg) | {r["value"] for r in con.execute(
        "SELECT value FROM list_items WHERE list_name='ministries'")}
    out = []
    for m in names:
        if not m:
            continue
        a = actual.get(m, 0.0)
        t = tg.get(m, 0.0)
        pct = (a / t * 100) if t else (100 if a > 0 else 0)
        out.append({"ministry": m, "target": t, "actual": a, "balance": t - a, "pct": pct})
    out.sort(key=lambda r: r["actual"], reverse=True)
    return out


# ---- records (filterable, paginated) ----
def query_transactions(con, f, limit=500, offset=0):
    where, p = ["1=1"], []
    for col, key in [("ministry", "ministry"), ("office", "office"), ("type", "type"),
                     ("tagging", "tagging")]:
        if f.get(key):
            where.append(f"{col}=?"); p.append(f[key])
    if f.get("month"):
        where.append("month=?"); p.append(f["month"])
    if f.get("year"):
        where.append("year=?"); p.append(f["year"])
    if f.get("from"):
        where.append("txn_date>=?"); p.append(f["from"])
    if f.get("to"):
        where.append("txn_date<=?"); p.append(f["to"])
    if f.get("q"):
        like = "%" + f["q"].lower() + "%"
        where.append("(lower(org_code||' '||ministry||' '||office||' '||type||' '||"
                     "coalesce(payment_method,'')||' '||coalesce(lbp_branch,'')||' '||"
                     "coalesce(remarks,'')||' '||coalesce(clearing,'')) LIKE ?)")
        p.append(like)
    wsql = " AND ".join(where)
    total_count = scalar(con, f"SELECT COUNT(*) FROM transactions WHERE {wsql}", p)
    total_amt = scalar(con, f"SELECT COALESCE(SUM(amount),0) FROM transactions WHERE {wsql}", p)
    data = rows(con, f"SELECT * FROM transactions WHERE {wsql} ORDER BY txn_date DESC "
                     f"LIMIT ? OFFSET ?", p + [limit, offset])
    return {"rows": data, "count": total_count, "total": total_amt}


# ---- consolidated matrix reports (summary / bytype) ----
def report_matrix(con, year, mode, groupby):
    gk = "office" if groupby == "office" else "ministry"
    w, p = actual_filter(year)
    names = _minnames(con)
    if mode == "summary":
        cols = [{"key": m, "label": m[:3].upper()} for m in MONTHS]
        data = {}
        for r in con.execute(f"SELECT {gk} g, month, SUM(amount) s FROM transactions "
                             f"WHERE {w} GROUP BY {gk}, month", p):
            data.setdefault(r["g"] or "(blank)", {})[r["month"]] = r["s"]
    else:  # bytype -> CR-MO-TAX buckets
        cols = [{"key": b, "label": b} for b in TAX_BUCKETS]
        data = {}
        for r in con.execute(f"SELECT {gk} g, type, SUM(amount) s FROM transactions "
                             f"WHERE {w} GROUP BY {gk}, type", p):
            g = r["g"] or "(blank)"
            b = bucket_of(r["type"])
            data.setdefault(g, {})
            data[g][b] = data[g].get(b, 0) + r["s"]
    keys = [c["key"] for c in cols]
    col_tot = {k: 0 for k in keys}
    grand = 0.0
    out_rows = []
    for g in sorted(data):
        cells = {k: data[g].get(k, 0) for k in keys}
        rt = sum(cells.values())
        for k in keys:
            col_tot[k] += cells[k]
        grand += rt
        out_rows.append({"name": g, "full": names.get(g, g), "cells": cells, "total": rt})
    return {"mode": mode, "cols": cols, "rows": out_rows, "colTot": col_tot, "grand": grand,
            "rowLabel": "Office / Agency" if groupby == "office" else "Ministry"}


# ---- quarterly summary (collection type x quarter) ----
QUARTERS = [("Q1", ["January", "February", "March"]),
            ("Q2", ["April", "May", "June"]),
            ("Q3", ["July", "August", "September"]),
            ("Q4", ["October", "November", "December"])]


# The 6 official categories (fixed rows, always shown) for the Summary of Report of Collection.
# Same as the CR-MO-TAX buckets, with Interest Income split out of Other Revenues.
QUARTERLY_ROWS = ["Natural Wealth Tax", "Contractor's Tax", "Travel Tax",
                  "Fees and Charges", "Other Revenues", "Interest Income"]


def _q_bucket(typ):
    """Map each collection type to one of the 6 official report categories. Verified to reproduce
    the office's Summary of Report of Collection to the centavo: the airport service fees
    (DPSC / Concessionaire Fee / Parking Fee) roll into Fees and Charges; Travel Tax and
    Interest Income stand alone; everything else is Other Revenues."""
    if typ == "Interest Income":
        return "Interest Income"
    if typ in ("Natural Wealth Tax", "Contractor's Tax", "Travel Tax"):
        return typ
    if typ in ("Fees and Charges", "DPSC", "Concessionaire Fee", "Parking Fee"):
        return "Fees and Charges"
    return "Other Revenues"


def report_quarterly(con, year):
    """Rows = the 6 official collection categories (fixed); columns = Q1..Q4 + Total."""
    w, p = actual_filter(year)
    m2q = {m: qk for qk, months in QUARTERS for m in months}
    cols = [{"key": qk, "label": f"{qk} ({months[0][:3]}-{months[2][:3]})"} for qk, months in QUARTERS]
    keys = [c["key"] for c in cols]
    data = {b: {k: 0 for k in keys} for b in QUARTERLY_ROWS}
    for r in con.execute(f"SELECT type, month, SUM(amount) s FROM transactions WHERE {w} GROUP BY type, month", p):
        qk = m2q.get(r["month"])
        if not qk:
            continue
        data[_q_bucket(r["type"])][qk] += r["s"]
    col_tot = {k: 0 for k in keys}
    grand = 0.0
    out_rows = []
    for b in QUARTERLY_ROWS:
        cells = data[b]
        rt = sum(cells.values())
        for k in keys:
            col_tot[k] += cells[k]
        grand += rt
        out_rows.append({"name": b, "full": "", "cells": cells, "total": rt})
    return {"mode": "quarterly", "cols": cols, "rows": out_rows, "colTot": col_tot,
            "grand": grand, "rowLabel": "Collection Type"}


# ---- official "Collection Report" (I/II/III/IV nested format) ----
# Section III leaf lines mirror the office's own "Collection Report" workbook sheet exactly.
# The category-level split (A1/A2/A3/B1 vs everything else) matches _q_bucket, which is
# already verified to the centavo against the office's official report -- this only adds
# presentational sub-grouping on top of what _q_bucket lumps into "Other Revenues"/"Interest
# Income", it doesn't change any classification.
OFFICIAL_TAX_LINES = [("A1. Regional Wealth Tax", "Natural Wealth Tax"),
                       ("A2. Contractor's Tax", "Contractor's Tax"),
                       ("A3. Travel Tax", "Travel Tax")]
OFFICIAL_FEES_TYPES = ["Fees and Charges", "DPSC", "Concessionaire Fee", "Parking Fee"]
OFFICIAL_OTHER_DIRECT = [("Rent Income / Rental Fees", "Rental Fees"),
                          ("Net Interest Income on Deposits", "Interest Income"),
                          ("Sale of OR Forms", "Sale of OR"),
                          ("Sale/Installment Payments of Housing Units",
                           "Sale/Installment Payments of Housing Units (MHSD Specific)"),
                          ("Lease Income (CASA)", "CASA (CASA Specific)")]
OFFICIAL_OTHER_RECEIPTS = [("Receipts from Unutilized Bid Docs Sales",
                             "Receipts from Unutilized Bid Docs Sales"),
                            ("Hospital Income", "Hospital Income"),
                            ("Other Remittances", "Trust Receipts")]
OFFICIAL_MAPPED_TYPES = ([t for _, t in OFFICIAL_TAX_LINES] + OFFICIAL_FEES_TYPES +
                          [t for _, t in OFFICIAL_OTHER_DIRECT] +
                          [t for _, t in OFFICIAL_OTHER_RECEIPTS])


def _oc_node(label, cells, children=None, is_total=False):
    return {"label": label, "cells": cells, "total": sum(cells.values()),
            "children": children or [], "isTotal": is_total}


def _oc_sum(nodes):
    out = {m: 0 for m in MONTHS}
    for n in nodes:
        for m in MONTHS:
            out[m] += n["cells"][m]
    return out


def _oc_ministry_rows(con, w, p, names, types=None, exclude=None):
    """Ministry->Office breakdown by month for a type filter. Reuses report_drill's exact
    nesting so ministry sub-totals (e.g. MOTC's airport-related offices) fall out naturally
    from orgmap -- no hardcoded per-ministry logic needed."""
    if types is not None:
        ph = ",".join("?" for _ in types)
        cond, qp = f"type IN ({ph})", list(types)
    else:
        ph = ",".join("?" for _ in exclude)
        cond, qp = f"(type IS NULL OR type NOT IN ({ph}))", list(exclude)
    q = (f"SELECT ministry, office, month, SUM(amount) s FROM transactions "
         f"WHERE {w} AND {cond} GROUP BY ministry, office, month")
    mp = {}
    for r in con.execute(q, p + qp):
        mn = r["ministry"] or "(unmapped)"
        off = r["office"] or "(unmapped)"
        mp.setdefault(mn, {}).setdefault(off, {})
        mp[mn][off][r["month"]] = mp[mn][off].get(r["month"], 0) + r["s"]
    out = []
    for mn in sorted(mp):
        offices = []
        for off in sorted(mp[mn]):
            ocells = {m: mp[mn][off].get(m, 0) for m in MONTHS}
            offices.append(_oc_node(off, ocells))
        label = f"{mn} - {names[mn]}" if names.get(mn) and names[mn] != mn else mn
        out.append(_oc_node(label, _oc_sum(offices), offices))
    return out


def report_official(con, year):
    """Nested I/II/III/IV 'Collection Report', matching the office's official workbook format
    (sheet 'Collection Report'). Sections I, II, IV are national-government transfers/
    appropriations this office doesn't collect or track in the database -- rendered as blank
    PHP 0.00 lines, same as they appear in the source template. Only Section III is
    data-driven."""
    w, p = actual_filter(year)
    names = _minnames(con)
    cols = [{"key": m, "label": m[:3].upper()} for m in MONTHS]
    zero = {m: 0 for m in MONTHS}

    def leaf(label, types):
        children = _oc_ministry_rows(con, w, p, names, types=types)
        return _oc_node(label, _oc_sum(children), children)

    # I / II -- manual, not tracked here.
    sec_i = _oc_node("I. Regional Share in the National Government Taxes, Fees and Charges "
                      "(75% Share)", dict(zero))
    sec_ii = _oc_node("II. Block Grant", dict(zero))
    transfers_total = _oc_node("TOTAL TRANSFERS FROM NATIONAL GOVERNMENT",
                                _oc_sum([sec_i, sec_ii]), [], True)

    # III.A Tax Revenues
    a_lines = [leaf(label, [typ]) for label, typ in OFFICIAL_TAX_LINES]
    a_total = _oc_node("TOTAL TAX REVENUE", _oc_sum(a_lines), [], True)
    sec_a = _oc_node("A. Tax Revenues", _oc_sum(a_lines), a_lines)

    # III.B Non-Tax Revenues -> B1 + C
    b1 = leaf("B1. Regional Fees and Charges", OFFICIAL_FEES_TYPES)
    b1_sub = _oc_node("Subtotal (B1. Regional Fees and Charges)", b1["cells"], [], True)

    c_direct = [leaf(label, [typ]) for label, typ in OFFICIAL_OTHER_DIRECT]
    c_receipt_lines = [leaf(label, [typ]) for label, typ in OFFICIAL_OTHER_RECEIPTS]
    c_receipts = _oc_node("Other Receipts", _oc_sum(c_receipt_lines), c_receipt_lines)
    # Catch-all: anything not explicitly mapped above (incl. literal type "Other Revenues",
    # "Unidentified", and any future admin-added type) -- always shown, never silently dropped.
    catchall_children = _oc_ministry_rows(con, w, p, names, exclude=OFFICIAL_MAPPED_TYPES)
    c_catchall = _oc_node("Other / Unclassified Revenues", _oc_sum(catchall_children),
                           catchall_children)
    c_children = c_direct + [c_receipts, c_catchall]
    sec_c = _oc_node("C. Other Revenues", _oc_sum(c_children), c_children)
    c_sub = _oc_node("Subtotal (C. Other Revenues)", sec_c["cells"], [], True)

    grand_nontax = _oc_node("GRAND TOTAL NON TAX REVENUES", _oc_sum([b1, sec_c]), [], True)
    sec_b = _oc_node("B. Non-Tax Revenues", grand_nontax["cells"],
                      [b1, b1_sub, sec_c, c_sub])

    grand_regional = _oc_node("GRAND TOTAL REGIONAL TAXES, FEES & CHARGES",
                               _oc_sum([a_total, grand_nontax]), [], True)
    sec_iii = _oc_node("III. Revenues from Regional Taxes, Fees and Charges",
                        grand_regional["cells"],
                        [sec_a, a_total, sec_b, grand_nontax, grand_regional])

    subsidy_total = _oc_node("TOTAL REGIONAL COLLECTION AND NATIONAL SUBSIDY",
                              _oc_sum([transfers_total, grand_regional]), [], True)

    # IV -- manual, not tracked here.
    iv_wealth = _oc_node("Share in the National Wealth", dict(zero))
    iv_sdf = _oc_node("Special Development Fund", dict(zero))
    iv_total = _oc_node("TOTAL REVENUES FROM APPROPRIATIONS AND BUDGETARY ALLOCATIONS FROM "
                         "THE NATIONAL GOVERNMENT", _oc_sum([iv_wealth, iv_sdf]), [], True)
    sec_iv = _oc_node("IV. Revenues from Appropriations and Other Budgetary Allocations from "
                       "the National Government", iv_total["cells"],
                       [iv_wealth, iv_sdf, iv_total])

    overall_total = _oc_node("OVER-ALL TOTAL REVENUES GENERATED DURING THE PERIOD",
                              _oc_sum([transfers_total, grand_regional, iv_total]), [], True)

    sections = [sec_i, sec_ii, transfers_total, sec_iii, subsidy_total, sec_iv, overall_total]
    return {"mode": "official", "cols": cols, "sections": sections, "grand": overall_total["total"]}


# ---- nested drill-down (Ministry -> Office) ----
def report_drill(con, year, mode, show_empty=False):
    w, p = actual_filter(year)
    names = _minnames(con)
    if mode == "summary":
        cols = [{"key": m, "label": m[:3].upper()} for m in MONTHS]
        q = f"SELECT ministry, office, month, SUM(amount) s FROM transactions WHERE {w} GROUP BY ministry, office, month"
    else:
        cols = [{"key": b, "label": b} for b in TAX_BUCKETS]
        q = f"SELECT ministry, office, type, SUM(amount) s FROM transactions WHERE {w} GROUP BY ministry, office, type"
    keys = [c["key"] for c in cols]
    mp = {}
    for r in con.execute(q, p):
        mn = r["ministry"] or "(unmapped)"
        off = r["office"] or "(unmapped)"
        ck = r["month"] if mode == "summary" else bucket_of(r["type"])
        mp.setdefault(mn, {}).setdefault(off, {})
        mp[mn][off][ck] = mp[mn][off].get(ck, 0) + r["s"]
    if show_empty:
        for o in con.execute("SELECT ministry, moa FROM orgmap"):
            if o["ministry"] and o["moa"]:
                mp.setdefault(o["ministry"], {}).setdefault(o["moa"], {})
    col_tot = {k: 0 for k in keys}
    grand = 0.0
    ministries = []
    for mn in sorted(mp):
        sub = {k: 0 for k in keys}
        offices = []
        for off in sorted(mp[mn]):
            cells = {k: mp[mn][off].get(k, 0) for k in keys}
            rt = sum(cells.values())
            if not show_empty and rt == 0:
                continue
            for k in keys:
                sub[k] += cells[k]
            offices.append({"name": off, "cells": cells, "total": rt})
        mtot = sum(sub.values())
        if not show_empty and mtot == 0 and not offices:
            continue
        for k in keys:
            col_tot[k] += sub[k]
        grand += mtot
        ministries.append({"name": mn, "full": names.get(mn, mn),
                           "offices": offices, "subtotal": {"cells": sub, "total": mtot}})
    return {"mode": mode, "cols": cols, "ministries": ministries, "colTot": col_tot, "grand": grand}


# ---- ledger ----
def report_ledger(con, year, f):
    where, p = ["tagging!='Target'"], []
    if year:
        where.append("year=?"); p.append(year)
    for col, key in [("ministry", "ministry"), ("office", "office"), ("type", "type")]:
        if f.get(key):
            where.append(f"{col}=?"); p.append(f[key])
    if f.get("month"):
        where.append("month=?"); p.append(f["month"])
    wsql = " AND ".join(where)
    data = rows(con, f"SELECT txn_date, ministry, office, payment_method, lbp_branch, type, amount "
                     f"FROM transactions WHERE {wsql} ORDER BY txn_date", p)
    total = sum(r["amount"] or 0 for r in data)
    return {"rows": data, "total": total, "count": len(data)}


# ---- certification data ----
def cert_data(con, office, month, year):
    schemes = _schemes(con)
    cmap = _cert_class_map(con)
    group = _cert_groups(con).get(office)
    certifiable = _certifiable_offices(con)
    if group and office in certifiable:
        group = None   # a real office always wins — a group (e.g. from a tampered seed) may never shadow one
    if not group and office not in certifiable:
        # fail loudly rather than issue an official zero-amount certification for a name that
        # matches nothing (e.g. a request left pending while its group was renamed or deleted)
        raise LookupError(f'"{office}" is not a known agency or certification group '
                          f"(it may have been renamed or deleted — decline or re-submit the request)")
    # a group certifies several member offices as one undifferentiated list;
    # its printed org code / head / agency name come from a designated member ("head_office")
    members = [m for m in (group.get("members") or []) if isinstance(m, str) and m] if group else [office]
    if not members:   # malformed group config (hand-edited seed/backup) — never emit "IN ()"
        members = [office]
    face = (group.get("head_office") or members[0]) if group else office
    # revenue scheme follows the real office the document represents, never the group's name
    scheme_name = (schemes.get("agencyScheme") or {}).get(face, "standard")
    if scheme_name == "airport":
        sc = schemes.get("airport", {})
        classes = sc.get("classes", [])
        def cls_of(typ):
            return (sc.get("map") or {}).get(typ, sc.get("catchAll"))
    else:
        sc = schemes.get("standard", {})
        classes = sc.get("classes", [])
        catch = sc.get("catchAll", "Other Revenue (300)")
        def cls_of(typ):
            return cmap.get(typ, catch)
    where = ["tagging!='Target'", f"office IN ({','.join('?' * len(members))})"]
    p = list(members)
    if year:
        where.append("year=?"); p.append(year)
    if month:
        where.append("month=?"); p.append(month)
    data = rows(con, f"SELECT txn_date, lbp_branch, type, amount, remarks FROM transactions "
                     f"WHERE {' AND '.join(where)} ORDER BY txn_date", p)
    totals = {c: 0 for c in classes}
    grand = 0.0
    lines = []
    for r in data:
        cls = cls_of(r["type"])
        amt = r["amount"] or 0
        totals[cls] = totals.get(cls, 0) + amt
        grand += amt
        lines.append({"date": r["txn_date"], "branch": r["lbp_branch"], "cls": cls,
                      "amount": amt, "remarks": r["remarks"], "type": r["type"]})
    om = con.execute("SELECT org_code, ministry FROM orgmap WHERE moa=? LIMIT 1", [face]).fetchone()
    head = con.execute("SELECT name, title, agency_name FROM agency_heads WHERE office=?", [face]).fetchone()
    ministry = ((group.get("ministry") or "") if group else "") or (om["ministry"] if om else "")
    org_code = ((group.get("org_code") or "") if group else "") or (om["org_code"] if om else "")
    agency_name = (head["agency_name"] if head and head["agency_name"] else "") \
        or full_name(con, ministry) or office
    return {
        "office": office, "month": month, "year": year, "classes": classes,
        "lines": lines, "totals": totals, "grand": grand,
        "org_code": org_code, "ministry": ministry,
        "agency_name": agency_name,
        "head": {"name": head["name"], "title": head["title"]} if head else {"name": "", "title": ""},
    }


def agencies_by_ministry(con, ministry):
    a = [r["moa"] for r in con.execute(
        "SELECT DISTINCT moa FROM orgmap WHERE ministry=? AND moa<>''", [ministry])]
    a += [r["office"] for r in con.execute(
        "SELECT DISTINCT office FROM transactions WHERE ministry=? AND office<>''", [ministry])]
    a += [name for name, g in _cert_groups(con).items() if (g.get("ministry") or "") == ministry]
    return sorted(set(a))


# ============================ WRITES (Step 4) ============================
def derive_date(date_str):
    """Parse a strict ISO 'YYYY-MM-DD' into (year, month-name, zero-padded day).
    Returns ('','','') for any malformed or non-calendar date (month 00/13, day 40,
    Feb 30, non-padded) so the caller rejects it instead of mis-filing it."""
    try:
        dt = datetime.date.fromisoformat((date_str or "").strip())
        return str(dt.year), MONTHS[dt.month - 1], str(dt.day).zfill(2)
    except (ValueError, TypeError):
        return "", "", ""


def add_audit(con, action, detail, user):
    con.execute("INSERT INTO audit(action, detail, by_user) VALUES(?,?,?)", (action, detail, user))


def _peso(n):
    return "PHP {:,.2f}".format(float(n or 0))


def _validate_txn(con, data):
    """Server-side validation + field derivation. Raises ValueError on bad input."""
    org = (data.get("org_code") or "").strip()
    pm = (data.get("payment_method") or "").strip()
    ty = (data.get("type") or "").strip()
    date = (data.get("date") or "").strip()
    if not org:
        raise ValueError("Organizational Code is required")
    if not pm:
        raise ValueError("Payment Method is required")
    if not ty:
        raise ValueError("Type of Collection is required")
    y, m, d = derive_date(date)
    if not y:
        raise ValueError("A valid Transaction Date is required (YYYY-MM-DD)")
    try:
        amt = float(data.get("amount"))
    except (TypeError, ValueError):
        raise ValueError("Amount must be a number")
    if not math.isfinite(amt):
        raise ValueError("Amount must be a finite number")
    if amt < 0:
        raise ValueError("Amount must not be negative")
    tag = data.get("tagging") or "Actual"
    if tag not in ("Actual", "Target"):
        raise ValueError("Tagging must be Actual or Target")
    o = con.execute("SELECT clearing, ministry, moa FROM orgmap WHERE org_code=?", [org]).fetchone()
    return {"org_code": org, "clearing": o["clearing"] if o else "",
            "ministry": o["ministry"] if o else "", "office": o["moa"] if o else "",
            "payment_method": pm, "lbp_branch": (data.get("lbp_branch") or "").strip(),
            "amount": amt, "date": date, "year": y, "month": m, "day": d,
            "type": ty, "tagging": tag, "remarks": (data.get("remarks") or "").strip()}


def add_transaction(con, data, user):
    r = _validate_txn(con, data)
    tid = "tx-" + uuid.uuid4().hex[:12]
    con.execute("""INSERT INTO transactions
        (id,org_code,clearing,ministry,office,payment_method,lbp_branch,amount,txn_date,year,month,day,
         type,tagging,remarks,created_by,created_ts)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))""",
        (tid, r["org_code"], r["clearing"], r["ministry"], r["office"], r["payment_method"], r["lbp_branch"],
         r["amount"], r["date"], r["year"], r["month"], r["day"], r["type"], r["tagging"], r["remarks"], user))
    add_audit(con, "Add", f'{r["org_code"]} {_peso(r["amount"])} ({r["type"]})', user)
    con.commit()
    return tid


def update_transaction(con, tid, data, user):
    if not con.execute("SELECT 1 FROM transactions WHERE id=?", [tid]).fetchone():
        raise LookupError("Transaction not found")
    r = _validate_txn(con, data)
    con.execute("""UPDATE transactions SET org_code=?,clearing=?,ministry=?,office=?,payment_method=?,
        lbp_branch=?,amount=?,txn_date=?,year=?,month=?,day=?,type=?,tagging=?,remarks=?,
        updated_by=?,updated_ts=datetime('now') WHERE id=?""",
        (r["org_code"], r["clearing"], r["ministry"], r["office"], r["payment_method"], r["lbp_branch"],
         r["amount"], r["date"], r["year"], r["month"], r["day"], r["type"], r["tagging"], r["remarks"], user, tid))
    add_audit(con, "Edit", f'{r["org_code"]} {_peso(r["amount"])} ({r["type"]})', user)
    con.commit()
    return tid


def delete_transaction(con, tid, user):
    row = con.execute("SELECT org_code, amount, type FROM transactions WHERE id=?", [tid]).fetchone()
    if not row:
        raise LookupError("Transaction not found")
    con.execute("DELETE FROM transactions WHERE id=?", [tid])
    add_audit(con, "Delete", f'{row["org_code"]} {_peso(row["amount"])} ({row["type"]})', user)
    con.commit()


def get_transaction(con, tid):
    r = con.execute("SELECT * FROM transactions WHERE id=?", [tid]).fetchone()
    return dict(r) if r else None


def next_cert_no(con, year, month_idx):
    ym = f"{year}-{str(int(month_idx) + 1).zfill(2)}"
    r = con.execute("SELECT last_no FROM cert_sequence WHERE ym=?", [ym]).fetchone()
    return f"{ym}-{str((r['last_no'] if r else 0) + 1).zfill(3)}"


def build_snapshot(con, kind, params):
    """Recompute the report/certification server-side so issued figures are authoritative."""
    if kind == "cert":
        d = cert_data(con, params.get("office"), params.get("month"), params.get("year"))
        sigs = rows(con, "SELECT role_label, name, position FROM signatories ORDER BY ord")
        payload = {"office": d["office"], "month": d["month"], "year": d["year"], "classes": d["classes"],
                   "lines": [[l["date"], l["branch"], l["cls"], l["amount"], l["remarks"]] for l in d["lines"]],
                   "totals": d["totals"], "grand": d["grand"], "org_code": d["org_code"],
                   "agency_name": d["agency_name"], "head": d["head"], "sigs": sigs,
                   "body": scalar(con, "SELECT value FROM settings WHERE key='cert_body'"),
                   "purpose": scalar(con, "SELECT value FROM settings WHERE key='cert_purpose'")}
        return {"kind": "cert", "total": d["grand"], "row_count": len(d["lines"]), "payload": payload}
    if kind in ("summary", "bytype"):
        if params.get("detail") == "drill":
            d = report_drill(con, params.get("year"), kind, params.get("empty") in ("1", True, "true"))
            return {"kind": kind, "total": d["grand"], "row_count": len(d["ministries"]), "payload": d}
        d = report_matrix(con, params.get("year"), kind, params.get("groupby", "ministry"))
        return {"kind": kind, "total": d["grand"], "row_count": len(d["rows"]), "payload": d}
    if kind == "quarterly":
        d = report_quarterly(con, params.get("year"))
        return {"kind": "quarterly", "total": d["grand"], "row_count": len(d["rows"]), "payload": d}
    if kind == "official":
        d = report_official(con, params.get("year"))
        return {"kind": "official", "total": d["grand"], "row_count": len(d["sections"]), "payload": d}
    if kind == "ledger":
        d = report_ledger(con, params.get("year"), params)
        return {"kind": "ledger", "total": d["total"], "row_count": d["count"], "payload": None}
    raise ValueError("Unknown report kind")


def issue_report(con, kind, params, user, cert_no=None):
    """Issue a report / certification. For certifications the official number is
    ALWAYS assigned server-side and atomically (any caller-supplied cert_no is
    ignored), so two concurrent issuances can neither share nor skip a number."""
    snap = build_snapshot(con, kind, params)
    rid = "ir-" + uuid.uuid4().hex[:12]
    cert_no = None
    if kind == "cert":
        yr, mon = params.get("year"), params.get("month")
        if yr and mon in MONTHS:
            ym = f"{yr}-{str(MONTHS.index(mon) + 1).zfill(2)}"
            # reserve-and-increment atomically inside this write transaction (single writer)
            con.execute("INSERT INTO cert_sequence(ym,last_no) VALUES(?,1) "
                        "ON CONFLICT(ym) DO UPDATE SET last_no=last_no+1", [ym])
            last = con.execute("SELECT last_no FROM cert_sequence WHERE ym=?", [ym]).fetchone()["last_no"]
            cert_no = f"{ym}-{str(last).zfill(3)}"
    con.execute("""INSERT INTO issued_reports
        (id,kind,cert_no,params_json,payload_json,total,row_count,by_user,generated_ts)
        VALUES(?,?,?,?,?,?,?,?,datetime('now'))""",
        (rid, snap["kind"], cert_no, json.dumps(params), json.dumps(snap["payload"]),
         snap["total"], snap["row_count"], user))
    label = "Issued Certification" if kind == "cert" else "Issued Report"
    add_audit(con, label, f'{cert_no or kind} {_peso(snap["total"])}', user)
    con.commit()
    return {"id": rid, "cert_no": cert_no, **snap}


# ---- admin: users & targets ----
def list_users(con):
    return rows(con, "SELECT id, username, name, role, active, created_ts FROM users ORDER BY username")


def create_user(con, username, name, password_hash, role, actor):
    if role not in ("admin", "encoder", "certifier", "viewer"):
        raise ValueError("Invalid role")
    if con.execute("SELECT 1 FROM users WHERE username=?", [username]).fetchone():
        raise ValueError("Username already exists")
    con.execute("INSERT INTO users(username,name,password_hash,role,active) VALUES(?,?,?,?,1)",
                (username, name, password_hash, role))
    add_audit(con, "Create User", f"{username} ({role})", actor)
    con.commit()


def set_user_active(con, uid, active, actor):
    con.execute("UPDATE users SET active=? WHERE id=?", [1 if active else 0, uid])
    add_audit(con, "User " + ("Enabled" if active else "Disabled"), str(uid), actor)
    con.commit()


def set_password(con, uid, password_hash, actor):
    con.execute("UPDATE users SET password_hash=? WHERE id=?", [password_hash, uid])
    add_audit(con, "Change Password", "user " + str(uid), actor)
    con.commit()


def set_target(con, ministry, year, target, actor):
    try:
        t = float(target)
    except (TypeError, ValueError):
        raise ValueError("Target must be a number")
    if not math.isfinite(t) or t < 0:
        raise ValueError("Target must be a non-negative finite number")
    con.execute("INSERT INTO targets(ministry,year,target) VALUES(?,?,?) "
                "ON CONFLICT(ministry,year) DO UPDATE SET target=excluded.target",
                [ministry, str(year), t])
    add_audit(con, "Set Target", f"{ministry} ({year}) = {_peso(t)}", actor)
    con.commit()


# ---- admin: certification-config editors (officials / signatories / class map / letterhead) ----
def _known_offices(con):
    """Every real office name (orgmap.moa ∪ transactions.office) — excludes groups."""
    offs = {r["moa"] for r in con.execute("SELECT DISTINCT moa FROM orgmap WHERE moa<>''")}
    offs |= {r["office"] for r in con.execute("SELECT DISTINCT office FROM transactions WHERE office<>''")}
    return offs


def _certifiable_offices(con):
    """_known_offices ∪ seeded agency heads — the same set cert_offices() lists and the
    admin's member picker offers, so validation and UI can never disagree."""
    return _known_offices(con) | {r["office"] for r in con.execute("SELECT office FROM agency_heads")}


def cert_offices(con):
    """Every certifiable office (orgmap.moa ∪ transactions.office ∪ seeded heads),
    joined to its agency head — so the admin can also fill heads that were never seeded."""
    offs = _known_offices(con)
    heads = {r["office"]: r for r in con.execute(
        "SELECT office, name, title, agency_name FROM agency_heads")}
    offs |= set(heads)
    out = []
    for o in sorted(offs):
        h = heads.get(o)
        name = (h["name"] if h else "") or ""
        out.append({"office": o, "name": name,
                    "title": (h["title"] if h else "") or "",
                    "agency_name": (h["agency_name"] if h else "") or "",
                    "has_head": bool(name.strip())})
    return out


def save_agency_head(con, office, name, title, agency_name, actor):
    office = (office or "").strip()
    if not office:
        raise ValueError("Office is required")
    con.execute("""INSERT INTO agency_heads(office,name,title,agency_name) VALUES(?,?,?,?)
                   ON CONFLICT(office) DO UPDATE SET
                     name=excluded.name, title=excluded.title, agency_name=excluded.agency_name""",
                (office, (name or "").strip(), (title or "").strip(), (agency_name or "").strip()))
    add_audit(con, "Set Agency Head", f"{office}: {(name or '').strip() or '(cleared)'}", actor)
    con.commit()


def save_signatories(con, sigs, actor):
    """Replace the whole signatory block (a small ordered list). Blank rows are dropped."""
    clean = []
    for s in sigs or []:
        rl = (s.get("role_label") or "").strip()
        nm = (s.get("name") or "").strip()
        ps = (s.get("position") or "").strip()
        if rl or nm or ps:
            clean.append((rl, nm, ps))
    if not clean:
        raise ValueError("At least one signatory is required")
    if len(clean) > 6:
        raise ValueError("Too many signatories (max 6)")
    con.execute("DELETE FROM signatories")
    for i, (rl, nm, ps) in enumerate(clean, 1):
        con.execute("INSERT INTO signatories(ord,role_label,name,position) VALUES(?,?,?,?)",
                    (i, rl, nm, ps))
    add_audit(con, "Set Signatories", f"{len(clean)} signatory line(s)", actor)
    con.commit()


def save_cert_class(con, name, cert_class, actor):
    name = (name or "").strip()
    cert_class = (cert_class or "").strip()
    if not name:
        raise ValueError("Collection type is required")
    if not cert_class:
        raise ValueError("A certification revenue-class is required")
    if not con.execute("SELECT 1 FROM collection_types WHERE name=?", [name]).fetchone():
        raise ValueError("Unknown collection type")
    # keep the mapping within the certification's actual columns, so a class total can
    # never silently diverge from the grand total on an official certification
    valid = set((_schemes(con).get("standard", {}) or {}).get("classes", []))
    if valid and cert_class not in valid:
        raise ValueError("Revenue-class must be one of: " + ", ".join(sorted(valid)))
    con.execute("UPDATE collection_types SET cert_class=? WHERE name=?", (cert_class, name))
    add_audit(con, "Set Class Mapping", f"{name} -> {cert_class}", actor)
    con.commit()


# ---- certification groups (merge several sub-offices into one certification) ----
def cert_group_rows(con):
    return [{"name": name,
             "ministry": g.get("ministry") or "",
             "org_code": g.get("org_code") or "",
             "head_office": g.get("head_office") or "",
             "members": g.get("members") or []}
            for name, g in sorted(_cert_groups(con).items())]


def _save_groups(con, groups):
    con.execute("INSERT INTO settings(key,value) VALUES('cert_groups',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value", [json.dumps(groups)])


def _as_text(v):
    """Coerce a JSON field to trimmed text; non-string junk becomes '' instead of a 500."""
    return v.strip() if isinstance(v, str) else ""


def save_cert_group(con, name, ministry, org_code, head_office, members, actor, original=None):
    name = _as_text(name)
    ministry = _as_text(ministry)
    org_code = _as_text(org_code)
    head_office = _as_text(head_office)
    original = _as_text(original)
    if not name:
        raise ValueError("A group name is required")
    if len(name) > 80:
        raise ValueError("Group name is too long (max 80 characters)")
    known = _certifiable_offices(con)
    if name.lower() in {o.lower() for o in known}:
        raise ValueError("That name already belongs to a real office — pick a distinct group name "
                         "(e.g. add \"(Consolidated)\")")
    groups = _cert_groups(con)
    if name.lower() in {k.lower() for k in groups if k != original}:
        raise ValueError("A group with that name already exists — edit that group instead of "
                         "creating a duplicate (its configuration would be overwritten)")
    clean = list(dict.fromkeys(m.strip() for m in (members or []) if isinstance(m, str) and m.strip()))
    if len(clean) < 2:
        raise ValueError("Pick at least two member offices (a single office needs no group)")
    bad = [m for m in clean if m not in known]
    if bad:
        raise ValueError("Unknown office(s): " + ", ".join(bad))
    # all members must share one revenue scheme, or the certificate's class breakdown
    # would silently misclassify the odd office's lines (e.g. airport types into Other Revenue)
    agency_scheme = _schemes(con).get("agencyScheme") or {}
    member_schemes = {agency_scheme.get(m, "standard") for m in clean}
    if len(member_schemes) > 1:
        raise ValueError("These offices use different revenue schemes and cannot share one "
                         "certification: " + ", ".join(f"{m} ({agency_scheme.get(m, 'standard')})" for m in clean))
    if not head_office:
        head_office = clean[0]
    if head_office not in clean:
        raise ValueError("The head office must be one of the group's members")
    if not ministry:
        r = con.execute("SELECT ministry FROM orgmap WHERE moa=? LIMIT 1", [head_office]).fetchone()
        ministry = (r["ministry"] if r else "") or ""
    if not ministry:
        raise ValueError("Choose the ministry this group appears under")
    if original and original != name:
        groups.pop(original, None)
    groups[name] = {"ministry": ministry, "org_code": org_code,
                    "head_office": head_office, "members": clean}
    _save_groups(con, groups)
    detail = f"{name} [{ministry}] = {', '.join(clean)} (head: {head_office}" \
             + (f", org code: {org_code}" if org_code else "") + ")"
    if original and original != name:
        detail += f" (renamed from: {original})"
    add_audit(con, "Set Certification Group", detail, actor)
    con.commit()


def delete_cert_group(con, name, actor):
    name = _as_text(name)
    groups = _cert_groups(con)
    if name not in groups:
        raise LookupError("No such group")
    g = groups.pop(name)
    _save_groups(con, groups)
    add_audit(con, "Delete Certification Group",
              f"{name} (was: {', '.join(g.get('members') or [])})", actor)
    con.commit()


# free-text settings the letterhead/text editor may touch (whitelist guards the settings table)
LETTERHEAD_KEYS = {"l1": "letterhead_l1", "l2": "letterhead_l2", "l3": "letterhead_l3",
                   "l4": "letterhead_l4", "body": "cert_body", "purpose": "cert_purpose"}


def save_letterhead(con, data, actor):
    """Update letterhead lines + certificate body/purpose (only the keys supplied)."""
    touched = 0
    for src, key in LETTERHEAD_KEYS.items():
        if data.get(src) is None:
            continue
        con.execute("INSERT INTO settings(key,value) VALUES(?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (key, (data.get(src) or "").strip()))
        touched += 1
    if not touched:
        raise ValueError("Nothing to update")
    add_audit(con, "Set Letterhead", "letterhead / certificate text updated", actor)
    con.commit()


def orgmap_rows(con):
    """Every org code with its classification, the # of transactions on it, and a suggested
    ministry learned from those transactions (to help fill blanks)."""
    learned = {}
    for r in con.execute("SELECT org_code, ministry, COUNT(*) n FROM transactions "
                         "WHERE ministry<>'' GROUP BY org_code, ministry"):
        learned.setdefault(r["org_code"], {})[r["ministry"]] = r["n"]
    cnt = {r["org_code"]: r["n"] for r in con.execute(
        "SELECT org_code, COUNT(*) n FROM transactions GROUP BY org_code")}
    out = []
    for r in con.execute("SELECT org_code, clearing, ministry, moa FROM orgmap ORDER BY org_code"):
        m = learned.get(r["org_code"])
        out.append({"org_code": r["org_code"], "clearing": r["clearing"] or "",
                    "ministry": r["ministry"] or "", "office": r["moa"] or "",
                    "txns": cnt.get(r["org_code"], 0),
                    "suggested_ministry": max(m, key=m.get) if m else ""})
    return out


ORGFIELD_MAX = 120  # each orgmap row ships to every user via /api/meta; keep fields bounded


def add_orgcode(con, org_code, ministry, office, clearing, actor):
    """Add a brand-new org code (a new office/ministry that will remit). Unlike
    save_orgcode, this creates a row rather than editing one."""
    org_code = (org_code or "").strip()
    ministry = (ministry or "").strip()
    office = (office or "").strip()
    clearing = (clearing or "").strip()
    if not org_code:
        raise ValueError("Org code is required")
    if not office:
        raise ValueError("Office name is required")
    for label, val in (("Org code", org_code), ("Office name", office),
                       ("Ministry", ministry), ("Clearing", clearing)):
        if len(val) > ORGFIELD_MAX:
            raise ValueError(f"{label} is too long (max {ORGFIELD_MAX} characters)")
    if con.execute("SELECT 1 FROM orgmap WHERE trim(org_code)=?", [org_code]).fetchone():
        raise ValueError("That org code already exists")
    try:
        con.execute("INSERT INTO orgmap(org_code,clearing,ministry,moa) VALUES(?,?,?,?)",
                    (org_code, clearing, ministry, office))
    except sqlite3.IntegrityError:
        # racing insert of the same PK slipped past the check above
        raise ValueError("That org code already exists")
    # a genuinely new office has no transactions (0-row update); this also backfills the
    # ministry onto any orphan entries that referenced the code before it was mapped.
    regrouped = 0
    if ministry:
        cur = con.execute("UPDATE transactions SET ministry=? "
                          "WHERE trim(org_code)=? AND ministry<>? AND tagging!='Target'",
                          (ministry, org_code, ministry))
        regrouped = cur.rowcount
    add_audit(con, "Add Org Code",
              f"{org_code} ({office}) -> '{ministry or '(blank)'}'"
              + (f" ({regrouped} entries regrouped)" if regrouped else ""), actor)
    con.commit()
    return {"regrouped": regrouped}


def save_orgcode(con, org_code, ministry, actor, regroup=True):
    """Set an org code's ministry; optionally re-group its existing transactions to match."""
    org_code = (org_code or "").strip()
    ministry = (ministry or "").strip()
    if not org_code:
        raise ValueError("Org code is required")
    if len(ministry) > ORGFIELD_MAX:
        raise ValueError(f"Ministry is too long (max {ORGFIELD_MAX} characters)")
    # match trim-tolerantly: a few seeded codes carry stray trailing whitespace
    row = con.execute("SELECT org_code FROM orgmap WHERE trim(org_code)=?", [org_code]).fetchone()
    if not row:
        raise ValueError("Unknown org code")
    real = row["org_code"]
    con.execute("UPDATE orgmap SET ministry=? WHERE org_code=?", (ministry, real))
    regrouped = 0
    if regroup and ministry:
        cur = con.execute("UPDATE transactions SET ministry=? "
                          "WHERE trim(org_code)=? AND ministry<>? AND tagging!='Target'",
                          (ministry, org_code, ministry))
        regrouped = cur.rowcount
    add_audit(con, "Set Org Code Ministry",
              f"{org_code} -> '{ministry or '(blank)'}'" + (f" ({regrouped} entries regrouped)" if regrouped else ""),
              actor)
    con.commit()
    return {"regrouped": regrouped}


def add_collection_type(con, name, cert_class, actor):
    name = (name or "").strip()
    cert_class = (cert_class or "").strip()
    if not name:
        raise ValueError("Collection type name is required")
    if con.execute("SELECT 1 FROM collection_types WHERE name=?", [name]).fetchone():
        raise ValueError("That collection type already exists")
    std = _schemes(con).get("standard", {}) or {}
    valid = set(std.get("classes", []))
    if not cert_class:
        cert_class = std.get("catchAll", "Other Revenue (300)")
    if valid and cert_class not in valid:
        raise ValueError("Revenue-class must be one of: " + ", ".join(sorted(valid)))
    con.execute("INSERT INTO collection_types(name, cert_class) VALUES(?,?)", (name, cert_class))
    add_audit(con, "Add Collection Type", f"{name} -> {cert_class}", actor)
    con.commit()


def get_audit(con, limit=500):
    return rows(con, "SELECT ts, action, detail, by_user FROM audit ORDER BY id DESC LIMIT ?", [limit])


def list_issued(con):
    return rows(con, "SELECT id, kind, cert_no, total, row_count, by_user, generated_ts "
                     "FROM issued_reports ORDER BY generated_ts DESC")


def get_issued(con, rid):
    r = con.execute("SELECT * FROM issued_reports WHERE id=?", [rid]).fetchone()
    if not r:
        return None
    d = dict(r)
    d["params"] = json.loads(d.pop("params_json") or "{}")
    d["payload"] = json.loads(d.pop("payload_json") or "null")
    return d


# ============================ report requests (#8) ============================
def ensure_migrations(con):
    """Create tables that may be missing from an older DB (idempotent)."""
    con.execute("""CREATE TABLE IF NOT EXISTS report_requests(
      id INTEGER PRIMARY KEY AUTOINCREMENT, requester TEXT NOT NULL, requester_name TEXT,
      kind TEXT NOT NULL, params_json TEXT, note TEXT,
      status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','fulfilled','declined')),
      created_ts TEXT NOT NULL DEFAULT (datetime('now')), handled_ts TEXT, handled_by TEXT,
      issued_report_id TEXT, decline_reason TEXT)""")
    # official certification numbers must be unique (partial index: many NULLs allowed for non-cert rows)
    try:
        con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_issued_cert_no "
                    "ON issued_reports(cert_no) WHERE cert_no IS NOT NULL")
    except sqlite3.IntegrityError:
        pass  # pre-existing duplicate cert_no (shouldn't occur) — leave for manual cleanup
    # seed certification groups from defaults.json ONCE for DBs built before the key existed
    # (an empty {} left by the admin deleting all groups is respected, never re-seeded)
    if scalar(con, "SELECT value FROM settings WHERE key='cert_groups'") is None:
        try:
            dflt = json.loads((pathlib.Path(__file__).resolve().parent / "defaults.json")
                              .read_text(encoding="utf-8"))
            groups = dflt.get("cert_groups", {})
        except (OSError, ValueError):
            groups = {}
        con.execute("INSERT INTO settings(key,value) VALUES('cert_groups',?)", [json.dumps(groups)])
    con.commit()


def create_request(con, requester, requester_name, kind, params, note):
    if kind not in ("cert", "summary", "bytype", "ledger", "quarterly", "official"):
        raise ValueError("Invalid report kind")
    if kind == "cert" and not params.get("office"):
        raise ValueError("Please choose an agency for the certification")
    cur = con.execute("INSERT INTO report_requests(requester,requester_name,kind,params_json,note) "
                      "VALUES(?,?,?,?,?)", [requester, requester_name, kind, json.dumps(params), note])
    add_audit(con, "Report Requested", f"{kind} by {requester_name or requester}", requester)
    con.commit()
    return cur.lastrowid


def _req_dict(r):
    d = dict(r)
    d["params"] = json.loads(d.pop("params_json") or "{}")
    return d


def list_requests(con, status=None, requester=None):
    w, p = [], []
    if status:
        w.append("status=?"); p.append(status)
    if requester:
        w.append("requester=?"); p.append(requester)
    wsql = ("WHERE " + " AND ".join(w)) if w else ""
    return [_req_dict(r) for r in con.execute(
        f"SELECT * FROM report_requests {wsql} ORDER BY id DESC", p).fetchall()]


def get_request(con, rid):
    r = con.execute("SELECT * FROM report_requests WHERE id=?", [rid]).fetchone()
    if not r:
        return None
    d = _req_dict(r)
    if d.get("issued_report_id"):
        d["issued"] = get_issued(con, d["issued_report_id"])
    return d


def fulfill_request(con, rid, user):
    r = con.execute("SELECT * FROM report_requests WHERE id=?", [rid]).fetchone()
    if not r:
        raise LookupError("Request not found")
    # atomically CLAIM the request: only the caller that flips it out of 'pending'
    # proceeds, so a double-click or two certifiers can't both issue for one request.
    claim = con.execute("UPDATE report_requests SET status='fulfilled', handled_ts=datetime('now'), "
                        "handled_by=? WHERE id=? AND status='pending'", [user, rid])
    if claim.rowcount != 1:
        raise ValueError("Request has already been handled")
    params = json.loads(r["params_json"] or "{}")
    res = issue_report(con, r["kind"], params, user)   # server-assigns cert_no atomically, commits
    con.execute("UPDATE report_requests SET issued_report_id=? WHERE id=?", [res["id"], rid])
    add_audit(con, "Fulfilled Request", f"#{rid} for {r['requester']} -> {res.get('cert_no') or r['kind']}", user)
    con.commit()
    return res


def decline_request(con, rid, user, reason):
    r = con.execute("SELECT status FROM report_requests WHERE id=?", [rid]).fetchone()
    if not r:
        raise LookupError("Request not found")
    if r["status"] != "pending":
        raise ValueError("Request has already been handled")
    con.execute("UPDATE report_requests SET status='declined', handled_ts=datetime('now'), "
                "handled_by=?, decline_reason=? WHERE id=?", [user, reason, rid])
    add_audit(con, "Declined Request", f"#{rid}: {reason}", user)
    con.commit()
