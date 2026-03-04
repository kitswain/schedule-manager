CREATE TABLE division_slot_preferences (
  division_code TEXT NOT NULL,
  venue_id TEXT NOT NULL,
  day_of_week TEXT NOT NULL CHECK (
    day_of_week IN ('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun')
  ),
  start_time TEXT NOT NULL,
  preference_level TEXT NOT NULL CHECK (
    preference_level IN ('preferred', 'allowed', 'avoid')
  ),
  notes TEXT,
  PRIMARY KEY (division_code, venue_id, day_of_week, start_time),
  FOREIGN KEY (division_code) REFERENCES divisions(division_code),
  FOREIGN KEY (venue_id) REFERENCES venues(venue_id)
);
