CREATE TABLE division_venue_preferences (
  division_code TEXT NOT NULL,
  venue_id TEXT NOT NULL,
  preference_rank INTEGER NOT NULL,
  is_primary INTEGER NOT NULL DEFAULT 0,
  notes TEXT,
  PRIMARY KEY (division_code, venue_id),
  FOREIGN KEY (division_code) REFERENCES divisions(division_code),
  FOREIGN KEY (venue_id) REFERENCES venues(venue_id)
);
