CREATE TABLE coaches (
  coach_id TEXT PRIMARY KEY,
  coach_last_name TEXT NOT NULL,
  coach_display_name TEXT NOT NULL,
  is_active INTEGER NOT NULL DEFAULT 1,
  notes TEXT
);
