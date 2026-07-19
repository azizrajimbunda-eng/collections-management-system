#!/usr/bin/env python3
"""Collection Database — Phase 2 server (Step 4: logins, roles, controlled writes).

Run:  python3 app.py            (serves on http://0.0.0.0:5057)
Reads require login (any role). Writes are role-gated. Admin can do everything.
The ported web UI arrives in Step 5.
"""
import io
from datetime import timedelta

from flask import Flask, g, jsonify, request, send_file, session

import auth
import db
import export_xlsx
from auth import get_db, require_login, require_role
from pwutil import hash_password

app = Flask(__name__, static_folder="static", static_url_path="/static")
app.secret_key = auth.get_secret()
app.config.update(
    PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=False,   # office LAN is HTTP; set True behind HTTPS
    MAX_CONTENT_LENGTH=2 * 1024 * 1024,  # every API body is small JSON; cap runaway posts
)

# ensure late-added tables exist on an already-built database
_c = db.connect()
db.ensure_migrations(_c)
_c.close()


@app.teardown_appcontext
def _close_db(exc):
    con = g.pop("db", None)
    if con is not None:
        con.close()


def qarg(name, default=None):
    v = request.args.get(name, default)
    return v if (v is not None and v != "") else default


def body():
    d = request.get_json(silent=True)
    return d if isinstance(d, dict) else {}


def actor():
    return g.user["username"]


WEAK_PASSWORDS = {"admin123", "password", "12345678", "changeme", "admin1234", "adminadmin"}


def password_error(pw):
    """Shared password policy for every set-password path (min length + common-password block)."""
    if len(pw or "") < 8:
        return "Password must be at least 8 characters"
    if (pw or "").strip().lower() in WEAK_PASSWORDS:
        return "That password is too common — please choose a stronger one"
    return None


# ============================ status & auth ============================
@app.get("/")
def root():
    return app.send_static_file("index.html")


@app.get("/api/health")
def health():
    con = get_db()
    return jsonify({"ok": True, "transactions": db.scalar(con, "SELECT COUNT(*) FROM transactions")})


@app.post("/api/login")
def login():
    d = body()
    u = auth.do_login((d.get("username") or "").strip(), d.get("password") or "")
    if not u:
        return jsonify({"error": "Invalid username or password"}), 401
    return jsonify({"user": u})


@app.post("/api/logout")
def logout():
    session.clear()
    return jsonify({"ok": True})


@app.get("/api/me")
def me():
    return jsonify({"user": auth.current_user()})


# ============================ reads (login required) ============================
@app.get("/api/meta")
@require_login
def meta():
    return jsonify(db.get_meta(get_db()))


@app.get("/api/dashboard")
@require_login
def dashboard():
    return jsonify(db.dashboard(get_db(), qarg("year")))


@app.get("/api/ministry-summary")
@require_login
def ministry_summary():
    return jsonify({"rows": db.ministry_summary(get_db(), qarg("year"))})


@app.get("/api/transactions")
@require_login
def transactions():
    f = {k: qarg(k) for k in ("ministry", "office", "type", "tagging", "month", "year", "from", "to", "q")}
    try:
        limit = min(int(qarg("limit", 500)), 5000)
        offset = int(qarg("offset", 0))
    except ValueError:
        return jsonify({"error": "limit/offset must be integers"}), 400
    return jsonify(db.query_transactions(get_db(), f, limit, offset))


@app.get("/api/report/<mode>")
@require_role("certifier")   # printable reports: certifier + admin (viewers use the Requests portal)
def report(mode):
    con, year = get_db(), qarg("year")
    if mode in ("summary", "bytype"):
        if qarg("detail") == "drill":
            return jsonify(db.report_drill(con, year, mode, qarg("empty") == "1"))
        return jsonify(db.report_matrix(con, year, mode, qarg("groupby", "ministry")))
    if mode == "quarterly":
        return jsonify(db.report_quarterly(con, year))
    if mode == "official":
        return jsonify(db.report_official(con, year))
    if mode == "ledger":
        f = {k: qarg(k) for k in ("ministry", "office", "type", "month")}
        return jsonify(db.report_ledger(con, year, f))
    return jsonify({"error": "unknown report mode"}), 404


