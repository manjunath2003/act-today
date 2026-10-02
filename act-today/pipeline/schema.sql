-- PostgreSQL schema (SQLite version is created automatically by pipeline.py)
CREATE TABLE IF NOT EXISTS companies (
  id          SERIAL PRIMARY KEY,
  name        TEXT NOT NULL,
  url         TEXT NOT NULL UNIQUE,
  created_at  TIMESTAMPTZ DEFAULT now()
);
CREATE TABLE IF NOT EXISTS snapshots (
  id          SERIAL PRIMARY KEY,
  company_id  INT REFERENCES companies(id),
  taken_at    TIMESTAMPTZ DEFAULT now(),
  content_hash TEXT NOT NULL,
  analysis    JSONB NOT NULL,          -- validated LLM output
  sources     JSONB NOT NULL           -- [{origin, tier, url}]
);
CREATE TABLE IF NOT EXISTS scores (
  snapshot_id INT PRIMARY KEY REFERENCES snapshots(id),
  raw REAL, final REAL, confidence REAL, verdict TEXT, action TEXT
);
CREATE TABLE IF NOT EXISTS changes (
  id          SERIAL PRIMARY KEY,
  company_id  INT REFERENCES companies(id),
  snapshot_id INT REFERENCES snapshots(id),
  kind TEXT, fact TEXT, verdict TEXT, strength INT, why TEXT
);
CREATE INDEX IF NOT EXISTS idx_snap_company ON snapshots(company_id, taken_at DESC);
