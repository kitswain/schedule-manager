CREATE TABLE divisions (
  division_code TEXT PRIMARY KEY,
  division_name TEXT NOT NULL,
  gender TEXT NOT NULL CHECK (gender IN ('Girls', 'Boys')),
  min_grade INTEGER NOT NULL,
  max_grade INTEGER NOT NULL,
  grade_band_label TEXT NOT NULL,
  sort_order INTEGER NOT NULL,
  is_active INTEGER NOT NULL DEFAULT 1,
  CHECK (min_grade <= max_grade)
);