@app.get("/api/report/official.xlsx")
@require_role("certifier")
def export_official_report():
    con, year = get_db(), qarg("year")
    d = db.report_official(con, year)
    wb = export_xlsx.build_official_report_workbook(d, year)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return send_file(buf, as_attachment=True,
                     download_name=f"collection-report-official-{year or 'all'}.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.get("/api/certification")
@require_role("certifier")
def certification():
    office = qarg("office")
    if not office:
        return jsonify({"error": "office is required"}), 400
    try:
        return jsonify(db.cert_data(get_db(), office, qarg("month"), qarg("year")))
    except LookupError as e:
        return jsonify({"error": str(e)}), 404


@app.get("/api/cert-next")
@require_role("certifier")
def cert_next():
    year, month = qarg("year"), qarg("month")
    if not (year and month) or month not in db.MONTHS:
        return jsonify({"cert_no": None})
    return jsonify({"cert_no": db.next_cert_no(get_db(), year, db.MONTHS.index(month))})


@app.get("/api/agencies")
@require_login
def agencies():
    ministry = qarg("ministry")
    if not ministry:
        return jsonify({"ministries": db.get_meta(get_db())["ministries"]})
    return jsonify({"ministry": ministry, "agencies": db.agencies_by_ministry(get_db(), ministry)})


@app.get("/api/agency-heads")
@require_login
def agency_heads():
    return jsonify({"rows": db.rows(get_db(),
                   "SELECT office, name, title, agency_name FROM agency_heads ORDER BY office")})


@app.get("/api/signatories")
@require_login
def signatories():
    return jsonify({"rows": db.rows(get_db(),
                   "SELECT ord, role_label, name, position FROM signatories ORDER BY ord")})


@app.get("/api/audit")
@require_login
def audit():
    try:
        limit = min(int(qarg("limit", 500)), 2000)
    except ValueError:
        return jsonify({"error": "limit must be an integer"}), 400
    return jsonify({"rows": db.get_audit(get_db(), limit)})


@app.get("/api/issued")
@require_role("certifier")
def issued():
    return jsonify({"rows": db.list_issued(get_db())})


@app.get("/api/issued/<rid>")
@require_role("certifier")
def issued_one(rid):
    r = db.get_issued(get_db(), rid)
    return (jsonify(r), 200) if r else (jsonify({"error": "not found"}), 404)


@app.get("/api/transactions/<tid>")
@require_login
def get_txn(tid):
    r = db.get_transaction(get_db(), tid)
    return (jsonify(r), 200) if r else (jsonify({"error": "not found"}), 404)


# ============================ writes (role-gated) ============================
@app.post("/api/transactions")
@require_role("encoder")
def add_txn():
    try:
        tid = db.add_transaction(get_db(), body(), actor())
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"id": tid}), 201


@app.put("/api/transactions/<tid>")
@require_role("encoder")
def edit_txn(tid):
    try:
        db.update_transaction(get_db(), tid, body(), actor())
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except LookupError as e:
        return jsonify({"error": str(e)}), 404
    return jsonify({"id": tid})


@app.delete("/api/transactions/<tid>")
@require_role("admin")   # deleting collection records is admin-only
def del_txn(tid):
    try:
        db.delete_transaction(get_db(), tid, actor())
    except LookupError as e:
        return jsonify({"error": str(e)}), 404
    return jsonify({"ok": True})


@app.post("/api/issue")
@require_role("certifier")
def issue():
    # the certification number is always assigned server-side in issue_report,
    # never taken from the client, so it can't be forced, reused, or skipped.
    d = body()
    try:
        res = db.issue_report(get_db(), d.get("kind"), d.get("params") or {}, actor())
    except LookupError as e:
        return jsonify({"error": str(e)}), 404
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify(res), 201


# ============================ report requests (#8) ============================
@app.post("/api/requests")
@require_login
def create_request():
    d = body()
    try:
        rid = db.create_request(get_db(), g.user["username"], g.user["name"],
                                d.get("kind"), d.get("params") or {}, (d.get("note") or "").strip())
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"id": rid}), 201


@app.get("/api/requests")
@require_login
def list_requests():
    u = g.user
    staff = u["role"] in ("admin", "certifier")
    rr = db.list_requests(get_db(), qarg("status"), None if staff else u["username"])
    return jsonify({"rows": rr, "canFulfill": staff, "me": u["username"]})


@app.get("/api/requests/<int:rid>")
@require_login
def get_request(rid):
    u = g.user
    r = db.get_request(get_db(), rid)
    if not r:
        return jsonify({"error": "not found"}), 404
    if u["role"] not in ("admin", "certifier") and r["requester"] != u["username"]:
        return jsonify({"error": "forbidden"}), 403
    return jsonify(r)


@app.post("/api/requests/<int:rid>/fulfill")
@require_role("certifier")
def fulfill_request(rid):
    try:
        res = db.fulfill_request(get_db(), rid, actor())
    except LookupError as e:
        return jsonify({"error": str(e)}), 404
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify(res)


@app.post("/api/requests/<int:rid>/decline")
@require_role("certifier")
def decline_request(rid):
    try:
        db.decline_request(get_db(), rid, actor(), (body().get("reason") or "").strip())
    except LookupError as e:
        return jsonify({"error": str(e)}), 404
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True})


