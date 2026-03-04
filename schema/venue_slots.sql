CREATE TABLE venue_slots (
  slot_id TEXT PRIMARY KEY,
  venue_id TEXT NOT NULL,
  day_of_week TEXT NOT NULL CHECK (
    day_of_week IN ('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun')
  ),
  start_time TEXT NOT NULL,
  is_available INTEGER NOT NULL DEFAULT 1,
  is_preferred INTEGER NOT NULL DEFAULT 1,
  notes TEXT,
  FOREIGN KEY (venue_id) REFERENCES venues(venue_id),
  UNIQUE (venue_id, day_of_week, start_time)
);
