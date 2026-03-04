CREATE TABLE venues (
  venue_id TEXT PRIMARY KEY,
  venue_code TEXT NOT NULL,
  venue_name TEXT NOT NULL,
  is_active INTEGER NOT NULL DEFAULT 1,
  notes TEXT
);