# ============================ admin ============================
@app.get("/api/users")
@require_role("admin")
def users():
    return jsonify({"rows": db.list_users(get_db())})


@app.post("/api/users")
@require_role("admin")
def create_user():
    d = body()
    un, nm = (d.get("username") or "").strip(), (d.get("name") or "").strip()
    pw, role = d.get("password") or "", d.get("role")
    if not un or not nm or role not in auth.ROLES:
        return jsonify({"error": "username, name and a valid role are required"}), 400
    perr = password_error(pw)
    if perr:
        return jsonify({"error": perr}), 400
    try:
        db.create_user(get_db(), un, nm, hash_password(pw), role, actor())
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True}), 201


@app.post("/api/users/<int:uid>/active")
@require_role("admin")
def user_active(uid):
    db.set_user_active(get_db(), uid, bool(body().get("active")), actor())
    return jsonify({"ok": True})


@app.post("/api/users/<int:uid>/password")
@require_role("admin")
def user_password(uid):
    pw = body().get("password") or ""
    perr = password_error(pw)
    if perr:
        return jsonify({"error": perr}), 400
    db.set_password(get_db(), uid, hash_password(pw), actor())
    return jsonify({"ok": True})


@app.get("/api/export.xlsx")
@require_role("admin")
def export_workbook():
    wb = export_xlsx.build_workbook(get_db())
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name="collection-export.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.post("/api/target")
@require_role("admin")
def set_target():
    d = body()
    if not d.get("ministry") or not d.get("year"):
        return jsonify({"error": "ministry and year are required"}), 400
    try:
        db.set_target(get_db(), d["ministry"], d["year"], d.get("target") or 0, actor())
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True})


# ---- admin: certification-config editors ----
@app.get("/api/cert-offices")
@require_role("admin")
def cert_offices():
    return jsonify({"rows": db.cert_offices(get_db())})


@app.post("/api/agency-head")
@require_role("admin")
def save_agency_head():
    d = body()
    try:
        db.save_agency_head(get_db(), d.get("office"), d.get("name"),
                            d.get("title"), d.get("agency_name"), actor())
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True})


@app.post("/api/signatories")
@require_role("admin")
def save_signatories():
    try:
        db.save_signatories(get_db(), body().get("signatories") or [], actor())
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True})


@app.post("/api/cert-class")
@require_role("admin")
def save_cert_class():
    d = body()
    try:
        db.save_cert_class(get_db(), d.get("name"), d.get("cert_class"), actor())
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True})


@app.post("/api/letterhead")
@require_role("admin")
def save_letterhead():
    try:
        db.save_letterhead(get_db(), body(), actor())
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True})


@app.get("/api/orgcodes")
@require_role("admin")
def orgcodes():
    return jsonify({"rows": db.orgmap_rows(get_db())})


@app.post("/api/orgcode-new")
@require_role("admin")
def add_orgcode():
    d = body()
    try:
        db.add_orgcode(get_db(), d.get("org_code"), d.get("ministry"), d.get("office"),
                       d.get("clearing"), actor())
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True}), 201


@app.post("/api/orgcode")
@require_role("admin")
def save_orgcode():
    d = body()
    try:
        res = db.save_orgcode(get_db(), d.get("org_code"), d.get("ministry"), actor(),
                              d.get("regroup", True))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify(res)


@app.get("/api/cert-groups")
@require_role("admin")
def cert_groups_list():
    return jsonify({"rows": db.cert_group_rows(get_db())})


@app.post("/api/cert-group")
@require_role("admin")
def cert_group_save():
    d = body()
    try:
        db.save_cert_group(get_db(), d.get("name"), d.get("ministry"), d.get("org_code"),
                           d.get("head_office"), d.get("members") or [], actor(), d.get("original"))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True})


@app.post("/api/cert-group-delete")
@require_role("admin")
def cert_group_delete():
    try:
        db.delete_cert_group(get_db(), body().get("name"), actor())
    except LookupError as e:
        return jsonify({"error": str(e)}), 404
    return jsonify({"ok": True})


@app.post("/api/collection-type")
@require_role("admin")
def add_collection_type():
    d = body()
    try:
        db.add_collection_type(get_db(), d.get("name"), d.get("cert_class"), actor())
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True}), 201


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=5057)
    ap.add_argument("--debug", action="store_true",
                    help="Werkzeug debugger — loopback only; refused on a network-facing host")
    a = ap.parse_args()
    if a.debug and a.host not in ("127.0.0.1", "localhost"):
        ap.error("--debug is only allowed with --host 127.0.0.1 (never expose the debugger on the LAN)")
    app.run(host=a.host, port=a.port, debug=a.debug)
