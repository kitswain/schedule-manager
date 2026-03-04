CREATE TABLE teams (
  team_id TEXT PRIMARY KEY,
  division_code TEXT NOT NULL,
  draft_slot INTEGER NOT NULL,
  coach_last_name TEXT NOT NULL,
  team_nickname TEXT NOT NULL,
  team_display_name TEXT NOT NULL,
  is_active INTEGER NOT NULL DEFAULT 1,
  FOREIGN KEY (division_code) REFERENCES divisions(division_code),
  UNIQUE (division_code, draft_slot)
);
