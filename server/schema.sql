-- Collection Database — Phase 2 schema (SQLite)
-- Real relational schema with the keys, constraints and indexes the single-file app could not enforce.
PRAGMA journal_mode = WAL;   -- allows concurrent readers while one writer works
PRAGMA foreign_keys = ON;

-- ---- Users & access control (roles: admin / encoder / certifier / viewer) ----
CREATE TABLE IF NOT EXISTS users (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  username      TEXT NOT NULL UNIQUE,
  name          TEXT NOT NULL,
  password_hash TEXT NOT NULL,
  role          TEXT NOT NULL CHECK (role IN ('admin','encoder','certifier','viewer')),
  active        INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1)),
  created_ts    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ---- Reference / lookup data ----
CREATE TABLE IF NOT EXISTS orgmap (
  org_code TEXT PRIMARY KEY,
  clearing TEXT,
  ministry TEXT,
  moa      TEXT
);

CREATE TABLE IF NOT EXISTS collection_types (
  name       TEXT PRIMARY KEY,
  -- certification revenue-class this type rolls into (fixes the Phase-1 "unclassified type" gap structurally)
  cert_class TEXT NOT NULL DEFAULT 'Other Revenue (300)'
);

CREATE TABLE IF NOT EXISTS list_items (
  list_name TEXT NOT NULL,          -- payment_methods | lbp_branches | ministries | offices | months
  value     TEXT NOT NULL,
  ord       INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (list_name, value)
);

CREATE TABLE IF NOT EXISTS ministry_names (
  code      TEXT PRIMARY KEY,
  full_name TEXT
);

-- ---- Core: collection transactions ----
CREATE TABLE IF NOT EXISTS transactions (
  id            TEXT PRIMARY KEY,
  org_code      TEXT,
  clearing      TEXT,
  ministry      TEXT,
  office        TEXT,
  payment_method TEXT,
  lbp_branch    TEXT,
  amount        REAL NOT NULL DEFAULT 0 CHECK (amount >= 0),
  txn_date      TEXT NOT NULL,        -- ISO 'YYYY-MM-DD' — the single source of truth for the period
  year          TEXT NOT NULL,        -- derived from txn_date by the server (never trusted from the client)
  month         TEXT NOT NULL,        -- full month name, derived
  day           TEXT,                 -- derived
  type          TEXT,
  tagging       TEXT NOT NULL DEFAULT 'Actual' CHECK (tagging IN ('Actual','Target')),
  remarks       TEXT,
  created_by    TEXT,
  created_ts    TEXT NOT NULL DEFAULT (datetime('now')),
  updated_by    TEXT,
  updated_ts    TEXT
);
CREATE INDEX IF NOT EXISTS idx_txn_year     ON transactions(year);
CREATE INDEX IF NOT EXISTS idx_txn_ministry ON transactions(ministry);
CREATE INDEX IF NOT EXISTS idx_txn_office   ON transactions(office);
CREATE INDEX IF NOT EXISTS idx_txn_date     ON transactions(txn_date);

-- ---- Year-scoped ministry targets ----
CREATE TABLE IF NOT EXISTS targets (
  ministry TEXT NOT NULL,
  year     TEXT NOT NULL,
  target   REAL NOT NULL DEFAULT 0 CHECK (target >= 0),
  PRIMARY KEY (ministry, year)
);

-- ---- Certification config ----
CREATE TABLE IF NOT EXISTS agency_heads (
  office      TEXT PRIMARY KEY,
  name        TEXT,
  title       TEXT,
  agency_name TEXT
);

CREATE TABLE IF NOT EXISTS signatories (
  ord        INTEGER PRIMARY KEY,   -- display order
  role_label TEXT,
  name       TEXT,
  position   TEXT
);

CREATE TABLE IF NOT EXISTS cert_sequence (
  ym      TEXT PRIMARY KEY,          -- 'YYYY-MM'
  last_no INTEGER NOT NULL DEFAULT 0
);

-- ---- Free-form settings (letterhead lines, cert body/purpose, revenue schemes JSON) ----
CREATE TABLE IF NOT EXISTS settings (
  key   TEXT PRIMARY KEY,
  value TEXT
);

-- ---- Issued reports & certifications (as-issued snapshots) ----
CREATE TABLE IF NOT EXISTS issued_reports (
  id           TEXT PRIMARY KEY,
  kind         TEXT NOT NULL,        -- cert | summary | bytype | ledger
  cert_no      TEXT,
  params_json  TEXT,
  payload_json TEXT,                 -- aggregated rows only, never raw HTML
  total        REAL,
  row_count    INTEGER,
  by_user      TEXT,
  generated_ts TEXT NOT NULL DEFAULT (datetime('now'))
);
-- official certification numbers must be unique (partial index: many NULLs allowed for non-cert rows)
CREATE UNIQUE INDEX IF NOT EXISTS idx_issued_cert_no ON issued_reports(cert_no) WHERE cert_no IS NOT NULL;

-- ---- End-user report requests (#8): request -> review -> fulfil -> receive ----
CREATE TABLE IF NOT EXISTS report_requests (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  requester      TEXT NOT NULL,     -- username who requested
  requester_name TEXT,
  kind           TEXT NOT NULL,     -- cert | summary | bytype | ledger
  params_json    TEXT,
  note           TEXT,              -- purpose / message
  status         TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','fulfilled','declined')),
  created_ts     TEXT NOT NULL DEFAULT (datetime('now')),
  handled_ts     TEXT,
  handled_by     TEXT,
  issued_report_id TEXT,            -- links to issued_reports.id when fulfilled
  decline_reason TEXT
);

-- ---- Append-only audit trail (tamper-evident: no UPDATE/DELETE in app code) ----
CREATE TABLE IF NOT EXISTS audit (
  id      INTEGER PRIMARY KEY AUTOINCREMENT,
  ts      TEXT NOT NULL DEFAULT (datetime('now')),
  action  TEXT,
  detail  TEXT,
  by_user TEXT
);
